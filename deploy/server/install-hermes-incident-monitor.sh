#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club}"
ENV_FILE="${ROMATIC_ENV_FILE:-$APP_DIR/.env}"
COMPOSE_FILE="${ROMATIC_COMPOSE_FILE:-$APP_DIR/compose.yaml}"
SERVICE_USER="${ROMATIC_SERVICE_USER:-${SUDO_USER:-velvet}}"
SHARED_INCIDENT_ENV="${HERMES_INCIDENT_ENV_FILE:-/srv/hermes-operator-control/incident.env}"
UNIT_SOURCE="$APP_DIR/deploy/systemd/romatic-hermes-incident-monitor.service"
UNIT_TARGET="/etc/systemd/system/romatic-hermes-incident-monitor.service"
MONITOR_SOURCE="$APP_DIR/scripts/hermes_incident_monitor.py"

for path in "$ENV_FILE" "$COMPOSE_FILE" "$SHARED_INCIDENT_ENV" "$UNIT_SOURCE" "$MONITOR_SOURCE"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  exit 2
fi
if ! systemctl is-active --quiet romatic-server-supervisor.service; then
  echo "romatic-server-supervisor.service is not active." >&2
  exit 3
fi

python3 - "$SHARED_INCIDENT_ENV" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
values = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
if values.get("HERMES_INCIDENT_ENABLED", "").casefold() not in {"1", "true", "yes", "on"}:
    raise SystemExit("HERMES_INCIDENT_ENABLED must be true in shared incident env")
if len(values.get("HERMES_API_KEY", "")) < 24:
    raise SystemExit("HERMES_API_KEY is missing or too short")
if not values.get("HERMES_BASE_URL", "").startswith("http://127.0.0.1:"):
    raise SystemExit("HERMES_BASE_URL must use the VPS loopback interface")
PY

install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$APP_DIR/server-data/runtime/supervisor"
install -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
systemctl daemon-reload
systemctl enable --now romatic-hermes-incident-monitor.service

if ! systemctl is-active --quiet romatic-hermes-incident-monitor.service; then
  systemctl --no-pager --full status romatic-hermes-incident-monitor.service >&2 || true
  exit 4
fi

systemctl --no-pager --full status romatic-hermes-incident-monitor.service
printf '%s\n' \
  "Romatic Hermes incident monitor installed." \
  "The monitor is read-only and routes code defects to the Max coder through the main Hermes."
