#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-.env}"
COMPOSE_FILE="${ROMATIC_COMPOSE_FILE:-compose.yaml}"

cd "$APP_DIR"

if [[ ! -f "$ENV_FILE" || ! -f "$COMPOSE_FILE" ]]; then
  echo "Backup archival requires $ENV_FILE and $COMPOSE_FILE in $APP_DIR" >&2
  exit 2
fi

mapfile -t recovery_config < <(python3 - "$ENV_FILE" <<'PY'
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
    ("ROMATIC_OFFSITE_BACKUP_DIR", ""),
    ("ROMATIC_BACKUP_ENCRYPTION_KEY_FILE", ""),
    ("ROMATIC_LOCAL_BACKUP_RETENTION_DAYS", "14"),
    ("ROMATIC_OFFSITE_BACKUP_RETENTION_DAYS", "90"),
):
    print(values.get(name, default))
PY
)

data_dir="${recovery_config[0]}"
offsite_dir="${recovery_config[1]}"
key_file="${recovery_config[2]}"
local_retention_days="${recovery_config[3]}"
offsite_retention_days="${recovery_config[4]}"

if [[ -z "$offsite_dir" || -z "$key_file" ]]; then
  echo "Encrypted off-host backup archive skipped: destination/key not configured"
  exit 0
fi

if [[ ! "$local_retention_days" =~ ^[1-9][0-9]*$ ]] || \
   [[ ! "$offsite_retention_days" =~ ^[1-9][0-9]*$ ]]; then
  echo "Backup retention values must be positive integers" >&2
  exit 2
fi
if [[ ! -d "$offsite_dir" || ! -f "$offsite_dir/.romatic-offsite-target" ]]; then
  echo "Off-host destination is missing its .romatic-offsite-target marker: $offsite_dir" >&2
  exit 2
fi
if [[ ! -s "$key_file" ]]; then
  echo "Backup encryption key file is missing or empty" >&2
  exit 2
fi

inputs=("$@")
if [[ "${#inputs[@]}" -eq 0 ]]; then
  latest_dump="$(
    find "$data_dir/backups" -maxdepth 1 -type f -name 'predeploy-*.dump' \
      -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-
  )"
  latest_session="$(
    find "$data_dir/backups" -maxdepth 1 -type f \
      -name 'userbot-session-predeploy-*.session' \
      -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-
  )"
  [[ -n "$latest_dump" ]] && inputs+=("$latest_dump")
  [[ -n "$latest_session" ]] && inputs+=("$latest_session")
fi

if [[ "${#inputs[@]}" -eq 0 ]]; then
  echo "No backup artifacts found for archival" >&2
  exit 2
fi

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
image_id="$("${compose[@]}" images -q bot | head -n 1)"
if [[ -z "$image_id" ]]; then
  "${compose[@]}" build bot
  image_id="$("${compose[@]}" images -q bot | head -n 1)"
fi
if [[ -z "$image_id" ]]; then
  echo "Bot image is unavailable for authenticated backup encryption" >&2
  exit 1
fi

archive_one() {
  local source="$1"
  local source_dir source_name target_name target_tmp target_final manifest
  local encrypt_output verify_output

  if [[ ! -s "$source" ]]; then
    echo "Backup artifact is missing or empty: $source" >&2
    return 1
  fi

  source_dir="$(cd "$(dirname "$source")" && pwd)"
  source_name="$(basename "$source")"
  target_name="${source_name}.aes256gcm"
  target_tmp="$offsite_dir/${target_name}.part"
  target_final="$offsite_dir/$target_name"
  manifest="$offsite_dir/${target_name}.json"
  rm -f "$target_tmp"

  encrypt_output="$(
    docker run --rm \
      --network none \
      --user "$(id -u):$(id -g)" \
      --read-only \
      --tmpfs /tmp:size=64m,mode=1777 \
      --cap-drop ALL \
      --security-opt no-new-privileges \
      -v "$source_dir:/input:ro" \
      -v "$offsite_dir:/output" \
      -v "$key_file:/run/romatic-backup-key:ro" \
      "$image_id" \
      python -m scripts.backup_archive encrypt \
        --source "/input/$source_name" \
        --target "/output/${target_name}.part" \
        --key-file /run/romatic-backup-key
  )"

  verify_output="$(
    docker run --rm \
      --network none \
      --user "$(id -u):$(id -g)" \
      --read-only \
      --tmpfs /tmp:size=64m,mode=1777 \
      --cap-drop ALL \
      --security-opt no-new-privileges \
      -v "$offsite_dir:/input:ro" \
      -v "$key_file:/run/romatic-backup-key:ro" \
      "$image_id" \
      python -m scripts.backup_archive verify \
        --source "/input/${target_name}.part" \
        --key-file /run/romatic-backup-key
  )"

  source_sha="$(sha256sum "$source" | awk '{print $1}')"
  encrypted_plain_sha="$(
    awk -F= '$1 == "plaintext_sha256" {print $2}' <<<"$verify_output"
  )"
  if [[ -z "$encrypted_plain_sha" || "$source_sha" != "$encrypted_plain_sha" ]]; then
    rm -f "$target_tmp"
    echo "Encrypted backup verification hash mismatch for $source_name" >&2
    return 1
  fi

  mv -f "$target_tmp" "$target_final"
  chmod 0600 "$target_final"
  archived_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  archive_sha="$(sha256sum "$target_final" | awk '{print $1}')"
  archive_size="$(stat -c %s "$target_final")"

  python3 - "$manifest" <<PY
import json
import os
from pathlib import Path

payload = {
    "archived_at": "$archived_at",
    "source_file": "$source_name",
    "source_sha256": "$source_sha",
    "archive_file": "$target_name",
    "archive_sha256": "$archive_sha",
    "archive_size_bytes": int("$archive_size"),
    "encryption": "AES-256-GCM",
    "verified": True,
}
path = Path("$manifest")
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

  echo "Encrypted off-host backup verified: $target_final"
  echo "$encrypt_output" | sed -n 's/^plaintext_size=/plaintext_size=/p'
}

for input in "${inputs[@]}"; do
  archive_one "$input"
done

find "$data_dir/backups" -maxdepth 1 -type f \
  \( -name 'predeploy-*.dump' -o -name 'userbot-session-predeploy-*.session' \) \
  -mtime "+$local_retention_days" -delete
find "$offsite_dir" -maxdepth 1 -type f \
  \( -name '*.aes256gcm' -o -name '*.aes256gcm.json' \) \
  -mtime "+$offsite_retention_days" -delete

echo "Backup retention applied: local=${local_retention_days}d offsite=${offsite_retention_days}d"
