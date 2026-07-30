import asyncio
import logging
import os
import sys
from typing import List

from aiogram import BaseMiddleware
from aiogram import Bot
from aiogram import Dispatcher, types

from bot.core.logging import configure_logging
from bot.core.time import utc_now
from bot.domain.auctions.deadlines import winner_deadline_reached
from bot.auction_notify import auction_notifications_loop, card_subscriptions_watch_loop, \
    daily_loop
from bot.handlers.admin.admin_panel import router as admin_panel_router
from bot.handlers.admin.broadcast import register_broadcast_handlers
from bot.handlers.admin.cards_admin import register_cards_admin_handlers
from bot.handlers.admin.helper.new.card_economy import router as card_economy_router
from bot.handlers.admin.moderation import router as moderation_router
from bot.handlers.admin.media_assets import router as media_assets_router
from bot.handlers.admin.services.market_add_flow import router as market_flow
from bot.handlers.admin.services.market_diamonds_flow import router as market_diamonds
from bot.handlers.admin.services.market_manage_flow import router as market_manage
from bot.handlers.admin.services.market_service import router as market_router
from bot.handlers.admin.services.schedule import router as luxury_schedule_router
from bot.handlers.admin.stats_posts import router as stats_posts_router
from bot.handlers.admin.uid_verification_admin import router as uid_verification_admin_router
from bot.handlers.auction_comments import router as comments_router, announce_winner
from bot.handlers.auction.schedule import router as auction_schedule_router
from bot.handlers.auctions import router as auctions_router, auction_publisher_loop
from bot.handlers.card_subscribe import register_card_subscribe_handlers
from bot.handlers.emoji_setup import router as emoji_setup_router
from bot.handlers.helper.appeals import router as admin_appeals_router
from bot.handlers.uid_verification import router as uid_verification_router
from bot.handlers.users import router as users_router
from bot.middlewares.expired_callback import ExpiredCallbackMiddleware
from bot.middlewares.user_sync import UserSyncMiddleware
from bot.core.legacy_config import BOT_TOKEN, AUCTION_CHANNEL_USERNAME, DISCUSSION_CHAT_ID
from db.legacy import (
    init_db,
    close_db,
    list_auctions,
    get_bids_for_auction,
    update_auction_status,
    is_admin,
)

logger = logging.getLogger("auction_bot")


def patch_bot_protect_content(bot: Bot) -> None:
    """Use Telegram's open default; Luxury handlers opt in explicitly."""

    setattr(bot, "_protect_content_patched", True)

# -------------------- logging --------------------

def setup_logging() -> None:
    configure_logging()


# -------------------- middleware --------------------

class DebugAllMessages(BaseMiddleware):
    """Опциональный дебаг входящих сообщений. Включается флагом DEBUG_MW=1."""

    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            logging.getLogger("auction").debug(
                "MSG chat=%s type=%s user=%s text=%r",
                getattr(event.chat, "id", "?"),
                getattr(event.chat, "type", "?"),
                getattr(event.from_user, "id", "?"),
                getattr(event, "text", None),
            )
        return await handler(event, data)


# -------------------- lifecycle --------------------

async def on_startup() -> None:
    await init_db()
    logger.info("Startup complete.")


async def on_shutdown(bot: Bot | None = None, tasks: List[asyncio.Task] | None = None) -> None:
    if tasks:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if bot:
        await bot.session.close()

    await close_db()
    logger.info("Shutdown complete.")


# -------------------- routers --------------------

def register_all_routers(dp: Dispatcher) -> None:
    # Включаем middleware только по флагу
    if os.getenv("DEBUG_MW") == "1":
        dp.message.outer_middleware(DebugAllMessages())

    # The outermost guard must see errors raised by every router/handler.
    dp.update.outer_middleware(ExpiredCallbackMiddleware())
    dp.update.outer_middleware(UserSyncMiddleware())

    # Read-only slash commands must be resolved before broad FSM handlers.
    # Otherwise an unfinished add-lot/exchange flow can consume `/day завтра`
    # as ordinary form input and the command appears to do nothing.
    dp.include_router(auction_schedule_router)
    dp.include_router(users_router)
    dp.include_router(auctions_router)
    dp.include_router(emoji_setup_router)

    # Остальные
    dp.include_router(comments_router)
    dp.include_router(media_assets_router)
    dp.include_router(admin_panel_router)
    dp.include_router(moderation_router)
    dp.include_router(admin_appeals_router)
    dp.include_router(luxury_schedule_router)
    dp.include_router(market_router)
    dp.include_router(market_flow)
    dp.include_router(market_diamonds)
    dp.include_router(market_manage)
    dp.include_router(stats_posts_router)
    dp.include_router(uid_verification_router)
    dp.include_router(uid_verification_admin_router)

    # Регистраторы
    admin_panel_router.include_router(card_economy_router)
    register_card_subscribe_handlers(auctions_router)
    register_broadcast_handlers(auctions_router)
    register_cards_admin_handlers(admin_panel_router)


from bot.application import ApplicationConfigurationError, run_bot  # noqa: E402


def main() -> int:
    """Process entrypoint with deterministic configuration-error exit status."""

    try:
        asyncio.run(run_bot())
    except ApplicationConfigurationError as error:
        logger.error("Configuration error: %s", error)
        return 2
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
