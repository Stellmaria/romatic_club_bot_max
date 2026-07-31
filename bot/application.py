"""Bot application lifecycle and composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher

from bot.bootstrap import build_background_task_specs, register_all_routers
from bot.core.settings import Settings, settings as default_settings
from bot.core.tasks import BackgroundTaskManager
from bot.telegram.protection import patch_bot_protect_content
from db.core import (
    close_db,
    init_db,
)
from db.admin import is_admin

logger = logging.getLogger("auction_bot")


class ApplicationConfigurationError(RuntimeError):
    """Raised before external resources are opened for invalid settings."""


def setup_logging(settings: Settings) -> None:
    level = logging.getLevelNamesMapping().get(settings.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("aiogram").setLevel(
        logging.DEBUG if settings.aiogram_debug else logging.WARNING
    )


def validate_settings(settings: Settings) -> None:
    errors = settings.bot_configuration_errors()
    if errors:
        raise ApplicationConfigurationError("; ".join(errors))


async def _run_polling_with_worker_monitor(
    dispatcher: Dispatcher,
    bot: Bot,
    task_manager: BackgroundTaskManager,
) -> None:
    polling = asyncio.create_task(dispatcher.start_polling(bot), name="telegram-polling")
    worker_monitor = asyncio.create_task(
        task_manager.wait_for_failure(),
        name="background-worker-monitor",
    )
    try:
        done, _ = await asyncio.wait(
            {polling, worker_monitor},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if worker_monitor in done:
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
            await worker_monitor

        worker_monitor.cancel()
        await asyncio.gather(worker_monitor, return_exceptions=True)
        await polling
    finally:
        # External cancellation (service stop, SIGTERM, test timeout) may occur
        # while both child tasks are pending.  Never leave polling alive while
        # the surrounding lifecycle is already closing Bot/DB resources.
        for task in (polling, worker_monitor):
            if not task.done():
                task.cancel()
        await asyncio.gather(polling, worker_monitor, return_exceptions=True)


async def run_bot(settings: Settings | None = None) -> None:
    app_settings = settings or default_settings
    setup_logging(app_settings)
    validate_settings(app_settings)

    bot: Bot | None = None
    task_manager: BackgroundTaskManager | None = None
    primary_error: BaseException | None = None
    try:
        await init_db()
        logger.info("Database startup complete")

        bot = Bot(token=app_settings.bot_token)
        patch_bot_protect_content(bot, is_admin=is_admin)
        dispatcher = Dispatcher()
        register_all_routers(
            dispatcher,
            debug_messages=app_settings.debug_middleware,
        )

        await bot.delete_webhook(
            drop_pending_updates=app_settings.drop_pending_updates,
        )
        if app_settings.drop_pending_updates:
            logger.info("Pending Telegram updates dropped before polling")

        task_manager = BackgroundTaskManager()
        task_manager.start(
            build_background_task_specs(
                bot,
                auction_channel_id=app_settings.auction_channel_id,
                auction_channel_username=app_settings.auction_channel_username,
            )
        )

        logger.info("Starting bot polling")
        await _run_polling_with_worker_monitor(dispatcher, bot, task_manager)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        if task_manager is not None:
            cleanup_steps.append(("background tasks", task_manager.stop))
        if bot is not None:
            cleanup_steps.append(("Telegram bot session", bot.session.close))
        cleanup_steps.append(("database pool", close_db))

        cleanup_error: BaseException | None = None
        for resource_name, cleanup in cleanup_steps:
            try:
                await cleanup()
            except BaseException as error:
                logger.exception("Failed to close %s", resource_name)
                if cleanup_error is None:
                    cleanup_error = error

        logger.info("Application shutdown complete")
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error
