#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
exec 9>"${TMPDIR:-/tmp}/romatic-deploy.lock"
if ! flock -n 9; then
  echo "Another Romatic Club deployment is already running." >&2
  exit 75
fi

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-.env}"
COMPOSE_FILE="${ROMATIC_COMPOSE_FILE:-compose.yaml}"
REMOTE="${ROMATIC_DEPLOY_REMOTE:-origin}"
BRANCH="${ROMATIC_DEPLOY_BRANCH:-main}"
TARGET_OVERRIDE="${ROMATIC_DEPLOY_TARGET_SHA:-}"
HEALTH_ATTEMPTS="${ROMATIC_HEALTH_ATTEMPTS:-60}"
HEALTH_INTERVAL="${ROMATIC_HEALTH_INTERVAL:-3}"
HEALTH_STABLE_POLLS="${ROMATIC_HEALTH_STABLE_POLLS:-3}"
MIN_FREE_BYTES="${ROMATIC_DEPLOY_MIN_FREE_BYTES:-1073741824}"

cd "$APP_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $APP_DIR/$ENV_FILE" >&2
  exit 2
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Missing $APP_DIR/$COMPOSE_FILE" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked working tree changes detected; deployment aborted." >&2
  git status --short >&2
  exit 3
fi
if ! [[ "$HEALTH_STABLE_POLLS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ROMATIC_HEALTH_STABLE_POLLS must be a positive integer." >&2
  exit 2
fi
if ! [[ "$MIN_FREE_BYTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "ROMATIC_DEPLOY_MIN_FREE_BYTES must be a positive integer." >&2
  exit 2
fi

mapfile -t host_config < <(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
for name, default in (
    ("ROMATIC_DATA_DIR", "/srv/romatic-club-max/server-data"),
    ("ROMATIC_APP_UID", "10001"),
    ("ROMATIC_APP_GID", "10001"),
):
    print(values.get(name, default))
PY
)

data_dir="${host_config[0]}"
app_uid="${host_config[1]}"
app_gid="${host_config[2]}"
if ! [[ "$app_uid" =~ ^[1-9][0-9]*$ && "$app_gid" =~ ^[1-9][0-9]*$ ]]; then
  echo "ROMATIC_APP_UID and ROMATIC_APP_GID must be positive integers." >&2
  exit 2
fi

mkdir -p \
  "$data_dir/backups" \
  "$data_dir/runtime/supervisor" \
  "$data_dir/runtime/docker-config" \
  "$data_dir/runtime/migrations" \
  "$data_dir/runtime/restore-drills"
chmod 0700 \
  "$data_dir/runtime/docker-config" \
  "$data_dir/runtime/migrations" \
  "$data_dir/runtime/restore-drills"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$data_dir/runtime/docker-config}"
export COMPOSE_BAKE=false
compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
compose_ops=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile operations)

previous_sha="$(git rev-parse HEAD)"
deployment_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$data_dir/backups/predeploy-${deployment_stamp}-${previous_sha:0:12}.dump"
session_snapshot_path="$data_dir/backups/userbot-session-predeploy-${deployment_stamp}-${previous_sha:0:12}.session"
deployment_log="$data_dir/runtime/supervisor/deploy-${deployment_stamp}-${previous_sha:0:12}.log"
migration_plan_path="$data_dir/runtime/migrations/plan-${deployment_stamp}-${previous_sha:0:12}.json"
migration_result_path="$data_dir/runtime/migrations/result-${deployment_stamp}-${previous_sha:0:12}.json"
session_snapshot_created=0
session_mutated=0
code_switched=0
runtime_replaced=0
schema_changed=0
schema_code_rollback_safe=1

: >"$deployment_log"
chmod 0600 "$deployment_log"
exec > >(tee -a "$deployment_log") 2>&1

echo "Deployment log: $deployment_log"

wait_service() {
  local service="$1"
  local container_id health status restart_count
  local stable_polls=0

  container_id="$("${compose[@]}" ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "Compose service was not created: $service" >&2
    return 1
  fi

  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    restart_count="$(docker inspect --format '{{.RestartCount}}' "$container_id")"

    if [[ "$restart_count" =~ ^[0-9]+$ ]] && ((restart_count > 0)); then
      echo "$service restarted during deployment smoke: restart_count=$restart_count" >&2
      "${compose[@]}" logs --tail 200 "$service" >&2 || true
      return 1
    fi

    case "$status" in
      restarting|exited|dead|removing)
        echo "$service failed with container state: $status" >&2
        "${compose[@]}" logs --tail 200 "$service" >&2 || true
        return 1
        ;;
    esac

    case "$health" in
      healthy|running)
        stable_polls=$((stable_polls + 1))
        if ((stable_polls >= HEALTH_STABLE_POLLS)); then
          return 0
        fi
        ;;
      unhealthy)
        echo "$service failed with health state: $health" >&2
        "${compose[@]}" logs --tail 200 "$service" >&2 || true
        return 1
        ;;
      *)
        stable_polls=0
        ;;
    esac

    sleep "$HEALTH_INTERVAL"
  done

  echo "$service did not remain healthy without restarts for $HEALTH_STABLE_POLLS polls." >&2
  "${compose[@]}" logs --tail 200 "$service" >&2 || true
  return 1
}

snapshot_userbot_session() {
  local exists_status snapshot_tmp

  if "${compose[@]}" run --rm -T --no-deps \
    --user "$app_uid:$app_gid" userbot python - <<'PY'
from pathlib import Path

raise SystemExit(
    0
    if Path("/run/romatic-userbot-session/userbot.session").is_file()
    else 44
)
PY
  then
    :
  else
    exists_status="$?"
    if [[ "$exists_status" == "44" ]]; then
      echo "Userbot session file is absent; compatibility probe skipped."
      return 0
    fi
    return "$exists_status"
  fi

  snapshot_tmp="${session_snapshot_path}.tmp"
  rm -f "$snapshot_tmp"
  if ! "${compose[@]}" run --rm -T --no-deps \
    --user "$app_uid:$app_gid" \
    -e ROMATIC_EXPECTED_APP_UID="$app_uid" \
    -e ROMATIC_EXPECTED_APP_GID="$app_gid" \
    userbot python - <<'PY' >"$snapshot_tmp"
import os
import sqlite3
import stat
import sys
from pathlib import Path

source_path = Path("/run/romatic-userbot-session/userbot.session")
target_path = Path("/tmp/userbot-session-snapshot.session")
expected_uid = int(os.environ["ROMATIC_EXPECTED_APP_UID"])
expected_gid = int(os.environ["ROMATIC_EXPECTED_APP_GID"])
source_stat = source_path.stat()
source_mode = stat.S_IMODE(source_stat.st_mode)
if source_stat.st_uid != expected_uid or source_stat.st_gid != expected_gid:
    raise RuntimeError(
        f"Unexpected Telethon session owner: {source_stat.st_uid}:{source_stat.st_gid}; "
        f"expected {expected_uid}:{expected_gid}"
    )
if source_mode & 0o077:
    raise RuntimeError(f"Telethon session permissions are too broad: {source_mode:04o}")

target_path.unlink(missing_ok=True)
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(target_path)
try:
    source.backup(target)
    result = target.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"Telethon session quick_check failed: {result!r}")
finally:
    target.close()
    source.close()

sys.stdout.buffer.write(target_path.read_bytes())
target_path.unlink(missing_ok=True)
PY
  then
    rm -f "$snapshot_tmp"
    return 1
  fi

  python3 - "$snapshot_tmp" <<'PY'
import sqlite3
import sys
from pathlib import Path

snapshot_path = Path(sys.argv[1])
if not snapshot_path.is_file() or snapshot_path.stat().st_size == 0:
    raise RuntimeError("Telethon session snapshot is empty")
connection = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"Host session snapshot quick_check failed: {result!r}")
finally:
    connection.close()
PY

  chmod 0600 "$snapshot_tmp"
  mv -f "$snapshot_tmp" "$session_snapshot_path"
  session_snapshot_created=1
  echo "Verified userbot session snapshot: $session_snapshot_path"
}

restore_userbot_session() {
  if [[ "$session_snapshot_created" != "1" ]]; then
    return 0
  fi

  echo "Restoring pre-deploy Telethon session snapshot..." >&2
  "${compose[@]}" stop userbot >&2 || true
  cat "$session_snapshot_path" | "${compose[@]}" run --rm -T --no-deps \
    --user "$app_uid:$app_gid" userbot python -c '
import os
import sqlite3
import sys
from pathlib import Path

session_file = Path("/run/romatic-userbot-session/userbot.session")
restore_path = Path(f"{session_file}.restore")
payload = sys.stdin.buffer.read()
if not payload:
    raise RuntimeError("Telethon session snapshot payload is empty")
for candidate in (
    Path(f"{session_file}-journal"),
    Path(f"{session_file}-shm"),
    Path(f"{session_file}-wal"),
    restore_path,
):
    candidate.unlink(missing_ok=True)
restore_path.write_bytes(payload)
os.chmod(restore_path, 0o600)
connection = sqlite3.connect(f"file:{restore_path}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"Snapshot quick_check failed: {result!r}")
finally:
    connection.close()
os.replace(restore_path, session_file)
print("Telethon session restored")
'
  session_mutated=0
  echo "Telethon session restored from $session_snapshot_path" >&2
}

rollback_deployment() {
  local exit_code="$?"
  local session_restore_ok=1
  local session_was_mutated="$session_mutated"

  trap - ERR INT TERM
  if [[ "$code_switched" == "1" ]]; then
    if [[ "$session_mutated" == "1" ]] && ! restore_userbot_session; then
      session_restore_ok=0
      echo "Telethon session restore failed; userbot will remain stopped." >&2
    fi

    if [[ "$schema_changed" == "1" && "$schema_code_rollback_safe" != "1" ]]; then
      echo "Deployment failed after a non-code-rollback-safe schema change." >&2
      echo "Automatic code rollback is blocked; forward-fix or verified database restore is required." >&2
      "${compose[@]}" stop bot userbot >&2 || true
      echo "Rollback matrix: strategy=forward-fix-or-restore code_rollback=blocked" >&2
    else
      echo "Deployment failed; rolling application code back to $previous_sha" >&2
      git reset --hard "$previous_sha" >&2 || true
      "${compose[@]}" build postgres bot userbot supervisor-proxy >&2 || true
      if [[ "$runtime_replaced" == "1" || "$session_was_mutated" == "1" ]]; then
        if [[ "$session_restore_ok" == "1" ]]; then
          "${compose[@]}" up -d postgres supervisor-proxy bot userbot >&2 || true
        else
          "${compose[@]}" up -d postgres supervisor-proxy bot >&2 || true
        fi
      else
        echo "Running containers were not replaced; runtime left untouched." >&2
      fi
      echo "Rollback matrix: strategy=code-only code_rollback=allowed" >&2
    fi

    echo "Database was not automatically restored." >&2
    echo "Verified pre-deploy dump: $backup_path" >&2
    if [[ "$session_snapshot_created" == "1" ]]; then
      echo "Verified pre-deploy Telethon session: $session_snapshot_path" >&2
    fi
    [[ -s "$migration_plan_path" ]] && echo "Migration plan: $migration_plan_path" >&2
    [[ -s "$migration_result_path" ]] && echo "Migration result: $migration_result_path" >&2
    echo "Full deployment log: $deployment_log" >&2
  fi
  exit "$exit_code"
}
trap rollback_deployment ERR INT TERM

echo "Fetching $REMOTE/$BRANCH..."
git fetch --prune "$REMOTE" "$BRANCH"
remote_sha="$(git rev-parse "$REMOTE/$BRANCH")"
if [[ -n "$TARGET_OVERRIDE" ]]; then
  target_sha="$(git rev-parse --verify "${TARGET_OVERRIDE}^{commit}")"
  if ! git merge-base --is-ancestor "$target_sha" "$remote_sha"; then
    echo "Requested target is not an ancestor of $REMOTE/$BRANCH: $target_sha" >&2
    exit 4
  fi
  echo "Using verified deployment target $target_sha"
else
  target_sha="$remote_sha"
fi

if [[ "$target_sha" == "$previous_sha" ]]; then
  echo "Romatic Club is already at $target_sha"
  exit 0
fi

echo "Starting PostgreSQL and validating deployment capacity..."
"${compose[@]}" up -d postgres
wait_service postgres

read -r postgres_version_num database_size_bytes < <(
  "${compose[@]}" exec -T postgres sh -ceu '
    psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
SELECT current_setting('\''server_version_num'\'')::integer;
SELECT pg_database_size(current_database());
SQL
  ' | tr '\n' ' '
)
if ((postgres_version_num / 10000 != 17)); then
  echo "Unsupported production PostgreSQL major: $postgres_version_num" >&2
  exit 4
fi
available_bytes="$(df -PB1 "$data_dir" | awk 'NR == 2 {print $4}')"
required_bytes=$((database_size_bytes * 3 + MIN_FREE_BYTES))
if ((available_bytes < required_bytes)); then
  echo "Insufficient free space for backup and restore drill: available=$available_bytes required=$required_bytes" >&2
  exit 4
fi
echo "Database preflight OK: postgres=$postgres_version_num size=$database_size_bytes free=$available_bytes"

echo "Creating pre-deploy PostgreSQL dump..."
"${compose[@]}" exec -T postgres sh -ceu '
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc
' > "$backup_path"
test -s "$backup_path"
"${compose[@]}" exec -T postgres pg_restore -l < "$backup_path" >/dev/null
chmod 0600 "$backup_path"
echo "Verified dump: $backup_path"

echo "Creating pre-deploy Telethon session snapshot..."
snapshot_userbot_session

echo "Preparing $target_sha..."
git reset --hard "$target_sha"
code_switched=1
"${compose[@]}" build --pull postgres bot userbot supervisor-proxy

echo "Validating target configuration before replacing runtime..."
"${compose[@]}" run --rm --no-deps bot python - <<'PY'
from bot.core.settings import BotProcessSettings

config = BotProcessSettings.from_env(project_root="/app")
assert config.bot.bot_token
assert config.database.url
assert config.database.auto_migrate is False
print("Bot configuration preflight OK")
PY

"${compose[@]}" run --rm --no-deps userbot python - <<'PY'
from bot.core.settings import UserbotProcessSettings

config = UserbotProcessSettings.from_env(project_root="/app")
assert config.userbot.api_id > 0
assert config.userbot.api_hash
assert config.database.url
assert config.database.auto_migrate is False
print("Userbot configuration preflight OK")
PY

echo "Running disposable PostgreSQL restore drill against target code..."
ROMATIC_APP_DIR="$APP_DIR" \
ROMATIC_ENV_FILE="$ENV_FILE" \
ROMATIC_COMPOSE_FILE="$COMPOSE_FILE" \
  bash deploy/server/restore-drill.sh "$backup_path"

echo "Planning production migrations through the controlled runner..."
migration_plan_json="$(
  "${compose_ops[@]}" run --rm -T migration-runner \
    python -m db.migrator plan --json
)"
printf '%s\n' "$migration_plan_json" > "$migration_plan_path"
chmod 0600 "$migration_plan_path"
python3 - "$migration_plan_path" <<'PY'
import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    "Migration plan OK: "
    f"current={plan['current_version']} target={plan['target_version']} "
    f"pending={plan['pending_count']} rollback={plan['rollback_strategies']}"
)
PY

if [[ "$session_snapshot_created" == "1" ]]; then
  echo "Checking target Telethon against an isolated session copy..."
  cat "$session_snapshot_path" | "${compose[@]}" run --rm -T --no-deps \
    --user "$app_uid:$app_gid" userbot python -c '
import sys
from pathlib import Path
from telethon.sessions import SQLiteSession
from userbot.session_schema import repair_session_schema

payload = sys.stdin.buffer.read()
if not payload:
    raise RuntimeError("Telethon session snapshot payload is empty")
probe_path = Path("/tmp/session-probe/userbot.session")
probe_path.parent.mkdir(parents=True, exist_ok=True)
probe_path.write_bytes(payload)
probe_path.chmod(0o600)
repair_session_schema(probe_path)
session = SQLiteSession("/tmp/session-probe/userbot")
try:
    if session.auth_key is None:
        raise RuntimeError("Target Telethon session has no auth key")
finally:
    session.close()
print("Target Telethon session compatibility preflight OK")
'
fi

echo "Applying production migrations through the single controlled runner..."
migration_result_json="$(
  "${compose_ops[@]}" run --rm -T migration-runner \
    python -m db.migrator apply --json
)"
printf '%s\n' "$migration_result_json" > "$migration_result_path"
chmod 0600 "$migration_result_path"
read -r applied_count rollback_safe pending_after < <(
  python3 - "$migration_result_path" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    result["applied_count"],
    1 if result["code_rollback_safe"] else 0,
    result["pending_count"],
)
PY
)
if [[ "$pending_after" != "0" ]]; then
  echo "Controlled migration runner left pending migrations" >&2
  exit 1
fi
if ((applied_count > 0)); then
  schema_changed=1
  schema_code_rollback_safe="$rollback_safe"
fi
echo "Controlled migration runner OK: applied=$applied_count code_rollback_safe=$rollback_safe"

if [[ "$session_snapshot_created" == "1" ]]; then
  echo "Stopping userbot before live Telethon session migration..."
  "${compose[@]}" stop userbot
  session_mutated=1
  "${compose[@]}" run --rm --no-deps userbot python - <<'PY'
from pathlib import Path
from telethon.sessions import SQLiteSession
from userbot.session import secure_session_files
from userbot.session_schema import repair_session_schema

session_path = Path("/run/romatic-userbot-session/userbot.session")
changed = repair_session_schema(session_path)
session = SQLiteSession("/run/romatic-userbot-session/userbot")
try:
    if session.auth_key is None:
        raise RuntimeError("Live Telethon session has no auth key")
finally:
    session.close()
secure_session_files("/run/romatic-userbot-session/userbot")
print("Live Telethon session repaired" if changed else "Live Telethon session schema OK")
PY
fi

echo "Deploying $target_sha..."
"${compose[@]}" up -d --remove-orphans postgres supervisor-proxy bot userbot
runtime_replaced=1

wait_service bot
wait_service userbot
wait_service supervisor-proxy
wait_service postgres

"${compose[@]}" exec -T bot python - <<'PY'
import asyncio
import json
import os
from urllib.request import urlopen

import asyncpg


async def database_probe() -> None:
    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        assert await connection.fetchval("SELECT 1") == 1
        pending = await connection.fetchval(
            "SELECT count(*) FROM public.schema_migrations WHERE version IS NULL"
        )
        assert pending == 0
    finally:
        await connection.close()


asyncio.run(database_probe())
with urlopen("http://127.0.0.1:8081/healthz", timeout=5) as response:
    assert response.status == 200
    assert json.load(response) == {"live": True}
with urlopen("http://127.0.0.1:8081/readyz", timeout=5) as response:
    readiness = json.load(response)
    assert response.status == 200
    assert readiness["ready"] is True
    assert readiness["database"] is True
with urlopen("http://127.0.0.1:8081/metrics", timeout=5) as response:
    metrics = response.read().decode("utf-8")
for metric in ("process_uptime_seconds", "application_ready 1", "database_ready 1"):
    assert metric in metrics
print("Romatic server smoke OK")
print("Observability deployment smoke OK")
PY

"${compose_ops[@]}" run --rm -T migration-runner \
  python -m db.migrator verify --json >/dev/null

deployed_sha="$(git rev-parse HEAD)"
if [[ "$deployed_sha" != "$target_sha" ]]; then
  echo "Deployed SHA mismatch: expected $target_sha, got $deployed_sha" >&2
  false
fi

archive_args=("$backup_path")
if [[ "$session_snapshot_created" == "1" ]]; then
  archive_args+=("$session_snapshot_path")
fi
ROMATIC_APP_DIR="$APP_DIR" \
ROMATIC_ENV_FILE="$ENV_FILE" \
ROMATIC_COMPOSE_FILE="$COMPOSE_FILE" \
  bash deploy/server/archive-backups.sh "${archive_args[@]}"

code_switched=0
runtime_replaced=0
session_mutated=0
trap - ERR INT TERM

echo "Romatic Club deployment succeeded: $deployed_sha"
echo "Verified pre-deploy backup: $backup_path"
if [[ "$session_snapshot_created" == "1" ]]; then
  echo "Verified pre-deploy Telethon session: $session_snapshot_path"
fi
echo "Migration plan: $migration_plan_path"
echo "Migration result: $migration_result_path"
if [[ "$schema_changed" == "1" && "$schema_code_rollback_safe" != "1" ]]; then
  echo "Rollback matrix: strategy=forward-fix-or-restore code_rollback=blocked"
else
  echo "Rollback matrix: strategy=code-only code_rollback=allowed"
fi
echo "Full deployment log: $deployment_log"
"${compose[@]}" ps
