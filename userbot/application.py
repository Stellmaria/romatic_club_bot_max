"""Userbot production lifecycle and dependency composition."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from bot.core.logging import configure_logging
from bot.core.settings import UserbotProcessSettings, UserbotSettings
from bot.core.tasks import (
    BackgroundTaskManager,
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
)
from userbot.health import (
    build_health_payload,
    health_file_path,
    health_reporter_loop,
    write_health,
)
from userbot.session import UserbotSessionError, validate_session_storage

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
    """Return the configured session path without legacy filesystem fallback."""

    del environ, project_root
    return config.session.strip()


def create_userbot_client(
    config: UserbotSettings,
    *,
    project_root: Path,
    client_factory: ClientFactory = TelegramClient,
    environ: Mapping[str, str] | None = None,
) -> TelegramClient:
    """Construct a production client only for an existing private session."""

    errors = userbot_configuration_errors(config)
    if errors:
        raise UserbotConfigurationError("; ".join(errors))
    session = resolve_userbot_session(config, environ=environ, project_root=project_root)
    validate_session_storage(session)
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


async def run_userbot_application(
    config: UserbotProcessSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    """Run a pre-authorized session and supervised workers without reading stdin."""

    configure_logging("INFO", structured=True)

    from bot.core.legacy_config import configure_legacy_config
    from bot.uid_crypto import configure_uid_crypto
    from db.lifecycle import close_db, init_db
    from db.pool import DatabaseRuntime
    from userbot.handlers import register_handlers, register_schedule_handlers
    from userbot.publication_recovery import run_issue99_publication_recovery
    from userbot.workers import autobid_watchdog, schedule_announcement_watchdog

    configure_legacy_config(config)
    userbot_settings = config.userbot
    configure_uid_crypto(
        userbot_settings.uid_hash_key,
        userbot_settings.uid_enc_key,
        (userbot_settings.uid_enc_key_previous,),
    )

    runtime_dir = config.runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    health_path = health_file_path(runtime_dir)
    write_health(
        health_path,
        build_health_payload(status="starting", connected=False, authorized=False),
    )

    database_runtime = DatabaseRuntime(config.database)
    telegram_client: TelegramClient | None = None
    task_manager: BackgroundTaskManager | None = None
    database_started = False
    authorized = False
    primary_error: BaseException | None = None

    try:
        telegram_client = create_userbot_client(
            userbot_settings,
            project_root=config.project_root,
            client_factory=client_factory,
        )
        await telegram_client.connect()
        if not await telegram_client.is_user_authorized():
            raise UserbotSessionError(
                "Userbot session is not authorized. "
                "Run `auction-userbot-provision authorize` outside the production process."
            )
        authorized = True

        # Session readiness is checked before database initialization so an
        # absent or revoked credential fails fast with the correct diagnosis.
        await init_db(database_runtime)
        database_started = True
        register_handlers(telegram_client)
        register_schedule_handlers(telegram_client, userbot_settings)

        try:
            await run_issue99_publication_recovery(
                telegram_client,
                userbot_settings,
            )
        except Exception:
            logger.exception(
                "Issue #99 publication recovery could not complete; "
                "userbot startup will continue for operator access"
            )

        task_manager = BackgroundTaskManager()
        task_manager.start(
            [
                BackgroundTaskSpec(
                    "userbot-autobid-watchdog",
                    lambda context: autobid_watchdog(
                        telegram_client,
                        heartbeat=context.heartbeat,
                    ),
                    criticality=WorkerCriticality.CRITICAL,
                    restart_policy=RestartPolicy.ON_FAILURE,
                    max_failures=4,
                    max_backoff=30.0,
                    heartbeat_timeout=60.0,
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
                BackgroundTaskSpec(
                    "userbot-health-reporter",
                    lambda context: health_reporter_loop(
                        context,
                        task_manager=task_manager,
                        telegram_client=telegram_client,
                        path=health_path,
                    ),
                    criticality=WorkerCriticality.RECOVERABLE,
                    restart_policy=RestartPolicy.ALWAYS,
                    max_failures=8,
                    max_backoff=30.0,
                    heartbeat_timeout=20.0,
                    shutdown_timeout=10.0,
                ),
            ]
        )
        current_user = await telegram_client.get_me()
        logger.info(
            "Userbot production session ready",
            extra={"telegram_user_id": getattr(current_user, "id", None)},
        )
        await _run_client_with_worker_monitor(telegram_client, task_manager)
    except BaseException as error:
        primary_error = error
        write_health(
            health_path,
            build_health_payload(
                status="failed",
                connected=bool(telegram_client and telegram_client.is_connected()),
                authorized=authorized,
                task_manager=task_manager,
                error=f"{type(error).__name__}: {error}",
            ),
        )
        raise
    finally:
        if task_manager is not None:
            await task_manager.stop()
        if telegram_client is not None and telegram_client.is_connected():
            await telegram_client.disconnect()
        if database_started:
            await close_db(database_runtime)
        if primary_error is None:
            write_health(
                health_path,
                build_health_payload(
                    status="stopped",
                    connected=False,
                    authorized=authorized,
                    task_manager=task_manager,
                ),
            )


__all__ = ["UserbotSessionError", "run_userbot_application"]
