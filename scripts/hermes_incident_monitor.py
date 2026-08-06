from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("romatic.hermes_incident_monitor")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "submitted", "running", "started", "stopping"})
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)[A-Z0-9_]*\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(postgres(?:ql)?://[^:\s/]+:)[^@\s]+(@)"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
)


def redact(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(r"\1[REDACTED]\2", result)
        elif pattern.groups == 1:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _notification_chat_id() -> int | None:
    candidates: list[str] = []
    for name in ("SUPERVISOR_NOTIFICATION_CHAT_ID", "LOG_CHAT_ID"):
        value = os.getenv(name, "").strip()
        if value:
            candidates.append(value)
    for name in ("ADMINS_OWNERS", "ADMINS"):
        candidates.extend(
            item.strip()
            for item in os.getenv(name, "").replace(";", ",").split(",")
            if item.strip()
        )
    for value in candidates:
        try:
            return int(value)
        except ValueError:
            continue
    return None


@dataclass(frozen=True, slots=True)
class Probe:
    service: str
    container_id: str | None
    running: bool
    status: str | None
    health: str | None
    restart_count: int
    exit_code: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "container_id": self.container_id,
            "running": self.running,
            "status": self.status,
            "health": self.health,
            "restart_count": self.restart_count,
            "exit_code": self.exit_code,
        }


class Monitor:
    def __init__(self) -> None:
        self.app_dir = Path(os.getenv("ROMATIC_APP_DIR", "/srv/romatic-club")).resolve()
        self.env_file = os.getenv("ROMATIC_ENV_FILE", ".env")
        self.compose_file = os.getenv("ROMATIC_COMPOSE_FILE", "compose.yaml")
        self.data_dir = Path(
            os.getenv("ROMATIC_DATA_DIR", str(self.app_dir / "server-data"))
        ).resolve()
        self.state_dir = self.data_dir / "runtime" / "supervisor"
        self.state_path = self.state_dir / "hermes-incident-monitor.json"
        self.log_path = self.state_dir / "hermes-incident-monitor.log"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_url = os.getenv("HERMES_BASE_URL", "http://127.0.0.1:8642").rstrip("/")
        self.hermes_key = os.getenv("HERMES_API_KEY", "").strip()
        if len(self.hermes_key) < 24:
            raise RuntimeError("HERMES_API_KEY is missing or too short")
        self.poll_seconds = _integer("HERMES_INCIDENT_POLL_SECONDS", 30, 5, 3600)
        self.not_running_polls = _integer("HERMES_INCIDENT_NOT_RUNNING_POLLS", 2, 1, 20)
        self.unhealthy_polls = _integer("HERMES_INCIDENT_UNHEALTHY_POLLS", 2, 1, 20)
        self.cooldown_seconds = _integer("HERMES_INCIDENT_COOLDOWN_SECONDS", 600, 30, 86400)
        self.run_timeout_seconds = _integer("HERMES_INCIDENT_RUN_TIMEOUT_SECONDS", 3600, 60, 14400)
        self.log_lines = _integer("HERMES_INCIDENT_LOG_LINES", 200, 20, 2000)
        self.services = ("bot", "userbot")
        self._stop = threading.Event()
        self._state = self._load_state()
        self._not_running_counts: dict[str, int] = {}
        self._initial_not_running: set[str] = set()
        self._unhealthy_counts: dict[str, int] = {}
        active_run = self._state.get("active_run")
        self._active_run = active_run if isinstance(active_run, str) and active_run else None
        self._active_status = str(self._state.get("active_status") or "idle")
        self._last_event_key = str(self._state.get("last_event_key") or "")
        try:
            self._last_event_at = float(self._state.get("last_event_at") or 0.0)
        except (TypeError, ValueError):
            self._last_event_at = 0.0
        self.telegram_token = os.getenv("BOT_TOKEN", "").strip()
        self.telegram_chat_id = _notification_chat_id()

    def stop(self) -> None:
        self._stop.set()

    def _compose(self, *args: str) -> list[str]:
        return ["docker", "compose", "--env-file", self.env_file, "-f", self.compose_file, *args]

    def _run(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=self.app_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def probe(self, service: str) -> Probe:
        lookup = self._run(self._compose("ps", "-q", service), 30)
        container_id = lookup.stdout.strip()
        if lookup.returncode != 0 or not container_id:
            return Probe(service, None, False, "missing", None, 0, None)
        inspected = self._run(["docker", "inspect", container_id], 30)
        try:
            item = json.loads(inspected.stdout)[0]
            state = item.get("State") or {}
            health = state.get("Health") or {}
            return Probe(
                service,
                container_id,
                bool(state.get("Running")),
                str(state.get("Status") or "") or None,
                str(health.get("Status") or "") or None,
                max(0, int(item.get("RestartCount", 0) or 0)),
                int(state.get("ExitCode")) if state.get("ExitCode") is not None else None,
            )
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
            logger.exception("Could not parse Docker inspect for %s", service)
            return Probe(service, container_id, False, "unknown", None, 0, None)

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _previous(self, service: str) -> dict[str, Any]:
        probes = self._state.get("probes")
        value = probes.get(service) if isinstance(probes, dict) else None
        return value if isinstance(value, dict) else {}

    def reason(self, probe: Probe) -> str | None:
        previous = self._previous(probe.service)
        if not probe.running:
            count = self._not_running_counts.get(probe.service, 0) + 1
            self._not_running_counts[probe.service] = count
            self._unhealthy_counts[probe.service] = 0
            if count == 1 and not previous:
                self._initial_not_running.add(probe.service)
            if count < self.not_running_polls:
                return None
            if probe.service in self._initial_not_running:
                return "initial-not-running"
            return "container-not-running"

        self._not_running_counts[probe.service] = 0
        self._initial_not_running.discard(probe.service)
        if probe.restart_count > max(0, int(previous.get("restart_count", 0) or 0)):
            return "container-auto-restarted"
        if probe.health == "unhealthy":
            count = self._unhealthy_counts.get(probe.service, 0) + 1
            self._unhealthy_counts[probe.service] = count
            if count >= self.unhealthy_polls:
                return "container-unhealthy"
        else:
            self._unhealthy_counts[probe.service] = 0
        return None

    def _save(self, probes: list[Probe]) -> None:
        value = {
            "updated_at": time.time(),
            "probes": {probe.service: probe.to_dict() for probe in probes},
            "active_run": self._active_run,
            "active_status": self._active_status,
            "last_event_key": self._last_event_key,
            "last_event_at": self._last_event_at,
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)
        os.chmod(self.state_path, 0o600)
        self._state = value

    def _logs(self, service: str) -> str:
        result = self._run(
            self._compose("logs", "--no-color", "--tail", str(self.log_lines), service), 60
        )
        return redact(result.stdout)[-12000:]

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {self.hermes_key}", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.hermes_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Hermes HTTP {error.code}: {redact(details)}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"Hermes unavailable: {type(error).__name__}") from error
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("Hermes returned unexpected response")
        return value

    def _notify(self, title: str, text: str) -> None:
        if not self.telegram_token or self.telegram_chat_id is None:
            logger.warning("Telegram notification skipped: destination is not configured")
            return
        payload = json.dumps(
            {"chat_id": self.telegram_chat_id, "text": f"{title}\n\n{redact(text)[:3500]}"},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except Exception as error:
            logger.warning("Could not send Telegram notification (%s)", type(error).__name__)

    @staticmethod
    def _event_key(probe: Probe, reason: str) -> str:
        return "|".join(
            (
                probe.service,
                reason,
                probe.container_id or "missing",
                str(probe.restart_count),
                probe.health or "none",
            )
        )

    def submit(self, probe: Probe, reason: str) -> None:
        event_key = self._event_key(probe, reason)
        if self._active_status in ACTIVE_STATUSES:
            return
        if (
            event_key == self._last_event_key
            and time.time() - self._last_event_at < self.cooldown_seconds
        ):
            return
        prompt = (
            "Разбери аварийный инцидент Romatic Club Max. Не выполняй merge, deployment, "
            "restart, update, rollback, изменение production БД или секретов. Если это "
            "вероятный дефект кода, поставь задачу через `python /opt/data/tools/coderctl.py "
            "submit max --source automatic-incident --task <очищенная задача>`, дождись "
            "результата через coderctl wait и независимо проверь PR и CI. Если данных "
            "недостаточно, не создавай задачу и укажи точный blocker.\n\n"
            f"service: max-{probe.service}\nreason: {reason}\n"
            f"running: {probe.running}\nhealth: {probe.health}\n"
            f"restart_count: {probe.restart_count}\nexit_code: {probe.exit_code}\n\n"
            "Очищенные логи:\n" + self._logs(probe.service)
        )
        response = self._request(
            "POST",
            "/v1/runs",
            {
                "input": prompt,
                "session_id": f"max-incident-{int(time.time())}-{probe.service}",
                "instructions": (
                    "Ты главный оператор Max. Верни диагноз, риск, task_id/run_id, PR, "
                    "тесты и blocker. Итог будет отправлен владельцу в Telegram."
                ),
            },
        )
        run_id = response.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("Hermes did not return run_id")
        self._active_run = run_id
        self._active_status = str(response.get("status") or "started")
        self._last_event_key = event_key
        self._last_event_at = time.time()
        self._notify(
            "Hermes начал разбор инцидента Max",
            f"service={probe.service}\nreason={reason}\nrun_id={run_id}",
        )
        threading.Thread(target=self._wait_run, args=(run_id,), daemon=True).start()

    def _wait_run(self, run_id: str) -> None:
        deadline = time.monotonic() + self.run_timeout_seconds
        try:
            while not self._stop.is_set():
                result = self._request("GET", f"/v1/runs/{run_id}")
                status = str(result.get("status") or "unknown")
                self._active_status = status
                if status in TERMINAL_STATUSES:
                    output = redact(str(result.get("output") or result.get("error") or "[empty]"))
                    self._notify(
                        "Hermes завершил разбор инцидента Max",
                        f"run_id={run_id}\nstatus={status}\n\n{output}",
                    )
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Hermes run {run_id} timed out")
                self._stop.wait(5)
        except Exception as error:
            self._active_status = "failed"
            self._notify(
                "Hermes не завершил разбор инцидента Max",
                f"run_id={run_id}\nerror={redact(str(error))}",
            )
        finally:
            self._active_run = None

    def run(self) -> int:
        if self._active_run and self._active_status in ACTIVE_STATUSES:
            threading.Thread(target=self._wait_run, args=(self._active_run,), daemon=True).start()
        while not self._stop.is_set():
            probes = [self.probe(service) for service in self.services]
            for probe in probes:
                reason = self.reason(probe)
                if reason:
                    try:
                        self.submit(probe, reason)
                    except Exception as error:
                        logger.warning("Could not submit Max incident (%s)", type(error).__name__)
            self._save(probes)
            self._stop.wait(self.poll_seconds)
        return 0


def main() -> int:
    monitor = Monitor()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(monitor.log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    def stop(_signum: int, _frame: Any) -> None:
        monitor.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Max Hermes incident monitor started interval=%s", monitor.poll_seconds)
    return monitor.run()


if __name__ == "__main__":
    raise SystemExit(main())