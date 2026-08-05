"""Composition of long-running application workers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypedDict, cast

from aiogram import Bot

from bot.auction_notify import card_subscriptions_watch_loop, daily_loop
from bot.core.observability import MetricsRegistry
from bot.core.tasks import (
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
)
from bot.handlers.admin.helper.user_helpers import luxury_status_sync_loop
from bot.handlers.auction.publication import auction_publisher_loop
from bot.handlers.auction.winner import announce_winner
from bot.handlers.uid_verification import uid_verification_watch_loop
from bot.repositories.privacy_cleanup import (
    PrivacyCleanupConflict,
    PrivacyCleanupLockUnavailable,
)
from bot.services.auction_finalization import auction_finalization_loop
from bot.services.auction_notifications import auction_notifications_loop
from bot.services.privacy_cleanup import PrivacyCleanupService
from bot.telegram.outbox import telegram_outbox_loop

logger = logging.getLogger("auction_bot.privacy_cleanup")


type _LegacyBotLoop = Callable[[Bot], Awaitable[None]]


class _WorkerOptions(TypedDict):
    criticality: WorkerCriticality
    restart_policy: RestartPolicy
    initial_backoff: float
    max_backoff: float
    max_failures: int
    shutdown_timeout: float


async def privacy_cleanup_loop(
    service: PrivacyCleanupService,
    metrics: MetricsRegistry,
    *,
    interval_seconds: float = 86_400.0,
    batch_limit: int = 1_000,
) -> None:
    """Run approved temporary cleanup once per day under repository safeguards."""

    while True:
        try:
            result = await service.run_approved_cleanup(batch_limit=batch_limit)
        except (PrivacyCleanupConflict, PrivacyCleanupLockUnavailable) as error:
            metrics.increment(
                "privacy_cleanup_runs_total",
                result="skipped",
                reason=type(error).__name__,
            )
            logger.warning(
                "Approved privacy cleanup skipped",
                extra={
                    "event": "privacy.cleanup_skipped",
                    "reason": type(error).__name__,
                },
            )
        except Exception:
            metrics.increment("privacy_cleanup_runs_total", result="failed")
            logger.exception(
                "Approved privacy cleanup failed",
                extra={"event": "privacy.cleanup_failed"},
            )
            raise
        else:
            metrics.increment(
                "privacy_cleanup_runs_total",
                result=result.status,
            )
            metrics.increment(
                "privacy_cleanup_deleted_rows_total",
                value=result.deleted_rows,
                rule=result.plan.rule_id,
            )
            metrics.gauge(
                "privacy_cleanup_last_deleted_rows",
                float(result.deleted_rows),
                rule=result.plan.rule_id,
            )
            logger.info(
                "Approved privacy cleanup completed",
                extra={
                    "event": "privacy.cleanup_completed",
                    "status": result.status,
                    "rule_id": result.plan.rule_id,
                    "deleted_rows": result.deleted_rows,
                    "plan_sha_prefix": result.plan.plan_sha256[:12],
                },
            )
        await asyncio.sleep(interval_seconds)


def build_background_task_specs(
    bot: Bot,
    *,
    auction_channel_username: str,
    privacy_cleanup: PrivacyCleanupService | None = None,
    metrics: MetricsRegistry | None = None,
    auction_channel_id: int | str | None = None,
) -> list[BackgroundTaskSpec]:
    recoverable: _WorkerOptions = {
        "criticality": WorkerCriticality.RECOVERABLE,
        "restart_policy": RestartPolicy.ALWAYS,
        "initial_backoff": 1.0,
        "max_backoff": 60.0,
        "max_failures": 8,
        "shutdown_timeout": 15.0,
    }
    critical: _WorkerOptions = {
        "criticality": WorkerCriticality.CRITICAL,
        "restart_policy": RestartPolicy.ON_FAILURE,
        "initial_backoff": 1.0,
        "max_backoff": 30.0,
        "max_failures": 4,
        "shutdown_timeout": 20.0,
    }
    specs = [
        BackgroundTaskSpec(
            "auction-publisher",
            lambda _context: auction_publisher_loop(
                bot,
                channel_id=auction_channel_id,
                channel_username=auction_channel_username,
            ),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "auction-notifications",
            lambda _context: auction_notifications_loop(bot, auction_channel_username),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "telegram-outbox",
            lambda _context: telegram_outbox_loop(bot),
            **critical,
        ),
        BackgroundTaskSpec(
            "auction-finalization",
            lambda _context: auction_finalization_loop(bot, announce_winner),
            **critical,
        ),
        BackgroundTaskSpec(
            "card-subscriptions-watch",
            lambda _context: cast(_LegacyBotLoop, card_subscriptions_watch_loop)(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "daily-maintenance",
            lambda _context: cast(_LegacyBotLoop, daily_loop)(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "uid-verification-watch",
            lambda _context: uid_verification_watch_loop(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "luxury-status-sync",
            lambda _context: cast(_LegacyBotLoop, luxury_status_sync_loop)(bot),
            **recoverable,
        ),
    ]
    if (privacy_cleanup is None) != (metrics is None):
        raise ValueError("privacy_cleanup and metrics must be provided together")
    if privacy_cleanup is not None and metrics is not None:
        specs.append(
            BackgroundTaskSpec(
                "privacy-approved-temporary-cleanup",
                lambda _context: privacy_cleanup_loop(privacy_cleanup, metrics),
                **recoverable,
            )
        )
    return specs


__all__ = ["build_background_task_specs", "privacy_cleanup_loop"]
