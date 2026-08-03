"""Bot process lifecycle and concrete composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher

from bot.core.logging import configure_logging
from bot.core.settings import BotProcessSettings
from bot.core.supervisor_client import SupervisorClient
from bot.core.tasks import BackgroundTaskManager

logger = logging.getLogger("auction_bot")


class ApplicationConfigurationError(RuntimeError):
    """Deprecated compatibility name for bot startup failures."""


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
        for task in (polling, worker_monitor):
            if not task.done():
                task.cancel()
        await asyncio.gather(polling, worker_monitor, return_exceptions=True)


async def run_bot(config: BotProcessSettings) -> None:
    """Run the bot from explicitly constructed configuration and adapters."""

    bot_settings = config.bot
    configure_logging(
        bot_settings.log_level,
        aiogram_debug=bot_settings.aiogram_debug,
    )

    from bot.core.legacy_config import configure_legacy_config
    from bot.uid_crypto import configure_uid_crypto

    configure_legacy_config(config)
    configure_uid_crypto(
        bot_settings.uid_hash_key,
        bot_settings.uid_enc_key,
        (bot_settings.uid_enc_key_previous,),
    )

    from bot.bootstrap import build_background_task_specs, register_all_routers
    from bot.bootstrap.container import ApplicationContainer
    from bot.telegram.protection import patch_bot_protect_content
    from db.admin import is_admin
    from db.lifecycle import close_db, init_db
    from db.pool import DatabaseRuntime

    supervisor_client = SupervisorClient.from_settings(config.supervisor)
    database_runtime = DatabaseRuntime(config.database)
    telegram_bot: Bot | None = None
    task_manager: BackgroundTaskManager | None = None
    primary_error: BaseException | None = None
    try:
        await init_db(database_runtime)
        logger.info("Database startup complete")

        container = ApplicationContainer.build(
            pool=database_runtime.require_pool(),
            storage_root=config.runtime_dir / "files",
        )

        if supervisor_client is not None:
            await supervisor_client.start()
            logger.info("Supervisor client session initialized")

        telegram_bot = Bot(token=bot_settings.bot_token)
        patch_bot_protect_content(telegram_bot, is_admin=is_admin)
        dispatcher = Dispatcher(
            supervisor_client=supervisor_client,
            application_container=container,
        )
        register_all_routers(
            dispatcher,
            debug_messages=bot_settings.debug_middleware,
        )

        await telegram_bot.delete_webhook(
            drop_pending_updates=bot_settings.drop_pending_updates,
        )
        if bot_settings.drop_pending_updates:
            logger.info("Pending Telegram updates dropped before polling")

        task_manager = BackgroundTaskManager()
        task_manager.start(
            build_background_task_specs(
                telegram_bot,
                auction_channel_id=bot_settings.auction_channel_id,
                auction_channel_username=bot_settings.auction_channel_username,
            )
        )

        logger.info("Starting bot polling")
        await _run_polling_with_worker_monitor(
            dispatcher,
            telegram_bot,
            task_manager,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        if task_manager is not None:
            cleanup_steps.append(("background tasks", task_manager.stop))
        if telegram_bot is not None:
            cleanup_steps.append(("Telegram bot session", telegram_bot.session.close))
        if supervisor_client is not None:
            cleanup_steps.append(("Supervisor client session", supervisor_client.close))
        cleanup_steps.append(("database runtime", lambda: close_db(database_runtime)))

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


__all__ = ["ApplicationConfigurationError", "run_bot"]
