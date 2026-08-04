#!/usr/bin/env python3
"""Validate the rendered Docker Compose security contract for production services."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import NoReturn


class ComposeHardeningError(ValueError):
    """Raised when rendered Compose configuration violates the security contract."""


def _fail(message: str) -> NoReturn:
    raise ComposeHardeningError(message)


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(f"{field} must be an array")
    return value


def _service(payload: Mapping[str, object], name: str) -> Mapping[str, object]:
    services = _mapping(payload.get("services"), field="services")
    return _mapping(services.get(name), field=f"services.{name}")


def _network_names(service: Mapping[str, object], *, name: str) -> set[str]:
    networks = _mapping(service.get("networks"), field=f"services.{name}.networks")
    return {str(network_name) for network_name in networks}


def _mount_targets(service: Mapping[str, object], *, name: str) -> set[str]:
    targets: set[str] = set()
    for item in _sequence(service.get("volumes", []), field=f"services.{name}.volumes"):
        if isinstance(item, Mapping):
            target = item.get("target")
            if target:
                targets.add(str(target))
        elif isinstance(item, str):
            parts = item.split(":", 2)
            if len(parts) >= 2:
                targets.add(parts[1])
    return targets


def _tmpfs_targets(service: Mapping[str, object], *, name: str) -> set[str]:
    targets: set[str] = set()
    for item in _sequence(service.get("tmpfs", []), field=f"services.{name}.tmpfs"):
        if isinstance(item, Mapping):
            target = item.get("target")
            if target:
                targets.add(str(target))
        elif isinstance(item, str):
            targets.add(item.split(":", 1)[0])
    return targets


def _secret_sources(service: Mapping[str, object], *, name: str) -> set[str]:
    sources: set[str] = set()
    for item in _sequence(service.get("secrets", []), field=f"services.{name}.secrets"):
        if isinstance(item, Mapping):
            source = item.get("source")
            if source:
                sources.add(str(source))
        elif isinstance(item, str):
            sources.add(item)
    return sources


def _healthcheck_command(service: Mapping[str, object], *, name: str) -> str:
    healthcheck = _mapping(service.get("healthcheck"), field=f"services.{name}.healthcheck")
    test = _sequence(healthcheck.get("test"), field=f"services.{name}.healthcheck.test")
    return " ".join(str(part) for part in test)


def _assert_non_root(service: Mapping[str, object], *, name: str) -> None:
    raw_user = str(service.get("user") or "").strip()
    if not raw_user:
        _fail(f"services.{name}.user must be explicit")
    principal = raw_user.split(":", 1)[0].strip().lower()
    if principal in {"0", "root"}:
        _fail(f"services.{name}.user must be non-root")


def _assert_positive_limit(service: Mapping[str, object], *, name: str, field: str) -> None:
    value = service.get(field)
    if value is None or str(value).strip().lower() in {"", "0", "0.0", "0b"}:
        _fail(f"services.{name}.{field} must be a positive explicit limit")


def _assert_common_runtime(service: Mapping[str, object], *, name: str) -> None:
    if service.get("read_only") is not True:
        _fail(f"services.{name}.read_only must be true")
    if service.get("init") is not True:
        _fail(f"services.{name}.init must be true")
    _assert_non_root(service, name=name)

    cap_drop = {
        str(value).upper()
        for value in _sequence(service.get("cap_drop", []), field=f"services.{name}.cap_drop")
    }
    if "ALL" not in cap_drop:
        _fail(f"services.{name}.cap_drop must contain ALL")

    security_opt = {
        str(value)
        for value in _sequence(
            service.get("security_opt", []), field=f"services.{name}.security_opt"
        )
    }
    if not any(value.startswith("no-new-privileges") for value in security_opt):
        _fail(f"services.{name}.security_opt must enable no-new-privileges")

    if _tmpfs_targets(service, name=name) != {"/tmp"}:  # noqa: S108
        _fail(f"services.{name}.tmpfs must contain only /tmp")

    for field in ("mem_limit", "cpus", "pids_limit"):
        _assert_positive_limit(service, name=name, field=field)
    if not service.get("stop_grace_period"):
        _fail(f"services.{name}.stop_grace_period must be explicit")
    if service.get("ports"):
        _fail(f"services.{name} must not publish host ports")


def _validate_writable_paths(bot: Mapping[str, object], userbot: Mapping[str, object]) -> None:
    if _mount_targets(bot, name="bot") != {"/app/var"}:
        _fail("services.bot writable mounts must be limited to /app/var")
    if _mount_targets(userbot, name="userbot") != {
        "/app/var",
        "/run/romatic-userbot-session",
    }:
        _fail("services.userbot writable mounts must be runtime and Telethon session only")


def _validate_supervisor_secrets(bot: Mapping[str, object], userbot: Mapping[str, object]) -> None:
    if _secret_sources(bot, name="bot") != {"supervisor_token"}:
        _fail("services.bot must receive only the supervisor_token secret")
    if "supervisor_token" in _secret_sources(userbot, name="userbot"):
        _fail("services.userbot must not receive the supervisor token")

    bot_environment = _mapping(bot.get("environment"), field="services.bot.environment")
    if bot_environment.get("SUPERVISOR_TOKEN") not in {"", None}:
        _fail("services.bot must not receive an inline Supervisor token")
    if bot_environment.get("SUPERVISOR_TOKEN_FILE") != "/run/secrets/supervisor_token":
        _fail("services.bot must use the Supervisor token file")

    userbot_environment = _mapping(userbot.get("environment"), field="services.userbot.environment")
    if str(userbot_environment.get("SUPERVISOR_ENABLED", "")).lower() != "false":
        _fail("services.userbot must disable Supervisor integration")
    for field in ("SUPERVISOR_TOKEN", "SUPERVISOR_TOKEN_FILE", "SUPERVISOR_BASE_URL"):
        if userbot_environment.get(field) not in {"", None}:
            _fail(f"services.userbot.{field} must be empty")


def _validate_healthchecks(bot: Mapping[str, object], userbot: Mapping[str, object]) -> None:
    if "/readyz" not in _healthcheck_command(bot, name="bot"):
        _fail("services.bot healthcheck must use application readiness")
    if "userbot.healthcheck" not in _healthcheck_command(userbot, name="userbot"):
        _fail("services.userbot healthcheck must use the bounded readiness module")


def _validate_service_networks(
    bot: Mapping[str, object],
    userbot: Mapping[str, object],
    postgres: Mapping[str, object],
    supervisor: Mapping[str, object],
) -> None:
    application_network = "romatic-application"
    database_network = "romatic-database"
    supervisor_network = "romatic-supervisor-control"

    if _network_names(bot, name="bot") != {
        application_network,
        database_network,
        supervisor_network,
    }:
        _fail("services.bot network allowlist is incorrect")
    if _network_names(userbot, name="userbot") != {
        application_network,
        database_network,
    }:
        _fail("services.userbot network allowlist is incorrect")
    if _network_names(postgres, name="postgres") != {database_network}:
        _fail("services.postgres must be isolated to the database network")

    supervisor_networks = _network_names(supervisor, name="supervisor-proxy")
    if application_network in supervisor_networks or database_network in supervisor_networks:
        _fail("services.supervisor-proxy must not join application or database networks")


def _validate_network_definitions(payload: Mapping[str, object]) -> None:
    networks = _mapping(payload.get("networks"), field="networks")
    application = _mapping(networks.get("romatic-application"), field="romatic-application")
    database = _mapping(networks.get("romatic-database"), field="romatic-database")
    control = _mapping(
        networks.get("romatic-supervisor-control"), field="romatic-supervisor-control"
    )
    if bool(application.get("internal", False)):
        _fail("romatic-application must permit required outbound Telegram access")
    if database.get("internal") is not True:
        _fail("romatic-database must be internal")
    if control.get("internal") is not True:
        _fail("romatic-supervisor-control must be internal")


def validate_compose_hardening(payload: Mapping[str, object]) -> None:
    """Validate bot/userbot privilege, storage, network and readiness boundaries."""

    bot = _service(payload, "bot")
    userbot = _service(payload, "userbot")
    postgres = _service(payload, "postgres")
    supervisor = _service(payload, "supervisor-proxy")

    _assert_common_runtime(bot, name="bot")
    _assert_common_runtime(userbot, name="userbot")
    _validate_writable_paths(bot, userbot)
    _validate_supervisor_secrets(bot, userbot)
    _validate_healthchecks(bot, userbot)
    _validate_service_networks(bot, userbot, postgres, supervisor)
    _validate_network_definitions(payload)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        validate_compose_hardening(_mapping(payload, field="root"))
    except (ComposeHardeningError, json.JSONDecodeError) as exc:
        print(f"Compose hardening contract failed: {exc}", file=sys.stderr)
        return 1
    print("Compose hardening contract OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
