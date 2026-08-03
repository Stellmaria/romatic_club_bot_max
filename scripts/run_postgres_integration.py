#!/usr/bin/env python3
"""Run the destructive PostgreSQL integration suite in a disposable container."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

DEFAULT_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
DATABASE = "integration_test"
USER = "integration"
PASSWORD = "integration_password"
ARTIFACT_DIR = Path("var/integration-artifacts")


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=capture_output,
        env=env,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_postgres(container_name: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                container_name,
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(1)
    raise RuntimeError("PostgreSQL container did not become healthy")


def _prepare_artifacts() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for path in ARTIFACT_DIR.iterdir():
        if path.is_file():
            path.unlink()


def _write_container_logs(container_name: str) -> None:
    result = _run(
        ["docker", "logs", container_name],
        check=False,
        capture_output=True,
    )
    (ARTIFACT_DIR / "postgres.log").write_text(
        result.stdout + result.stderr,
        encoding="utf-8",
    )


def _dump_failed_databases(container_name: str) -> None:
    failed = ARTIFACT_DIR / "failed-databases.txt"
    if not failed.exists():
        return

    seen: set[str] = set()
    for line in failed.read_text(encoding="utf-8").splitlines():
        database_name = line.split("\t", 1)[0].strip()
        if not database_name or database_name in seen:
            continue
        seen.add(database_name)
        result = _run(
            [
                "docker",
                "exec",
                container_name,
                "pg_dump",
                "--username",
                USER,
                "--dbname",
                database_name,
                "--schema-only",
                "--no-owner",
                "--no-privileges",
            ],
            check=False,
            capture_output=True,
        )
        (ARTIFACT_DIR / f"{database_name}.sql").write_text(
            result.stdout + result.stderr,
            encoding="utf-8",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Start PostgreSQL 17, run tests/integration with destructive safety "
            "confirmation, and preserve diagnostics on failure."
        )
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="leave PostgreSQL running after the test command",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="additional arguments passed to pytest after --",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if shutil.which("docker") is None:
        print("docker executable is required", file=sys.stderr)
        return 2

    _prepare_artifacts()
    port = _free_port()
    container_name = f"romatic-postgres-it-{uuid.uuid4().hex[:10]}"
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--env",
        f"POSTGRES_DB={DATABASE}",
        "--env",
        f"POSTGRES_USER={USER}",
        "--env",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "--publish",
        f"127.0.0.1:{port}:5432",
        "--health-cmd",
        f"pg_isready -U {USER} -d {DATABASE}",
        "--health-interval",
        "1s",
        "--health-timeout",
        "5s",
        "--health-retries",
        "30",
        args.image,
    ]

    started = False
    exit_code = 1
    try:
        _run(command)
        started = True
        _wait_for_postgres(container_name)

        env = os.environ.copy()
        env.update(
            {
                "TEST_DATABASE_URL": (
                    f"postgresql://{USER}:{PASSWORD}@127.0.0.1:{port}/{DATABASE}"
                ),
                "POSTGRES_INTEGRATION_CONFIRM": "1",
                "POSTGRES_KEEP_FAILED_DATABASES": "1",
                "POSTGRES_INTEGRATION_ARTIFACT_DIR": str(ARTIFACT_DIR),
                "BOT_TOKEN": env.get(
                    "BOT_TOKEN",
                    "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                ),
                "UID_HASH_KEY": env.get("UID_HASH_KEY", "integration-only-hmac-key"),
                "UID_ENC_KEY": env.get(
                    "UID_ENC_KEY",
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                ),
            }
        )
        pytest_args = list(args.pytest_args)
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        result = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-m",
                "integration",
                "tests/integration",
                *pytest_args,
            ],
            check=False,
            env=env,
        )
        exit_code = int(result.returncode)
        if exit_code:
            _write_container_logs(container_name)
            _dump_failed_databases(container_name)
            print(
                f"integration diagnostics: {ARTIFACT_DIR.resolve()}",
                file=sys.stderr,
            )
        return exit_code
    except Exception as exc:
        if started:
            _write_container_logs(container_name)
            _dump_failed_databases(container_name)
        print(f"PostgreSQL integration runner failed: {exc}", file=sys.stderr)
        return exit_code
    finally:
        if started and not args.keep_container:
            _run(
                ["docker", "rm", "--force", container_name],
                check=False,
                capture_output=True,
            )
        elif started:
            print(
                f"PostgreSQL container kept: {container_name} on port {port}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
