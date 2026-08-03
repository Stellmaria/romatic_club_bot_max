"""Composition of long-running application workers."""

from __future__ import annotations

from aiogram import Bot

from bot.auction_notify import card_subscriptions_watch_loop, daily_loop
from bot.core.tasks import (
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
)
from bot.handlers.admin.helper.user_helpers import luxury_status_sync_loop
from bot.handlers.auction.publication import auction_publisher_loop
from bot.handlers.auction.winner import announce_winner
from bot.handlers.uid_verification import uid_verification_watch_loop
from bot.services.auction_finalization import auction_finalization_loop
from bot.services.auction_notifications import auction_notifications_loop
from bot.telegram.outbox import telegram_outbox_loop


def build_background_task_specs(
    bot: Bot,
    *,
    auction_channel_username: str,
    auction_channel_id: int | str | None = None,
) -> list[BackgroundTaskSpec]:
    recoverable = {
        "criticality": WorkerCriticality.RECOVERABLE,
        "restart_policy": RestartPolicy.ALWAYS,
        "initial_backoff": 1.0,
        "max_backoff": 60.0,
        "max_failures": 8,
        "shutdown_timeout": 15.0,
    }
    critical = {
        "criticality": WorkerCriticality.CRITICAL,
        "restart_policy": RestartPolicy.ON_FAILURE,
        "initial_backoff": 1.0,
        "max_backoff": 30.0,
        "max_failures": 4,
        "shutdown_timeout": 20.0,
    }
    return [
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
            lambda _context: card_subscriptions_watch_loop(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "daily-maintenance",
            lambda _context: daily_loop(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "uid-verification-watch",
            lambda _context: uid_verification_watch_loop(bot),
            **recoverable,
        ),
        BackgroundTaskSpec(
            "luxury-status-sync",
            lambda _context: luxury_status_sync_loop(bot),
            **recoverable,
        ),
    ]
