"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime
from html import escape
from typing import Any, Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.time import to_moscow_wall
from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.admin_menu import send_admin_main_menu
from bot.handlers.admin.helper.new.keyboards import period_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.auction.exchange.catalog import (
    kb_exchange_approved_root,
    safe_edit_text_or_caption,
)
from bot.handlers.auction.exchange.moderation import (
    show_pending_exchange_requests,
    show_pending_exchange_requests_all,
)
from bot.telegram.callback_parser import split_callback_data
from bot.telegram.states import PreviewScheduleFSM
from db.auctions import get_auctions_by_date_with_owners

router = Router(name=__name__)
logger = logging.getLogger(__name__)

_MAIN_MENU_CALLBACKS = {
    "admin_back",
    "addadmin_cancel",
    "removeadmin_cancel",
    "givetrusted_cancel",
    "removetrusted_cancel",
    "universal_cancel",
}

_AUCTION_KIND_LABELS = {
    "standard": "⭐ Стандартный",
    "reverse": "✨ Обратный",
    "fast": "⚡ Быстрый",
    "free": "🪶 Свободный",
    "black": "👑 Чёрный",
    "exchange": "🛍 Биржа",
}


def _exchange_admin_root_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🧾 Заявки на модерацию",
        callback_data="admreq|pending|exchange",
    )
    kb.button(text="✅ Принятые лоты", callback_data="ex_appr:root")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)
    return kb.as_markup()


def _owners_from_snapshot(raw: object) -> list[dict[str, Any]]:
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _grouped_schedule_lines(auctions: Iterable[dict[str, Any]]) -> list[str]:
    grouped: dict[
        tuple[datetime, datetime, str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for auction in auctions:
        start_time = auction.get("start_time")
        end_time = auction.get("end_time")
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            continue

        start = to_moscow_wall(start_time)
        end = to_moscow_wall(end_time)
        card_name = str(auction.get("card_name") or "Без названия").strip()
        kind = str(auction.get("auction_kind") or "standard").strip().lower()
        grouped[(start, end, card_name, kind)].extend(
            _owners_from_snapshot(auction.get("owners_json"))
        )

    lines: list[str] = []
    for (start, end, card_name, kind), owners in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].casefold(), item[0][3]),
    ):
        owner_labels: list[str] = []
        seen: set[tuple[str, str]] = set()
        for owner in owners:
            user_id = str(owner.get("user_id") or "").strip()
            username = str(owner.get("username") or "").strip().lstrip("@")
            identity = (user_id, username.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            if username:
                owner_labels.append(f"@{escape(username)}")
            elif user_id:
                owner_labels.append(f"id:{escape(user_id)}")

        owners_text = ", ".join(owner_labels) if owner_labels else "—"
        kind_text = _AUCTION_KIND_LABELS.get(kind, escape(kind or "—"))
        lines.append(
            f"⏰ {start.strftime('%H:%M')}–{end.strftime('%H:%M')} | "
            f"<b>{escape(card_name)}</b> | ⚙️ {kind_text} | {owners_text}"
        )
    return lines


def _schedule_message_chunks(
    selected_date: date,
    lines: list[str],
    *,
    limit: int = 3600,
) -> list[str]:
    title = f"📅 <b>Расписание на {selected_date.strftime('%d.%m.%Y')}</b>"
    if not lines:
        return [f"{title}\n\nНет запланированных лотов на этот день."]

    chunks: list[str] = []
    current = title
    for line in lines:
        addition = f"\n\n{line}"
        if len(current) + len(addition) > limit and current != title:
            chunks.append(current)
            current = f"{title} <i>(продолжение)</i>\n\n{line}"
        else:
            current += addition
    chunks.append(current)
    return chunks


@router.message(Command("admin"), F.chat.type == "private")
@router.message(Command("admin_panel"), F.chat.type == "private")
@admin_only
async def show_admin_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.message(
    F.text.lower().in_(["назад", "⬅️ назад", "отмена", "❌ отмена", "cancel"]),
    F.chat.type == "private",
)
@admin_only
async def back_to_admin_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.callback_query(F.data.in_(_MAIN_MENU_CALLBACKS))
@admin_only
async def callback_to_admin_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    message = call.message
    if isinstance(message, Message):
        try:
            await message.delete()
        except Exception:
            pass
        user = call.from_user
        await send_admin_main_menu(message, user_id=user.id if user is not None else None)
    await call.answer()


@router.message(F.text == "📅 Расписание", F.chat.type == "private")
@admin_only
async def schedule_button(message: Message, state: FSMContext) -> None:
    """Open the grouped read-only schedule preview."""

    await start_preview_schedule(message, state)


@router.callback_query(F.data.startswith("preview_schedule|"))
@admin_only
async def preview_schedule_navigation(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    """Handle month/day selection and render the whole day without callback stalls."""

    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение с расписанием недоступно.", show_alert=True)
        return

    try:
        _, raw_date = split_callback_data(call.data or "", "|", 1)
        parts = raw_date.split("-")
    except ValueError:
        await call.answer("Некорректная дата.", show_alert=True)
        return

    if len(parts) == 2:
        try:
            year, month = map(int, parts)
            month_start = datetime(year, month, 1)
        except (TypeError, ValueError):
            await call.answer("Некорректный месяц.", show_alert=True)
            return

        await call.answer()
        await state.update_data(preview_year=year, preview_month=month)
        await state.set_state(PreviewScheduleFSM.choosing_day)
        await message.answer(
            "Выберите день для просмотра расписания:",
            reply_markup=period_keyboard(
                period="day",
                prefix="preview_schedule",
                base_date=month_start,
            ),
        )
        return

    if len(parts) == 3:
        try:
            year, month, day = map(int, parts)
            selected_date = date(year, month, day)
        except (TypeError, ValueError):
            await call.answer("Некорректный день.", show_alert=True)
            return

        await call.answer("Загружаю расписание…")
        try:
            auctions = await get_auctions_by_date_with_owners(selected_date)
            lines = _grouped_schedule_lines(auctions)
            for text in _schedule_message_chunks(selected_date, lines):
                await message.answer(text, parse_mode="HTML")
        except Exception:
            logger.exception(
                "Failed to render schedule preview for %s",
                selected_date,
            )
            await message.answer(
                "❌ Не удалось загрузить расписание. Ошибка записана в журнал."
            )
        finally:
            await state.clear()
        return

    await call.answer("Некорректный формат даты.", show_alert=True)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🛒 <b>Биржа</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=_exchange_admin_root_keyboard(),
    )


@router.callback_query(F.data == "admreq|pending|exchange")
@admin_only
async def exchange_pending_requests(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await call.answer("Открываю заявки…")
    if isinstance(call.message, Message):
        await show_pending_exchange_requests(call.message)


@router.callback_query(F.data.startswith("expend_mode|"))
@admin_only
async def exchange_pending_mode_compat(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    """Keep already-sent mode keyboards functional after the routing fix."""

    try:
        _, mode = split_callback_data(call.data or "", "|", 1)
    except ValueError:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    if mode not in {"all", "one"}:
        await call.answer("Неизвестный режим.", show_alert=True)
        return

    await state.clear()
    await call.answer("Открываю заявки…")
    if not isinstance(call.message, Message):
        return
    if mode == "all":
        await show_pending_exchange_requests_all(call.message)
    else:
        await show_pending_exchange_requests(call.message)


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def exchange_approved_root(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        reply_markup=kb_exchange_approved_root(),
    )
    await call.answer()


__all__ = [
    "router",
    "show_admin_main_menu",
    "back_to_admin_main_menu",
    "callback_to_admin_main_menu",
    "schedule_button",
    "preview_schedule_navigation",
    "exchange_menu_button",
    "exchange_pending_requests",
    "exchange_pending_mode_compat",
    "exchange_approved_root",
]
