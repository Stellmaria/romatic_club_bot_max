"""Safe direct editing of schedule card and deck fields."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.schedule_setup_ui import send_card
from bot.services.schedule_setup import normalize_rarity
from bot.telegram.callback_parser import rsplit_callback_data
from db.schedule_setup import get_card_for_setup, get_setup_session, set_setup_session
from db.schedule_setup_extensions import update_schedule_card_field, update_schedule_deck_field

router = Router(name=__name__)

FIELDS: tuple[tuple[str, str], ...] = (
    ("card_name", "Название карты"), ("hero_name", "Имя героя"),
    ("num", "Номер в колоде"), ("rarity", "Редкость"),
    ("story", "История"), ("quote", "Цитата"),
    ("image_id", "Фото / file_id"), ("deck_name", "Название колоды"),
    ("deck_type", "Тип колоды"),
)
LABELS = dict(FIELDS)
NULLABLE = frozenset({"hero_name", "story", "quote", "image_id"})


class CardFieldInput(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user or message.chat.type != "private":
            return False
        session = await get_setup_session(int(message.from_user.id))
        return bool(session and str(session.get("stage")) == "card_field")


def fields_keyboard(card_id: int) -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(FIELDS), 2):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"schfield:{field}:{card_id}")
            for field, label in FIELDS[index:index + 2]
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"schcard:show:{card_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("schcard:fields:"))
@admin_only
async def choose_field(call: CallbackQuery) -> None:
    card_id = int(rsplit_callback_data(call.data, ":", 1)[1])
    if not await get_card_for_setup(card_id):
        await call.answer("Карточка не найдена", show_alert=True)
        return
    await call.answer()
    if call.message:
        await call.message.answer(
            "📝 <b>Что исправить?</b>\n\nИзменение сразу попадёт в PostgreSQL. "
            "ID карточки и колоды намеренно недоступны.",
            parse_mode="HTML", reply_markup=fields_keyboard(card_id),
        )


@router.callback_query(F.data.startswith("schfield:"))
@admin_only
async def request_value(call: CallbackQuery) -> None:
    try:
        _, field, raw_id = str(call.data).split(":", 2)
        card_id = int(raw_id)
    except (TypeError, ValueError):
        await call.answer("Некорректная кнопка", show_alert=True)
        return
    if field not in LABELS:
        await call.answer("Поле недоступно", show_alert=True)
        return
    card = await get_card_for_setup(card_id)
    if not card:
        await call.answer("Карточка не найдена", show_alert=True)
        return
    await set_setup_session(
        int(call.from_user.id), stage="card_field", asset_key=field,
        deck_id=int(card["deck_id"]), card_id=card_id,
    )
    await call.answer()
    if not call.message:
        return
    hints = {
        "num": "Целое положительное число.",
        "rarity": "bronze, silver, gold или epic.",
        "image_id": "Новое фото или Telegram file_id.",
        "deck_type": "roulette или resource.",
    }
    clear = " Для очистки отправьте «-»." if field in NULLABLE else ""
    await call.message.answer(
        f"Введите: <b>{LABELS[field]}</b>. {hints.get(field, 'Одним сообщением.')}{clear}",
        parse_mode="HTML",
   )


@router.callback_query(F.data.startswith("schcard:show:"))
@admin_only
async def show_card(call: CallbackQuery) -> None:
    card_id = int(rsplit_callback_data(call.data, ":", 1)[1])
    card = await get_card_for_setup(card_id)
    if not card:
        await call.answer("Карточка не найдена", show_alert=True)
        return
    await set_setup_session(
        int(call.from_user.id), stage="card_review",
        deck_id=int(card["deck_id"]), card_id=card_id,
    )
    await call.answer()
    if call.message:
        await send_card(call.message, card, review=True)


def parse_value(field: str, message: Message) -> object:
    raw = str(message.text or message.caption or "").strip()
    if field == "image_id" and message.photo:
        return str(message.photo[-1].file_id)
    if raw.casefold() in {"-", "—", "пусто", "очистить", "null"}:
        if field not in NULLABLE:
            raise ValueError("Это поле нельзя оставить пустым.")
        return None
    if not raw:
        raise ValueError("Значение не должно быть пустым.")
    if field == "num":
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("Номер должен быть целым числом.") from exc
        if value <= 0:
            raise ValueError("Номер волжен быть больше нуля.")
        return value
    if field == "rarity":
        value = normalize_rarity(raw)
        if not value:
            raise ValueError("Редкость: bronze, silver, gold или epic.")
        return value
    if field == "deck_type":
        value = {"рулеточная": "roulette", "ресурсная": "resource"}.get(raw.casefold(), raw.casefold())
        if value not in {"roulette", "resource"}:
            raise ValueError("Тип колоды: roulette или resource.")
        return value
    if field in {"card_name", "hero_name", "deck_name"} and len(raw) > 255:
        raise ValueError("Значение длиннее 255 символов.")
    return raw


@router.message(CardFieldInput())
@admin_only
async def process_value(message: Message) -> None:
    user_id = int(message.from_user.id)
    session = await get_setup_session(user_id)
    if not session:
        return
    field = str(session.get("asset_key") or "")
    card_id = int(session["card_id"])
    if field not in LABELS:
        await message.answer("Не удалось определить редактируемое поле.")
        return
    try:
        value = parse_value(field, message)
        if field in {"deck_name", "deck_type"}:
            db_field = "name" if field == "deck_name" else "deck_type"
            await update_schedule_deck_field(int(session["deck_id"]), db_field, value)
        else:
            await update_schedule_card_field(card_id, field, value)
    except ValueError as exc:
        await message.answer(f"❌ {exc}")
        return
    except Exception:
        await message.answer("❌ Не удалось обновить поле. Подробности записаны в журнал бота.")
        raise

    card = await get_card_for_setup(card_id)
    if not card:
        await message.answer("Карточка не найдена после обновления.")
        return
    await set_setup_session(
        user_id, stage="card_review", deck_id=int(card["deck_id"]), card_id=card_id,
    )
    await message.answer(f"✅ Поле «{LABELS[field]}».обновлено.")
    await send_card(message, card, review=True)


__all__ = ["router"]
