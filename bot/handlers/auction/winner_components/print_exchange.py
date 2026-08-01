from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.auction_winners import AuctionWinnerService
from bot.core.legacy_config import legacy_config
from bot.legacy_fsm import PrintExStates

from .common import mention_html
from bot.telegram.callback_parser import split_callback_data

router = Router(name="auction_winner_print_exchange")


def _kb_print_ex(batch_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="📨 Отправить обоим", callback_data=f"pex|send_both|{batch_id}")
    keyboard.button(text="👑 Отправить владельцу", callback_data=f"pex|send_owner|{batch_id}")
    keyboard.button(text="🏆 Отправить победителю", callback_data=f"pex|send_winner|{batch_id}")
    keyboard.button(text="🏆 Сменить победителя", callback_data=f"pex|set_winner|{batch_id}")
    keyboard.button(text="💰 Сменить цену", callback_data=f"pex|set_price|{batch_id}")
    keyboard.button(text="♻️ Сброс", callback_data=f"pex|reset|{batch_id}")
    keyboard.button(text="🧩 Мастер", callback_data=f"pex|master|{batch_id}")
    keyboard.button(text="🔄 Обновить", callback_data=f"pex|refresh|{batch_id}")
    keyboard.adjust(1, 2, 2, 2, 1)
    return keyboard.as_markup()


def _parse_winner(raw: str) -> tuple[int | None, str | None]:
    value = (raw or "").strip()
    if not value:
        return None, None
    if value.startswith("@"):
        return None, value.lstrip("@")
    if value.isdigit():
        return int(value), None
    return None, value


async def _render_print_ex_text(batch: dict, cards: list[dict], stats: dict | None) -> str:
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None
    owner = mention_html(owner_id, owner_username)

    winner_id = int(stats["manual_winner_id"]) if stats and stats.get("manual_winner_id") else None
    winner_name = (stats.get("manual_winner_name") or "").strip() if stats else ""
    winner = mention_html(winner_id, winner_name) if winner_id else (f"@{winner_name}" if winner_name else "—")

    price = stats.get("manual_price") if stats else None
    if price is None:
        price = batch.get("price")
    link = (stats.get("manual_link") or "").strip() if stats else ""
    link = link or "—"

    card_lines = []
    for card in cards:
        title = f"{card.get('hero_name') or ''} — {card.get('card_name') or ''}".strip(" —")
        card_lines.append(f"• {title} (id={card['card_id']}) × {card['qty']}")
    cards_block = "\n".join(card_lines) if card_lines else "—"

    return (
        "🛒 <b>PRINT EX</b>\n"
        f"🆔 batch_id: <code>{batch['batch_id']}</code>\n"
        f"Статус: <b>{batch.get('status', '?')}</b>\n\n"
        f"👑 Владелец: {owner}\n"
        f"🏆 Победитель: {winner}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link}\n\n"
        f"<b>Состав:</b>\n{cards_block}"
    )


async def _refresh_print_ex(call: CallbackQuery, service: AuctionWinnerService, batch: dict) -> None:
    batch_id = int(batch["batch_id"])
    cards = await service.exchange_cards(batch_id)
    stats = await service.exchange_print_stats(batch_id)
    text = await _render_print_ex_text(batch, cards, stats)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_kb_print_ex(batch_id))
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


@router.message(Command("ex_owners"))
async def cmd_ex_owners(message: Message) -> None:
    if message.from_user.id not in legacy_config.ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /ex_owners <card_id>")
        return
    try:
        card_id = int(parts[1].strip())
    except ValueError:
        await message.answer("card_id должен быть числом.")
        return

    service = await AuctionWinnerService.create()
    batches = await service.exchange_batches_for_card(card_id, status="approved")
    if not batches:
        await message.answer(f"🛒 По карте id={card_id} нет одобренных заявок биржи.")
        return

    lines = [f"🛒 <b>Владельцы по карте</b> <code>{card_id}</code> (биржа):", ""]
    for batch in batches:
        owner = f"@{batch['username']}" if batch.get("username") else f"id{batch['user_id']}"
        lines.append(f"• 🆔 batch <code>{batch['batch_id']}</code> — {owner} × <b>{batch['qty']}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("print_ex"))
async def cmd_print_ex(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in legacy_config.ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /print_ex <batch_id>")
        return
    try:
        batch_id = int(parts[1].strip())
    except ValueError:
        await message.answer("batch_id должен быть числом.")
        return

    service = await AuctionWinnerService.create()
    batch = await service.exchange_batch(batch_id)
    if not batch:
        await message.answer(f"Не нашёл заявку биржи batch_id={batch_id}")
        return
    cards = await service.exchange_cards(batch_id)
    stats = await service.exchange_print_stats(batch_id)
    await message.answer(
        await _render_print_ex_text(batch, cards, stats),
        parse_mode="HTML",
        reply_markup=_kb_print_ex(batch_id),
    )


@router.callback_query(F.data.startswith("pex|"))
async def cb_print_ex(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id not in legacy_config.ADMINS:
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        _, action, batch_id_raw = split_callback_data(call.data or "", "|", 2)
        batch_id = int(batch_id_raw)
    except (ValueError, AttributeError):
        await call.answer("Неверные данные.", show_alert=True)
        return

    service = await AuctionWinnerService.create()
    batch = await service.exchange_batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    if action == "refresh":
        await _refresh_print_ex(call, service, batch)
        await call.answer("Обновлено.")
        return
    if action == "reset":
        await service.reset_exchange_print_stats(batch_id, updated_by=call.from_user.id)
        await _refresh_print_ex(call, service, batch)
        await call.answer("Сброшено.")
        return
    if action in {"set_winner", "set_price", "master"}:
        await state.set_state(PrintExStates.waiting_manual)
        await state.update_data(
            ex_batch_id=batch_id,
            ex_action=action,
            ex_msg_chat=call.message.chat.id,
            ex_msg_id=call.message.message_id,
        )
        if action == "set_winner":
            prompt = "Введи победителя: <code>@username</code> или <code>user_id</code>"
        elif action == "set_price":
            prompt = "Введи новую цену числом (без валюты)."
        else:
            prompt = (
                "🧩 <b>Мастер ручного итога</b>\n"
                "Отправь 2–3 строки:\n"
                "1) победитель: <code>@username</code> или <code>user_id</code>\n"
                "2) ссылка на биржу (t.me/...)\n"
                "3) цена (необязательно)\n"
            )
        await call.message.answer(prompt, parse_mode="HTML")
        await call.answer()
        return

    stats = await service.exchange_print_stats(batch_id)
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None
    winner_id = int(stats["manual_winner_id"]) if stats and stats.get("manual_winner_id") else None
    winner_name = (stats.get("manual_winner_name") or "").strip() if stats else ""
    if not winner_id and winner_name:
        winner_user = await service.user_by_username(winner_name)
        if winner_user:
            winner_id = int(winner_user["user_id"])
    price = stats.get("manual_price") if stats else None
    if price is None:
        price = batch.get("price")
    link = (stats.get("manual_link") or "").strip() if stats else ""

    if action in {"send_winner", "send_both"} and not winner_id:
        await call.answer("Сначала укажи победителя, который запускал бота.", show_alert=True)
        return

    owner_text = (
        "🛒 <b>Биржа: сделка</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"🏆 Победитель: {winner_name or (f'id{winner_id}' if winner_id else '—')}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )
    winner_text = (
        "🛒 <b>Биржа: ты победитель</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"👑 Владелец: {mention_html(owner_id, owner_username)}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )

    async def send(user_id: int, text: str) -> bool:
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
            return True
        except (TelegramForbiddenError, TelegramBadRequest):
            return False

    owner_ok: bool | None = None
    winner_ok: bool | None = None
    if action in {"send_owner", "send_both"}:
        owner_ok = await send(owner_id, owner_text)
    if action in {"send_winner", "send_both"} and winner_id:
        winner_ok = await send(winner_id, winner_text)

    owner_status = "—" if owner_ok is None else ("✅" if owner_ok else "❌")
    winner_status = "—" if winner_ok is None else ("✅" if winner_ok else "❌")
    await call.answer(f"Отправлено: владелец={owner_status} победитель={winner_status}")


@router.message(PrintExStates.waiting_manual)
async def ex_manual_input(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in legacy_config.ADMINS:
        await state.clear()
        return
    data = await state.get_data()
    batch_id = int(data["ex_batch_id"])
    action = str(data["ex_action"])
    text = (message.text or "").strip()
    service = await AuctionWinnerService.create()

    if action == "set_winner":
        winner_id, winner_name = _parse_winner(text)
        if winner_name and not winner_id:
            user = await service.user_by_username(winner_name)
            if user:
                winner_id = int(user["user_id"])
        await service.upsert_exchange_print_stats(
            batch_id,
            winner_id=winner_id,
            winner_name=winner_name,
            updated_by=message.from_user.id,
        )
    elif action == "set_price":
        price = int(re.sub(r"[^\d]", "", text) or "0")
        await service.upsert_exchange_print_stats(
            batch_id,
            price=price,
            updated_by=message.from_user.id,
        )
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        winner_line = lines[0] if lines else ""
        link_line = lines[1] if len(lines) >= 2 else ""
        price_line = lines[2] if len(lines) >= 3 else ""
        winner_id, winner_name = _parse_winner(winner_line)
        if winner_name and not winner_id:
            user = await service.user_by_username(winner_name)
            if user:
                winner_id = int(user["user_id"])
        price = int(re.sub(r"[^\d]", "", price_line) or "0") if price_line else None
        await service.upsert_exchange_print_stats(
            batch_id,
            winner_id=winner_id,
            winner_name=winner_name,
            link=link_line,
            price=price,
            updated_by=message.from_user.id,
        )

    await state.clear()
    await message.answer("✅ Сохранено. Теперь жми 🔄 Обновить в /print_ex.")
