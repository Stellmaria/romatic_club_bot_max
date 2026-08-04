#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-.env}"
COMPOSE_FILE="${ROMATIC_COMPOSE_FILE:-compose.yaml}"
DUMP_PATH="${1:-}"
HEALTH_ATTEMPTS="${ROMATIC_RESTORE_HEALTH_ATTEMPTS:-60}"
HEALTH_INTERVAL="${ROMATIC_RESTORE_HEALTH_INTERVAL:-2}"

cd "$APP_DIR"

if [[ ! -f "$ENV_FILE" || ! -f "$COMPOSE_FILE" ]]; then
  echo "Restore drill requires $ENV_FILE and $COMPOSE_FILE in $APP_DIR" >&2
  exit 2
fi

read -r data_dir < <(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
print(values.get("ROMATIC_DATA_DIR", "/srv/romatic-club-max/server-data"))
PY
)

if [[ -z "$DUMP_PATH" ]]; then
  DUMP_PATH="$(
    find "$data_dir/backups" -maxdepth 1 -type f -name 'predeploy-*.dump' \
      -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-
  )"
fi

if [[ -z "$DUMP_PATH" || ! -s "$DUMP_PATH" ]]; then
  echo "Verified PostgreSQL dump not found: ${DUMP_PATH:-<empty>}" >&2
  exit 2
fi

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile operations)
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_dir="$data_dir/runtime/restore-drills"
report_path="$report_dir/restore-drill-${stamp}.json"
mkdir -p "$report_dir"
chmod 0700 "$report_dir"

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  "${compose[@]}" rm -sf restore-drill-postgres >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

"${compose[@]}" rm -sf restore-drill-postgres >/dev/null 2>&1 || true
"${compose[@]}" up -d --no-deps restore-drill-postgres

container_id="$("${compose[@]}" ps -q restore-drill-postgres)"
if [[ -z "$container_id" ]]; then
  echo "Restore drill PostgreSQL container was not created" >&2
  exit 1
fi

for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
  if [[ "$state" == "running" && "$health" == "healthy" ]]; then
    break
  fi
  if [[ "$state" =~ ^(exited|dead|removing)$ || "$health" == "unhealthy" ]]; then
    "${compose[@]}" logs --tail 200 restore-drill-postgres >&2 || true
    echo "Restore drill PostgreSQL failed: state=$state health=$health" >&2
    exit 1
  fi
  if ((attempt == HEALTH_ATTEMPTS)); then
    "${compose[@]}" logs --tail 200 restore-drill-postgres >&2 || true
    echo "Restore drill PostgreSQL did not become healthy" >&2
    exit 1
  fi
  sleep "$HEALTH_INTERVAL"
done

"${compose[@]}" exec -T restore-drill-postgres pg_restore \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  --username restore_drill \
  --dbname restore_drill < "$DUMP_PATH"

pre_probe="$(${compose[*]} exec -T restore-drill-postgres sh -ceu '
psql -X -At -U restore_drill -d restore_drill <<"SQL"
SELECT current_setting('\''server_version_num'\'')::integer;
SELECT pg_database_size(current_database());
SELECT CASE WHEN to_regclass('\''public.schema_migrations'\'') IS NOT NULL THEN 1 ELSE 0 END;
SELECT CASE WHEN to_regclass('\''public.auctions'\'') IS NOT NULL THEN 1 ELSE 0 END;
SELECT CASE
  WHEN to_regclass('\''public.auctions'\'') IS NULL THEN 0
  ELSE (SELECT count(*) FROM public.auctions WHERE message_id IS NOT NULL AND message_id <= 0)
END;
SQL
')"

mapfile -t pre_values <<<"$pre_probe"
if [[ "${#pre_values[@]}" -ne 5 ]]; then
  echo "Unexpected restore pre-probe output" >&2
  exit 1
fi

pg_version_num="${pre_values[0]}"
database_size="${pre_values[1]}"
has_schema_migrations="${pre_values[2]}"
has_auctions="${pre_values[3]}"
non_positive_message_ids="${pre_values[4]}"

if ((pg_version_num / 10000 != 17)); then
  echo "Restore drill PostgreSQL major mismatch: $pg_version_num" >&2
  exit 1
fi
if [[ "$has_schema_migrations" != "1" || "$has_auctions" != "1" ]]; then
  echo "Restore drill is missing required production tables" >&2
  exit 1
fi
if [[ "$non_positive_message_ids" != "0" ]]; then
  echo "Restore drill business invariant failed: non-positive message IDs" >&2
  exit 1
fi

restore_url="postgresql://restore_drill:restore_drill_ephemeral_only@restore-drill-postgres:5432/restore_drill"
plan_json="$(
  "${compose[@]}" run --rm -T --no-deps \
    -e DATABASE_URL="$restore_url" \
    -e DB_AUTO_MIGRATE=false \
    migration-runner python -m db.migrator plan --json
)"

apply_json="$(
  "${compose[@]}" run --rm -T --no-deps \
    -e DATABASE_URL="$restore_url" \
    -e DB_AUTO_MIGRATE=false \
    migration-runner python -m db.migrator apply --json
)"

verify_json="$(
  "${compose[@]}" run --rm -T --no-deps \
    -e DATABASE_URL="$restore_url" \
    -e DB_AUTO_MIGRATE=false \
    migration-runner python -m db.migrator verify --json
)"

read -r pending_before applied_count current_version target_version pending_after < <(
  python3 - "$plan_json" "$apply_json" "$verify_json" <<'PY'
import json
import sys

plan = json.loads(sys.argv[1])
apply = json.loads(sys.argv[2])
verify = json.loads(sys.argv[3])
print(
    plan["pending_count"],
    apply["applied_count"],
    verify["current_version"],
    verify["target_version"],
    verify["pending_count"],
)
PY
)

if [[ "$pending_after" != "0" || "$current_version" != "$target_version" ]]; then
  echo "Restore drill target schema verification failed" >&2
  exit 1
fi

post_invalid="$(${compose[*]} exec -T restore-drill-postgres sh -ceu '
psql -X -At -U restore_drill -d restore_drill -c "SELECT count(*) FROM public.auctions WHERE message_id IS NOT NULL AND message_id <= 0"
')"
if [[ "$post_invalid" != "0" ]]; then
  echo "Restore drill post-migration business invariant failed" >&2
  exit 1
fi

dump_sha256="$(sha256sum "$DUMP_PATH" | awk '{print $1}')"
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "$report_path" <<PY
import json
import os
from pathlib import Path

report = {
    "status": "passed",
    "started_at": "$started_at",
    "completed_at": "$completed_at",
    "dump_file": "$(basename "$DUMP_PATH")",
    "dump_sha256": "$dump_sha256",
    "postgres_version_num": int("$pg_version_num"),
    "database_size_bytes": int("$database_size"),
    "pending_before": int("$pending_before"),
    "applied_in_drill": int("$applied_count"),
    "current_version": int("$current_version"),
    "target_version": int("$target_version"),
    "pending_after": int("$pending_after"),
    "business_probes": {
        "schema_migrations_present": True,
        "auctions_present": True,
        "non_positive_message_ids": 0,
    },
}
path = Path("$report_path")
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

echo "Disposable PostgreSQL restore drill passed"
echo "Restore drill report: $report_path"
echo "Restore drill schema: current=$current_version target=$target_version pending=0"
