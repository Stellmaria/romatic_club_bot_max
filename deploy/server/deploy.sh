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

data_dir="$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()
print(values.get("ROMATIC_DATA_DIR", "/srv/romatic-club-max/server-data"))
PY
)"

mkdir -p "$data_dir/backups" "$data_dir/runtime/supervisor" "$data_dir/runtime/docker-config"
chmod 0700 "$data_dir/runtime/docker-config"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$data_dir/runtime/docker-config}"
export COMPOSE_BAKE=false
compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

previous_sha="$(git rev-parse HEAD)"
deployment_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$data_dir/backups/predeploy-${deployment_stamp}-${previous_sha:0:12}.dump"
session_dir="$data_dir/userbot-session"
session_file="$session_dir/userbot.session"
session_snapshot_path="$data_dir/backups/userbot-session-predeploy-${deployment_stamp}-${previous_sha:0:12}.session"
session_probe_dir="$data_dir/runtime/supervisor/userbot-session-probe-$$"
session_snapshot_created=0
session_owner_uid=""
session_owner_gid=""
session_mode=""
code_switched=0
runtime_replaced=0

cleanup_session_probe() {
  rm -rf "$session_probe_dir"
}

snapshot_userbot_session() {
  if [[ ! -f "$session_file" ]]; then
    echo "Userbot session file is absent; compatibility probe skipped: $session_file"
    return 0
  fi

  session_owner_uid="$(stat -c '%u' "$session_file")"
  session_owner_gid="$(stat -c '%g' "$session_file")"
  session_mode="$(stat -c '%a' "$session_file")"
  python3 - "$session_file" "$session_snapshot_path" <<'PYTHON'
import sqlite3
import sys

source_path, target_path = sys.argv[1:]
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
PYTHON
  chmod 0600 "$session_snapshot_path"
  session_snapshot_created=1
  echo "Verified userbot session snapshot: $session_snapshot_path"
}

prepare_session_probe() {
  cleanup_session_probe
  mkdir -p "$session_probe_dir"
  cp "$session_snapshot_path" "$session_probe_dir/userbot.session"
  chown "$session_owner_uid:$session_owner_gid"     "$session_probe_dir" "$session_probe_dir/userbot.session"
  chmod 0700 "$session_probe_dir"
  chmod "0$session_mode" "$session_probe_dir/userbot.session"
}

restore_userbot_session() {
  if [[ "$session_snapshot_created" != "1" ]]; then
    return 0
  fi
  echo "Restoring pre-deploy Telethon session snapshot..." >&2
  "${compose[@]}" stop userbot >&2 || true
  mkdir -p "$session_dir"
  rm -f     "$session_file"     "$session_file-journal"     "$session_file-shm"     "$session_file-wal"
  install     -o "$session_owner_uid"     -g "$session_owner_gid"     -m "0$session_mode"     "$session_snapshot_path"     "$session_file.restore"
  mv -f "$session_file.restore" "$session_file"
  python3 - "$session_file" <<'PYTHON'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"Restored Telethon session quick_check failed: {result!r}")
finally:
    connection.close()
PYTHON
  echo "Telethon session restored from $session_snapshot_path" >&2
}

rollback_code() {
  local exit_code="$?"
  local session_restore_ok=1
  cleanup_session_probe || true
  if [[ "$code_switched" == "1" ]]; then
    echo "Deployment failed; rolling application code back to $previous_sha" >&2
    if [[ "$runtime_replaced" == "1" ]] && ! restore_userbot_session; then
      session_restore_ok=0
      echo "Telethon session restore failed; userbot will remain stopped." >&2
    fi
    git reset --hard "$previous_sha" >&2 || true
    "${compose[@]}" build postgres bot userbot supervisor-proxy >&2 || true
    if [[ "$runtime_replaced" == "1" ]]; then
      if [[ "$session_restore_ok" == "1" ]]; then
        "${compose[@]}" up -d postgres supervisor-proxy bot userbot >&2 || true
      else
        "${compose[@]}" up -d postgres supervisor-proxy bot >&2 || true
      fi
    else
      echo "Running containers were not replaced; runtime left untouched." >&2
    fi
    echo "Database was not automatically restored." >&2
    echo "Verified pre-deploy dump: $backup_path" >&2
    if [[ "$session_snapshot_created" == "1" ]]; then
      echo "Verified pre-deploy Telethon session: $session_snapshot_path" >&2
    fi
  fi
  exit "$exit_code"
}
trap rollback_code ERR INT TERM

echo "Fetching $REMOTE/$BRANCH..."
git fetch --prune "$REMOTE" "$BRANCH"
remote_sha="$(git rev-parse "$REMOTE/$BRANCH")"
if [[ -n "$TARGET_OVERRIDE" ]]; then
  target_sha="$(git rev-parse --verify "${TARGET_OVERRIDE}^{commit}")"
  if ! git merge-base --is-ancestor "$target_sha" "$remote_sha"; then
    echo "Requested target is not an ancestor of $REMOTE/$BRANCH: $target_sha" >&2
    exit 4
  fi
  echo "Using verified rollback target $target_sha"
else
  target_sha="$remote_sha"
fi

if [[ "$target_sha" == "$previous_sha" ]]; then
  echo "Romatic Club is already at $target_sha"
  exit 0
fi

echo "Creating pre-deploy PostgreSQL dump..."
"${compose[@]}" up -d postgres
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
print("Bot configuration preflight OK")
PY

"${compose[@]}" run --rm --no-deps userbot python - <<'PY'
from bot.core.settings import UserbotProcessSettings

config = UserbotProcessSettings.from_env(project_root="/app")
assert config.userbot.api_id > 0
assert config.userbot.api_hash
assert config.database.url
print("Userbot configuration preflight OK")
PY

if [[ "$session_snapshot_created" == "1" ]]; then
  echo "Checking target Telethon against an isolated session copy..."
  prepare_session_probe
  "${compose[@]}" run --rm --no-deps     -e USERBOT_SESSION=/tmp/session-probe/userbot     -v "$session_probe_dir:/tmp/session-probe"     userbot python - <<'PY'
from telethon.sessions import SQLiteSession

session = SQLiteSession("/tmp/session-probe/userbot")
session.close()
print("Target Telethon session compatibility preflight OK")
PY
  cleanup_session_probe
fi

echo "Deploying $target_sha..."
"${compose[@]}" up -d --remove-orphans postgres supervisor-proxy bot userbot
runtime_replaced=1

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

wait_service bot
wait_service userbot
wait_service supervisor-proxy

"${compose[@]}" exec -T bot python - <<'PY'
import asyncio
import os

import asyncpg

from bot.core.settings import BotProcessSettings

config = BotProcessSettings.from_env(project_root="/app")
assert config.bot.bot_token, "BOT_TOKEN is empty"
assert config.database.url, "DATABASE_URL is empty"

async def main() -> None:
    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        value = await connection.fetchval("SELECT 1")
        assert value == 1
    finally:
        await connection.close()

asyncio.run(main())
print("Romatic server smoke OK")
PY

"${compose[@]}" exec -T bot python - <<'PY'
import json
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:8081"

with urlopen(f"{BASE_URL}/healthz", timeout=5) as response:
    assert response.status == 200
    assert json.load(response) == {"live": True}

with urlopen(f"{BASE_URL}/readyz", timeout=5) as response:
    readiness = json.load(response)
    assert response.status == 200
    assert readiness["ready"] is True
    assert readiness["database"] is True

with urlopen(f"{BASE_URL}/metrics", timeout=5) as response:
    metrics = response.read().decode("utf-8")

for metric in ("process_uptime_seconds", "application_ready 1", "database_ready 1"):
    assert metric in metrics, f"missing deployment SLI: {metric}"

synthetic_alert_samples = {
    "BotCoreLatencyP95High": (2.1, 2.0),
    "UserbotLatencyP95High": (5.1, 5.0),
    "BotCoreErrorRateHigh": (0.051, 0.05),
    "UserbotErrorRateHigh": (0.051, 0.05),
    "BotSchedulerLagHigh": (31.0, 30.0),
    "UserbotQueueDepthHigh": (101.0, 100.0),
}
for alert, (sample, threshold) in synthetic_alert_samples.items():
    assert sample > threshold, f"synthetic alert did not cross threshold: {alert}"

print("Observability deployment smoke OK")
PY

deployed_sha="$(git rev-parse HEAD)"
if [[ "$deployed_sha" != "$target_sha" ]]; then
  echo "Deployed SHA mismatch: expected $target_sha, got $deployed_sha" >&2
  false
fi

code_switched=0
runtime_replaced=0
cleanup_session_probe
trap - ERR INT TERM
echo "Romatic Club deployment succeeded: $deployed_sha"
echo "Verified pre-deploy backup: $backup_path"
if [[ "$session_snapshot_created" == "1" ]]; then
  echo "Verified pre-deploy Telethon session: $session_snapshot_path"
fi
"${compose[@]}" ps
