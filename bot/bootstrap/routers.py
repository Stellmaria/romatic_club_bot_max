"""Aiogram router composition in deterministic dispatch order."""

from __future__ import annotations

from aiogram import Dispatcher

from bot.handlers.admin.admin_panel import router as admin_panel_router
from bot.handlers.admin.broadcast import register_broadcast_handlers
from bot.handlers.admin.cards_admin import register_cards_admin_handlers
from bot.handlers.admin.helper.new.card_economy import router as card_economy_router
from bot.handlers.admin.moderation import router as moderation_router
from bot.handlers.admin.outbox import router as outbox_admin_router
from bot.handlers.admin.services.market_add_flow import router as market_flow
from bot.handlers.admin.services.market_diamonds_flow import router as market_diamonds
from bot.handlers.admin.services.market_manage_flow import router as market_manage
from bot.handlers.admin.services.market_service import router as market_router
from bot.handlers.admin.services.schedule import router as luxury_schedule_router
from bot.handlers.admin.stats_posts import router as stats_posts_router
from bot.handlers.admin.uid_verification_admin import router as uid_verification_admin_router
from bot.handlers.auction.admin_lifecycle import router as auction_admin_lifecycle_router
from bot.handlers.auction.autobid import router as auction_autobid_router
from bot.handlers.auction.bidding import router as auction_bidding_router
from bot.handlers.auction.exchange import router as auction_exchange_router
from bot.handlers.auction.exchange_diagnostics import router as auction_exchange_diagnostics_router
from bot.handlers.auction.schedule import router as auction_schedule_router
from bot.handlers.auction.warnings import router as auction_warnings_router
from bot.handlers.auction.winner_exchange import router as auction_winner_exchange_router
from bot.handlers.auction.winner_manual import router as auction_winner_manual_router
from bot.handlers.auction.winner_print import router as auction_winner_print_router
from bot.handlers.auction.submission import addlot_start
from bot.handlers.auction_comments import router as comments_router
from bot.handlers.auctions import router as auctions_router
from bot.handlers.card_subscribe import register_card_subscribe_handlers, start_subscribe_card
from bot.handlers.emoji_setup import router as emoji_setup_router
from bot.handlers.helper.appeals import router as admin_appeals_router
from bot.handlers.uid_verification import router as uid_verification_router
from bot.handlers.users import router as users_router
from bot.middlewares.debug import DebugAllMessages
from bot.middlewares.expired_callback import ExpiredCallbackMiddleware
from bot.middlewares.user_sync import UserSyncMiddleware
from bot.telegram.user_entrypoints import configure_user_entrypoints


def register_all_routers(
    dispatcher: Dispatcher,
    *,
    debug_messages: bool = False,
) -> None:
    """Attach every middleware and router exactly once."""

    # Dynamic registrations append handlers to their owning routers. Configure
    # them before the routers are attached to a dispatcher.
    configure_user_entrypoints(
        add_lot=addlot_start,
        card_subscription=start_subscribe_card,
    )
    admin_panel_router.include_router(card_economy_router)
    register_card_subscribe_handlers(auctions_router)
    register_broadcast_handlers(auctions_router)
    register_cards_admin_handlers(admin_panel_router)

    if debug_messages:
        dispatcher.message.outer_middleware(DebugAllMessages())

    # Priority commands go before broad FSM handlers in the auction monolith.
    dispatcher.include_router(auction_schedule_router)
    dispatcher.include_router(users_router)
    dispatcher.include_router(auctions_router)
    # The package router owns submission, moderation and catalog. Diagnostics
    # remains a separate legacy router until its final package extraction.
    dispatcher.include_router(auction_exchange_router)
    dispatcher.include_router(auction_exchange_diagnostics_router)
    dispatcher.include_router(emoji_setup_router)

    dispatcher.update.outer_middleware(ExpiredCallbackMiddleware())
    dispatcher.update.outer_middleware(UserSyncMiddleware())

    dispatcher.include_router(auction_bidding_router)
    dispatcher.include_router(auction_autobid_router)
    dispatcher.include_router(auction_admin_lifecycle_router)
    dispatcher.include_router(auction_warnings_router)
    # Preserve the former winner.py handler order.  In particular the exact
    # /print_win_missed command must precede the broad /print_win prefix.
    dispatcher.include_router(auction_winner_manual_router)
    dispatcher.include_router(auction_winner_exchange_router)
    dispatcher.include_router(auction_winner_print_router)
    dispatcher.include_router(comments_router)
    dispatcher.include_router(outbox_admin_router)
    dispatcher.include_router(admin_panel_router)
    dispatcher.include_router(moderation_router)
    dispatcher.include_router(admin_appeals_router)
    dispatcher.include_router(luxury_schedule_router)
    dispatcher.include_router(market_router)
    dispatcher.include_router(market_flow)
    dispatcher.include_router(market_diamonds)
    dispatcher.include_router(market_manage)
    dispatcher.include_router(stats_posts_router)
    dispatcher.include_router(uid_verification_router)
    dispatcher.include_router(uid_verification_admin_router)
