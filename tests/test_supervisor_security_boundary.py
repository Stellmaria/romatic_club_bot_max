from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def service_block(compose: str, name: str, next_name: str | None) -> str:
    start = compose.index(f"  {name}:")
    if next_name is None:
        return compose[start:]
    end = compose.index(f"\n  {next_name}:", start)
    return compose[start:end]


def test_only_bot_and_control_adapter_can_reach_proxy() -> None:
    compose = source("compose.yaml")
    proxy = service_block(compose, "supervisor-proxy", "bot")
    bot = service_block(compose, "bot", "userbot")
    userbot = service_block(compose, "userbot", None).split("\nvolumes:", 1)[0]

    assert "romatic-supervisor-control:" in proxy
    assert "hermes-supervisor-control:" in proxy
    assert "default: {}" not in proxy

    assert "default: {}" in bot
    assert "romatic-supervisor-control: {}" in bot
    assert "supervisor_token" in bot

    assert "default: {}" in userbot
    assert "romatic-supervisor-control" not in userbot
    assert 'SUPERVISOR_ENABLED: "false"' in userbot
    assert 'SUPERVISOR_TOKEN: ""' in userbot
    assert 'SUPERVISOR_TOKEN_FILE: ""' in userbot
    assert 'SUPERVISOR_BASE_URL: ""' in userbot


def test_supervisor_token_is_file_backed_and_not_shared_through_env() -> None:
    compose = source("compose.yaml")
    env_example = source(".env.example")
    client = source("bot/core/supervisor_client.py")
    unit = source("deploy/systemd/romatic-server-supervisor.service")

    assert "SUPERVISOR_TOKEN_FILE: /run/secrets/supervisor_token" in compose
    assert "SUPERVISOR_TOKEN_FILE_HOST" in compose
    assert "SUPERVISOR_TOKEN_FILE_HOST=" in env_example
    assert "SUPERVISOR_TOKEN=change_me" not in env_example
    assert 'Path(token_file).read_text(encoding="utf-8")' in client
    assert "EnvironmentFile=%DATA_DIR%/runtime/supervisor/supervisor.env" in unit
    assert "EnvironmentFile=%APP_DIR%/.env" not in unit


def test_unix_socket_uses_dedicated_group_and_mode_0660() -> None:
    runtime = source("scripts/server_supervisor.py")
    unit = source("deploy/systemd/romatic-server-supervisor.service")
    installer = source("deploy/server/install-server-supervisor.sh")

    assert "SOCKET_MODE = 0o660" in runtime
    assert "os.chmod(SOCKET_PATH, SOCKET_MODE)" in runtime
    assert "0o666" not in runtime
    assert "Group=%SUPERVISOR_GROUP%" in unit
    assert "UMask=0007" in unit
    assert 'SUPERVISOR_GROUP="${ROMATIC_SUPERVISOR_GROUP:-romatic-supervisor}"' in installer
    assert 'SUPERVISOR_GID="${ROMATIC_SUPERVISOR_GID:-10001}"' in installer
    assert 'groupadd --gid "$SUPERVISOR_GID" "$SUPERVISOR_GROUP"' in installer
    assert "Supervisor socket mode is not 0660" in installer


def test_control_operations_are_audited_idempotent_and_rate_limited() -> None:
    runtime = source("scripts/server_supervisor.py")
    client = source("bot/core/supervisor_client.py")

    for marker in (
        "X-Request-ID",
        "X-Actor",
        "ActorRateLimiter",
        "remembered_response",
        "remember_response",
        'outcome="replayed"',
        'outcome="rate_limited"',
        "_append_audit",
    ):
        assert marker in runtime or marker in client
    assert "uuid.uuid4().hex" in client
    assert "for attempt in range(2)" in client
    assert "aiohttp.ClientSession" in client
    assert "async def close" in client


def test_installer_proves_userbot_cannot_use_control_plane() -> None:
    installer = source("deploy/server/install-server-supervisor.sh")

    assert "exec -T userbot" in installer
    assert 'test -z "${SUPERVISOR_TOKEN:-}"' in installer
    assert 'test -z "${SUPERVISOR_TOKEN_FILE:-}"' in installer
    assert "socket.create_connection(('supervisor-proxy', 8765), 2)" in installer
    assert "Security check failed: userbot reached Supervisor proxy." in installer
