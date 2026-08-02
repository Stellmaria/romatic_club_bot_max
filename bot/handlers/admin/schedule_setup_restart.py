"""Restart and audit commands for extended schedule setup."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.schedule_setup_ui import show_next
from bot.services.schedule_setup import validate_card_economy
from db.schedule_setup import (
    clear_setup_session,
    get_all_decks_for_setup,
    get_cards_for_setup,
    get_setup_audit,
)
from db.schedule_setup_extensions import restart_schedule_card_reviews, temporary_emoji_counts

router = Router(name=__name__)


async def _restart(message: Message, user_id: int) -> None:
    await restart_schedule_card_reviews()
    await clear_setup_session(user_id)
    await message.answer(
        "🔄 Проверка начата с первой карточки. Эмодзи, временные заглушки, "
        "экономика и поля сохранены; сброшены только отметки «проверено»."
    )
    await show_next(message, user_id)


@router.message(Command("schedule_setup_restart"), F.chat.type == "private")
@admin_only
async def restart_schedule_setup(message: Message) -> None:
    await _restart(message, int(message.from_user.id))


@router.callback_query(F.data == "schsetup:restart")
@admin_only
async def restart_schedule_setup_callback(call: CallbackQuery) -> None:
    if not call.message:
        await call.answer("Сообщение недоступно", show_alert=True)
        return
    await call.answer("Начинаю с первой карточки")
    await _restart(call.message, int(call.from_user.id))


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
        f"Общие эмодзи: {audit['common_configured']}/{audit['common_total']} (временных: {temp['assets']})\n"
        f"Колоды: {audit['decks_configured']}/{audit['decks_total']} (временных: {temp['decks']})\n"
        f"Карты: {audit['cards_verified']}/{audit['cards_total']} (временных: {temp['cards']})\n"
        f"Ошибки экономики: {len(errors)}\n\n{tail}\n\n"
        "С начала: /schedule_setup_restart · временные: /schedule_temp",
        parse_mode="HTML",
    )

__all__ = ["router"]
