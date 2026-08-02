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
SUPERVISOR_GROUP="${ROMATIC_SUPERVISOR_GROUP:-romatic-supervisor}"
SUPERVISOR_GID="${ROMATIC_SUPERVISOR_GID:-10001}"
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
if ! [[ "$SUPERVISOR_GID" =~ ^[1-9][0-9]*$ ]]; then
  echo "ROMATIC_SUPERVISOR_GID must be a positive integer." >&2
  exit 2
fi

if getent group "$SUPERVISOR_GROUP" >/dev/null 2>&1; then
  actual_gid="$(getent group "$SUPERVISOR_GROUP" | cut -d: -f3)"
  if [[ "$actual_gid" != "$SUPERVISOR_GID" ]]; then
    echo "Group $SUPERVISOR_GROUP uses gid $actual_gid, expected $SUPERVISOR_GID." >&2
    exit 2
  fi
else
  existing_group="$(getent group "$SUPERVISOR_GID" | cut -d: -f1 || true)"
  if [[ -n "$existing_group" ]]; then
    echo "Reusing existing group $existing_group for gid $SUPERVISOR_GID."
    SUPERVISOR_GROUP="$existing_group"
  else
    groupadd --gid "$SUPERVISOR_GID" "$SUPERVISOR_GROUP"
  fi
fi
usermod -a -G "$SUPERVISOR_GROUP" "$SERVICE_USER"

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

install -d -m 0770 -o "$SERVICE_USER" -g "$SUPERVISOR_GROUP" "$data_dir/runtime/supervisor"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$data_dir/runtime/docker-config"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$data_dir/backups"

python3 - "$ENV_FILE" "$APP_DIR" "$data_dir" "$SUPERVISOR_GID" <<'PY'
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
app_dir = Path(sys.argv[2])
data_dir = Path(sys.argv[3])
supervisor_gid = sys.argv[4]
token_path = data_dir / "runtime/supervisor/token"
supervisor_env_path = data_dir / "runtime/supervisor/supervisor.env"

lines = path.read_text(encoding="utf-8").splitlines()
values: dict[str, str] = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")

token = ""
try:
    token = token_path.read_text(encoding="utf-8").strip()
except OSError:
    token = values.get("SUPERVISOR_TOKEN", "")
if len(token) < 24 or "change_me" in token.casefold():
    token = secrets.token_urlsafe(48)

updates = {
    "ROMATIC_DATA_DIR": str(data_dir),
    "ROMATIC_SUPERVISOR_GID": supervisor_gid,
    "SUPERVISOR_ENABLED": "true",
    "SUPERVISOR_TOKEN": "",
    "SUPERVISOR_TOKEN_FILE_HOST": str(token_path),
    "SUPERVISOR_BASE_URL": "http://supervisor-proxy:8765",
    "SUPERVISOR_CLIENT_TIMEOUT_SECONDS": "20",
    "SUPERVISOR_ACTOR": "telegram-bot",
    "SERVER_SUPERVISOR_SOCKET_HOST": str(
        data_dir / "runtime/supervisor/romatic-server-supervisor.sock"
    ),
    "SERVER_SUPERVISOR_SOCKET": "/runtime/supervisor/romatic-server-supervisor.sock",
    "ROMATIC_SUPERVISOR_NETWORK": "romatic-supervisor-control",
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

token_path.write_text(token + "\n", encoding="utf-8")
supervisor_env_path.write_text(
    "\n".join(
        [
            f"SUPERVISOR_TOKEN={token}",
            f"SERVER_SUPERVISOR_SOCKET_GID={supervisor_gid}",
            "SUPERVISOR_COMMAND_TIMEOUT_SECONDS=1800",
            "SUPERVISOR_RATE_LIMIT=6",
            "SUPERVISOR_RATE_WINDOW_SECONDS=60",
        ]
    )
    + "\n",
    encoding="utf-8",
)
os.chmod(token_path, 0o640)
os.chmod(supervisor_env_path, 0o640)
PY
chmod 600 "$ENV_FILE"
chown "$SERVICE_USER:$SUPERVISOR_GROUP" \
  "$data_dir/runtime/supervisor/token" \
  "$data_dir/runtime/supervisor/supervisor.env"
chmod 0640 \
  "$data_dir/runtime/supervisor/token" \
  "$data_dir/runtime/supervisor/supervisor.env"

render_unit() {
  local source="$1"
  local target="$2"
  python3 - \
    "$source" \
    "$target" \
    "$APP_DIR" \
    "$data_dir" \
    "$SERVICE_USER" \
    "$SUPERVISOR_GROUP" \
    "$SUPERVISOR_GID" <<'PY'
from pathlib import Path
import sys

(
    source,
    target,
    app_dir,
    data_dir,
    service_user,
    supervisor_group,
    supervisor_gid,
) = sys.argv[1:]
text = Path(source).read_text(encoding="utf-8")
text = (
    text.replace("%APP_DIR%", app_dir)
    .replace("%DATA_DIR%", data_dir)
    .replace("%SERVICE_USER%", service_user)
    .replace("%SUPERVISOR_GROUP%", supervisor_group)
    .replace("%SUPERVISOR_GID%", supervisor_gid)
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

main_pid="$(systemctl show romatic-server-supervisor.service -p MainPID --value)"
service_gid="$(ps -o egid= -p "$main_pid" | tr -d '[:space:]')"
if [[ "$service_gid" != "$SUPERVISOR_GID" ]]; then
  echo "Supervisor process gid is $service_gid, expected $SUPERVISOR_GID." >&2
  exit 4
fi

sudo -u "$SERVICE_USER" env \
  DOCKER_CONFIG="$data_dir/runtime/docker-config" \
  COMPOSE_BAKE=false \
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build --pull supervisor-proxy

if systemctl is-active --quiet romatic-compose.service; then
  systemctl reload romatic-compose.service
else
  systemctl enable --now romatic-compose.service
fi

proxy_gid="$(
  sudo -u "$SERVICE_USER" env \
    DOCKER_CONFIG="$data_dir/runtime/docker-config" \
    COMPOSE_BAKE=false \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T \
      supervisor-proxy id -g | tr -d '[:space:]'
)"
if [[ "$proxy_gid" != "$SUPERVISOR_GID" ]]; then
  echo "Supervisor proxy gid is $proxy_gid, expected $SUPERVISOR_GID." >&2
  exit 4
fi

python3 - "$data_dir/runtime/supervisor/romatic-server-supervisor.sock" "$SUPERVISOR_GID" <<'PY'
from __future__ import annotations

import socket
import stat
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
expected_gid = int(sys.argv[2])
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if path.is_socket():
        metadata = path.stat()
        if stat.S_IMODE(metadata.st_mode) != 0o660:
            raise SystemExit("Supervisor socket mode is not 0660")
        if metadata.st_gid != expected_gid:
            raise SystemExit("Supervisor socket group is incorrect")
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

sudo -u "$SERVICE_USER" env \
  DOCKER_CONFIG="$data_dir/runtime/docker-config" \
  COMPOSE_BAKE=false \
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T userbot \
  sh -ec 'test -z "${SUPERVISOR_TOKEN:-}"; test -z "${SUPERVISOR_TOKEN_FILE:-}"; test "${SUPERVISOR_ENABLED:-}" = false'

if sudo -u "$SERVICE_USER" env \
  DOCKER_CONFIG="$data_dir/runtime/docker-config" \
  COMPOSE_BAKE=false \
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T userbot \
  python -c "import socket; socket.create_connection(('supervisor-proxy', 8765), 2)"; then
  echo "Security check failed: userbot reached Supervisor proxy." >&2
  exit 5
fi

systemctl --no-pager --full status romatic-server-supervisor.service
