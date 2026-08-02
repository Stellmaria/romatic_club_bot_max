"""Userbot process lifecycle and dependency composition."""

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

from bot.core.settings import UserbotProcessSettings, UserbotSettings

ClientFactory = Callable[[str, int, str], Any]
logger = logging.getLogger("userbot")


class UserbotConfigurationError(RuntimeError):
    """Raised when a manually constructed userbot model is incomplete."""


def userbot_configuration_errors(config: UserbotSettings) -> tuple[str, ...]:
    errors: list[str] = []
    if config.api_id <= 0:
        errors.append("USERBOT_API_ID is not configured")
    if not config.api_hash.strip():
        errors.append("USERBOT_API_HASH is not configured")
    if not config.discussion_chat_id:
        errors.append("DISCUSSION_CHAT_ID is not configured")
    if not config.session.strip():
        errors.append("USERBOT_SESSION is empty")
    return tuple(errors)


def resolve_userbot_session(
    config: UserbotSettings,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path,
) -> str:
    """Preserve an existing root session unless a session was configured."""
    environment = os.environ if environ is None else environ
    configured_session = config.session.strip()
    if (environment.get("USERBOT_SESSION") or "").strip():
        return configured_session

    configured_path = Path(configured_session)
    default_path = config.runtime_dir / "userbot_session"
    legacy_path = project_root / "userbot_session"
    if configured_path == default_path and legacy_path.with_suffix(".session").is_file():
        return str(legacy_path)
    return configured_session


def create_userbot_client(
    config: UserbotSettings,
    *,
    project_root: Path,
    client_factory: ClientFactory = TelegramClient,
    environ: Mapping[str, str] | None = None,
) -> TelegramClient:
    """Construct, but do not connect, a Telegram client."""
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
    return client_factory(session, config.api_id, config.api_hash)


async def run_userbot_application(config: UserbotProcessSettings) -> None:
    """Run authorization, handlers and workers with deterministic cleanup."""
    logging.basicConfig(level=logging.INFO)

    from bot.core.legacy_config import configure_legacy_config
    from bot.uid_crypto import configure_uid_crypto

    configure_legacy_config(config)
    userbot_settings = config.userbot
    configure_uid_crypto(userbot_settings.uid_hash_key, userbot_settings.uid_enc_key)

    from db.lifecycle import close_db, init_db
    from db.pool import DatabaseRuntime
    from userbot.handlers import register_handlers, register_schedule_handlers
    from userbot.workers import (
        autobid_watchdog,
        publication_reconciliation_watchdog,
        schedule_announcement_watchdog,
    )

    database_runtime = DatabaseRuntime(config.database)
    telegram_client = create_userbot_client(
        userbot_settings,
        project_root=config.project_root,
    )
    register_handlers(telegram_client)
    register_schedule_handlers(telegram_client)
    worker_tasks: list[asyncio.Task[None]] = []

    try:
        await init_db(database_runtime)
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

        worker_tasks.extend(
            (
                asyncio.create_task(
                    autobid_watchdog(telegram_client),
                    name="userbot-autobid-watchdog",
                ),
                asyncio.create_task(
                    publication_reconciliation_watchdog(telegram_client),
                    name="userbot-publication-reconciliation-watchdog",
                ),
                asyncio.create_task(
                    schedule_announcement_watchdog(
                        telegram_client,
                        config=userbot_settings,
                    ),
                    name="userbot-schedule-announcement-watchdog",
                ),
            )
        )
        current_user = await telegram_client.get_me()
        logger.info("Userbot logged in as @%s", current_user.username or current_user.id)
        logger.info("Listening discussion chat for bids/moderation/rules…")
        await telegram_client.run_until_disconnected()
    finally:
        for task in worker_tasks:
            task.cancel()
        for task in worker_tasks:
            with suppress(asyncio.CancelledError):
                await task
        try:
            if telegram_client.is_connected():
                await telegram_client.disconnect()
        finally:
            await close_db(database_runtime)


__all__ = [
    "UserbotConfigurationError",
    "create_userbot_client",
    "resolve_userbot_session",
    "run_userbot_application",
    "userbot_configuration_errors",
]
