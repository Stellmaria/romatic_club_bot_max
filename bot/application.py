"""Bot process lifecycle and concrete composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher

from bot.core.logging import configure_logging
from bot.core.settings import BotProcessSettings
from bot.core.supervisor_client import SupervisorClient
from bot.core.tasks import BackgroundTaskManager
from bot.middlewares.observability import ObservabilityMiddleware

if TYPE_CHECKING:
    from bot.core.observability import HealthProbeServer

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


async def run_bot(config: BotProcessSettings) -> None:  # noqa: C901
    """Run the bot from explicitly constructed configuration and adapters."""

    bot_settings = config.bot
    configure_logging(
        bot_settings.log_level,
        aiogram_debug=bot_settings.aiogram_debug,
        structured=True,
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
    from bot.core.observability import HealthProbeServer, MetricsRegistry
    from bot.telegram.protection import patch_bot_protect_content
    from db.admin import is_admin
    from db.lifecycle import close_db, init_db
    from db.pool import DatabaseRuntime

    supervisor_client = SupervisorClient.from_settings(config.supervisor)
    database_runtime = DatabaseRuntime(config.database)
    telegram_bot: Bot | None = None
    task_manager: BackgroundTaskManager | None = None
    health_server: HealthProbeServer | None = None
    metrics = MetricsRegistry()
    primary_error: BaseException | None = None

    def database_metrics() -> dict[str, float]:
        pool = database_runtime.pool
        if pool is None:
            return {}
        values: dict[str, float] = {}
        methods = {
            "database_pool_size": "get_size",
            "database_pool_idle": "get_idle_size",
            "database_pool_min_size": "get_min_size",
            "database_pool_max_size": "get_max_size",
        }
        for metric_name, method_name in methods.items():
            getter = getattr(pool, method_name, None)
            if callable(getter):
                values[metric_name] = float(getter())
        return values

    try:
        await init_db(database_runtime)
        logger.info("Database startup complete", extra={"event": "database.ready"})

        container = ApplicationContainer.build(
            pool=database_runtime.require_pool(),
            storage_root=config.runtime_dir / "files",
        )

        if supervisor_client is not None:
            await supervisor_client.start()
            logger.info(
                "Supervisor client session initialized",
                extra={"event": "supervisor.ready"},
            )

        telegram_bot = Bot(token=bot_settings.bot_token)
        patch_bot_protect_content(telegram_bot, is_admin=is_admin)
        dispatcher = Dispatcher(
            supervisor_client=supervisor_client,
            application_container=container,
            metrics_registry=metrics,
        )
        dispatcher.update.outer_middleware(ObservabilityMiddleware())
        register_all_routers(
            dispatcher,
            debug_messages=bot_settings.debug_middleware,
        )

        await telegram_bot.delete_webhook(
            drop_pending_updates=bot_settings.drop_pending_updates,
        )
        if bot_settings.drop_pending_updates:
            logger.info(
                "Pending Telegram updates dropped before polling",
                extra={"event": "telegram.pending_updates_dropped"},
            )

        task_manager = BackgroundTaskManager()
        task_manager.start(
            build_background_task_specs(
                telegram_bot,
                auction_channel_id=bot_settings.auction_channel_id,
                auction_channel_username=bot_settings.auction_channel_username,
            )
        )

        health_server = HealthProbeServer(
            database_ready=lambda: database_runtime.started,
            task_manager=lambda: task_manager,
            metrics=metrics,
            database_metrics=database_metrics,
            port=8081,
        )
        await health_server.start()
        logger.info(
            "Health and metrics probes started",
            extra={"event": "observability.ready", "probe_port": 8081},
        )

        metrics.increment("application_starts_total", process="bot")
        logger.info(
            "Starting bot polling",
            extra={"event": "telegram.polling_started"},
        )
        await _run_polling_with_worker_monitor(
            dispatcher,
            telegram_bot,
            task_manager,
        )
    except asyncio.CancelledError as error:
        primary_error = error
        logger.info(
            "Application cancellation requested",
            extra={"event": "application.cancelled"},
        )
        raise
    # This is the process lifecycle boundary: record the failure, then re-raise it.
    except Exception as error:
        primary_error = error
        metrics.increment("application_failures_total", process="bot")
        logger.exception(
            "Application failed",
            extra={"event": "application.failed", "error_type": type(error).__name__},
        )
        raise
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        if health_server is not None:
            cleanup_steps.append(("health probe server", health_server.close))
        if task_manager is not None:
            cleanup_steps.append(("background tasks", task_manager.stop))
        if telegram_bot is not None:
            cleanup_steps.append(("Telegram bot session", telegram_bot.session.close))
        if supervisor_client is not None:
            cleanup_steps.append(("Supervisor client session", supervisor_client.close))
        cleanup_steps.append(("database runtime", lambda: close_db(database_runtime)))

        cleanup_error: Exception | None = None
        for resource_name, cleanup in cleanup_steps:
            try:
                await cleanup()
            # Cleanup must continue so every remaining resource gets a close attempt.
            except Exception as error:
                logger.exception(
                    "Failed to close resource",
                    extra={
                        "event": "application.cleanup_failed",
                        "resource": resource_name,
                        "error_type": type(error).__name__,
                    },
                )
                if cleanup_error is None:
                    cleanup_error = error

        logger.info(
            "Application shutdown complete",
            extra={"event": "application.stopped"},
        )
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


__all__ = ["ApplicationConfigurationError", "run_bot"]
