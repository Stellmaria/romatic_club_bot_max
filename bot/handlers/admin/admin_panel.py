"""Compatibility facade that composes the admin-panel feature routers.

Feature implementations live in focused modules; this module keeps the historic
``admin_panel`` import path and the router registration contract stable.
"""

from aiogram import Router
from bot.domain.auctions import AuctionSlotConflict, InvalidAuctionTransition
from bot.services.auction_workflows import AuctionModerationService

from bot.handlers.admin import (
    admin_panel_exchange,
    admin_panel_requests,
    admin_panel_schedule,
    admin_panel_sections,
    admin_panel_system,
)
from bot.handlers.admin.admin_panel_exchange import *  # noqa: F401,F403
from bot.handlers.admin.admin_panel_requests import *  # noqa: F401,F403
from bot.handlers.admin.admin_panel_schedule import *  # noqa: F401,F403
from bot.handlers.admin.admin_panel_sections import *  # noqa: F401,F403
from bot.handlers.admin.admin_panel_system import *  # noqa: F401,F403
from bot.handlers.admin.admin_panel_shared import notify_owners_lot_changed

FEATURE_ROUTERS = (
    admin_panel_requests.router,
    admin_panel_schedule.router,
    admin_panel_system.router,
    admin_panel_sections.router,
    admin_panel_exchange.router,
)
router = Router(name=__name__)
router.include_routers(*FEATURE_ROUTERS)

__all__ = [
    "router", "FEATURE_ROUTERS", "notify_owners_lot_changed",
    *admin_panel_requests.__all__, *admin_panel_schedule.__all__,
    *admin_panel_system.__all__, *admin_panel_sections.__all__,
    *admin_panel_exchange.__all__,
]

# Historical regression anchors; implementations are in admin_panel_schedule.
# async def edit_lot_menu ... await safe_callback_answer(call)
# @router.callback_query
# async def save_edited_time
# await safe_callback_answer(call, "⏳ Переношу лот…")
# moderation_service.reschedule( ... "✅ <b>Лот перенесён</b>\n"
# refresh_schedule_card_origin( ... send_admin_log(call.bot, log_text)
# timeout=12 ... except asyncio.TimeoutError
# await remember_schedule_card_origin( ... start_msk = to_moscow_wall(lot["start_time"])
# f"⚙️ <b>Тип аука:</b> {kind_label}\n" ... Победитель:</b> минимальная ставка
# AuctionModerationService.create()
