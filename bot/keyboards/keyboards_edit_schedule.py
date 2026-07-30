from calendar import monthrange
from datetime import datetime

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def months_keyboard():
    today = datetime.now()
    kb = []
    for i in range(3):
        month = (today.month + i - 1) % 12 + 1
        year = today.year + ((today.month + i - 1) // 12)
        name = datetime(year, month, 1).strftime('%B %Y')
        kb.append([InlineKeyboardButton(text=name, callback_data=f"edit_sched_month|{year}-{month:02d}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def days_keyboard_simple(year, month):
    kb = []
    last_day = monthrange(year, month)[1]
    for day in range(1, last_day + 1):
        kb.append([
            types.InlineKeyboardButton(
                text=f"{day:02d}.{month:02d}.{year}",
                callback_data=f"edit_sched_day|{year}-{month:02d}-{day:02d}"
            )
        ])
    return types.InlineKeyboardMarkup(inline_keyboard=kb)


def preview_months_keyboard():
    today = datetime.now()
    kb = []
    for i in range(3):
        month = (today.month + i - 1) % 12 + 1
        year = today.year + ((today.month + i - 1) // 12)
        name = datetime(year, month, 1).strftime('%B %Y')
        kb.append([InlineKeyboardButton(
            text=name,
            callback_data=f"preview_sched_month|{year}-{month:02d}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def preview_days_keyboard(year, month):
    last_day = monthrange(year, month)[1]
    buttons = [
        [InlineKeyboardButton(
            text=f"{day:02d}.{month:02d}.{year}",
            callback_data=f"preview_sched_day|{year}-{month:02d}-{day:02d}"
        )]
        for day in range(1, last_day + 1)
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
