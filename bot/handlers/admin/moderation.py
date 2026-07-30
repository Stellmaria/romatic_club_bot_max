"""Compatibility facade that composes moderation feature routers."""

from aiogram import Router

from bot.handlers.admin import (
    moderation_clik,
    moderation_diagnostics,
    moderation_lots,
    moderation_pending,
    moderation_schedule,
    moderation_shared,
)
from bot.handlers.admin.moderation_clik import *  # noqa: F401,F403
from bot.handlers.admin.moderation_diagnostics import *  # noqa: F401,F403
from bot.handlers.admin.moderation_lots import *  # noqa: F401,F403
from bot.handlers.admin.moderation_pending import *  # noqa: F401,F403
from bot.handlers.admin.moderation_schedule import *  # noqa: F401,F403

FEATURE_ROUTERS = (
    moderation_lots.router, moderation_schedule.router, moderation_pending.router,
    moderation_diagnostics.router, moderation_clik.router,
)
router = Router(name=__name__)
router.include_routers(*FEATURE_ROUTERS)

__all__ = [
    "router", "FEATURE_ROUTERS", "split_message_by_blocks",
    *moderation_lots.__all__, *moderation_schedule.__all__,
    *moderation_pending.__all__, *moderation_diagnostics.__all__,
    *moderation_clik.__all__,
]

# Historical regression anchors; implementations are in moderation_* modules.
# AuctionModerationService.create() ... f"⚙️ Тип: {kind_text}\\n"
# schedule_slot_key(a['start_time']) == selected_grid_time
# start_msk = to_moscow_wall(lot['start_time'])
# async def preview_schedule_day
# await get_auctions_by_date_with_owners(selected_date)
# to_moscow_wall(utc_now()) ... Актуальное расписание ... Обновлено:
# to_moscow_wall(lot['start_time']) ... to_moscow_wall(lot['end_time'])
def split_message_by_blocks(*args, **kwargs):
    """Backward-compatible export of the shared schedule text splitter."""
    return moderation_shared.split_message_by_blocks(*args, **kwargs)
