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
    admin_user_lists,
)
from bot.handlers.admin.admin_panel_exchange import (
    cmd_card_video,
    cmd_fileid,
    cmd_print_ex,
    cb_print_ex,
    howmax_cmd,
    pex_set_winner,
    pex_set_price,
    pex_set_link,
    cmd_id,
    ex_back_to_moderation,
    ex1_approve,
    ex1_delete_ask,
    ex1_delete_no,
    ex1_delete_yes,
    ex1_reject_start,
    ex1_reject_reason,
)
from bot.handlers.admin.admin_panel_requests import (
    admreq_back,
    cmd_ex_owners,
    admreq_select,
    show_admin_menu,
    moderation_menu,
    start_give_trusted,
    give_trusted_user,
    start_remove_trusted,
    remove_trusted_user,
    start_add_admin,
    add_admin_user,
    start_remove_admin,
    remove_admin_user,
    pendinglots_cmd,
    show_delete_requests_cmd,
    schedule_button,
    exchange_menu_button,
    ex_appr_decks,
    ex_appr_whole,
    ex_appr_lotdeck_show,
    cb_exchange_approved_root,
)
from bot.handlers.admin.admin_panel_schedule import (
    edit_schedule_button,
    edit_lot_menu,
    exchange_pending_mode_pick,
    edit_field_handler,
    set_auction_kind_handler,
    set_craft_uid_handler,
    edit_schedule_value_photo,
    edit_schedule_value_text,
    edit_time_months,
    edit_schedule_router,
    edit_lot_back,
    edit_schedule_back_any,
    delete_lot_confirm,
    delete_lot_final,
    edit_time_slot_confirm,
    save_edited_time,
    set_currency_handler,
    set_currency_price_handler,
    edit_price_handler,
)
from bot.handlers.admin.admin_panel_sections import (
    show_decks_for_cards,
    show_cards_in_deck,
    users_menu,
    logs_menu,
    audit_logs_cmd,
    broadcast_menu,
    start_broadcast_from_menu,
    stats_menu,
    stats_full_schedule,
    stats_schedule_set_month,
    stats_schedule_today,
    stats_schedule_noop,
    cards_menu,
    add_deck_button,
    check_admin_password,
    deck_name_received,
    confirm_add_deck,
    cancel_add_deck,
    add_card_button,
    universal_cancel_callback,
    universal_cancel,
    universal_back_to_main,
    admin_inline_back,
    pending_menu_auctions,
    admin_help,
)
from bot.handlers.admin.admin_panel_system import (
    show_admin_menu_with_system,
    show_system_menu,
    show_restart_confirmation,
    show_system_callback,
    show_system_confirmation,
    run_system_operation,
    show_system_logs,
    close_system_callback,
)
from bot.handlers.admin.admin_user_lists import (
    show_admins_list,
    show_users_list,
    show_trusted_list,
    paginate_admin_user_list,
)
from bot.services.admin_auction_notifications import notify_owners_lot_changed

# Preserve the historical feature inventory and ordering for compatibility.
# The system router itself is attached directly by bot.bootstrap.routers before
# broad legacy/FSM routers, so it must not also be nested under this facade.
FEATURE_ROUTERS = (
    admin_panel_system.router,
    admin_panel_requests.router,
    admin_panel_schedule.router,
    admin_panel_sections.router,
    admin_user_lists.router,
    admin_panel_exchange.router,
)
router = Router(name=__name__)
router.include_routers(*FEATURE_ROUTERS[1:])

__all__ = [
    "router", "FEATURE_ROUTERS", "notify_owners_lot_changed",
    *admin_panel_system.__all__, *admin_panel_requests.__all__,
    *admin_panel_schedule.__all__, *admin_panel_sections.__all__,
    *admin_user_lists.__all__, *admin_panel_exchange.__all__,
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
