"""Temporary Premium emoji controls for schedule setup."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.schedule_setup_ui import (
    ASSET_BY_KEY,
    ORIGINAL_UPSERT_ASSET,
    ORIGINAL_UPSERT_CARD,
    ORIGINAL_UPSERT_DECK,
    send_card,
    show_next,
)
from db.schedule_setup import get_card_for_setup, set_setup_session
from db.schedule_setup_extensions import create_temporary_emoji, get_temporary_emoji_marks

router = Router(name=__name__)


@router.callback_query(F.data.startswith("schtmp:"))
@admin_only
async def set_temporary_emoji(call: CallbackQuery) -> None:
    try:
        _, scope, key = str(call.data).split(":", 2)
    except ValueError:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    fallback = ASSET_BY_KEY[key].fallback if scope == "asset" and key in ASSET_BY_KEY else {"deck": "🗂", "card": "🎴"}.get(scope)
    if not fallback:
        await call.answer("Неизвестный тип эмодзи", show_alert=True)
        return
    try:
        await create_temporary_emoji(
            scope,
            key,
            fallback=fallback,
            updated_by=int(call.from_user.id),
            upsert_asset=ORIGINAL_UPSERT_ASSET,
            upsert_deck=ORIGINAL_UPSERT_DECK,
            upsert_card=ORIGINAL_UPSERT_CARD,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.answer("Временный эмодзи сохранён")
    if not call.message:
        return
    if scope == "card":
        card = await get_card_for_setup(int(key))
        if not card:
            await call.message.answer("Карточка не найдена после обновления.")
            return
        await set_setup_session(
            int(call.from_user.id), stage="card_review",
            deck_id=int(card["deck_id"]), card_id=int(key),
        )
        await send_card(call.message, card, review=True)
    else:
        await show_next(call.message, int(call.from_user.id))


@router.message(Command("schedule_temp"), F.chat.type == "private")
@admin_only
async def list_temporary_emojis(message: Message) -> None:
    marks = await get_temporary_emoji_marks()
    if not marks:
        await message.answer("Временных эмодзи нет.")
        return
    rows = []
    labels = {"asset": "Общий", "deck": "Колода", "card": "Карта"}
    for mark in marks[:80]:
        scope, key = str(mark["scope"]), str(mark["entity_key"])
        rows.append([InlineKeyboardButton(
            text=f"{labels.get(scope, scope)}: {key}",
            callback_data=f"schtmpreplace:{scope}:{key}",
        )])
    await message.answer(
        "Выберите временный эмодзи, который уже можно заменить настоящим:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("schtmpreplace:"))
@admin_only
async def replace_temporary_emoji(call: CallbackQuery) -> None:
    try:
        _, scope, key = str(call.data).split(":", 2)
    except ValueError:
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    kwargs: dict[str, object] = {}
    if scope == "asset":
        kwargs = {"stage": "asset_emoji", "asset_key": key}
    elif scope == "deck":
        kwargs = {"stage": "deck_emoji", "deck_id": int(key)}
    elif scope == "card":
        card = await get_card_for_setup(int(key))
        if not card:
            await call.answer("Карточка не найдена", show_alert=True)
            return
        kwargs = {"stage": "card_emoji", "deck_id": int(card["deck_id"]), "card_id": int(key)}
    else:
        await call.answer("Неизвестный тип", show_alert=True)
        return
    await set_setup_session(int(call.from_user.id), **kwargs)
    await call.answer()
    if call.message:
        await call.message.answer("Пришлите один настоящий Premium-эмодзи. Временная отметка снимется автоматически.")

__all__ = ["router"]
