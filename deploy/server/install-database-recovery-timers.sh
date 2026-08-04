#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this installer as root (for example with sudo)." >&2
  exit 2
fi

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-$APP_DIR/.env}"
SERVICE_USER="${ROMATIC_SERVICE_USER:-${SUDO_USER:-velvet}}"
SERVICE_GROUP="${ROMATIC_SERVICE_GROUP:-$(id -gn "$SERVICE_USER")}"
SYSTEMD_DIR="${ROMATIC_SYSTEMD_DIR:-/etc/systemd/system}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 2
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Unknown service user: $SERVICE_USER" >&2
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
):
    print(values.get(name, default))
PY
)

data_dir="${recovery_config[0]}"
offsite_dir="${recovery_config[1]}"
key_file="${recovery_config[2]}"

mkdir -p "$data_dir/runtime/restore-drills"
chown "$SERVICE_USER:$SERVICE_GROUP" "$data_dir/runtime/restore-drills"
chmod 0700 "$data_dir/runtime/restore-drills"

render_unit() {
  local source="$1"
  local target="$2"
  local offsite_value="${offsite_dir:-/nonexistent/romatic-offsite-disabled}"

  sed \
    -e "s|%APP_DIR%|$APP_DIR|g" \
    -e "s|%DATA_DIR%|$data_dir|g" \
    -e "s|%OFFSITE_DIR%|$offsite_value|g" \
    -e "s|%SERVICE_USER%|$SERVICE_USER|g" \
    -e "s|%SERVICE_GROUP%|$SERVICE_GROUP|g" \
    "$source" > "$target"
  chmod 0644 "$target"
}

render_unit \
  "$APP_DIR/deploy/systemd/romatic-restore-drill.service" \
  "$SYSTEMD_DIR/romatic-restore-drill.service"
install -m 0644 \
  "$APP_DIR/deploy/systemd/romatic-restore-drill.timer" \
  "$SYSTEMD_DIR/romatic-restore-drill.timer"

archive_enabled=0
if [[ -n "$offsite_dir" || -n "$key_file" ]]; then
  if [[ -z "$offsite_dir" || -z "$key_file" ]]; then
    echo "Both ROMATIC_OFFSITE_BACKUP_DIR and ROMATIC_BACKUP_ENCRYPTION_KEY_FILE are required." >&2
    exit 2
  fi
  if [[ ! -d "$offsite_dir" || ! -f "$offsite_dir/.romatic-offsite-target" ]]; then
    echo "Off-host directory or marker is missing: $offsite_dir" >&2
    exit 2
  fi
  if [[ ! -s "$key_file" ]]; then
    echo "Backup encryption key is missing or empty: $key_file" >&2
    exit 2
  fi
  render_unit \
    "$APP_DIR/deploy/systemd/romatic-backup-archive.service" \
    "$SYSTEMD_DIR/romatic-backup-archive.service"
  install -m 0644 \
    "$APP_DIR/deploy/systemd/romatic-backup-archive.timer" \
    "$SYSTEMD_DIR/romatic-backup-archive.timer"
  archive_enabled=1
fi

systemd-analyze verify "$SYSTEMD_DIR/romatic-restore-drill.service"
if [[ "$archive_enabled" == "1" ]]; then
  systemd-analyze verify "$SYSTEMD_DIR/romatic-backup-archive.service"
fi
systemctl daemon-reload
systemctl enable --now romatic-restore-drill.timer
if [[ "$archive_enabled" == "1" ]]; then
  systemctl enable --now romatic-backup-archive.timer
else
  systemctl disable --now romatic-backup-archive.timer >/dev/null 2>&1 || true
fi

echo "Database recovery timers installed"
echo "restore_drill_timer=enabled"
echo "backup_archive_timer=$([[ "$archive_enabled" == "1" ]] && echo enabled || echo disabled)"
