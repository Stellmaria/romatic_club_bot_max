"""Composition of long-running application workers."""

from __future__ import annotations

from aiogram import Bot

from bot.auction_notify import (
    card_subscriptions_watch_loop,
    daily_loop,
)
from bot.core.tasks import BackgroundTaskSpec
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
    return [
        BackgroundTaskSpec(
            "auction-publisher",
            lambda: auction_publisher_loop(
                bot,
                channel_id=auction_channel_id,
                channel_username=auction_channel_username,
            ),
        ),
        BackgroundTaskSpec(
            "auction-notifications",
            lambda: auction_notifications_loop(bot, auction_channel_username),
        ),
        BackgroundTaskSpec("telegram-outbox", lambda: telegram_outbox_loop(bot)),
        BackgroundTaskSpec(
            "auction-finalization",
            lambda: auction_finalization_loop(bot, announce_winner),
        ),
        BackgroundTaskSpec(
            "card-subscriptions-watch",
            lambda: card_subscriptions_watch_loop(bot),
        ),
        BackgroundTaskSpec("daily-maintenance", lambda: daily_loop(bot)),
        BackgroundTaskSpec(
            "uid-verification-watch",
            lambda: uid_verification_watch_loop(bot),
        ),
        BackgroundTaskSpec(
            "luxury-status-sync",
            lambda: luxury_status_sync_loop(bot),
        ),
    ]
