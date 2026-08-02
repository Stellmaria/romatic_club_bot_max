"""Telegram adapter for the luxury schedule application use case."""
from __future__ import annotations

import re
from datetime import date, datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.use_cases.luxury_schedule import (
    LuxuryScheduleAccessDenied,
    LuxuryScheduleUseCase,
    chunks,
)
from bot.handlers.admin.helper.admin_keyboards import days_keyboard, months_keyboard
from bot.legacy_fsm import LuxScheduleFSM
from db.legacy import (
    get_all_decks,
    get_auctions_by_date_with_owners,
    get_cards_meta_bulk,
    get_deck_obtain_totals,
    get_deck_treasure_sum,
    get_last_nonempty_card_deck_id,
    get_max_obtain_for_rarity,
    get_obtain_variants_for_rarity,
    is_luxury_user,
)

router = Router(name="luxury_schedule")
PREFIX = "luxsched"


def _back_to_months_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к месяцам", callback_data=f"{PREFIX}|back_months")]
        ]
    )


def _extract_ymd(data: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d{4})[-_/.](\d{1,2})(?:[-_/.](\d{1,2}))?", data)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


async def _selected_date(data: str, state: FSMContext) -> date | None:
    parsed = _extract_ymd(data)
    if parsed and parsed[2]:
        try:
            return date(*parsed)
        except ValueError:
            return None

    day_match = re.search(r"(?:^|[|:])day[|:](\d{1,2})(?:[|:].*)?$", data)
    if not day_match:
        return None
    state_data = await state.get_data()
    now = datetime.now()
    try:
        return date(
            int(state_data.get("year") or now.year),
            int(state_data.get("month") or now.month),
            int(day_match.group(1)),
        )
    except (TypeError, ValueError):
        return None


def _use_case() -> LuxuryScheduleUseCase:
    return LuxuryScheduleUseCase(
        is_luxury_user=is_luxury_user,
        get_all_decks=get_all_decks,
        get_last_nonempty_deck_id=get_last_nonempty_card_deck_id,
        get_lots=get_auctions_by_date_with_owners,
        get_cards_meta=get_cards_meta_bulk,
        get_max_obtain_for_rarity=get_max_obtain_for_rarity,
        get_obtain_variants_for_rarity=get_obtain_variants_for_rarity,
        get_deck_treasure_sum=get_deck_treasure_sum,
        get_deck_obtain_totals=get_deck_obtain_totals,
    )


@router.message(F.chat.type == "private", F.text.in_({"/vip_schedule"}))
async def lux_start(message: types.Message, state: FSMContext) -> None:
    if not await is_luxury_user(message.from_user.id):
        await message.answer(
            "Эта функция доступна только для Лакшери-пользователей.",
            protect_content=True,
        )
        return
    await state.clear()
    await state.set_state(LuxScheduleFSM.choosing_month)
    await message.answer(
        "Выберите месяц:",
        reply_markup=months_keyboard(prefix=PREFIX, auction_id=None),
        protect_content=True,
    )


@router.callback_query(LuxScheduleFSM.choosing_month, F.data.startswith(PREFIX))
async def lux_choose_month(call: types.CallbackQuery, state: FSMContext) -> None:
    parsed = _extract_ymd(call.data or "")
    if not parsed:
        await call.answer("Неверный формат месяца", show_alert=True)
        return
    year, month, _ = parsed
    await state.update_data(year=year, month=month)
    await state.set_state(LuxScheduleFSM.choosing_day)
    await call.message.answer(
        "Выберите день:",
        reply_markup=days_keyboard(PREFIX, None, year, month),
        protect_content=True,
    )
    await call.answer()


@router.callback_query(LuxScheduleFSM.choosing_day, F.data.startswith(PREFIX))
async def lux_choose_day(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data or ""
    if data.endswith("|back_months") or data.endswith(":back_months"):
        await state.set_state(LuxScheduleFSM.choosing_month)
        await call.message.edit_text(
            "Выберите месяц:",
            reply_markup=months_keyboard(prefix=PREFIX, auction_id=None),
        )
        await call.answer()
        return

    selected = await _selected_date(data, state)
    if selected is None:
        await call.answer("Не удалось определить дату.", show_alert=True)
        return

    try:
        view = await _use_case().execute(user_id=call.from_user.id, selected_date=selected)
    except LuxuryScheduleAccessDenied:
        await call.answer("Доступ ограничен.", show_alert=True)
        return

    for message in view.messages:
        await call.message.answer(
            message,
            parse_mode="HTML" if view.has_lots else None,
            reply_markup=_back_to_months_keyboard(),
            protect_content=True,
        )
    await call.answer()
