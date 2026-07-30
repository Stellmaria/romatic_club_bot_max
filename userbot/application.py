"""Userbot composition and process lifecycle.

This module owns runtime configuration validation and ``TelegramClient``
construction.  Importing the legacy-compatible entrypoint therefore has no
network, session or credential-validation side effects.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from getpass import getpass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from bot.core.settings import PROJECT_ROOT, Settings, settings
from db.core import close_db, init_db
from userbot.handlers import register_handlers
from userbot.workers import autobid_watchdog

ClientFactory = Callable[[str, int, str], Any]
logger = logging.getLogger("userbot")


class UserbotConfigurationError(RuntimeError):
    """Raised by the application bootstrap when required settings are absent."""


def userbot_configuration_errors(config: Settings = settings) -> tuple[str, ...]:
    """Return actionable configuration errors without exposing credentials."""

    errors: list[str] = []
    if config.userbot_api_id <= 0:
        errors.append("USERBOT_API_ID is not configured")
    if not config.userbot_api_hash.strip():
        errors.append("USERBOT_API_HASH is not configured")
    if not config.discussion_chat_id:
        errors.append("DISCUSSION_CHAT_ID is not configured")
    if not config.userbot_session.strip():
        errors.append("USERBOT_SESSION is empty")
    return tuple(errors)


def resolve_userbot_session(
    config: Settings = settings,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> str:
    """Resolve a session base while preserving the historical root session.

    ``Settings`` now defaults to ``var/userbot_session``.  Existing deployments
    may still have the live ``userbot_session.session`` beside the entrypoint.
    Unless ``USERBOT_SESSION`` was explicitly configured, keep using that file
    so a refactor cannot silently start a second Telegram authorization session.
    """

    environment = os.environ if environ is None else environ
    configured_session = config.userbot_session.strip()
    if (environment.get("USERBOT_SESSION") or "").strip():
        return configured_session

    configured_path = Path(configured_session)
    default_path = Path(config.runtime_dir) / "userbot_session"
    legacy_path = Path(project_root) / "userbot_session"
    if configured_path == default_path and legacy_path.with_suffix(".session").is_file():
        return str(legacy_path)
    return configured_session


def create_userbot_client(
    config: Settings = settings,
    *,
    client_factory: ClientFactory = TelegramClient,
    environ: Mapping[str, str] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> TelegramClient:
    """Validate settings and construct, but do not connect, a Telegram client."""

    errors = userbot_configuration_errors(config)
    if errors:
        raise UserbotConfigurationError("; ".join(errors))

    session = resolve_userbot_session(
        config,
        environ=environ,
        project_root=project_root,
    )
    session_path = Path(session)
    if session_path.parent != Path("."):
        session_path.parent.mkdir(parents=True, exist_ok=True)
    return client_factory(
        session,
        config.userbot_api_id,
        config.userbot_api_hash,
    )


async def run_userbot_application() -> None:
    """Run authorization, handlers and workers with deterministic cleanup."""

    logging.basicConfig(level=logging.INFO)
    telegram_client = create_userbot_client()
    register_handlers(telegram_client)
    watchdog_task: asyncio.Task[None] | None = None

    try:
        await init_db()
        await telegram_client.connect()
        if not await telegram_client.is_user_authorized():
            phone = input("Введите телефон (+7...): ").strip()
            await telegram_client.send_code_request(phone)
            code = input("Введите код: ").strip()
            try:
                await telegram_client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = getpass("Введите пароль 2FA: ").strip()
                await telegram_client.sign_in(phone=phone, password=password)

        watchdog_task = asyncio.create_task(
            autobid_watchdog(telegram_client),
            name="userbot-autobid-watchdog",
        )
        current_user = await telegram_client.get_me()
        logger.info("Userbot logged in as @%s", current_user.username or current_user.id)
        logger.info("Listening discussion chat for bids/moderation/rules…")
        await telegram_client.run_until_disconnected()
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        try:
            if telegram_client.is_connected():
                await telegram_client.disconnect()
        finally:
            await close_db()


__all__ = [
    "UserbotConfigurationError",
    "create_userbot_client",
    "resolve_userbot_session",
    "run_userbot_application",
    "userbot_configuration_errors",
]
