"""Shared UI extensions for the schedule setup master."""

from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers.admin import schedule_setup as base
from bot.services.schedule_setup import (
    ASSET_BY_KEY,
    format_card_review,
    select_next_setup_step,
    validate_card_economy,
)
from db.schedule_setup import (
    clear_setup_session,
    get_cards_for_setup,
    get_setup_audit,
    set_setup_session,
)
from db.schedule_setup_extensions import (
    clear_schedule_deck_scope,
    clear_temporary_emoji,
    get_schedule_deck_scope,
    is_temporary_emoji,
    temporary_emoji_counts,
)

ORIGINAL_UPSERT_ASSET = base.upsert_emoji_asset
ORIGINAL_UPSERT_DECK = base.upsert_deck_emoji
ORIGINAL_UPSERT_CARD = base.upsert_card_emoji


async def _real_asset(*args: Any, **kwargs: Any) -> None:
    await ORIGINAL_UPSERT_ASSET(*args, **kwargs)
    await clear_temporary_emoji("asset", args[0])


async def _real_deck(*args: Any, **kwargs: Any) -> None:
    await ORIGINAL_UPSERT_DECK(*args, **kwargs)
    await clear_temporary_emoji("deck", args[0])


async def _real_card(*args: Any, **kwargs: Any) -> None:
    await ORIGINAL_UPSERT_CARD(*args, **kwargs)
    await clear_temporary_emoji("card", args[0])


def review_keyboard(card_id: int, *, economy_ok: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if economy_ok:
        rows.append([InlineKeyboardButton(text="✅ Всё верно, следующая", callback_data=f"schcard:ok:{card_id}")])
    rows.extend(
        [
            [
                InlineKeyboardButton(text="✏️ Экономика", callback_data=f"schcard:econ:{card_id}"),
                InlineKeyboardButton(text="📝 Поля карточки", callback_data=f"schcard:fields:{card_id}"),
            ],
            [
                InlineKeyboardButton(text="🔁 Premium-эмодзи", callback_data=f"schcard:emoji:{card_id}"),
                InlineKeyboardButton(text="🪄 Временный эмодзи", callback_data=f"schtmp:card:{card_id}"),
            ],
            [InlineKeyboardButton(text="⏸ Остановить мастер", callback_data="schsetup:stop")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _missing_card_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🪄 Поставить временный эмодзи", callback_data=f"schtmp:card:{card_id}")],
            [InlineKeyboardButton(text="⏸ Остановить мастер", callback_data="schsetup:stop")],
        ]
    )


async def send_card(message: Message, card: dict[str, Any], *, review: bool) -> None:
    text = format_card_review(card)
    if await is_temporary_emoji("card", card["card_id"]):
        text += "\n\n⚠️ <b>Используется временный Premium-эмодзи.</b> Его можно заменить позже."
    if review:
        economy_ok, _ = validate_card_economy(card)
        text += (
            "\n\nПроверьте фото, поля, награду и эмодзи. Изменения пишутся прямо "
            "в базу; ID карточки и колоды не редактируются."
        )
        keyboard = review_keyboard(int(card["card_id"]), economy_ok=economy_ok)
    else:
        text += "\n\nПришлите один Premium-эмодзи или поставьте временную заглушку."
        keyboard = _missing_card_keyboard(int(card["card_id"]))

    image_id = str(card.get("image_id") or "").strip()
    if image_id:
        try:
            await message.answer_photo(image_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
            return
        except TelegramAPIError:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _temporary_button(scope: str, key: object) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🪄 Временный эмодзи", callback_data=f"schtmp:{scope}:{key}")],
            [InlineKeyboardButton(text="⏸ Остановить мастер", callback_data="schsetup:stop")],
        ]
    )


def _choose_deck_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗂 Выбрать другую колоду", callback_data="schsetup:restart")]
        ]
    )


async def _show_next_scoped_card(message: Message, user_id: int, deck_id: int) -> bool:
    for card in await get_cards_for_setup(deck_id):
        if bool(card.get("emoji_verified")):
            continue
        stage = "card_review" if card.get("card_emoji_id") else "card_emoji"
        await set_setup_session(
            user_id,
            stage=stage,
            deck_id=deck_id,
            card_id=int(card["card_id"]),
        )
        await send_card(message, card, review=stage == "card_review")
        return True
    return False


async def show_next(message: Message, user_id: int) -> None:
    scope_deck_id = await get_schedule_deck_scope(user_id)
    if scope_deck_id is not None:
        if await _show_next_scoped_card(message, user_id, scope_deck_id):
            return
        await clear_schedule_deck_scope(user_id)
        await clear_setup_session(user_id)
        await message.answer(
            "✅ <b>Проверка выбранной колоды завершена</b>\n\n"
            f"Колода №{scope_deck_id} проверена. Другие колоды и их отметки не затронуты.",
            parse_mode="HTML",
            reply_markup=_choose_deck_keyboard(),
        )
        return

    step = await select_next_setup_step(user_id)
    kind = step["kind"]
    if kind == "asset":
        asset = step["asset"]
        await message.answer(
            "🧩 <b>Общий Premium-эмодзи</b>\n\n"
            f"Пришлите один эмодзи для {asset.label}.\nКлюч: <code>{asset.key}</code>\n\n"
            "При отсутствии подходящего поставьте временную заглушку.",
            parse_mode="HTML",
            reply_markup=_temporary_button("asset", asset.key),
        )
        return
    if kind == "deck":
        deck = step["deck"]
        deck_id = int(deck["deck_id"])
        await message.answer(
            "🗂 <b>Эмодзи колоды</b>\n\n"
            f"Колода №{deck_id}: <b>{deck.get('deck_name') or '—'}</b>\n"
            "Пришлите Premium-эмодзи номера колоды или поставьте временный.",
            parse_mode="HTML",
            reply_markup=_temporary_button("deck", deck_id),
        )
        return
    if kind == "card":
        await send_card(message, step["card"], review=step["stage"] == "card_review")
        return

    await clear_schedule_deck_scope(user_id)
    await clear_setup_session(user_id)
    audit = await get_setup_audit()
    temp = await temporary_emoji_counts()
    await message.answer(
        "✅ <b>Мастер завершён</b>\n\n"
        f"Общие эмодзи: {audit['common_configured']}/{audit['common_total']} (временных: {temp['assets']})\n"
        f"Колоды: {audit['decks_configured']}/{audit['decks_total']} (временных: {temp['decks']})\n"
        f"Карты: {audit['cards_verified']}/{audit['cards_total']} (временных: {temp['cards']})\n\n"
        "Повторная проверка выбранной колоды: /schedule_setup_restart\n"
        "Замена временных эмодзи: /schedule_temp",
        parse_mode="HTML",
        reply_markup=_choose_deck_keyboard(),
    )


# The base handler resolves these globals at runtime.
base.upsert_emoji_asset = _real_asset
base.upsert_deck_emoji = _real_deck
base.upsert_card_emoji = _real_card
base._card_review_keyboard = review_keyboard
base._send_card = send_card
base._show_next_step = show_next

__all__ = [
    "ASSET_BY_KEY",
    "ORIGINAL_UPSERT_ASSET",
    "ORIGINAL_UPSERT_CARD",
    "ORIGINAL_UPSERT_DECK",
    "send_card",
    "show_next",
]
