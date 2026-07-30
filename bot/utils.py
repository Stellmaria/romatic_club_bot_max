import html
import time
from datetime import datetime, timezone, timedelta, time, date
from functools import lru_cache, wraps
from typing import List

from aiogram import Bot, types

from bot.core.time import to_moscow_wall
from bot.handlers.admin.helper.admin_constants import BUTTONS
from bot.handlers.constants import USER_MESSAGES
from bot.keyboards.keyboards import back_to_menu_keyboard
from config import ADMIN_LOG_CHATS
from db.db import get_user_by_username, get_user, logger


def currency_emoji(currency: str) -> str:
    return "💎" if currency == "алмазы" else "🍵"


def format_time_msg(start_time, end_time) -> str:
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    if isinstance(end_time, str):
        end_time = datetime.fromisoformat(end_time)
    start_time = to_moscow_wall(start_time)
    end_time = to_moscow_wall(end_time)
    return f"{start_time.strftime('%d.%m %H:%M')} - {end_time.strftime('%H:%M')} (МСК)"


def format_owner(user_id, username, full_name) -> str:
    if username:
        return f'<a href="https://t.me/{username}">@{username}</a> ({full_name})'
    return f"{full_name}\nID: {user_id}"


def all_30min_slots_for_date(selected_date: date) -> List[datetime]:
    slots = []
    slot_start = datetime.combine(selected_date, time(hour=11, minute=0))
    slot_end = datetime.combine(selected_date, time(hour=23, minute=0))
    while slot_start < slot_end:
        slots.append(slot_start)
        slot_start += timedelta(minutes=30)
    return slots


def generate_free_slots_for_date(selected_date: date, occupied: list) -> List[datetime]:
    """Свободные позиции на получасовой сетке по времени старта.

    Конец аукциона хранится с дополнительными 59 секундами для приёма
    ставок. Эти секунды не должны блокировать следующий слот.
    """
    def _as_grid_time(value):
        if isinstance(value, datetime):
            value = value.time()
        return value.replace(second=0, microsecond=0)

    busy_starts = {_as_grid_time(start) for start, _end in occupied}
    return [
        slot
        for slot in all_30min_slots_for_date(selected_date)
        if _as_grid_time(slot) not in busy_starts
    ]


async def resolve_user_id(identifier: str):
    if identifier.isdigit():
        user = await get_user(int(identifier))
    elif identifier.startswith("@"):
        user = await get_user_by_username(identifier[1:])
    else:
        return None
    return user['user_id'] if user else None


async def log_admin_lot_action(
        bot: Bot,
        action: str,
        call_or_msg: types.Message | types.CallbackQuery,
        auction_id: int,
        lot: dict = None,
        owner: dict = None,
        extra: str = "",
):
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    admin_link = (
        f'<a href="tg://user?id={call_or_msg.from_user.id}">'
        f'{call_or_msg.from_user.username or call_or_msg.from_user.full_name or call_or_msg.from_user.id}</a>'
    )
    owner_link = "-"
    if owner:
        owner_link = (
            f'<a href="tg://user?id={owner.get("user_id", "-")}">'
            f'{owner.get("username", "") or owner.get("user_id", "-")}</a>'
        )
    elif lot and lot.get("owner_id"):
        owner_link = f'{lot["owner_id"]}'

    date_str = time_str = "-"
    if lot:
        start_time = lot.get('start_time')
        end_time = lot.get('end_time')
        if start_time and end_time:
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(end_time)
            date_str = start_time.strftime('%d.%m.%Y')
            time_str = f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')} (МСК)"

    action_map = {
        "approve_lot": "✅ <b>Лот подтверждён</b>",
        "reject_lot": "❌ <b>Лот отклонён</b>",
        "edit_lot": "✏️ <b>Лот отредактирован</b>",
        "delete_lot": "🗑️ <b>Лот удалён</b>",
        "new_lot": "🆕 <b>Новая заявка</b>"
    }
    action_line = action_map.get(action, f"🔧 <b>Действие: {action}</b>")

    msg_parts = [
        action_line,
        f"🕒 <b>{now_str} (МСК)</b>",
        f"👤 <b>Админ:</b> {admin_link}",
        f"🎴 <b>Лот №{auction_id}</b>: {html.escape(lot.get('card_name', '-')) if lot else '-'}",
        f"🙍‍♂️ <b>Владелец:</b> {owner_link}",
        f"💰 <b>Старт:</b> {lot.get('start_price', '-') if lot else '-'} "
        f"{currency_emoji(lot.get('currency', '')) if lot else ''}",
        f"💬 <b>Комментарий:</b> {html.escape(lot.get('comment') or '-') if lot else '-'}"
    ]
    if lot and lot.get("status") in ("scheduled", "active"):
        if date_str != "-":
            msg_parts.append(f"📅 <b>Дата заявки:</b> {date_str}")
        if time_str != "-":
            msg_parts.append(f"⏰ <b>Время:</b> {time_str}")
    if extra:
        msg_parts.append(extra)

    msg_parts.append(f"<i>Действие: <code>{action}</code> через бота.</i>")

    log_text = "\n".join(msg_parts)

    for chat_id in ADMIN_LOG_CHATS:
        if chat_id:
            try:
                await bot.send_message(chat_id=chat_id, text=log_text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                print(f"[LOG ERROR] {e}")


async def send_lots_list(message: types.Message, lots):
    if not lots:
        await message.answer(USER_MESSAGES["no_lots"], reply_markup=back_to_menu_keyboard())
        return
    for lot in lots:
        emoji = currency_emoji(lot.get('currency'))
        price = f"{int(lot['start_price'])} {emoji}" if 'start_price' in lot else "-"
        msg = (
            f"🗂 <b>{lot['card_name']}</b> (ID: {lot['auction_id']})\n"
            f"Герой: {lot.get('hero_name') or '-'}\n"
            f"Цена: {price}\n"
            f"Время: {format_time_msg(lot['start_time'], lot['end_time'])}\n"
            f"Статус: {lot.get('status', '?')}"
        )
        kb = get_edit_delete_keyboard(lot['auction_id'])
        if lot.get('image_id'):
            await message.answer_photo(lot['image_id'], caption=msg, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(msg, parse_mode="HTML", reply_markup=kb)


def skip_old_event(timeout_seconds=30):
    def decorator(func):
        @wraps(func)
        async def wrapper(event, *args, **kwargs):
            if hasattr(event, "message"):
                msg_date = event.message.date.replace(tzinfo=timezone.utc)
                cb = event
            else:  # Это Message
                msg_date = event.date.replace(tzinfo=timezone.utc)
                cb = None
            now = datetime.now(timezone.utc)
            if (now - msg_date).total_seconds() > timeout_seconds:
                try:
                    if cb:
                        await cb.answer("Кнопка устарела, повтори действие.", show_alert=True)
                except Exception:
                    pass
                return None
            return await func(event, *args, **kwargs)

        return wrapper

    return decorator


def log_time(func):
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        duration = time.perf_counter() - start
        if duration > 0.5:
            logger.warning(f"[PERF] {func.__name__} took {duration:.3f}s")
        return result

    return wrapper


def format_lot_message(lot, owner):
    emoji = currency_emoji(lot.get('currency'))
    price = f"{int(lot['start_price'])} {emoji}" if 'start_price' in lot else "-"
    msg = (
        f"🗂 <b>{lot['card_name']}</b> (ID: {lot['auction_id']})\n"
        f"Герой: {lot.get('hero_name') or '-'}\n"
        f"Цена: {price}\n"
        f"Время: {format_time_msg(lot['start_time'], lot['end_time'])}\n"
        f"Владелец: {owner.get('username') or owner.get('full_name') or owner.get('user_id')}\n"
        f"Статус: {lot.get('status', '?')}"
    )
    return msg


@lru_cache
def get_edit_delete_keyboard(lot_id):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=BUTTONS["edit"], callback_data=f"editlot|{lot_id}")],
        [types.InlineKeyboardButton(text=BUTTONS["delete"], callback_data=f"delete_lot|{lot_id}")]
    ])


def months_keyboard_for_edit_schedule(months_ahead: int = 3):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import datetime
    today = datetime.datetime.now().date()
    kb = []
    for i in range(months_ahead):
        month = (today.month + i - 1) % 12 + 1
        year = today.year + ((today.month + i - 1) // 12)
        name = datetime.datetime(year, month, 1).strftime('%B %Y')
        kb.append([InlineKeyboardButton(
            text=name,
            callback_data=f"edit_sched_month|{year}-{month:02d}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=kb)
