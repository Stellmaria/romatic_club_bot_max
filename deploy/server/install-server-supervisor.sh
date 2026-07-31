#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-.env}"
COMPOSE_FILE="${ROMATIC_COMPOSE_FILE:-compose.yaml}"
SERVICE_USER="${ROMATIC_SERVICE_USER:-${SUDO_USER:-velvet}}"
SERVER_UNIT_SOURCE="$APP_DIR/deploy/systemd/romatic-server-supervisor.service"
COMPOSE_UNIT_SOURCE="$APP_DIR/deploy/systemd/romatic-compose.service"
SERVER_UNIT_TARGET="/etc/systemd/system/romatic-server-supervisor.service"
COMPOSE_UNIT_TARGET="/etc/systemd/system/romatic-compose.service"

cd "$APP_DIR"
for path in "$ENV_FILE" "$COMPOSE_FILE" "$SERVER_UNIT_SOURCE" "$COMPOSE_UNIT_SOURCE"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $SERVICE_USER" >&2
  exit 2
fi

python3 - "$ENV_FILE" "$APP_DIR" <<'PY'
from __future__ import annotations

import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
app_dir = Path(sys.argv[2])
lines = path.read_text(encoding="utf-8").splitlines()
values: dict[str, str] = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

data_dir = values.get("ROMATIC_DATA_DIR", str(app_dir / "server-data")).rstrip("/")
token = values.get("SUPERVISOR_TOKEN", "")
if len(token) < 24 or "change_me" in token.casefold():
    token = secrets.token_urlsafe(48)

updates = {
    "ROMATIC_DATA_DIR": data_dir,
    "SUPERVISOR_ENABLED": "true",
    "SUPERVISOR_TOKEN": token,
    "SUPERVISOR_BASE_URL": "http://supervisor-proxy:8765",
    "SUPERVISOR_CLIENT_TIMEOUT_SECONDS": "20",
    "SUPERVISOR_COMMAND_TIMEOUT_SECONDS": "1800",
    "SERVER_SUPERVISOR_SOCKET_HOST": (
        f"{data_dir}/runtime/supervisor/romatic-server-supervisor.sock"
    ),
    "SERVER_SUPERVISOR_SOCKET": (
        "/runtime/supervisor/romatic-server-supervisor.sock"
    ),
}
seen: set[str] = set()
result: list[str] = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        result.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        result.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        result.append(line)

missing = [key for key in updates if key not in seen]
if missing:
    result.extend(["", "# Server Supervisor (Linux/VPS)"])
    result.extend(f"{key}={updates[key]}" for key in missing)
path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
PY
chmod 600 "$ENV_FILE"

data_dir="$(python3 - "$ENV_FILE" "$APP_DIR" <<'PY'
from pathlib import Path
import sys

values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()
print(values.get("ROMATIC_DATA_DIR", str(Path(sys.argv[2]) / "server-data")))
PY
)"

install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$data_dir/runtime/supervisor"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$data_dir/runtime/docker-config"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$data_dir/backups"

render_unit() {
  local source="$1"
  local target="$2"
  python3 - "$source" "$target" "$APP_DIR" "$data_dir" "$SERVICE_USER" <<'PY'
from pathlib import Path
import sys

source, target, app_dir, data_dir, service_user = sys.argv[1:]
text = Path(source).read_text(encoding="utf-8")
text = (
    text.replace("%APP_DIR%", app_dir)
    .replace("%DATA_DIR%", data_dir)
    .replace("%SERVICE_USER%", service_user)
)
Path(target).write_text(text, encoding="utf-8")
PY
  chmod 0644 "$target"
}

render_unit "$SERVER_UNIT_SOURCE" "$SERVER_UNIT_TARGET"
render_unit "$COMPOSE_UNIT_SOURCE" "$COMPOSE_UNIT_TARGET"
systemctl daemon-reload
systemctl enable romatic-server-supervisor.service
systemctl restart romatic-server-supervisor.service

sudo -u "$SERVICE_USER" env \
  DOCKER_CONFIG="$data_dir/runtime/docker-config" \
  COMPOSE_BAKE=false \
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build --pull supervisor-proxy

if systemctl is-active --quiet romatic-compose.service; then
  systemctl reload romatic-compose.service
else
  systemctl enable --now romatic-compose.service
fi

python3 - "$data_dir/runtime/supervisor/romatic-server-supervisor.sock" <<'PY'
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if path.is_socket():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(path))
            client.sendall(
                b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            response = client.recv(4096)
        if b"200 OK" in response and b'"ok": true' in response:
            print("Romatic Server Supervisor Unix API is healthy.")
            break
    time.sleep(1)
else:
    raise SystemExit("Romatic Server Supervisor Unix API did not become healthy.")
PY

sudo -u "$SERVICE_USER" env \
  DOCKER_CONFIG="$data_dir/runtime/docker-config" \
  COMPOSE_BAKE=false \
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
systemctl --no-pager --full status romatic-server-supervisor.service
