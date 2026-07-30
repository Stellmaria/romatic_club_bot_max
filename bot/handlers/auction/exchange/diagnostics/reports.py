from __future__ import annotations

import html
import json
from datetime import datetime

from aiogram import F, Router
from aiogram.types import Message

from bot.handlers.admin.action_support.compat import _safe_user_mention
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.exchange_diagnostics import ExchangeDiagnosticsService

router = Router(name="auction_exchange_diagnostics_reports")

@router.message(F.text.regexp(r"^/ex_lot\s+\d+$"))
@admin_only
async def cmd_ex_lot(message: Message):
    diagnostics = await ExchangeDiagnosticsService.create()
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    batch = await diagnostics.batch(batch_id)
    if not batch:
        await message.answer(f"🛒 Заявка биржи <code>{batch_id}</code> не найдена.", parse_mode="HTML")
        return

    owner_id = int(batch.get("user_id") or 0)
    owner = await diagnostics.user_by_id(owner_id)
    owner_un = ((owner or {}).get("username") or "").strip() or None

    # безопасный mention (если где-то уже есть _safe_user_mention)
    try:
        owner_txt = _safe_user_mention(owner_id, owner_un)  # type: ignore[name-defined]
    except Exception:
        uname = f"@{html.escape(owner_un)}" if owner_un else str(owner_id)
        owner_txt = f"<a href='tg://user?id={owner_id}'>{uname}</a>"

    status = html.escape(str(batch.get("status") or "—"))
    mode = html.escape(str(batch.get("mode") or "—"))
    deck_id = batch.get("deck_id")
    price = batch.get("price")
    currency = html.escape(str(batch.get("currency") or "—"))
    created_at = batch.get("created_at")

    def _fmt_dt(dt_obj: object) -> str:
        if isinstance(dt_obj, datetime):
            return dt_obj.strftime("%d.%m.%Y %H:%M")
        return "—" if dt_obj is None else html.escape(str(dt_obj))

    # список карточек в заявке
    items = await diagnostics.batch_items(batch_id)
    items_lines: list[str] = []
    for it in (items or [])[:25]:
        cid = it.get("card_id")
        cn = (it.get("card_name") or "").strip()
        hn = (it.get("hero_name") or "").strip()
        title = " — ".join([x for x in [hn, cn] if x]) or "—"
        prefix = f"<code>{cid}</code> " if cid else ""
        items_lines.append(f"• {prefix}{html.escape(title)}")
    if items and len(items) > 25:
        items_lines.append(f"… и ещё {len(items) - 25} шт.")

    items_block = "\n".join(items_lines) if items_lines else "—"

    # стандартный аукцион по владельцу (активное/расписание/модерация)
    lots = await diagnostics.standard_lots_by_owner(owner_id)

    def _status_ru(st: str) -> str:
        s = (st or "").lower()
        return {
            "pending": "на модерации",
            "approved": "одобрено",
            "scheduled": "в расписании",
            "active": "идёт",
            "finished": "завершён",
            "rejected": "отклонён",
        }.get(s, s or "—")

    active_statuses = {"pending", "approved", "scheduled", "active"}
    std_active = [r for r in (lots or []) if (str(r.get("status") or "").lower() in active_statuses)]

    std_lines: list[str] = []
    if std_active:
        for r in std_active[:25]:
            aid = r.get("auction_id")
            st = _status_ru(str(r.get("status") or ""))
            cn = (r.get("card_name") or "").strip()
            hn = (r.get("hero_name") or "").strip()
            lot_title = " — ".join([x for x in [hn, cn] if x]) or "—"
            st_time = _fmt_dt(r.get("start_time"))
            std_lines.append(f"• <code>{aid}</code> | {html.escape(st)} | {html.escape(lot_title)} | {st_time}")
        if len(std_active) > 25:
            std_lines.append(f"… и ещё {len(std_active) - 25} шт.")
    else:
        std_lines.append("—")

    text = (
            f"🛒 <b>Биржа: проверка лота</b>\n"
            f"Batch: <code>{batch_id}</code> | статус: <b>{status}</b>\n"
            f"Владелец заявки: {owner_txt} (id:<code>{owner_id}</code>)\n"
            f"Колода: <code>{deck_id}</code> | mode: <code>{mode}</code>\n"
            f"Цена: <code>{price if price is not None else '—'}</code> {currency}\n"
            f"Создано: <code>{_fmt_dt(created_at)}</code> (как в БД)\n\n"
            f"🎴 <b>Состав заявки (первые 25)</b>:\n{items_block}\n\n"
            f"⭐ <b>Стандартный аукцион этого пользователя (активное/расписание/модерация)</b>:\n"
            + "\n".join(std_lines)
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text.regexp(r"^/ex_user\s+\S+$"), F.chat.type == "private")
@admin_only
async def cmd_ex_user(message: Message):
    diagnostics = await ExchangeDiagnosticsService.create()
    parts = (message.text or "").split(maxsplit=1)
    raw = (parts[1] if len(parts) > 1 else "").strip()

    if not raw:
        await message.answer("Формат: <code>/ex_user @username</code> или <code>/ex_user 123456789</code>",
                             parse_mode="HTML")
        return

    # 1) если цифры — считаем что это user_id
    u = None
    uid = None

    if raw.isdigit():
        uid = int(raw)
        u = await diagnostics.user_by_id(uid)
        if not u:
            await message.answer(f"Пользователь с id <code>{uid}</code> не найден в БД.", parse_mode="HTML")
            return
    else:
        # 2) иначе — username
        username = raw.lstrip("@").strip().lower()
        if not username:
            await message.answer("Формат: <code>/ex_user @username</code> или <code>/ex_user 123456789</code>",
                                 parse_mode="HTML")
            return

        u = await diagnostics.user_by_username(username)
        if not u:
            await message.answer(f"Пользователь @{html.escape(username)} не найден в БД.", parse_mode="HTML")
            return

        uid = int(u["user_id"])

    uname = ((u.get("username") or "").strip() or None)

    # Read model is assembled by the diagnostics service.
    cards_stat = await diagnostics.user_card_stats(uid)
    batches_stat = await diagnostics.user_batch_stats(uid)

    def _stat_line(rows, key_cnt: str) -> str:
        if not rows:
            return "—"
        return ", ".join(
            f"{html.escape(str(r.get('status') or '—'))}: <code>{int(r.get(key_cnt) or 0)}</code>"
            for r in rows
        )

    batches = await diagnostics.recent_user_batches(uid, limit=12)


    batch_lines: list[str] = []
    for b in batches or []:
        bid = int(b["batch_id"])
        b_status = html.escape(str(b.get("status") or "—"))
        b_deck = b.get("deck_id")
        b_mode = html.escape(str(b.get("mode") or "—"))
        b_price = b.get("price")
        b_cur = html.escape(str(b.get("currency") or "—"))

        items = await diagnostics.batch_items(bid)
        short_items: list[str] = []
        for it in (items or [])[:10]:
            cn = (it.get("card_name") or "").strip()
            hn = (it.get("hero_name") or "").strip()
            short_items.append(" — ".join([x for x in [hn, cn] if x]) or "—")

        items_txt = "; ".join(html.escape(x) for x in short_items) if short_items else "—"
        if items and len(items) > 10:
            items_txt += f" …(+{len(items) - 10})"

        batch_lines.append(
            f"• batch <code>{bid}</code> | {b_status} | deck <code>{b_deck}</code> | mode <code>{b_mode}</code> | "
            f"цена <code>{b_price if b_price is not None else '—'}</code> {b_cur}\n"
            f"  🎴 {items_txt}"
        )

    who = _safe_user_mention(uid, uname)

    text = (
            f"🛒 <b>Биржа: пользователь</b>\n"
            f"Пользователь: {who} (id:<code>{uid}</code>)\n\n"
            f"📦 <b>Заявки (batch) по статусам</b>: {_stat_line(batches_stat, 'batches_cnt')}\n"
            f"🎴 <b>Карты (items) по статусам</b>: {_stat_line(cards_stat, 'cards_cnt')}\n\n"
            f"📌 <b>Последние заявки (до 12)</b>:\n"
            + ("\n".join(batch_lines) if batch_lines else "—")
            + "\n\n"
              f"Подробно по конкретной заявке: <code>/ex_lot &lt;batch_id&gt;</code>"
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text.regexp(r"^/ex_dump(?:\s+\d+)?$"), F.chat.type == "private")
@admin_only
async def cmd_ex_dump(message: Message):
    """Сводка биржи по пользователям и одному proof-файлу."""
    diagnostics = await ExchangeDiagnosticsService.create()

    parts = (message.text or "").split()
    page = 1
    if len(parts) >= 2 and parts[1].isdigit():
        page = max(1, int(parts[1]))

    per_page = 8
    offset = (page - 1) * per_page

    def _compress_ranges(ids: list[int]) -> str:
        if not ids:
            return "—"
        ids = sorted({int(x) for x in ids})
        start = prev = ids[0]
        out: list[str] = []
        for x in ids[1:]:
            if x == prev + 1:
                prev = x
                continue
            out.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = x
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        return ", ".join(out)

    def _short_proof(s: str) -> str:
        s = (s or "").strip()
        if not s or s.upper() == "NO_PROOF":
            return "NO_PROOF"
        return (s[:30] + "…" + s[-10:]) if len(s) > 50 else s

    # 1) total groups (user_id + proof) — без плейсхолдеров
    total = await diagnostics.dump_group_count()
    if total == 0:
        await message.answer("🛒 На бирже нет заявок (pending/approved).", parse_mode="HTML")
        return

    pages = (total + per_page - 1) // per_page
    if page > pages:
        page = pages
        offset = (page - 1) * per_page

    # 2) page of groups + aggregated cards in one query (без N+1)
    rows = await diagnostics.dump_groups(limit=per_page, offset=offset)


    header = (
        f"🛒 <b>Биржа: кто сколько подал (по одному пруфу)</b>\n"
        f"Страница: <b>{page}</b>/<b>{pages}</b> | групп: <b>{total}</b>\n"
        f"Команда: <code>/ex_dump</code> | <code>/ex_dump 2</code>\n"
    )

    out = header

    for r in rows or []:
        user_id = int(r.get("user_id") or 0)
        username = (r.get("username") or "").strip() or None
        proof = (r.get("proof") or "NO_PROOF").strip()
        batch_ids = list(r.get("batch_ids") or [])
        batches_cnt = int(r.get("batches_cnt") or 0)
        items_total = int(r.get("items_total") or 0)

        # cards может прийти как list[dict] (если настроен json codec) ИЛИ как str
        cards_raw = r.get("cards")
        if cards_raw is None:
            cards: list[dict] = []
        elif isinstance(cards_raw, str):
            try:
                parsed = json.loads(cards_raw)
                cards = parsed if isinstance(parsed, list) else []
            except Exception:
                cards = []
        elif isinstance(cards_raw, list):
            cards = [x for x in cards_raw if isinstance(x, dict)]
        else:
            cards = []

        lots_ranges = _compress_ranges([int(x) for x in batch_ids])
        proof_label = _short_proof(proof)
        who = _safe_user_mention(user_id, username)

        lines = [
            "",
            f"👤 {who} (id:<code>{user_id}</code>)",
            f"📸 Пруф: <code>{html.escape(proof_label)}</code>",
            f"🧾 Лоты (batch_id): <code>{html.escape(lots_ranges)}</code> | шт: <b>{batches_cnt}</b>",
            f"🎴 Всего позиций (items): <b>{items_total}</b> | уникальных карт: <b>{len(cards)}</b>",
            "🎴 Карты (qty = сколько одинаковых подано):",
        ]

        show_limit = 25
        for c in cards[:show_limit]:
            if not isinstance(c, dict):
                continue
            qty = int(c.get("qty") or 0)
            cid = c.get("card_id")
            hn = (c.get("hero_name") or "").strip()
            cn = (c.get("card_name") or "").strip()
            title = " — ".join([x for x in [hn, cn] if x]) or "—"
            cid_txt = f"<code>{cid}</code> " if cid else ""
            lines.append(f"• ×<b>{qty}</b> | {cid_txt}{html.escape(title)}")

        if len(cards) > show_limit:
            lines.append(f"… и ещё <b>{len(cards) - show_limit}</b> разных карт (Telegram не резиновый).")

        block = "\n".join(lines)

        # лимит сообщений Telegram
        if len(out) + len(block) > 3500:
            await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
            out = header + block
        else:
            out += block

    if out.strip():
        await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text.regexp(r"^/ex_proof\s+\d+$"), F.chat.type == "private")
@admin_only
async def cmd_ex_proof(message: Message):
    diagnostics = await ExchangeDiagnosticsService.create()
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    b = await diagnostics.batch(batch_id)
    if not b:
        await message.answer(f"🛒 Заявка биржи <code>{batch_id}</code> не найдена.", parse_mode="HTML")
        return

    proof = (b.get("proof_photo_id") or "").strip()
    if (not proof) or (proof.upper() == "NO_PROOF"):
        await message.answer(f"📸 Пруф для <code>{batch_id}</code> не прикреплён.", parse_mode="HTML")
        return

    caption = (
        f"📸 Пруф заявки биржи <code>{batch_id}</code>\n"
        f"<code>{html.escape(proof)}</code>"
    )

    # пытаемся отправить как фото/видео/анимацию/документ
    try:
        await message.answer_photo(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_video(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_animation(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_document(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    await message.answer(
        f"❌ Не удалось отправить пруф для <code>{batch_id}</code>.\n"
        f"Скорее всего file_id битый/не того типа:\n<code>{html.escape(proof)}</code>",
        parse_mode="HTML",
    )
