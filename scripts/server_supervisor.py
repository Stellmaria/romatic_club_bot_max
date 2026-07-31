from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import socketserver
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("romatic.server_supervisor")

APP_DIR = Path(os.getenv("ROMATIC_APP_DIR", "/srv/romatic-club-max")).resolve()
ENV_FILE = os.getenv("ROMATIC_ENV_FILE", ".env")
COMPOSE_FILE = os.getenv("ROMATIC_COMPOSE_FILE", "compose.yaml")
DATA_DIR = Path(os.getenv("ROMATIC_DATA_DIR", str(APP_DIR / "server-data"))).resolve()
SOCKET_PATH = Path(
    os.getenv(
        "SERVER_SUPERVISOR_SOCKET_HOST",
        str(DATA_DIR / "runtime/supervisor/romatic-server-supervisor.sock"),
    )
)
TOKEN = os.getenv("SUPERVISOR_TOKEN", "").strip()
COMMAND_TIMEOUT = max(30, int(os.getenv("SUPERVISOR_COMMAND_TIMEOUT_SECONDS", "1800")))
STATE_DIR = DATA_DIR / "runtime/supervisor"
STATE_PATH = STATE_DIR / "state.json"
LOG_PATH = STATE_DIR / "operations.log"


@dataclass(slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def combined(self) -> str:
        parts = [part.strip() for part in (self.stdout, self.stderr) if part.strip()]
        return "\n".join(parts)


class SupervisorState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "operation": {
                "id": "",
                "kind": "",
                "status": "idle",
                "message": "Операций ещё не было.",
                "started_at": None,
                "finished_at": None,
            },
            "rollback_sha": "",
        }
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return
            if isinstance(data, dict):
                self._state.update(data)

    def save(self) -> None:
        with self._lock:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            temp = STATE_PATH.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp.replace(STATE_PATH)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def start(self, kind: str) -> str:
        with self._lock:
            current = self._state.get("operation", {})
            if current.get("status") == "running":
                raise RuntimeError("Другая системная операция уже выполняется.")
            operation_id = uuid.uuid4().hex[:12]
            self._state["operation"] = {
                "id": operation_id,
                "kind": kind,
                "status": "running",
                "message": "Operation is running.",
                "started_at": time.time(),
                "finished_at": None,
            }
            self.save()
            return operation_id

    def finish(self, *, status: str, message: str) -> None:
        with self._lock:
            operation = dict(self._state.get("operation") or {})
            operation.update(
                status=status,
                message=message[-12000:],
                finished_at=time.time(),
            )
            self._state["operation"] = operation
            self.save()

    def set_rollback_sha(self, sha: str) -> None:
        with self._lock:
            self._state["rollback_sha"] = sha
            self.save()


state = SupervisorState()


def _command_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    docker_config = DATA_DIR / "runtime/docker-config"
    docker_config.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "ROMATIC_APP_DIR": str(APP_DIR),
            "ROMATIC_ENV_FILE": ENV_FILE,
            "ROMATIC_COMPOSE_FILE": COMPOSE_FILE,
            "ROMATIC_DATA_DIR": str(DATA_DIR),
            "DOCKER_CONFIG": str(docker_config),
            "COMPOSE_BAKE": "false",
        }
    )
    if extra:
        env.update(extra)
    return env


def _run(
    args: list[str],
    *,
    timeout: int = COMMAND_TIMEOUT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> CommandResult:
    process = subprocess.run(
        args,
        cwd=APP_DIR,
        env=env or _command_env(),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(process.stdout, process.stderr, process.returncode)
    if check and process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {' '.join(args)}\n{result.combined}"
        )
    return result


def _compose(*args: str, check: bool = True) -> CommandResult:
    return _run(
        [
            "docker",
            "compose",
            "--env-file",
            ENV_FILE,
            "-f",
            COMPOSE_FILE,
            *args,
        ],
        check=check,
    )


def _git(*args: str, check: bool = True) -> CommandResult:
    return _run(["git", *args], check=check)


def _container_status(service: str) -> dict[str, Any]:
    container_id = _compose("ps", "-q", service, check=False).stdout.strip()
    if not container_id:
        return {"running": False, "pid": None, "status": "missing"}
    result = _run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .State}}",
            container_id,
        ],
        check=False,
    )
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        data = {}
    return {
        "running": bool(data.get("Running")),
        "pid": data.get("Pid"),
        "status": data.get("Status") or "unknown",
        "started_at": data.get("StartedAt"),
        "restart_count": _inspect_restart_count(container_id),
    }


def _inspect_restart_count(container_id: str) -> int:
    result = _run(
        ["docker", "inspect", "--format", "{{.RestartCount}}", container_id],
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _git_status() -> dict[str, Any]:
    branch = _git("branch", "--show-current", check=False).stdout.strip() or "detached"
    commit = _git("rev-parse", "HEAD", check=False).stdout.strip()
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no", check=False).stdout.strip())
    return {"branch": branch, "commit": commit, "clean": not dirty}


def _wait_running(service: str, attempts: int = 60, interval: float = 2.0) -> None:
    for _ in range(attempts):
        if _container_status(service).get("running"):
            return
        time.sleep(interval)
    raise RuntimeError(f"Compose service did not become running: {service}")


def _append_operation_log(kind: str, status: str, message: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "time": time.time(),
                    "kind": kind,
                    "status": status,
                    "message": message[-12000:],
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _restart_bot() -> str:
    result = _compose("restart", "bot")
    _wait_running("bot")
    return result.combined or "Основной bot перезапущен."


def _restart_userbot() -> str:
    result = _compose("restart", "userbot")
    _wait_running("userbot")
    return result.combined or "Userbot перезапущен."


def _deploy_main() -> str:
    previous_sha = _git("rev-parse", "HEAD").stdout.strip()
    result = _run(["bash", "deploy/server/deploy.sh"])
    if previous_sha != _git("rev-parse", "HEAD").stdout.strip():
        state.set_rollback_sha(previous_sha)
    return result.combined or "Update completed."


def _rollback() -> str:
    rollback_sha = str(state.snapshot().get("rollback_sha") or "").strip()
    if not rollback_sha:
        raise RuntimeError("Нет сохранённого commit для отката.")
    current_sha = _git("rev-parse", "HEAD").stdout.strip()
    result = _run(
        ["bash", "deploy/server/deploy.sh"],
        env=_command_env({"ROMATIC_DEPLOY_TARGET_SHA": rollback_sha}),
    )
    state.set_rollback_sha(current_sha)
    return result.combined or "Rollback completed."


def _execute_operation(kind: str, action: Callable[[], str]) -> None:
    try:
        message = str(action() or "Operation completed.")
    except Exception as exc:
        logger.exception("Server Supervisor operation failed kind=%s", kind)
        message = str(exc)
        state.finish(status="error", message=f"Operation failed.\n{message}")
        _append_operation_log(kind, "error", message)
    else:
        state.finish(status="success", message=message)
        _append_operation_log(kind, "success", message)


def _start_operation(kind: str, action: Callable[[], str]) -> str:
    operation_id = state.start(kind)
    thread = threading.Thread(
        target=_execute_operation,
        args=(kind, action),
        name=f"romatic-supervisor-{kind}-{operation_id}",
        daemon=True,
    )
    thread.start()
    return operation_id


def _status_payload() -> dict[str, Any]:
    snapshot = state.snapshot()
    return {
        "ok": True,
        "pid": os.getpid(),
        "bot": _container_status("bot"),
        "userbot": _container_status("userbot"),
        "git": _git_status(),
        "operation": snapshot.get("operation", {}),
        "rollback_sha": snapshot.get("rollback_sha", ""),
        "runtime": "romatic_server_supervisor",
    }


def _logs_payload() -> dict[str, Any]:
    result = _compose("logs", "--tail", "120", "bot", "userbot", check=False)
    snapshot = state.snapshot()
    return {
        "ok": True,
        "logs": result.combined[-12000:],
        "rollback_sha": snapshot.get("rollback_sha", ""),
    }


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "RomaticServerSupervisor/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("Server Supervisor API - " + format, *args)

    def _authorized(self) -> bool:
        if not TOKEN:
            return False
        return secrets.compare_digest(
            self.headers.get("Authorization", ""),
            f"Bearer {TOKEN}",
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> bool:
        if self.path == "/health":
            return True
        if self._authorized():
            return True
        self._send(401, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            if self.path == "/health":
                self._send(200, {"ok": True})
            elif self.path == "/v1/status":
                self._send(200, _status_payload())
            elif self.path == "/v1/logs":
                self._send(200, _logs_payload())
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            logger.exception("GET failed path=%s", self.path)
            self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        actions: dict[str, tuple[str, Callable[[], str]]] = {
            "/v1/restart": ("restart", _restart_bot),
            "/v1/restart-userbot": ("userbot-restart", _restart_userbot),
            "/v1/update": ("update", _deploy_main),
            "/v1/rollback": ("rollback", _rollback),
        }
        item = actions.get(self.path)
        if item is None:
            self._send(404, {"ok": False, "error": "not found"})
            return
        kind, action = item
        try:
            operation_id = _start_operation(kind, action)
        except RuntimeError as exc:
            self._send(409, {"ok": False, "error": str(exc)})
            return
        self._send(202, {"ok": True, "operation_id": operation_id})


def main() -> None:
    if not TOKEN or len(TOKEN) < 24:
        raise SystemExit("SUPERVISOR_TOKEN must contain at least 24 characters")
    if shutil.which("docker") is None or shutil.which("git") is None:
        raise SystemExit("docker and git are required")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with UnixHTTPServer(str(SOCKET_PATH), RequestHandler) as server:
        os.chmod(SOCKET_PATH, 0o666)
        logger.info("Romatic Server Supervisor listening on %s", SOCKET_PATH)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            try:
                SOCKET_PATH.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
