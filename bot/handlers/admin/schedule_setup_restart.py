# ruff: noqa: RUF001
"""Restart, deck selection and audit commands for extended schedule setup."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.schedule_setup_ui import show_next
from bot.services.schedule_setup import validate_card_economy
from bot.telegram.callback_parser import rsplit_callback_data
from db.schedule_setup import (
    clear_setup_session,
    get_all_decks_for_setup,
    get_cards_for_setup,
    get_setup_audit,
)
from db.schedule_setup_extensions import (
    clear_schedule_deck_scope,
    restart_schedule_card_reviews,
    set_schedule_deck_scope,
    temporary_emoji_counts,
)

router = Router(name=__name__)

_INCOMPLETE_CARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("card_name", "название карты"),
    ("hero_name", "имя героя"),
    ("image_id", "изображение"),
    ("rarity", "редкость"),
    ("obtain_type", "тип награды"),
    ("obtain_amount", "размер награды"),
    ("story", "история"),
    ("quote", "цитата"),
)


def _deck_picker_keyboard(decks: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🔄 Проверить все колоды",
                callback_data="schsetup:restart:all",
            )
        ]
    ]
    deck_buttons: list[InlineKeyboardButton] = []
    for deck in decks:
        deck_id = int(deck["deck_id"])
        name = " ".join(str(deck.get("deck_name") or "Без названия").split())
        if len(name) > 28:
            name = name[:27].rstrip() + "…"
        deck_buttons.append(
            InlineKeyboardButton(
                text=f"№{deck_id} · {name}",
                callback_data=f"schsetup:restart:{deck_id}",
            )
        )
    for index in range(0, len(deck_buttons), 2):
        rows.append(deck_buttons[index : index + 2])
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="schsetup:stop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _missing_card_fields(card: dict[str, object]) -> list[str]:
    return [label for field, label in _INCOMPLETE_CARD_FIELDS if _is_blank(card.get(field))]


def _split_messages(header: str, lines: list[str], *, limit: int = 3800) -> list[str]:
    chunks: list[str] = []
    current = header
    for line in lines:
        candidate = f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


async def _show_deck_picker(message: Message) -> None:
    decks = await get_all_decks_for_setup()
    if not decks:
        await message.answer("В базе нет колод для проверки.")
        return
    await message.answer(
        "🗂 <b>Какую колоду проверить заново?</b>\n\n"
        "Будут сброшены только отметки проверки выбранной колоды. "
        "Эмодзи, экономика, тексты и остальные колоды останутся без изменений.",
        parse_mode="HTML",
        reply_markup=_deck_picker_keyboard(decks),
    )


async def _restart_all(message: Message, user_id: int) -> None:
    await restart_schedule_card_reviews()
    await clear_schedule_deck_scope(user_id)
    await clear_setup_session(user_id)
    await message.answer(
        "🔄 Проверка всех колод начата с первой карточки. Эмодзи, временные "
        "заглушки, экономика и поля сохранены; сброшены только отметки «проверено»."
    )
    await show_next(message, user_id)


async def _restart_deck(message: Message, user_id: int, deck_id: int) -> None:
    decks = {int(deck["deck_id"]): deck for deck in await get_all_decks_for_setup()}
    deck = decks.get(deck_id)
    if not deck:
        await message.answer("Выбранная колода больше не существует в базе.")
        return
    await restart_schedule_card_reviews(deck_id)
    await clear_setup_session(user_id)
    await set_schedule_deck_scope(user_id, deck_id)
    await message.answer(
        "🔄 <b>Повторная проверка выбранной колоды</b>\n\n"
        f"Колода №{deck_id}: <b>{deck.get('deck_name') or '—'}</b>\n"
        "После последней карты мастер остановится и не перейдёт к другим колодам.",
        parse_mode="HTML",
    )
    await show_next(message, user_id)


@router.message(Command("schedule_setup"), F.chat.type == "private")
@admin_only
async def start_full_schedule_setup(message: Message) -> None:
    user_id = int(message.from_user.id)
    await clear_schedule_deck_scope(user_id)
    await show_next(message, user_id)


@router.message(Command("schedule_setup_restart"), F.chat.type == "private")
@admin_only
async def restart_schedule_setup(message: Message) -> None:
    await _show_deck_picker(message)


@router.message(Command("schedule_setup_incomplete"), F.chat.type == "private")
@admin_only
async def show_incomplete_schedule_cards(message: Message) -> None:
    lines: list[str] = []
    total_cards = 0
    incomplete_cards = 0
    for deck in await get_all_decks_for_setup():
        deck_id = int(deck["deck_id"])
        deck_name = " ".join(str(deck.get("deck_name") or "Без названия").split())
        for card in await get_cards_for_setup(deck_id):
            total_cards += 1
            missing = _missing_card_fields(card)
            if not missing:
                continue
            incomplete_cards += 1
            card_id = int(card["card_id"])
            number = card.get("num")
            hero = " ".join(str(card.get("hero_name") or "—").split())
            card_name = " ".join(str(card.get("card_name") or "—").split())
            number_text = f"№{number}" if number is not None else f"ID {card_id}"
            lines.append(
                f"• колода {deck_id} «{deck_name}», {number_text}, card_id={card_id}: "
                f"{hero} — {card_name}; нет: {', '.join(missing)}"
            )

    if not lines:
        await message.answer(f"✅ Все {total_cards} карт заполнены по обязательным полям.")
        return

    header = (
        "🧩 <b>Карты с незаполненными полями</b>\n"
        f"Найдено: <b>{incomplete_cards}</b> из <b>{total_cards}</b>."
    )
    for chunk in _split_messages(header, lines):
        await message.answer(chunk, parse_mode="HTML")


@router.callback_query(F.data == "schsetup:restart")
@admin_only
async def restart_schedule_setup_callback(call: CallbackQuery) -> None:
    await call.answer()
    if not call.message:
        return
    await _show_deck_picker(call.message)


@router.callback_query(F.data.startswith("schsetup:restart:"))
@admin_only
async def restart_selected_scope(call: CallbackQuery) -> None:
    if not call.message:
        await call.answer("Сообщение недоступно", show_alert=True)
        return
    scope_value = rsplit_callback_data(call.data, ":", 1)[1]
    user_id = int(call.from_user.id)
    if scope_value == "all":
        await call.answer("Начинаю проверку всех колод")
        await _restart_all(call.message, user_id)
        return
    try:
        deck_id = int(scope_value)
    except ValueError:
        await call.answer("Некорректная колода", show_alert=True)
        return
    await call.answer(f"Начинаю колоду №{deck_id}")
    await _restart_deck(call.message, user_id, deck_id)


@router.message(Command("schedule_setup_cancel"), F.chat.type == "private")
@admin_only
async def cancel_extended_schedule_setup(message: Message) -> None:
    user_id = int(message.from_user.id)
    await clear_schedule_deck_scope(user_id)
    await clear_setup_session(user_id)
    await message.answer("Мастер остановлен. Всё уже сохранённое осталось в базе.")


@router.callback_query(F.data == "schsetup:stop")
@admin_only
async def stop_extended_schedule_setup(call: CallbackQuery) -> None:
    user_id = int(call.from_user.id)
    await clear_schedule_deck_scope(user_id)
    await clear_setup_session(user_id)
    await call.answer("Мастер остановлен")
    if call.message:
        await call.message.answer("Мастер остановлен. Всё уже сохранённое осталось в базе.")


@router.message(Command("schedule_audit"), F.chat.type == "private")
@admin_only
async def extended_schedule_audit(message: Message) -> None:
    audit = await get_setup_audit()
    temp = await temporary_emoji_counts()
    errors: list[str] = []
    for deck in await get_all_decks_for_setup():
        for card in await get_cards_for_setup(int(deck["deck_id"])):
            ok, reason = validate_card_economy(card)
            if not ok:
                errors.append(
                    f"• колода {card['deck_id']}, карта {card['card_id']} "
                    f"({card.get('hero_name') or card.get('card_name') or '—'}): {reason}"
                )
    tail = "\n".join(errors[:50]) or "Ошибок экономики не найдено."
    if len(errors) > 50:
        tail += f"\n…и ещё {len(errors) - 50}."
    await message.answer(
        "🔎 <b>Аудит шаблона расписания</b>\n\n"
        f"Общие эмодзи: {audit['common_configured']}/{audit['common_total']} "
        f"(временных: {temp['assets']})\n"
        f"Колоды: {audit['decks_configured']}/{audit['decks_total']} "
        f"(временных: {temp['decks']})\n"
        f"Карты: {audit['cards_verified']}/{audit['cards_total']} "
        f"(временных: {temp['cards']})\n"
        f"Ошибки экономики: {len(errors)}\n\n{tail}\n\n"
        "Выбор колоды: /schedule_setup_restart · незаполненные: "
        "/schedule_setup_incomplete · временные: /schedule_temp",
        parse_mode="HTML",
    )


__all__ = ["router"]
