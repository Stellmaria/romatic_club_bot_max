"""Exchange print workflow and missed-mailing diagnostics."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.settings import (
    ADMINS,
)
from bot.core.time import to_moscow
from bot.telegram.states import PrintExStates

from .common import (
    get_exchange_batch_by_id,
    get_exchange_batches_for_card,
    get_exchange_cards_for_batch,
    get_exchange_print_stats,
    get_print_win_missed_for_day,
    reset_exchange_print_stats,
    upsert_exchange_print_stats,
)


async def cmd_print_win_missed(message: types.Message) -> None:
    args = (message.text or "").split(maxsplit=1)

    # дата по умолчанию: сегодня (по МСК)
    msk = ZoneInfo("Europe/Moscow")
    today_msk = datetime.now(msk).date()

    if len(args) == 1:
        target_date = today_msk
    else:
        raw = args[1].strip()
        parsed: date | None = None

        for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m"):
            try:
                d = datetime.strptime(raw, fmt).date()
                if fmt == "%d.%m":
                    d = d.replace(year=today_msk.year)
                parsed = d
                break
            except ValueError:
                continue

        if not parsed:
            await message.answer("❌ Неверный формат даты. Примеры: 20.01.2026 или 20.01")
            return

        target_date = parsed

    rows = await get_print_win_missed_for_day(target_date)

    if not rows:
        await message.answer(
            f"✅ За {target_date.strftime('%d.%m.%Y')} пропусков /print_win не найдено."
        )
        return

    lines = [
        f"⚠️ За {target_date.strftime('%d.%m.%Y')} НЕ было рассылок /print_win (только лоты из расписания):",
        "",
    ]

    for r in rows:
        auction_id = int(r["auction_id"])
        st = r.get("start_time")
        t = to_moscow(st).strftime("%H:%M") if isinstance(st, datetime) else "??:??"

        bids_count = int(r.get("bids_count") or 0)
        no_bids_mark = " 😿 без ставок" if bids_count == 0 else ""

        hero = (r.get("hero_name") or "").strip()
        card = (r.get("card_name") or "").strip()
        lot_name = f" — {hero} • {card}" if (hero or card) else ""

        lines.append(f"{t} — {auction_id}{no_bids_mark}{lot_name}")

    # чтобы не упереться в лимит 4096
    text = "\n".join(lines)
    if len(text) <= 3800:
        await message.answer(text)
        return

    # режем по строкам
    chunk: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > 3800:
            await message.answer("\n".join(chunk))
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk))


async def cmd_ex_owners(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /ex_owners <card_id>")
        return

    try:
        card_id = int(parts[1].strip())
    except Exception:
        await message.answer("card_id должен быть числом.")
        return

    batches = await get_exchange_batches_for_card(card_id, status="approved")
    if not batches:
        await message.answer(f"🛒 По карте id={card_id} нет одобренных заявок биржи.")
        return

    lines = [f"🛒 <b>Владельцы по карте</b> <code>{card_id}</code> (биржа):", ""]
    for r in batches:
        uname = f"@{r['username']}" if r.get("username") else f"id{r['user_id']}"
        lines.append(f"• 🆔 batch <code>{r['batch_id']}</code> — {uname} × <b>{r['qty']}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


def _mention_html(user_id: int, username: str | None) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">id{user_id}</a>'


def _kb_print_ex(batch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📨 Отправить обоим", callback_data=f"pex|send_both|{batch_id}")
    kb.button(text="👑 Отправить владельцу", callback_data=f"pex|send_owner|{batch_id}")
    kb.button(text="🏆 Отправить победителю", callback_data=f"pex|send_winner|{batch_id}")
    kb.button(text="🏆 Сменить победителя", callback_data=f"pex|set_winner|{batch_id}")
    kb.button(text="💰 Сменить цену", callback_data=f"pex|set_price|{batch_id}")
    kb.button(text="♻️ Сброс", callback_data=f"pex|reset|{batch_id}")
    kb.button(text="🧩 Мастер", callback_data=f"pex|master|{batch_id}")
    kb.button(text="🔄 Обновить", callback_data=f"pex|refresh|{batch_id}")
    kb.adjust(1, 2, 2, 2, 1)
    return kb.as_markup()


async def _render_print_ex_text(batch: dict, cards: list[dict], st: dict | None) -> str:
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None
    owner = _mention_html(owner_id, owner_username)

    winner_id = int(st["manual_winner_id"]) if st and st.get("manual_winner_id") else None
    winner_name = (st.get("manual_winner_name") or "").strip() if st else ""
    winner_ref = (
        _mention_html(winner_id, winner_name)
        if winner_id
        else (f"@{winner_name}" if winner_name else "—")
    )

    price = st.get("manual_price") if st else None
    if price is None:
        price = batch.get("price")
    link = (st.get("manual_link") or "").strip() if st else ""
    if not link:
        link = "—"

    cards_lines = []
    for c in cards:
        title = f"{c.get('hero_name') or ''} — {c.get('card_name')}".strip(" —")
        cards_lines.append(f"• {title} (id={c['card_id']}) × {c['qty']}")

    cards_block = "\n".join(cards_lines) if cards_lines else "—"

    return (
        f"🛒 <b>PRINT EX</b>\n"
        f"🆔 batch_id: <code>{batch['batch_id']}</code>\n"
        f"Статус: <b>{batch.get('status', '?')}</b>\n\n"
        f"👑 Владелец: {owner}\n"
        f"🏆 Победитель: {winner_ref}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link}\n\n"
        f"<b>Состав:</b>\n{cards_block}"
    )


async def cmd_print_ex(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /print_ex <batch_id>")
        return

    try:
        batch_id = int(parts[1].strip())
    except Exception:
        await message.answer("batch_id должен быть числом.")
        return

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer(f"Не нашёл заявку биржи batch_id={batch_id}")
        return

    cards = await get_exchange_cards_for_batch(batch_id)
    st = await get_exchange_print_stats(batch_id)

    text = await _render_print_ex_text(batch, cards, st)
    await message.answer(text, parse_mode="HTML", reply_markup=_kb_print_ex(batch_id))


async def cb_print_ex(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id not in ADMINS:
        await call.answer("Нет доступа.", show_alert=True)
        return

    _, action, bid_s = (call.data or "").split("|", 2)
    batch_id = int(bid_s)

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    async def _refresh() -> None:
        cards = await get_exchange_cards_for_batch(batch_id)
        st = await get_exchange_print_stats(batch_id)
        text = await _render_print_ex_text(batch, cards, st)
        try:
            await call.message.edit_text(
                text, parse_mode="HTML", reply_markup=_kb_print_ex(batch_id)
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                raise

    if action == "refresh":
        await _refresh()
        await call.answer("Обновлено.")
        return

    if action == "reset":
        await reset_exchange_print_stats(batch_id, updated_by=call.from_user.id)
        await _refresh()
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
            await call.message.answer(
                "Введи победителя: <code>@username</code> или <code>user_id</code>",
                parse_mode="HTML",
            )
        elif action == "set_price":
            await call.message.answer("Введи новую цену числом (без валюты).", parse_mode="HTML")
        else:
            await call.message.answer(
                "🧩 <b>Мастер ручного итога</b>\n"
                "Отправь 2–3 строки:\n"
                "1) победитель: <code>@username</code> или <code>user_id</code>\n"
                "2) ссылка на биржу (t.me/...)\n"
                "3) цена (необязательно)\n",
                parse_mode="HTML",
            )
        await call.answer()
        return

    # SEND
    st = await get_exchange_print_stats(batch_id)
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None

    winner_id = int(st["manual_winner_id"]) if st and st.get("manual_winner_id") else None
    winner_name = (st.get("manual_winner_name") or "").strip() if st else ""
    price = st.get("manual_price") if st else None
    if price is None:
        price = batch.get("price")
    link = (st.get("manual_link") or "").strip() if st else ""

    if action in {"send_winner", "send_both"} and not (winner_id or winner_name):
        await call.answer("Сначала укажи победителя (🏆).", show_alert=True)
        return

    text_owner = (
        f"🛒 <b>Биржа: сделка</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"🏆 Победитель: {winner_name or (f'id{winner_id}' if winner_id else '—')}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )
    text_winner = (
        f"🛒 <b>Биржа: ты победитель</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"👑 Владелец: {_mention_html(owner_id, owner_username)}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )

    async def _send(uid: int, txt: str) -> bool:
        try:
            await bot.send_message(uid, txt, parse_mode="HTML")
            return True
        except (TelegramForbiddenError, TelegramBadRequest):
            return False

    ok1 = ok2 = True
    if action in {"send_owner", "send_both"}:
        ok1 = await _send(owner_id, text_owner)
    if action in {"send_winner", "send_both"}:
        if winner_id:
            ok2 = await _send(int(winner_id), text_winner)

    await call.answer(
        f"Отправлено: владелец={'✅' if ok1 else '❌'} победитель={'✅' if ok2 else '❌'}"
    )


async def ex_manual_input(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMINS:
        await state.clear()
        return

    data = await state.get_data()
    batch_id = int(data["ex_batch_id"])
    action = data["ex_action"]

    text = (message.text or "").strip()

    def _parse_winner(s: str) -> tuple[int | None, str | None]:
        s = s.strip()
        if not s:
            return None, None
        if s.startswith("@"):
            return None, s.lstrip("@")
        if s.isdigit():
            return int(s), None
        return None, s  # как есть

    if action == "set_winner":
        wid, wname = _parse_winner(text)
        await upsert_exchange_print_stats(
            batch_id, winner_id=wid, winner_name=wname, updated_by=message.from_user.id
        )

    elif action == "set_price":
        try:
            p = int(re.sub(r"[^\d]", "", text) or "0")
        except Exception:
            p = 0
        await upsert_exchange_print_stats(batch_id, price=p, updated_by=message.from_user.id)

    else:  # master
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        winner_line = lines[0] if len(lines) >= 1 else ""
        link_line = lines[1] if len(lines) >= 2 else ""
        price_line = lines[2] if len(lines) >= 3 else ""

        wid, wname = _parse_winner(winner_line)
        p = None
        if price_line:
            try:
                p = int(re.sub(r"[^\d]", "", price_line) or "0")
            except Exception:
                p = None

        await upsert_exchange_print_stats(
            batch_id,
            winner_id=wid,
            winner_name=wname,
            link=link_line,
            price=p,
            updated_by=message.from_user.id,
        )

    await state.clear()
    await message.answer("✅ Сохранено. Теперь жми 🔄 Обновить в /print_ex.")
