"""Administrative card-economy mutation commands and FSM handlers."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from html import escape
from math import ceil
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.auction_notify import _kb_equal
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log as _send_admin_log
from bot.handlers.card_subscribe import decks_keyboard, presets_manage_keyboard
from bot.services.card_economy import CardEconomyService
from bot.services.card_subscriptions import CardSubscriptionsService
from bot.telegram.callbacks import safe_callback_answer
from bot.core.legacy_config import legacy_config
from db.cards import (
    get_card,
    get_deck,
    norm_obtain_type,
    set_card_obtain,
    set_deck_type,
    get_all_decks,
)
from db.users import (
    get_user_id_by_username,
    is_luxury_user,
)
from db.subscriptions import (
    list_broadcast_targets,
    list_user_card_subs,
    mark_subscription_confirmed,
    mark_unreachable_user,
    unsubscribe_subscription,
)
from db.auctions import get_auction_winner
from bot.telegram.states import CardSubscribeFSM, EconomyFSM

# ---------------------------------------------------------------------------
# Router / constants
# ---------------------------------------------------------------------------

from bot.handlers.admin.helper.new.card_economy_shared import (
    CANCEL_TEXT,
    SEND_HTML_KW,
    card_name,
    cancel_kb,
    deck_name,
    log_with_ctx,
)

router = Router(name="admin_card_economy_mutation")


def _kb_economy_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Тип колоды", callback_data="economy:decktype"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Получение карты", callback_data="economy:obtain"
                )
            ],
        ]
    )


def _kb_deck_types() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Рулеточная")],
            [KeyboardButton(text="Ресурсная")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _kb_obtain_types() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Алмазы")],
            [KeyboardButton(text="Чай")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        selective=True,
    )


@router.message(F.text == "💰 Экономика")
@admin_only
async def economy_root(message: types.Message) -> None:
    await message.answer("Выберите раздел:", reply_markup=_kb_economy_root())


@router.callback_query(F.data.startswith("economy:"))
@admin_only
async def economy_cb(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data or ""
    parts = data.split(":", 1)
    if len(parts) < 2 or call.message is None:
        await call.answer()
        return

    action = parts[1]
    if action == "decktype":
        await state.set_state(EconomyFSM.deck_id)
        await call.message.answer(
            "Введите ID колоды:", reply_markup=cancel_kb()
        )
    elif action == "obtain":
        await state.set_state(EconomyFSM.obtain_card_id)
        await call.message.answer(
            "Введите card_id карты для настройки «Получения»:",
            reply_markup=cancel_kb(),
        )
    await call.answer()


# ------------------ /decktype ------------------


@router.message(Command("decktype"))
@admin_only
async def cmd_decktype(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await economy_root(message)
        return

    _, did, dtype = parts
    try:
        deck_id = int(did)
    except ValueError:
        await message.answer("deck_id должен быть целым числом.")
        return

    deck = await get_deck(deck_id)
    if not isinstance(deck, dict):
        await message.answer("Такой колоды нет.")
        return

    try:
        before, after = await set_deck_type(deck_id, dtype)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    deck_title = deck_name(deck, deck_id)
    await message.answer(
        f"✅ Тип колоды обновлён: <b>{deck_title}</b>\n"
        f"{before or '-'} → <b>{after}</b>",
        **SEND_HTML_KW,
    )

    await log_with_ctx(
        message,
        "<b>⚙️ Тип колоды</b>\n"
        f"ID: {deck_id} {deck_title}\n"
        f"{before or '-'} → <b>{after}</b>",
    )


@router.message(EconomyFSM.deck_id, F.text)
@admin_only
async def fsm_deck_id(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    try:
        deck_id = int(text)
    except ValueError:
        await message.answer("Нужен целый ID колоды.")
        return

    deck = await get_deck(deck_id)
    if not deck:
        await message.answer("Такой колоды нет.")
        return

    await state.update_data(deck_id=deck_id)
    await message.answer(
        "Выберите тип колоды:", reply_markup=_kb_deck_types()
    )
    await state.set_state(EconomyFSM.deck_type)


@router.message(EconomyFSM.deck_type, F.text)
@admin_only
async def fsm_deck_type(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    data = await state.get_data()
    deck_id = int(data["deck_id"])

    try:
        before, after = await set_deck_type(deck_id, text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    deck = await get_deck(deck_id)
    deck_title = deck_name(deck, deck_id)

    await message.answer(
        f"✅ Тип колоды обновлён: <b>{deck_title}</b>\n"
        f"{before or '-'} → <b>{after}</b>",
        reply_markup=ReplyKeyboardRemove(),
        **SEND_HTML_KW,
    )

    await log_with_ctx(
        message,
        "<b>⚙️ Тип колоды</b>\n"
        f"ID: {deck_id} {deck_title}\n"
        f"{before or '-'} → <b>{after}</b>",
    )
    await state.clear()


# ------------------ /obtain ------------------


@router.message(Command("obtain"))
@admin_only
async def cmd_obtain(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=3)
    if len(parts) < 4:
        await economy_root(message)
        return

    _, cid, t, amt = parts[:4]
    try:
        card_id = int(cid)
        amount = int(amt)
    except ValueError:
        await message.answer("card_id и amount должны быть целыми числами.")
        return

    await _apply_obtain(message, card_id, t, amount)


@router.message(EconomyFSM.obtain_card_id, F.text)
@admin_only
async def fsm_obtain_card_id(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    try:
        card_id = int(text)
    except ValueError:
        await message.answer("Нужен целый card_id.")
        return

    card = await get_card(card_id)
    if not card:
        await message.answer("Такой карты нет.")
        return

    await state.update_data(card_id=card_id)
    await message.answer(
        "Выберите тип получения:", reply_markup=_kb_obtain_types()
    )
    await state.set_state(EconomyFSM.obtain_type)


@router.message(EconomyFSM.obtain_type, F.text)
@admin_only
async def fsm_obtain_type(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    obtain = norm_obtain_type(text)
    if obtain not in {"diamonds", "tea"}:
        await message.answer(
            "Тип должен быть: Алмазы или Чай (diamonds|tea)."
        )
        return

    await state.update_data(obtain_type=obtain)
    await message.answer(
        "Введите количество (целое):", reply_markup=cancel_kb()
    )
    await state.set_state(EconomyFSM.obtain_amount)


@router.message(EconomyFSM.obtain_amount, F.text.regexp(r"^\d+$"))
@admin_only
async def fsm_obtain_amount(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if not text:
        await message.answer("Нужно целое число.")
        return
    amount = int(text)

    data = await state.get_data()
    raw_id = data.get("card_id") or data.get("obtain_card_id")
    if raw_id is None or not isinstance(raw_id, (str, int)):
        await message.answer("card_id не найден или имеет неверный формат.")
        return
    try:
        card_id = int(raw_id)
    except (TypeError, ValueError):
        await message.answer("card_id имеет неверный формат.")
        return

    t_val = data.get("obtain_type")
    obtain_type = t_val if isinstance(t_val, str) else ""
    if not obtain_type:
        await message.answer("Тип получения не найден.")
        return

    await _apply_obtain(message, card_id, obtain_type, amount)
    await state.clear()


async def _apply_obtain(
        message: types.Message,
        card_id: int,
        obtain_type: str,
        amount: int,
) -> None:
    card = await get_card(card_id)
    if not isinstance(card, dict):
        await message.answer("Такой карты нет.")
        return

    try:
        before, after = await set_card_obtain(card_id, obtain_type, amount)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    name = card_name(card, card_id)
    await message.answer(
        "✅ Получение карты обновлено: "
        f"<b>{name}</b>\n"
        f"type {before[0]} → <b>{after[0]}</b>\n"
        f"amount {before[1]} → <b>{after[1]}</b>",
        **SEND_HTML_KW,
    )

    await log_with_ctx(
        message,
        "<b>🛒 Получение карты</b>\n"
        f"Card #{card_id} {name}\n"
        f"type {before[0]} → <b>{after[0]}</b>; "
        f"amount {before[1]} → <b>{after[1]}</b>",
    )


# ---------------------------------------------------------------------------
# Топ подписок (просмотр, пагинация)
# ---------------------------------------------------------------------------
