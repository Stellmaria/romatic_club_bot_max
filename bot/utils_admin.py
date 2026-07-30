import html

from bot.core.time import to_moscow_wall
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers.admin.helper.admin_constants import BUTTONS, SYSTEM_MESSAGES
from bot.utils import resolve_user_id
from bot.core.legacy_config import ADMIN_SECRET
from db.legacy import get_user


def lot_admin_keyboard(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BUTTONS["approve"], callback_data=f"approve_{auction_id}")],
        [InlineKeyboardButton(text=BUTTONS["reject"], callback_data=f"reject_{auction_id}")],
        [InlineKeyboardButton(text=BUTTONS["edit"], callback_data=f"editlot|{auction_id}")]
    ])


async def notify_user(bot, user_id, msg, parse_mode="HTML"):
    try:
        await bot.send_message(user_id, msg, parse_mode=parse_mode)
    except Exception as e:
        print(f"[ERROR notify user {user_id}]: {e}")


def parse_username_arg(message, usage_example: str) -> str | None:
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].startswith("@"):
        return None
    return parts[1]


def parse_date_or_none(text: str):
    from datetime import datetime
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def safe_lot_field(field: str) -> str:
    allowed = {"card_name", "start_price", "currency", "comment", "image_id"}
    if field in allowed:
        return field
    raise ValueError(SYSTEM_MESSAGES["field_edit_error"])


async def resolve_admin_action_args(message: types.Message, usage: str) -> tuple[int | None, dict | None]:
    parts = message.text.strip().split()
    if len(parts) != 3:
        await message.answer(SYSTEM_MESSAGES["syntax_error"].format(example=usage))
        return None, None
    who, password = parts[1], parts[2]
    if password != ADMIN_SECRET:
        await message.answer(SYSTEM_MESSAGES["invalid_password"])
        return None, None
    user_id = await resolve_user_id(who)
    if not user_id:
        await message.answer(SYSTEM_MESSAGES["user_not_found"])
        return None, None
    user = await get_user(user_id)
    return user_id, user


def format_log_entry(log: dict) -> str:
    """Форматирование одной строки аудита для вывода."""
    created_at = log.get("created_at") or log.get("timestamp")
    time_str = created_at.strftime("%d.%m %H:%M") if created_at else "—"
    return (
        f"\n🕒 <b>{time_str}</b>\n"
        f"👤 <code>{log['user_id']}</code>\n"
        f"🔧 <b>{log['action_type']}</b>\n"
        f"🎴 Лот: <code>{log['auction_id']}</code>\n"
        f"📄 {html.escape(log['details']) if log['details'] else '-'}\n"
    )


def format_schedule_lot(lot, owner):
    start = lot.get("start_time")
    end = lot.get("end_time")
    if start and end:
        start = to_moscow_wall(start)
        end = to_moscow_wall(end)
        dt_str = f"{start:%H:%M}–{end:%H:%M}"
    else:
        dt_str = "время не назначено"
    emoji = "💎" if lot.get("currency") == "алмазы" else "🍵"
    price = f"{lot['start_price']} {emoji}" if lot.get("start_price") else "-"
    owner_str = (
        f'<a href="https://t.me/{owner["username"]}">@{owner["username"]}</a> ({owner["full_name"]})'
        if owner and owner.get("username") else f"id={lot['owner_id']}"
    )
    return (
        f"🔹 <b>{html.escape(str(lot.get('card_name', '-')))}</b> (ID: {lot.get('auction_id')})\n"
        f"Герой: {html.escape(str(lot.get('hero_name') or '-'))}\n"
        f"Владелец: {owner_str}\n"
        f"Цена: {price}\n"
        f"Время: {dt_str}\n"
        f"Статус: {html.escape(str(lot.get('status', '-')))}\n"
        f"Комментарий: {html.escape(str(lot.get('comment', '-')))}\n"
        "────────────"
    )
