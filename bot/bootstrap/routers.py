"""Declarative aiogram router composition and route inventory."""

from __future__ import annotations

from functools import lru_cache

from aiogram import Dispatcher, Router

from bot.bootstrap.router_registry import (
    FeatureRegistration,
    MiddlewareRegistration,
    MiddlewareScope,
    PrepareHook,
    RoutePriority,
    RouterRegistry,
)
from bot.handlers.admin.admin_navigation import router as admin_navigation_router
from bot.handlers.admin.admin_panel import router as admin_panel_router
from bot.handlers.admin.admin_panel_system import router as admin_panel_system_router
from bot.handlers.admin.broadcast import register_broadcast_handlers
from bot.handlers.admin.cards_admin import register_cards_admin_handlers
from bot.handlers.admin.helper.new.card_economy import router as card_economy_router
from bot.handlers.admin.media_assets import router as media_assets_router
from bot.handlers.admin.moderation import router as moderation_router
from bot.handlers.admin.outbox import router as outbox_admin_router
from bot.handlers.admin.publication_diagnostics import (
    router as publication_diagnostics_router,
)
from bot.handlers.admin.schedule_setup import router as schedule_setup_router
from bot.handlers.admin.schedule_setup_fields import router as schedule_setup_fields_router
from bot.handlers.admin.schedule_setup_restart import router as schedule_setup_restart_router
from bot.handlers.admin.schedule_setup_temp import router as schedule_setup_temp_router
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
from bot.handlers.auction.schedule import router as auction_schedule_router
from bot.handlers.auction.submission import addlot_start
from bot.handlers.auction.submission_recovery import router as submission_recovery_router
from bot.handlers.auction.warnings import router as auction_warnings_router
from bot.handlers.auction.winner_exchange import router as auction_winner_exchange_router
from bot.handlers.auction.winner_manual import router as auction_winner_manual_router
from bot.handlers.auction.winner_print import router as auction_winner_print_router
from bot.handlers.auction_comments import router as comments_router
from bot.handlers.auctions import router as auctions_router
from bot.handlers.card_subscribe import register_card_subscribe_handlers, start_subscribe_card
from bot.handlers.emoji_setup import router as emoji_setup_router
from bot.handlers.helper.appeals import router as admin_appeals_router
from bot.handlers.profile import router as profile_router
from bot.handlers.uid_verification import router as uid_verification_router
from bot.handlers.user_access_control import router as user_access_control_router
from bot.handlers.user_menu import router as user_menu_router
from bot.handlers.users import router as users_router
from bot.middlewares.debug import DebugAllMessages
from bot.middlewares.expired_callback import ExpiredCallbackMiddleware
from bot.middlewares.telegram_boundary import TelegramBoundaryMiddleware
from bot.middlewares.user_sync import UserSyncMiddleware
from bot.telegram.user_entrypoints import configure_user_entrypoints


def _configure_user_entrypoints() -> None:
    configure_user_entrypoints(add_lot=addlot_start, card_subscription=start_subscribe_card)


def _register_auction_extension_handlers() -> None:
    register_card_subscribe_handlers(auctions_router)
    register_broadcast_handlers(auctions_router)


def _register_admin_extension_handlers() -> None:
    register_cards_admin_handlers(admin_panel_router)


def _feature(
    name: str,
    priority: RoutePriority,
    router: Router,
    *,
    parent: str | None = None,
    dependencies: tuple[str, ...] = (),
    commands: tuple[str, ...] = (),
    callbacks: tuple[str, ...] = (),
    hooks: tuple[PrepareHook, ...] = (),
    description: str,
) -> FeatureRegistration:
    return FeatureRegistration(
        name=name,
        priority=priority,
        router=router,
        parent=parent,
        dependencies=dependencies,
        commands=commands,
        callback_namespaces=callbacks,
        prepare_hooks=hooks,
        description=description,
    )


@lru_cache(maxsize=1)
def get_router_registry() -> RouterRegistry:
    """Return the immutable process-wide feature registry."""

    features = (
        FeatureRegistration(
            name="system.middleware",
            priority=RoutePriority.SYSTEM_OWNER,
            middlewares=(
                MiddlewareRegistration(
                    name="debug-all-messages",
                    scope=MiddlewareScope.MESSAGE,
                    factory=DebugAllMessages,
                    debug_only=True,
                ),
                MiddlewareRegistration(
                    name="telegram-boundary",
                    scope=MiddlewareScope.UPDATE,
                    factory=TelegramBoundaryMiddleware,
                ),
                MiddlewareRegistration(
                    name="expired-callback",
                    scope=MiddlewareScope.UPDATE,
                    factory=ExpiredCallbackMiddleware,
                ),
                MiddlewareRegistration(
                    name="user-sync",
                    scope=MiddlewareScope.UPDATE,
                    factory=UserSyncMiddleware,
                ),
            ),
            prepare_hooks=(PrepareHook("user-entrypoints", _configure_user_entrypoints),),
            description="Global Telegram boundary and user lifecycle middleware.",
        ),
        _feature(
            "admin.navigation",
            RoutePriority.SYSTEM_OWNER,
            admin_navigation_router,
            callbacks=("admin_nav",),
            description="Owner/admin navigation entrypoints.",
        ),
        _feature(
            "admin.system",
            RoutePriority.SYSTEM_OWNER,
            admin_panel_system_router,
            commands=("admin", "admin_panel", "system", "supervisor", "restart", "restart_userbot"),
            callbacks=("system",),
            description="Owner-only process and Supervisor controls.",
        ),
        _feature(
            "schedule.setup.fields",
            RoutePriority.SETUP_FSM,
            schedule_setup_fields_router,
            callbacks=("schedule_setup_fields",),
            description="Narrow field-editing stages for schedule setup.",
        ),
        _feature(
            "schedule.setup.restart",
            RoutePriority.SETUP_FSM,
            schedule_setup_restart_router,
            callbacks=("schedule_setup_restart",),
            description="Restart/recovery transitions for schedule setup.",
        ),
        _feature(
            "schedule.setup.temporary",
            RoutePriority.SETUP_FSM,
            schedule_setup_temp_router,
            callbacks=("schedule_setup_temp",),
            description="Temporary schedule setup stages.",
        ),
        _feature(
            "schedule.setup.base",
            RoutePriority.SETUP_FSM,
            schedule_setup_router,
            dependencies=(
                "schedule.setup.fields",
                "schedule.setup.restart",
                "schedule.setup.temporary",
            ),
            callbacks=("schedule_setup",),
            description="Broad base setup FSM, deliberately after its narrow stages.",
        ),
        _feature(
            "users.access-control",
            RoutePriority.EXACT_COMMANDS,
            user_access_control_router,
            callbacks=("user_access",),
            description="Administrative user access controls.",
        ),
        _feature(
            "admin.publication-diagnostics",
            RoutePriority.EXACT_COMMANDS,
            publication_diagnostics_router,
            commands=("publication_diag",),
            description="Auction publication integrity diagnostics.",
        ),
        _feature(
            "auctions.schedule",
            RoutePriority.EXACT_COMMANDS,
            auction_schedule_router,
            callbacks=("auction_schedule",),
            description="Auction schedule selection and publication entrypoints.",
        ),
        _feature(
            "users.menu",
            RoutePriority.EXACT_COMMANDS,
            user_menu_router,
            commands=("start",),
            callbacks=(
                "user_menu",
                "user_day",
                "user_schedule",
                "user_exchange",
                "user_notify",
                "notify_toggle",
            ),
            description="Canonical private-user menu and exact /start entrypoint.",
        ),
        _feature(
            "users.profile",
            RoutePriority.EXACT_COMMANDS,
            profile_router,
            callbacks=("profile",),
            description="User profile actions.",
        ),
        _feature(
            "users.core",
            RoutePriority.EXACT_COMMANDS,
            users_router,
            callbacks=("users",),
            description="User lots and account commands.",
        ),
        _feature(
            "auctions.submission-recovery",
            RoutePriority.EXACT_COMMANDS,
            submission_recovery_router,
            callbacks=("submission_recovery",),
            description="Recovery before broad auction submission handlers.",
        ),
        _feature(
            "auctions.core",
            RoutePriority.EXACT_COMMANDS,
            auctions_router,
            callbacks=("auction",),
            hooks=(
                PrepareHook(
                    "auctions-extension-handlers",
                    _register_auction_extension_handlers,
                ),
            ),
            description="Core auction commands plus registry-managed legacy extensions.",
        ),
        _feature(
            "exchange.catalog",
            RoutePriority.EXACT_COMMANDS,
            auction_exchange_router,
            callbacks=("exchange", "ex_view"),
            description="Exchange submission and approved-offer browsing.",
        ),
        _feature(
            "emoji.setup",
            RoutePriority.EXACT_COMMANDS,
            emoji_setup_router,
            callbacks=("emoji_setup",),
            description="Exact emoji setup entrypoints.",
        ),
        _feature(
            "auctions.bidding",
            RoutePriority.CALLBACKS,
            auction_bidding_router,
            callbacks=("bid",),
            description="Bid placement callbacks.",
        ),
        _feature(
            "auctions.autobid",
            RoutePriority.CALLBACKS,
            auction_autobid_router,
            callbacks=("autobid",),
            description="Automatic bidding callbacks.",
        ),
        _feature(
            "auctions.admin-lifecycle",
            RoutePriority.CALLBACKS,
            auction_admin_lifecycle_router,
            callbacks=("auction_admin",),
            description="Moderation and lifecycle callbacks for auctions.",
        ),
        _feature(
            "auctions.warnings",
            RoutePriority.CALLBACKS,
            auction_warnings_router,
            callbacks=("auction_warning",),
            description="Auction warning and confirmation callbacks.",
        ),
        _feature(
            "auctions.winner-manual",
            RoutePriority.CALLBACKS,
            auction_winner_manual_router,
            callbacks=("winner_manual",),
            description="Manual winner processing.",
        ),
        _feature(
            "auctions.winner-exchange",
            RoutePriority.CALLBACKS,
            auction_winner_exchange_router,
            callbacks=("winner_exchange",),
            description="Exchange winner processing.",
        ),
        _feature(
            "auctions.winner-print",
            RoutePriority.CALLBACKS,
            auction_winner_print_router,
            callbacks=("winner_print",),
            description="Winner printable output.",
        ),
        _feature(
            "auctions.comments",
            RoutePriority.CALLBACKS,
            comments_router,
            callbacks=("auction_comments",),
            description="Auction comment callbacks.",
        ),
        _feature(
            "admin.outbox",
            RoutePriority.CALLBACKS,
            outbox_admin_router,
            callbacks=("outbox",),
            description="Transactional outbox diagnostics.",
        ),
        _feature(
            "admin.media-assets",
            RoutePriority.CALLBACKS,
            media_assets_router,
            callbacks=("media_assets",),
            description="Media asset administration.",
        ),
        _feature(
            "admin.panel",
            RoutePriority.CALLBACKS,
            admin_panel_router,
            callbacks=("admin_panel",),
            hooks=(
                PrepareHook(
                    "admin-extension-handlers",
                    _register_admin_extension_handlers,
                ),
            ),
            description="Main admin panel and registry-managed extension handlers.",
        ),
        _feature(
            "admin.card-economy",
            RoutePriority.CALLBACKS,
            card_economy_router,
            parent="admin.panel",
            callbacks=("card_economy",),
            description="Nested card economy routes owned by the admin panel.",
        ),
        _feature(
            "admin.moderation",
            RoutePriority.CALLBACKS,
            moderation_router,
            callbacks=("moderation",),
            description="General moderation callbacks.",
        ),
        _feature(
            "admin.appeals",
            RoutePriority.CALLBACKS,
            admin_appeals_router,
            callbacks=("appeals",),
            description="Appeal review callbacks.",
        ),
        _feature(
            "schedule.luxury",
            RoutePriority.CALLBACKS,
            luxury_schedule_router,
            callbacks=("luxsched",),
            description="Luxury schedule callbacks.",
        ),
        _feature(
            "market.core",
            RoutePriority.CALLBACKS,
            market_router,
            callbacks=("market",),
            description="Market administration root.",
        ),
        _feature(
            "market.add-flow",
            RoutePriority.CALLBACKS,
            market_flow,
            dependencies=("market.core",),
            callbacks=("market_add",),
            description="Market item creation flow.",
        ),
        _feature(
            "market.diamonds",
            RoutePriority.CALLBACKS,
            market_diamonds,
            dependencies=("market.core",),
            callbacks=("market_diamonds",),
            description="Diamond market flow.",
        ),
        _feature(
            "market.manage",
            RoutePriority.CALLBACKS,
            market_manage,
            dependencies=("market.core",),
            callbacks=("market_manage",),
            description="Market management flow.",
        ),
        _feature(
            "admin.stats-posts",
            RoutePriority.CALLBACKS,
            stats_posts_router,
            callbacks=("stats_posts",),
            description="Statistics publication callbacks.",
        ),
        _feature(
            "uid.user-verification",
            RoutePriority.CALLBACKS,
            uid_verification_router,
            callbacks=("uid_verify",),
            description="User UID verification flow.",
        ),
        _feature(
            "uid.admin-verification",
            RoutePriority.CALLBACKS,
            uid_verification_admin_router,
            callbacks=("uid_admin",),
            description="Administrative UID verification flow.",
        ),
    )
    return RouterRegistry(features)


def register_all_routers(dispatcher: Dispatcher, *, debug_messages: bool = False) -> None:
    """Validate and install every feature exactly once."""

    get_router_registry().install(dispatcher, debug_messages=debug_messages)


def route_inventory_json(*, indent: int | None = 2) -> str:
    """Return the operator/CI route inventory without mutating a dispatcher."""

    return get_router_registry().inventory_json(indent=indent)


__all__ = ["get_router_registry", "register_all_routers", "route_inventory_json"]
