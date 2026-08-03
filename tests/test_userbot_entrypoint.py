from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from telethon import events

from bot.core.legacy_config import configure_legacy_config, reset_legacy_config_for_testing
from bot.core.settings import DatabaseSettings, UserbotProcessSettings, UserbotSettings
from userbot import entrypoint, runtime
from userbot.application import (
    UserbotConfigurationError,
    create_userbot_client,
    resolve_userbot_session,
)
from userbot.handlers import register_handlers
from userbot.session import UserbotSessionError, prepare_session_storage, secure_session_files

ROOT = Path(__file__).resolve().parents[1]
FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def _config(tmp_path: Path) -> UserbotSettings:
    return UserbotSettings.from_env(
        {
            "USERBOT_API_ID": "12345",
            "USERBOT_API_HASH": "secret-hash",
            "AUCTION_CHANNEL_ID": "-100111",
            "DISCUSSION_CHAT_ID": "-100222",
            "DATABASE_URL": "postgresql://localhost/test",
            "UID_HASH_KEY": "test-only-hmac-key",
            "UID_ENC_KEY": FERNET_KEY,
        },
        project_root=tmp_path,
    )


def _private_session(path: Path) -> None:
    session_file = prepare_session_storage(path)
    session_file.write_bytes(b"sqlite")
    secure_session_files(path)


def test_entrypoint_has_no_import_time_client_or_environment_bootstrap() -> None:
    source = (ROOT / "userbot/entrypoint.py").read_text(encoding="utf-8")
    application_source = (ROOT / "userbot/application.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "load_dotenv" not in source
    assert "os.getenv" not in source
    assert "input(" not in application_source
    assert "getpass" not in application_source
    assert not any(isinstance(node, ast.Raise) for node in tree.body)
    assert not any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "load_project_environment"
        for node in tree.body
    )
    assert len(source.splitlines()) < 80


def test_register_handlers_preserves_count_order_and_chat_filters(tmp_path: Path) -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.registrations: list[tuple[object, object]] = []

        def add_event_handler(self, callback, event) -> None:
            self.registrations.append((callback, event))

    userbot = _config(tmp_path)
    configure_legacy_config(
        UserbotProcessSettings(
            userbot=userbot,
            database=DatabaseSettings("postgresql://localhost/test"),
            project_root=tmp_path,
            runtime_dir=tmp_path / "var",
        )
    )
    client = RecordingClient()
    previous_client = runtime.bound_client()
    try:
        runtime._client = None
        register_handlers(client)
        assert [callback.__name__ for callback, _ in client.registrations] == [
            "on_new_message",
            "on_edited",
            "on_deleted",
        ]
        assert [type(event) for _, event in client.registrations] == [
            events.NewMessage,
            events.MessageEdited,
            events.MessageDeleted,
        ]
        assert [event.chats for _, event in client.registrations] == [
            userbot.discussion_chat_id,
            userbot.discussion_chat_id,
            userbot.discussion_chat_id,
        ]
    finally:
        runtime._client = previous_client
        reset_legacy_config_for_testing()


def test_client_factory_uses_private_existing_session_without_connecting(tmp_path: Path) -> None:
    calls: list[tuple[str, int, str]] = []
    sentinel = object()

    def factory(session: str, api_id: int, api_hash: str):
        calls.append((session, api_id, api_hash))
        return sentinel

    configured = tmp_path / "sessions" / "custom"
    _private_session(configured)
    config = replace(_config(tmp_path), session=str(configured))
    result = create_userbot_client(
        config,
        project_root=tmp_path,
        client_factory=factory,
        environ={"USERBOT_SESSION": str(configured)},
    )
    assert result is sentinel
    assert calls == [(str(configured), 12345, "secret-hash")]


def test_invalid_manual_configuration_fails_before_client_construction(tmp_path: Path) -> None:
    constructed = False

    def factory(*_args):
        nonlocal constructed
        constructed = True
        return object()

    config = replace(_config(tmp_path), api_id=0, api_hash="", discussion_chat_id=0)
    with pytest.raises(UserbotConfigurationError) as captured:
        create_userbot_client(
            config,
            project_root=tmp_path,
            client_factory=factory,
            environ={},
        )
    error_message = str(captured.value)
    assert constructed is False
    assert "USERBOT_API_ID" in error_message
    assert "USERBOT_API_HASH" in error_message
    assert "DISCUSSION_CHAT_ID" in error_message


def test_default_session_does_not_use_legacy_root_fallback(tmp_path: Path) -> None:
    legacy = tmp_path / "userbot_session.session"
    legacy.touch(mode=0o600)
    runtime_dir = tmp_path / "var"
    config = replace(
        _config(tmp_path),
        runtime_dir=runtime_dir,
        session=str(runtime_dir / "userbot_session"),
    )
    result = resolve_userbot_session(config, environ={}, project_root=tmp_path)
    assert result == str(runtime_dir / "userbot_session")
    with pytest.raises(UserbotSessionError, match="directory does not exist"):
        create_userbot_client(config, project_root=tmp_path, client_factory=lambda *_: object())


def test_explicit_session_is_returned_unchanged(tmp_path: Path) -> None:
    configured = tmp_path / "sessions" / "named"
    config = replace(_config(tmp_path), session=str(configured))
    result = resolve_userbot_session(
        config,
        environ={"USERBOT_SESSION": str(configured)},
        project_root=tmp_path,
    )
    assert result == str(configured)


def test_entrypoint_exports_only_run() -> None:
    assert entrypoint.__all__ == ["run"]
