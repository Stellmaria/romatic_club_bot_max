from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from telethon import events

from userbot import entrypoint
from config import settings
from userbot.application import (
    UserbotConfigurationError,
    create_userbot_client,
    resolve_userbot_session,
)
from userbot import runtime

ROOT = Path(__file__).resolve().parents[1]


def test_entrypoint_has_no_import_time_client_or_decorator_registration() -> None:
    source = (ROOT / "userbot/entrypoint.py").read_text(encoding="utf-8")
    application_source = (ROOT / "userbot/application.py").read_text(encoding="utf-8")
    service_source = (ROOT / "userbot/services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "load_dotenv" not in source
    assert "os.getenv" not in source
    assert 'password = input(' not in application_source
    assert 'password = getpass(' in application_source
    assert not any(isinstance(node, ast.Raise) for node in tree.body)

    top_level_calls = [
        node.value.func
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Call)
    ]
    assert not any(
        isinstance(func, ast.Name) and func.id == "TelegramClient"
        for func in top_level_calls
    )

    decorated_handlers = [
        decorator
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "on"
    ]
    assert decorated_handlers == []

    service_tree = ast.parse(service_source)
    thread_root_definitions = [
        node
        for node in service_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_auction_thread_root"
    ]
    assert len(thread_root_definitions) == 1
    assert len(source.splitlines()) < 80


def test_register_handlers_preserves_count_order_and_chat_filters() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.registrations: list[tuple[object, object]] = []

        def add_event_handler(self, callback, event) -> None:
            self.registrations.append((callback, event))

    client = RecordingClient()
    previous_client = runtime.bound_client()
    try:
        runtime._client = None
        entrypoint.register_handlers(client)

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
            entrypoint.DISCUSSION_CHAT_ID,
            entrypoint.DISCUSSION_CHAT_ID,
            entrypoint.DISCUSSION_CHAT_ID,
        ]
    finally:
        runtime._client = previous_client


def test_entrypoint_preserves_public_handler_imports() -> None:
    from userbot.handlers import on_deleted, on_edited, on_new_message, register_handlers

    assert entrypoint.on_new_message is on_new_message
    assert entrypoint.on_edited is on_edited
    assert entrypoint.on_deleted is on_deleted
    assert entrypoint.register_handlers is register_handlers


def test_client_factory_uses_settings_without_connecting() -> None:
    calls: list[tuple[str, int, str]] = []
    sentinel = object()

    def factory(session: str, api_id: int, api_hash: str):
        calls.append((session, api_id, api_hash))
        return sentinel

    config = replace(
        settings,
        userbot_api_id=12345,
        userbot_api_hash="secret-hash",
        userbot_session="custom-session",
        discussion_chat_id=-100123,
    )

    result = create_userbot_client(
        config,
        client_factory=factory,
        environ={"USERBOT_SESSION": "custom-session"},
    )

    assert result is sentinel
    assert calls == [("custom-session", 12345, "secret-hash")]


def test_invalid_configuration_fails_before_client_construction() -> None:
    constructed = False

    def factory(*_args):
        nonlocal constructed
        constructed = True
        return object()

    config = replace(
        settings,
        userbot_api_id=0,
        userbot_api_hash="",
        discussion_chat_id=0,
    )

    try:
        create_userbot_client(config, client_factory=factory, environ={})
    except UserbotConfigurationError as exc:
        error_message = str(exc)
    else:
        raise AssertionError("invalid userbot configuration was accepted")

    assert constructed is False
    assert "USERBOT_API_ID" in error_message
    assert "USERBOT_API_HASH" in error_message
    assert "DISCUSSION_CHAT_ID" in error_message


def test_default_session_falls_back_to_existing_legacy_root(tmp_path: Path) -> None:
    legacy_file = tmp_path / "userbot_session.session"
    legacy_file.touch()
    runtime_dir = tmp_path / "var"
    config = replace(
        settings,
        runtime_dir=runtime_dir,
        userbot_session=str(runtime_dir / "userbot_session"),
    )

    result = resolve_userbot_session(config, environ={}, project_root=tmp_path)

    assert result == str(tmp_path / "userbot_session")


def test_explicit_session_never_uses_legacy_fallback(tmp_path: Path) -> None:
    (tmp_path / "userbot_session.session").touch()
    configured = tmp_path / "sessions" / "named"
    config = replace(settings, userbot_session=str(configured))

    result = resolve_userbot_session(
        config,
        environ={"USERBOT_SESSION": str(configured)},
        project_root=tmp_path,
    )

    assert result == str(configured)
