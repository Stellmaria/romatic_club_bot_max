from datetime import date

from bot.handlers.admin.helper.admin_constants import ADMIN_ERRORS
from bot.handlers.admin.helper.new.admin_actions import send_admin_log, remove_admin_role
from bot.handlers.admin.helper.user_helpers import resolve_user_from_message, format_user_ref, \
    build_grouped_schedule_lines_with_prefixes, find_free_slots, filter_slots_by_user_type
from config import ADMINS_OWNERS
from db.db import get_lot_by_id, get_lot_owners, is_luxury_user, get_auctions_by_date_with_owners
from db.db import log_audit_action


async def process_remove_admin(message, user=None, state=None, bot=None, password_check=True):
    if user is None:
        user = await resolve_user_from_message(message)
    if not user:
        await message.answer(ADMIN_ERRORS["user_not_found"])
        return
    if user["user_id"] in ADMINS_OWNERS:
        await send_admin_log(
            bot or message.bot,
            f"🚫 Попытка удалить владельца! <a href='tg://user?id={message.from_user.id}'>id:{message.from_user.id}</a>"
        )
        await message.answer(ADMIN_ERRORS["cant_remove_owner"])
        return
    await remove_admin_role(
        user["user_id"], message.from_user.id, bot or message.bot, message.from_user.username
    )
    await message.answer(f"Админ удалён: {format_user_ref(user)}")
    await send_admin_log(bot or message.bot, f"remove_admin: {message.from_user.id} -> {format_user_ref(user)}")
    await log_audit_action(
        user_id=message.from_user.id,
        action_type="remove_admin",
        auction_id=None,
        details=f"remove_admin user_id={user['user_id']} (@{user.get('username')})"
    )
    if state:
        await state.clear()


async def parse_auction_and_date_from_callback(data: str, state) -> tuple | None:
    parts = data.split("|")
    if len(parts) == 3:
        _, auction_id, date_str = parts
        try:
            auction_id = int(auction_id)
        except ValueError:
            auction_id = None
    elif len(parts) == 2:
        _, date_str = parts
        auction_id = (await state.get_data()).get("auction_id")
        if auction_id is not None:
            try:
                auction_id = int(auction_id)
            except ValueError:
                auction_id = None
    else:
        return None, None
    return auction_id, date_str


async def get_free_slots_and_schedule_for_lot(auction_id: int, selected_date: date):
    lot = await get_lot_by_id(auction_id)
    auctions = await get_auctions_by_date_with_owners(selected_date)
    current_owners = await get_lot_owners(int(auction_id))
    current_owner_ids = [o['user_id'] for o in current_owners]
    schedule_lines = await build_grouped_schedule_lines_with_prefixes(auctions, lot, current_owner_ids)
    schedule_str = "\n".join(schedule_lines) if schedule_lines else "Нет запланированных лотов на этот день."
    free_slots = await find_free_slots(auctions, lot, auction_id, selected_date)
    owner_id = current_owner_ids[0] if current_owner_ids else None
    is_luxury = await is_luxury_user(owner_id) if owner_id else False
    free_slots = filter_slots_by_user_type(free_slots, is_luxury)
    return free_slots, is_luxury, schedule_str, lot, auctions
