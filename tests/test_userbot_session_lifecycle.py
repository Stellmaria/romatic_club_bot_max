from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from bot.core.settings import DatabaseSettings, UserbotProcessSettings, UserbotSettings
from userbot import application
from userbot.provision import ProvisioningSettings
from userbot.session import (
    UserbotSessionError,
    prepare_session_storage,
    secure_session_files,
    session_file_path,
    validate_session_storage,
)

FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def test_production_application_has_no_interactive_authentication() -> None:
    source = inspect.getsource(application)
    assert "input(" not in source
    assert "getpass" not in source


def test_provisioning_settings_do_not_require_database_or_bot_secret(tmp_path: Path) -> None:
    settings = ProvisioningSettings.from_env(
        {
            "USERBOT_API_ID": "12345",
            "USERBOT_API_HASH": "hash",
            "USERBOT_SESSION": "private/userbot",
        },
        project_root=tmp_path,
    )
    assert settings.api_id == 12345
    assert settings.session == str(tmp_path / "private" / "userbot")


def test_missing_session_fails_without_creating_files(tmp_path: Path) -> None:
    configured = tmp_path / "session" / "userbot"
    with pytest.raises(UserbotSessionError, match="does not exist"):
        validate_session_storage(configured)
    assert not configured.parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_session_permissions_are_enforced(tmp_path: Path) -> None:
    configured = tmp_path / "session" / "userbot"
    session_file = prepare_session_storage(configured)
    session_file.write_text("sqlite", encoding="utf-8")
    session_file.chmod(0o644)

    with pytest.raises(UserbotSessionError, match="permissions are too broad"):
        validate_session_storage(configured)

    secure_session_files(configured)
    assert validate_session_storage(configured) == session_file
    assert session_file_path(configured) == session_file
    assert session_file.stat().st_mode & 0o777 == 0o600
    assert session_file.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_unauthorized_session_fails_before_database_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "session" / "userbot"
    session_file = prepare_session_storage(configured)
    session_file.write_bytes(b"sqlite")
    secure_session_files(configured)

    userbot = UserbotSettings.from_env(
        {
            "USERBOT_API_ID": "12345",
            "USERBOT_API_HASH": "secret-hash",
            "USERBOT_SESSION": str(configured),
            "AUCTION_CHANNEL_ID": "-100111",
            "DISCUSSION_CHAT_ID": "-100222",
            "DATABASE_URL": "postgresql://localhost/test",
            "UID_HASH_KEY": "test-only-hmac-key",
            "UID_ENC_KEY": FERNET_KEY,
        },
        project_root=tmp_path,
    )
    config = UserbotProcessSettings(
        userbot=userbot,
        database=DatabaseSettings("postgresql://localhost/test"),
        project_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )

    database_started = False

    async def fake_init_db(_runtime) -> None:
        nonlocal database_started
        database_started = True

    async def fake_close_db(_runtime) -> None:
        return None

    class FakeClient:
        def __init__(self) -> None:
            self.connected = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def is_user_authorized(self) -> bool:
            return False

        def is_connected(self) -> bool:
            return self.connected

        async def disconnect(self) -> None:
            self.connected = False
            self.disconnected = True

    client = FakeClient()
    monkeypatch.setattr(application, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bot.core.legacy_config.configure_legacy_config", lambda _config: None)
    monkeypatch.setattr("bot.uid_crypto.configure_uid_crypto", lambda *_args: None)
    monkeypatch.setattr("db.lifecycle.init_db", fake_init_db)
    monkeypatch.setattr("db.lifecycle.close_db", fake_close_db)
    monkeypatch.setattr("db.pool.DatabaseRuntime", lambda _settings: object())

    with pytest.raises(UserbotSessionError, match="not authorized"):
        await application.run_userbot_application(
            config,
            client_factory=lambda *_args: client,
        )

    assert database_started is False
    assert client.disconnected is True
    assert session_file.exists()
