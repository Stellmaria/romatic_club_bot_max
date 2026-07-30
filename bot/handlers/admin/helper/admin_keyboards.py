import calendar
from datetime import datetime, time, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.admin_constants import BUTTONS, CALLBACK_CONFIRM_LOT, CALLBACK_CHOOSE_TIME_BACK
from db.db import get_occupied_slots


def months_keyboard(prefix="month", months_ahead=3, auction_id=None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    today = datetime.now().date()
    for i in range(months_ahead):
        month = (today.month + i - 1) % 12 + 1
        year = today.year + ((today.month + i - 1) // 12)
        text = datetime(year, month, 1).strftime('%B %Y')
        if auction_id is not None:
            cb = f"{prefix}|{auction_id}|{year}-{month:02d}"
        else:
            cb = f"{prefix}|{year}-{month:02d}"
        kb.add(InlineKeyboardButton(text=text, callback_data=cb))
    kb.adjust(1)
    return kb.as_markup()


def days_keyboard(prefix, auction_id, year, month) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    days_in_month = calendar.monthrange(year, month)[1]
    for day in range(1, days_in_month + 1):
        cb = f"{prefix}|{auction_id}|{year}-{month:02d}-{day:02d}"
        kb.add(InlineKeyboardButton(text=str(day), callback_data=cb))
    kb.adjust(7)
    return kb.as_markup()

async def get_free_slots_for_day(auction_date: datetime.date, is_luxury: bool) -> list[datetime]:
    slots = []
    t = datetime.combine(auction_date, time(11, 0))
    end = datetime.combine(auction_date, time(23, 0))
    while t <= end:
        slots.append(t)
        t += timedelta(minutes=30)
    busy_times = await get_occupied_slots(auction_date)
    busy_set = set(bt[0] for bt in busy_times)
    free = [s for s in slots if s.time() not in busy_set]
    if not is_luxury:
        free = [s for s in free if 12 <= s.hour <= 20]
    return free


def confirm_time_keyboard(auction_id: int, slot_ok: datetime) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(
            text=f"{BUTTONS['confirm']} Да, выбрать это время",
            callback_data=f"{CALLBACK_CONFIRM_LOT}|{auction_id}|{slot_ok.isoformat()}"
        )
    )
    kb.add(InlineKeyboardButton(text=BUTTONS["back"], callback_data=CALLBACK_CHOOSE_TIME_BACK))
    return kb.as_markup()


def confirm_publish_keyboard(auction_id: int, iso_str: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(
            text=BUTTONS["confirm"],
            callback_data=f"{CALLBACK_CONFIRM_LOT}|{auction_id}|{iso_str}"
        )
    )
    kb.add(InlineKeyboardButton(text=BUTTONS["back"], callback_data=CALLBACK_CHOOSE_TIME_BACK))
    return kb.as_markup()
