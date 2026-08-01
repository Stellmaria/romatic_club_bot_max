"""Compatibility facade that composes moderation feature routers."""

from aiogram import Router

from bot.handlers.admin import (
    moderation_clik,
    moderation_diagnostics,
    moderation_lots,
    moderation_pending,
    moderation_schedule,
)
from bot.handlers.admin.moderation_clik import (
    clik_cmd,
    clik_noop,
    clik_root,
    clik_price,
    clik_instruction,
    clik_ask,
    clik_got_question,
    clik_order,
    clik_pay,
    clik_story_page,
    clik_story_pick,
    clik_back_to_stories,
    clik_task_toggle,
    clik_ach_open,
    clik_ach_set,
    clik_ach_off,
    clik_ach_back,
    clik_love_open,
    clik_love_set_generic,
    clik_love_off_generic,
    clik_love_back_generic,
    clik_love_pvt_toggle,
    clik_love_pvt_done,
    clik_love_pvt_off,
    clik_love_pvt_back,
    clik_other_open,
    clik_other_text,
    clik_tasks_next,
    clik_cups_back,
    clik_cups_set,
    clik_final_back_cups,
    clik_final_back_tasks,
    clik_got_order,
)
from bot.handlers.admin.moderation_diagnostics import (
    cmd_lux_wait,
    cmd_lux_wait_dbg,
    cmd_multi_auctions,
    proof_cmd,
    pending_menu_router,
    ex_show_proof,
    ex_approve,
    ex_reject_start,
    cmd_user_dbg,
)
from bot.handlers.admin.moderation_lots import (
    fsm_back_handler,
    handle_reject_pending_reason,
    handle_reject_delete_reason,
    add_admin_cmd,
    remove_admin_cmd,
    choose_month,
    choose_day,
    choose_time,
    legacy_choose_time_back,
    handle_confirm_lot,
    start_reject_lot,
    show_proof_photo,
    back_to_lot,
    show_delete_requests_cmd_command,
    approve_delete_request,
    reject_delete_request,
    process_reject_reason,
    add_deck_command,
)
from bot.handlers.admin.moderation_pending import (
    edit_pending_lot_menu,
    edit_pending_kind,
    edit_pending_craft,
    pending_set_craft,
    edit_pending_comment,
    save_pending_comment,
    pending_set_kind,
    edit_pending_price,
    save_pending_price,
    set_lot_photo_from_lot,
    handle_uploaded_lot_photo,
    handle_uploaded_lot_not_photo,
    edit_pending_currency,
    save_pending_currency,
    universal_cancel_text,
    universal_trusted_cancel,
)
from bot.handlers.admin.moderation_schedule import (
    schedule_command,
    preview_schedule_month,
    preview_schedule_day,
    edit_schedule_command,
    force_publish_handler,
)
from bot.handlers.admin.moderation_schedule import split_message_by_blocks

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
# AuctionModerationService.create() ... f"⚙️ Тип: {kind_text}\n"
# Побеждает минимальная ставка
# schedule_slot_key(a['start_time']) == selected_grid_time
# start_msk = to_moscow_wall(lot['start_time'])
# async def preview_schedule_day
# await get_auctions_by_date_with_owners(selected_date)
# to_moscow_wall(utc_now()) ... Актуальное расписание ... Обновлено:
# to_moscow_wall(lot['start_time']) ... to_moscow_wall(lot['end_time'])
