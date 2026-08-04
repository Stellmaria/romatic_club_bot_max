from __future__ import annotations

import io
import json
from copy import deepcopy

import pytest

from scripts.validate_compose_hardening import (
    ComposeHardeningError,
    main,
    validate_compose_hardening,
)


def _runtime_service(*, healthcheck: str, networks: tuple[str, ...]) -> dict[str, object]:
    return {
        "read_only": True,
        "init": True,
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:size=64m,mode=1777"],  # noqa: S108
        "mem_limit": 536870912,
        "cpus": 1.0,
        "pids_limit": 256,
        "stop_grace_period": "45s",
        "healthcheck": {"test": ["CMD", "python", "-c", healthcheck]},
        "networks": dict.fromkeys(networks),
    }


def _runtime_permissions_service() -> dict[str, object]:
    return {
        "image": "romaticclub-bot",
        "user": "0:0",
        "read_only": True,
        "network_mode": "none",
        "volumes": [
            {"type": "bind", "target": "/runtime/bot"},
            {"type": "bind", "target": "/runtime/userbot"},
        ],
        "tmpfs": ["/tmp:size=16m,mode=1777"],  # noqa: S108
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": 67108864,
        "cpus": 0.25,
        "pids_limit": 32,
        "stop_grace_period": "10s",
        "command": [
            "mkdir -p /runtime/bot /runtime/userbot; "
            "chown -R 10001:10001 /runtime/bot /runtime/userbot; "
            "chmod 0700 /runtime/bot /runtime/userbot"
        ],
    }


def _valid_payload() -> dict[str, object]:
    bot = _runtime_service(
        healthcheck="open('http://127.0.0.1:8081/readyz')",
        networks=(
            "romatic-application",
            "romatic-database",
            "romatic-supervisor-control",
        ),
    )
    bot.update(
        {
            "image": "romaticclub-bot",
            "volumes": [{"type": "bind", "target": "/app/var"}],
            "secrets": [{"source": "supervisor_token"}],
            "environment": {
                "SUPERVISOR_TOKEN": "",
                "SUPERVISOR_TOKEN_FILE": "/run/secrets/supervisor_token",
            },
            "depends_on": {
                "runtime-permissions": {
                    "condition": "service_completed_successfully"
                }
            },
        }
    )

    userbot = _runtime_service(
        healthcheck="python -m userbot.healthcheck",
        networks=("romatic-application", "romatic-database"),
    )
    userbot.update(
        {
            "volumes": [
                {"type": "bind", "target": "/app/var"},
                {"type": "bind", "target": "/run/romatic-userbot-session"},
            ],
            "secrets": [],
            "environment": {
                "SUPERVISOR_ENABLED": "false",
                "SUPERVISOR_TOKEN": "",
                "SUPERVISOR_TOKEN_FILE": "",
                "SUPERVISOR_BASE_URL": "",
            },
            "depends_on": {
                "runtime-permissions": {
                    "condition": "service_completed_successfully"
                }
            },
        }
    )

    return {
        "services": {
            "runtime-permissions": _runtime_permissions_service(),
            "bot": bot,
            "userbot": userbot,
            "postgres": {"networks": {"romatic-database": None}},
            "supervisor-proxy": {
                "networks": {
                    "romatic-supervisor-control": None,
                    "hermes-supervisor-control": None,
                }
            },
        },
        "networks": {
            "romatic-application": {"driver": "bridge"},
            "romatic-database": {"driver": "bridge", "internal": True},
            "romatic-supervisor-control": {"driver": "bridge", "internal": True},
        },
    }


def test_rendered_compose_hardening_contract_accepts_expected_boundaries() -> None:
    validate_compose_hardening(_valid_payload())


@pytest.mark.parametrize(
    ("service_name", "field", "value"),
    [
        ("bot", "read_only", False),
        ("bot", "user", "0:0"),
        ("userbot", "cap_drop", []),
        ("userbot", "pids_limit", 0),
    ],
)
def test_rendered_compose_hardening_contract_rejects_weak_runtime_settings(
    service_name: str,
    field: str,
    value: object,
) -> None:
    payload = _valid_payload()
    services = payload["services"]
    assert isinstance(services, dict)
    service = services[service_name]
    assert isinstance(service, dict)
    service[field] = value

    with pytest.raises(ComposeHardeningError):
        validate_compose_hardening(payload)


def test_runtime_permission_bootstrap_capabilities_are_bounded() -> None:
    payload = deepcopy(_valid_payload())
    services = payload["services"]
    assert isinstance(services, dict)
    bootstrap = services["runtime-permissions"]
    assert isinstance(bootstrap, dict)
    bootstrap["cap_add"] = ["CHOWN", "SYS_ADMIN"]

    with pytest.raises(ComposeHardeningError, match="capability allowlist"):
        validate_compose_hardening(payload)


def test_bot_must_wait_for_runtime_permission_bootstrap() -> None:
    payload = deepcopy(_valid_payload())
    services = payload["services"]
    assert isinstance(services, dict)
    bot = services["bot"]
    assert isinstance(bot, dict)
    bot["depends_on"] = {}

    with pytest.raises(ComposeHardeningError, match="runtime-permissions"):
        validate_compose_hardening(payload)


def test_userbot_cannot_join_supervisor_network() -> None:
    payload = _valid_payload()
    services = payload["services"]
    assert isinstance(services, dict)
    userbot = services["userbot"]
    assert isinstance(userbot, dict)
    networks = userbot["networks"]
    assert isinstance(networks, dict)
    networks["romatic-supervisor-control"] = None

    with pytest.raises(ComposeHardeningError, match="userbot network allowlist"):
        validate_compose_hardening(payload)


def test_userbot_cannot_receive_supervisor_secret() -> None:
    payload = deepcopy(_valid_payload())
    services = payload["services"]
    assert isinstance(services, dict)
    userbot = services["userbot"]
    assert isinstance(userbot, dict)
    userbot["secrets"] = [{"source": "supervisor_token"}]

    with pytest.raises(ComposeHardeningError, match="must not receive"):
        validate_compose_hardening(payload)


def test_bot_healthcheck_must_use_readiness() -> None:
    payload = deepcopy(_valid_payload())
    services = payload["services"]
    assert isinstance(services, dict)
    bot = services["bot"]
    assert isinstance(bot, dict)
    bot["healthcheck"] = {"test": ["CMD", "pgrep", "python"]}

    with pytest.raises(ComposeHardeningError, match="application readiness"):
        validate_compose_hardening(payload)


def test_cli_accepts_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_payload())))
    assert main() == 0


def test_cli_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
    assert main() == 1
