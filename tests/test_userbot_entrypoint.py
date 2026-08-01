from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

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


def test_entrypoint_has_no_import_time_client_or_environment_bootstrap() -> None:
    source = (ROOT / "userbot/entrypoint.py").read_text(encoding="utf-8")
    application_source = (ROOT / "userbot/application.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "load_dotenv" not in source
    assert "os.getenv" not in source
    assert 'password = input(' not in application_source
    assert 'password = getpass(' in application_source
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


def test_client_factory_uses_typed_settings_without_connecting(tmp_path: Path) -> None:
    calls: list[tuple[str, int, str]] = []
    sentinel = object()

    def factory(session: str, api_id: int, api_hash: str):
        calls.append((session, api_id, api_hash))
        return sentinel

    config = replace(_config(tmp_path), session="custom-session")
    result = create_userbot_client(
        config,
        project_root=tmp_path,
        client_factory=factory,
        environ={"USERBOT_SESSION": "custom-session"},
    )
    assert result is sentinel
    assert calls == [("custom-session", 12345, "secret-hash")]


def test_invalid_manual_configuration_fails_before_client_construction(tmp_path: Path) -> None:
    constructed = False

    def factory(*_args):
        nonlocal constructed
        constructed = True
        return object()

    config = replace(_config(tmp_path), api_id=0, api_hash="", discussion_chat_id=0)
    try:
        create_userbot_client(
            config,
            project_root=tmp_path,
            client_factory=factory,
            environ={},
        )
    except UserbotConfigurationError as exc:
        error_message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid userbot configuration was accepted")
    assert constructed is False
    assert "USERBOT_API_ID" in error_message
    assert "USERBOT_API_HASH" in error_message
    assert "DISCUSSION_CHAT_ID" in error_message


def test_default_session_falls_back_to_existing_legacy_root(tmp_path: Path) -> None:
    (tmp_path / "userbot_session.session").touch()
    runtime_dir = tmp_path / "var"
    config = replace(
        _config(tmp_path),
        runtime_dir=runtime_dir,
        session=str(runtime_dir / "userbot_session"),
    )
    result = resolve_userbot_session(config, environ={}, project_root=tmp_path)
    assert result == str(tmp_path / "userbot_session")


def test_explicit_session_never_uses_legacy_fallback(tmp_path: Path) -> None:
    (tmp_path / "userbot_session.session").touch()
    configured = tmp_path / "sessions" / "named"
    config = replace(_config(tmp_path), session=str(configured))
    result = resolve_userbot_session(
        config,
        environ={"USERBOT_SESSION": str(configured)},
        project_root=tmp_path,
    )
    assert result == str(configured)


def test_entrypoint_exports_only_composition_functions() -> None:
    assert entrypoint.__all__ == ["main", "run"]
