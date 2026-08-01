"""Interactive admin master for schedule emoji and economy setup."""

from __future__ import annotations

import re
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.schedule_setup import (
    ASSET_BY_KEY,
    extract_single_custom_emoji,
    format_card_review,
    normalize_obtain_type,
    select_next_setup_step,
    validate_card_economy,
)
from db.cards import set_card_obtain
from db.schedule_setup import (
    clear_setup_session,
    get_all_decks_for_setup,
    get_card_for_setup,
    get_cards_for_setup,
    get_setup_audit,
    get_setup_session,
    mark_card_emoji_verified,
    set_preview_target,
    set_setup_session,
    upsert_card_emoji,
    upsert_deck_emoji,
    upsert_emoji_asset,
)
from bot.telegram.callback_parser import rsplit_callback_data

router = Router(name=__name__)

_ECONOMY_RE = re.compile(r"^\s*(diamonds?|tea|алмазы?|чай)\s+(\d+)\s*$", re.IGNORECASE)
_SET_SCHEDULE_RE = re.compile(r"^/set(?:@\w+)?\s+расписание\s*$", re.IGNORECASE)


class ActiveScheduleSetupFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user or message.chat.type != "private":
            return False
        return await get_setup_session(int(message.from_user.id)) is not None


def _card_review_keyboard(card_id: int, *, economy_ok: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if economy_ok:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Всё верно, следующая",
                    callback_data=f"schcard:ok:{card_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Исправить экономику",
                callback_data=f"schcard:econ:{card_id}",
            ),
            InlineKeyboardButton(
                text="🔁 Заменить эмодзи",
                callback_data=f"schcard:emoji:{card_id}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⏸ Остановить мастер",
                callback_data="schsetup:stop",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_card(
    message: Message,
    card: dict[str, Any],
    *,
    review: bool,
) -> None:
    text = format_card_review(card)
    if review:
        economy_ok, _ = validate_card_economy(card)
        text += (
            "\n\nПроверьте фото, поля карточки, награду и сохранённый Premium-эмодзи. "
            "После подтверждения мастер перейдёт к следующей карте по порядку в колоде."
        )
        keyboard = _card_review_keyboard(int(card["card_id"]), economy_ok=economy_ok)
    else:
        text += "\n\nПришлите одним сообщением ровно один Premium-эмодзи этой карты."
        keyboard = None

    image_id = str(card.get("image_id") or "").strip()
    if image_id:
        try:
            await message.answer_photo(
                photo=image_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramAPIError:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_next_step(message: Message, user_id: int) -> None:
    step = await select_next_setup_step(user_id)
    kind = step["kind"]

    if kind == "asset":
        asset = step["asset"]
        await message.answer(
            "🧩 <b>Общий Premium-эмодзи</b>\n\n"
            f"Пришлите ровно один кастомный эмодзи для {asset.label}.\n"
            f"Ключ: <code>{asset.key}</code>",
            parse_mode="HTML",
        )
        return

    if kind == "deck":
        deck = step["deck"]
        await message.answer(
            "🗂 <b>Эмодзи колоды</b>\n\n"
            f"Колода №{int(deck['deck_id'])}: <b>{deck.get('deck_name') or '—'}</b>\n"
            "Пришлите один Premium-эмодзи номера этой колоды. "
            "Он будет стоять после имени героя и в строке всей колоды.",
            parse_mode="HTML",
        )
        return

    if kind == "card":
        await _send_card(message, step["card"], review=step["stage"] == "card_review")
        return

    await clear_setup_session(user_id)
    audit = await get_setup_audit()
    await message.answer(
        "✅ <b>Мастер завершён</b>\n\n"
        f"Общие эмодзи: {audit['common_configured']}/{audit['common_total']}\n"
        f"Колоды: {audit['decks_configured']}/{audit['decks_total']}\n"
        f"Проверенные карты: {audit['cards_verified']}/{audit['cards_total']}\n\n"
        "Перед публикацией используйте /schedule_audit для итоговой проверки.",
        parse_mode="HTML",
    )


@router.message(Command("schedule_setup"), F.chat.type == "private")
@admin_only
async def start_schedule_setup(message: Message) -> None:
    await _show_next_step(message, int(message.from_user.id))


@router.message(Command("schedule_setup_cancel"), F.chat.type == "private")
@admin_only
async def cancel_schedule_setup(message: Message) -> None:
    await clear_setup_session(int(message.from_user.id))
    await message.answer("Мастер остановлен. Прогресс эмодзи и проверенных карт сохранён.")


@router.message(Command("schedule_audit"), F.chat.type == "private")
@admin_only
async def schedule_setup_audit(message: Message) -> None:
    audit = await get_setup_audit()
    economy_errors: list[str] = []
    for deck in await get_all_decks_for_setup():
        for card in await get_cards_for_setup(int(deck["deck_id"])):
            ok, reason = validate_card_economy(card)
            if not ok:
                economy_errors.append(
                    f"• колода {card['deck_id']}, карта {card['card_id']} "
                    f"({card.get('hero_name') or card.get('card_name') or '—'}): {reason}"
                )

    tail = "\n".join(economy_errors[:20]) or "Ошибок экономики не найдено."
    if len(economy_errors) > 20:
        tail += f"\n…и ещё {len(economy_errors) - 20}."
    await message.answer(
        "🔎 <b>Аудит шаблона расписания</b>\n\n"
        f"Общие эмодзи: {audit['common_configured']}/{audit['common_total']}\n"
        f"Колоды: {audit['decks_configured']}/{audit['decks_total']}\n"
        f"Проверенные карты: {audit['cards_verified']}/{audit['cards_total']}\n"
        f"Ошибки экономики: {len(economy_errors)}\n\n"
        + tail,
        parse_mode="HTML",
    )


@router.message(Command("set_schedule"))
@admin_only
async def set_schedule_target_command(message: Message) -> None:
    await _set_schedule_target(message)


@router.message(F.text.regexp(_SET_SCHEDULE_RE))
@admin_only
async def set_schedule_target_alias(message: Message) -> None:
    await _set_schedule_target(message)


async def _set_schedule_target(message: Message) -> None:
    thread_id = int(message.message_thread_id) if message.message_thread_id else None
    await set_preview_target(
        chat_id=int(message.chat.id),
        thread_id=thread_id,
        set_by=int(message.from_user.id),
    )
    location = f"ветка <code>{thread_id}</code>" if thread_id else "основной чат"
    await message.answer(
        "✅ Место для проверки расписания сохранено.\n"
        f"Чат: <code>{message.chat.id}</code>\n"
        f"Раздел: {location}\n\n"
        "В 22:30 МСК сюда придёт превью с кнопками подтверждения и отклонения.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("schcard:ok:"))
@admin_only
async def confirm_schedule_card(call: CallbackQuery) -> None:
    card_id = int(rsplit_callback_data(call.data, ":", 1)[1])
    card = await get_card_for_setup(card_id)
    if not card or not card.get("card_emoji_id"):
        await call.answer("У карты нет сохранённого Premium-эмодзи.", show_alert=True)
        return
    economy_ok, reason = validate_card_economy(card)
    if not economy_ok:
        await call.answer(reason, show_alert=True)
        return

    await mark_card_emoji_verified(card_id, verified_by=int(call.from_user.id))
    await call.answer("Карта подтверждена")
    if call.message:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await _show_next_step(call.message, int(call.from_user.id))


@router.callback_query(F.data.startswith("schcard:econ:"))
@admin_only
async def edit_schedule_card_economy(call: CallbackQuery) -> None:
    card_id = int(rsplit_callback_data(call.data, ":", 1)[1])
    card = await get_card_for_setup(card_id)
    if not card:
        await call.answer("Карточка не найдена.", show_alert=True)
        return
    await set_setup_session(
        int(call.from_user.id),
        stage="card_economy",
        deck_id=int(card["deck_id"]),
        card_id=card_id,
    )
    await call.answer()
    if call.message:
        await call.message.answer(
            "Введите правильную награду одним сообщением:\n"
            "<code>tea 8</code> или <code>diamonds 80</code>\n\n"
            "После сохранения бот снова покажет карточку для проверки.",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("schcard:emoji:"))
@admin_only
async def replace_schedule_card_emoji(call: CallbackQuery) -> None:
    card_id = int(rsplit_callback_data(call.data, ":", 1)[1])
    card = await get_card_for_setup(card_id)
    if not card:
        await call.answer("Карточка не найдена.", show_alert=True)
        return
    await set_setup_session(
        int(call.from_user.id),
        stage="card_emoji",
        deck_id=int(card["deck_id"]),
        card_id=card_id,
    )
    await call.answer()
    if call.message:
        await call.message.answer("Пришлите один новый Premium-эмодзи этой карты.")


@router.callback_query(F.data == "schsetup:stop")
@admin_only
async def stop_schedule_setup(call: CallbackQuery) -> None:
    await clear_setup_session(int(call.from_user.id))
    await call.answer("Мастер остановлен")
    if call.message:
        await call.message.answer("Мастер остановлен. Всё уже сохранённое осталось в базе.")


@router.message(ActiveScheduleSetupFilter())
@admin_only
async def process_schedule_setup_input(message: Message) -> None:
    user_id = int(message.from_user.id)
    session = await get_setup_session(user_id)
    if not session:
        return
    stage = str(session["stage"])

    if stage in {"asset_emoji", "deck_emoji", "card_emoji"}:
        emoji_id = extract_single_custom_emoji(message)
        if not emoji_id:
            await message.answer(
                "Нужен ровно один кастомный Premium-эмодзи в одном сообщении. "
                "Обычные эмодзи, картинки и несколько значков не принимаются."
            )
            return

        if stage == "asset_emoji":
            key = str(session.get("asset_key") or "")
            asset = ASSET_BY_KEY.get(key)
            if not asset:
                await message.answer("Не удалось определить настраиваемый общий элемент.")
                return
            await upsert_emoji_asset(
                key,
                emoji_id,
                fallback=asset.fallback,
                updated_by=user_id,
            )
            await message.answer(f"✅ Сохранён <code>{key}</code>.", parse_mode="HTML")
            await _show_next_step(message, user_id)
            return

        if stage == "deck_emoji":
            deck_id = int(session["deck_id"])
            await upsert_deck_emoji(deck_id, emoji_id, updated_by=user_id)
            await message.answer(f"✅ Эмодзи колоды №{deck_id} сохранён.")
            await _show_next_step(message, user_id)
            return

        card_id = int(session["card_id"])
        await upsert_card_emoji(card_id, emoji_id, updated_by=user_id)
        card = await get_card_for_setup(card_id)
        if not card:
            await message.answer("Карточка исчезла из базы во время настройки.")
            return
        await set_setup_session(
            user_id,
            stage="card_review",
            deck_id=int(card["deck_id"]),
            card_id=card_id,
        )
        await _send_card(message, card, review=True)
        return

    if stage == "card_economy":
        match = _ECONOMY_RE.match(message.text or "")
        if not match:
            await message.answer(
                "Формат не распознан. Используйте <code>tea 8</code> "
                "или <code>diamonds 80</code>.",
                parse_mode="HTML",
            )
            return
        obtain_type = normalize_obtain_type(match.group(1))
        amount = int(match.group(2))
        card_id = int(session["card_id"])
        await set_card_obtain(card_id, str(obtain_type), amount)
        card = await get_card_for_setup(card_id)
        if not card:
            await message.answer("Карточка не найдена после обновления экономики.")
            return
        await set_setup_session(
            user_id,
            stage="card_review",
            deck_id=int(card["deck_id"]),
            card_id=card_id,
        )
        await _send_card(message, card, review=True)
        return

    await message.answer("На этом шаге используйте кнопки под карточкой.")


__all__ = ["router"]
