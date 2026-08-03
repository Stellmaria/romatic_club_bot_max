"""Userbot process lifecycle and dependency composition."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from getpass import getpass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from bot.core.settings import UserbotProcessSettings, UserbotSettings
from bot.core.tasks import (
    BackgroundTaskManager,
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
)

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


async def _run_client_with_worker_monitor(
    telegram_client: TelegramClient,
    task_manager: BackgroundTaskManager,
) -> None:
    disconnected = asyncio.create_task(
        telegram_client.run_until_disconnected(),
        name="userbot-disconnected-monitor",
    )
    worker_monitor = asyncio.create_task(
        task_manager.wait_for_failure(),
        name="userbot-worker-monitor",
    )
    try:
        done, _ = await asyncio.wait(
            {disconnected, worker_monitor},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_monitor in done:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            await worker_monitor
        worker_monitor.cancel()
        await asyncio.gather(worker_monitor, return_exceptions=True)
        await disconnected
    finally:
        for task in (disconnected, worker_monitor):
            if not task.done():
                task.cancel()
        await asyncio.gather(disconnected, worker_monitor, return_exceptions=True)


async def run_userbot_application(config: UserbotProcessSettings) -> None:
    """Run authorization, handlers and supervised workers with deterministic cleanup."""

    logging.basicConfig(level=logging.INFO)

    from bot.core.legacy_config import configure_legacy_config
    from bot.uid_crypto import configure_uid_crypto

    configure_legacy_config(config)
    userbot_settings = config.userbot
    configure_uid_crypto(userbot_settings.uid_hash_key, userbot_settings.uid_enc_key)

    from db.lifecycle import close_db, init_db
    from db.pool import DatabaseRuntime
    from userbot.handlers import register_handlers, register_schedule_handlers
    from userbot.workers import autobid_watchdog, schedule_announcement_watchdog

    database_runtime = DatabaseRuntime(config.database)
    telegram_client = create_userbot_client(
        userbot_settings,
        project_root=config.project_root,
    )
    register_handlers(telegram_client)
    register_schedule_handlers(telegram_client)
    task_manager: BackgroundTaskManager | None = None

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

        task_manager = BackgroundTaskManager()
        task_manager.start(
            [
                BackgroundTaskSpec(
                    "userbot-autobid-watchdog",
                    lambda _context: autobid_watchdog(telegram_client),
                    criticality=WorkerCriticality.CRITICAL,
                    restart_policy=RestartPolicy.ON_FAILURE,
                    max_failures=4,
                    max_backoff=30.0,
                    shutdown_timeout=20.0,
                ),
                BackgroundTaskSpec(
                    "userbot-schedule-announcement-watchdog",
                    lambda _context: schedule_announcement_watchdog(
                        telegram_client,
                        config=userbot_settings,
                    ),
                    criticality=WorkerCriticality.RECOVERABLE,
                    restart_policy=RestartPolicy.ALWAYS,
                    max_failures=8,
                    max_backoff=60.0,
                    shutdown_timeout=20.0,
                ),
            ]
        )
        current_user = await telegram_client.get_me()
        logger.info("Userbot logged in as @%s", current_user.username or current_user.id)
        logger.info("Listening discussion chat for bids/moderation/rules…")
        await _run_client_with_worker_monitor(telegram_client, task_manager)
    finally:
        if task_manager is not None:
            await task_manager.stop()
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
