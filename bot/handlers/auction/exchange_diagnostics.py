from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.admin.action_support.exchange import _safe_user_mention
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import short_media_id
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.exchange_diagnostics import ExchangeDiagnosticsQueries
from bot.core.legacy_config import legacy_config
from db.cards import get_deck_by_id
from db.exchange import (
    get_exchange_batch_by_id,
    get_exchange_items_by_batch_id,
    mark_exchange_manual_sent,
    set_exchange_manual_winner,
)
from db.users import (
    get_user,
    get_user_by_username,
)

from bot.handlers.auction.exchange import currency_to_emoji

router = Router(name="auction_exchange_diagnostics")


@router.message(Command("print_ex_multi"))
@admin_only
async def cmd_print_ex_multi(message: types.Message):
    bot = message.bot
    parts = (message.text or "").split()
    args = parts[1:]

    if not args:
        await message.answer("Формат: /print_ex_multi <winner_id|@username> <batch_id> <batch_id> ...")
        return

    winner_id: int | None = None
    winner_un: str | None = None

    # 1) пробуем взять победителя из reply (если админ ответил на пересланное сообщение победителя)
    if message.reply_to_message:
        u = getattr(message.reply_to_message, "forward_from", None) or getattr(message.reply_to_message, "from_user",
                                                                               None)
        if u:
            winner_id = u.id
            winner_un = u.username or u.full_name

    # 2) если победителя нет из reply, читаем первым аргументом
    batch_tokens: list[str]
    if winner_id is None:
        if len(args) < 2:
            await message.answer("Нужно: /print_ex_multi <winner_id|@username> <batch_ids...>")
            return

        winner_token = args[0].strip()
        batch_tokens = args[1:]

        if winner_token.startswith("@"):
            uname = winner_token[1:]
            u = await get_user_by_username(uname)
            if not u:
                await message.answer("Победитель по @username не найден в БД.")
                return
            winner_id = int(u["user_id"])
            winner_un = u.get("username") or str(winner_id)
        elif winner_token.isdigit():
            winner_id = int(winner_token)
            u = await get_user(winner_id)
            winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)
        else:
            await message.answer("Победитель должен быть @username или числовой id.")
            return
    else:
        batch_tokens = args

    batch_ids = _parse_batch_ids(batch_tokens)
    if not batch_ids:
        await message.answer("Не вижу batch-id. Пример: /print_ex_multi 123456 149 143 122")
        return

    # нормализуем имя победителя
    if not winner_un and winner_id:
        u = await get_user(winner_id)
        winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)

    winner_mention = _safe_user_mention(winner_id, winner_un or str(winner_id))
    moderator = admin_tag(message.from_user)
    thanks_kb = await build_thanks_kb(batch_ids[0], moderator)

    missing: list[int] = []
    lots: list[dict] = []
    owner_username: dict[int, str] = {}

    for bid in batch_ids:
        batch = await get_exchange_batch_by_id(bid)
        if not batch:
            missing.append(bid)
            continue

        items = await get_exchange_items_by_batch_id(bid)
        owner_id = int(batch.get("user_id") or 0)

        uo = await get_user(owner_id)
        owner_un = (uo.get("username") or uo.get("full_name")) if uo else str(owner_id)
        owner_username[owner_id] = owner_un

        deck_id = int(batch.get("deck_id") or 0)
        deck = await get_deck_by_id(deck_id) if deck_id else None
        deck_name = deck["name"] if deck else (str(deck_id) if deck_id else "—")

        cur = (batch.get("currency") or "diamonds").strip()
        price = int(batch.get("price") or 0)
        mode = (batch.get("mode") or "").strip()

        card_count = 0
        for it in items or []:
            card_count += int(it.get("qty") or 1)

        lots.append(
            {
                "batch_id": bid,
                "owner_id": owner_id,
                "owner_mention": _safe_user_mention(owner_id, owner_un),
                "deck_name": deck_name,
                "mode": mode,
                "mode_label": _ex_mode_label(mode),
                "currency": cur,
                "price": price,
                "items": items,
                "cards_preview": _cards_preview(items),
                "card_count": card_count,
            }
        )

    if not lots:
        await message.answer("Не нашла ни одного валидного batch-id.")
        return

    # группировка оплат и лотов
    pay_map: dict[tuple[int, str], int] = defaultdict(int)
    lots_by_owner: dict[int, list[dict]] = defaultdict(list)
    total_cards = 0
    for lot in lots:
        pay_map[(lot["owner_id"], lot["currency"])] += lot["price"]
        lots_by_owner[lot["owner_id"]].append(lot)
        total_cards += lot["card_count"]

    # платежи победителю
    pay_lines: list[str] = []
    for (oid, cur), amount in sorted(pay_map.items(), key=lambda x: (-x[1], x[0][0])):
        om = _safe_user_mention(oid, owner_username.get(oid, str(oid)))
        pay_lines.append(f"• {om}: <b>{amount}</b> {currency_to_emoji(cur)}")

    # состав по лотам
    lot_lines: list[str] = []
    for lot in lots:
        price_line = f"<b>{lot['price']}</b> {currency_to_emoji(lot['currency'])}"
        lot_lines.append(
            f"• <code>{lot['batch_id']}</code> — {price_line} • {lot['mode_label']} • {lot['deck_name']}\n"
            f"  Владелец: {lot['owner_mention']}\n"
            f"  Карты: {lot['cards_preview']}"
        )

    winner_text = (
            "🎉 <b>Биржа</b> • ты выбран победителем по нескольким лотам\n"
            f"Победитель: {winner_mention}\n"
            f"Лотов: <b>{len(lots)}</b> • Карт: <b>{total_cards}</b>\n\n"
            "💳 <b>Кому и сколько платить:</b>\n"
            + "\n".join(pay_lines)
            + "\n\n📦 <b>Состав по лотам:</b>\n"
            + "\n".join(lot_lines)
            + "\n\n"
              f"🛡️ <b>Модератор биржи:</b> {moderator}\n"
              "Если хочешь, можешь сказать спасибо модератору ниже ❤️\n"
    )

    # отправка победителю
    ok_winner = True
    try:
        await bot.send_message(winner_id, winner_text, parse_mode="HTML", reply_markup=thanks_kb)
    except Exception:
        ok_winner = False

    # отправка каждому владельцу
    owners_ok = 0
    owners_fail = 0

    for owner_id, owner_lots in lots_by_owner.items():
        totals_by_cur: dict[str, int] = defaultdict(int)
        owner_cards = 0
        for lot in owner_lots:
            totals_by_cur[lot["currency"]] += lot["price"]
            owner_cards += lot["card_count"]

        totals_line = ", ".join(f"<b>{amt}</b> {currency_to_emoji(cur)}" for cur, amt in totals_by_cur.items())

        owner_lot_lines: list[str] = []
        for lot in owner_lots:
            price_line = f"<b>{lot['price']}</b> {currency_to_emoji(lot['currency'])}"
            owner_lot_lines.append(
                f"• <code>{lot['batch_id']}</code> — {price_line} • {lot['mode_label']} • {lot['deck_name']}\n"
                f"  Карты: {lot['cards_preview']}"
            )

        owner_text = (
                "✅ <b>Биржа</b> • у тебя выкупают несколько лотов\n"
                f"Покупатель: {winner_mention}\n"
                f"Лотов: <b>{len(owner_lots)}</b> • Карт: <b>{owner_cards}</b>\n"
                f"💰 <b>К оплате тебе:</b> {totals_line}\n\n"
                "📦 <b>Состав:</b>\n"
                + "\n".join(owner_lot_lines)
                + "\n\n"
                  f"🛡️ <b>Модератор биржи:</b> {moderator}\n"
                  "Если хочешь, можешь сказать спасибо модератору ниже ❤️"
        )

        try:
            await bot.send_message(owner_id, owner_text, parse_mode="HTML", reply_markup=thanks_kb)
            owners_ok += 1
        except Exception:
            owners_fail += 1

    # записываем победителя и помечаем как разосланное
    for lot in lots:
        try:
            await set_exchange_manual_winner(
                batch_id=int(lot["batch_id"]),
                winner_id=int(winner_id),
                winner_username=(winner_un or str(winner_id)),
                admin_id=message.from_user.id,
            )
        except Exception:
            pass
        try:
            await mark_exchange_manual_sent(int(lot["batch_id"]))
        except Exception:
            pass

    report = f"✅ /print_ex_multi готово. winner_ok={ok_winner}, owners_ok={owners_ok}, owners_fail={owners_fail}"
    if missing:
        report += f"\n⚠️ Не найдено batch-id: {', '.join(map(str, missing))}"
    await message.answer(report)


def _ex_mode_label(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("whole_deck", "deck"):
        return "Колода целиком"
    if m in ("card",):
        return "Карта"
    if m == "deck_split":
        return "Карта"
    return mode or "—"


def _cards_preview(items: list[dict], limit: int = 6) -> str:
    names: list[str] = []
    for it in items or []:
        hero = (it.get("hero_name") or "").strip()
        card = (it.get("card_name") or "").strip()
        qty = int(it.get("qty") or 1)
        base = f"{hero} — {card}".strip(" —")
        if not base:
            base = "—"
        if qty > 1:
            base = f"{base} ×{qty}"
        names.append(base)

    if not names:
        return "—"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" … +{len(names) - limit}"


def _parse_batch_ids(tokens: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for t in tokens:
        for part in (t or "").replace(";", ",").split(","):
            s = part.strip()
            if not s:
                continue
            if s.isdigit():
                i = int(s)
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out


# --- ADMIN: биржа -> владелец + проверка стандартного аука ---
@router.message(F.text.regexp(r"^/ex_lot\s+\d+$"))
@admin_only
async def cmd_ex_lot(message: Message):
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer(f"🛒 Заявка биржи <code>{batch_id}</code> не найдена.", parse_mode="HTML")
        return

    owner_id = int(batch.get("user_id") or 0)
    owner = await get_user(owner_id)
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
    items = await get_exchange_items_by_batch_id(batch_id)
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
    queries = await ExchangeDiagnosticsQueries.create()
    lots = await queries.standard_lots_for_owner(owner_id)

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
        u = await get_user(uid)
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

        u = await get_user_by_username(username)
        if not u:
            await message.answer(f"Пользователь @{html.escape(username)} не найден в БД.", parse_mode="HTML")
            return

        uid = int(u["user_id"])

    uname = ((u.get("username") or "").strip() or None)

    # сколько карточек на бирже (по exchange_items) + сколько заявок (по exchange_batches)
    queries = await ExchangeDiagnosticsQueries.create()
    cards_stat = await queries.user_cards_stats(uid)
    batches_stat = await queries.user_batches_stats(uid)

    def _stat_line(rows, key_cnt: str) -> str:
        if not rows:
            return "—"
        return ", ".join(
            f"{html.escape(str(r.get('status') or '—'))}: <code>{int(r.get(key_cnt) or 0)}</code>"
            for r in rows
        )

    batches = await queries.recent_user_batches(uid)

    batch_lines: list[str] = []
    for b in batches or []:
        bid = int(b["batch_id"])
        b_status = html.escape(str(b.get("status") or "—"))
        b_deck = b.get("deck_id")
        b_mode = html.escape(str(b.get("mode") or "—"))
        b_price = b.get("price")
        b_cur = html.escape(str(b.get("currency") or "—"))

        items = await get_exchange_items_by_batch_id(bid)
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
    """
    Админ-команда: сводка биржи по пользователям и ОДНОМУ пруфу (одно фото = пачка лотов).
    /ex_dump
    /ex_dump 2
    """
    import json

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

    # 1) total groups (user_id + proof)
    queries = await ExchangeDiagnosticsQueries.create()
    total = await queries.active_submission_group_count()
    if total == 0:
        await message.answer("🛒 На бирже нет заявок (pending/approved).", parse_mode="HTML")
        return

    pages = (total + per_page - 1) // per_page
    if page > pages:
        page = pages
        offset = (page - 1) * per_page

    # 2) page of groups + aggregated cards in one query (без N+1)
    rows = await queries.active_submission_groups(
        limit=per_page,
        offset=offset,
    )

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
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    b = await get_exchange_batch_by_id(batch_id)
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


def _short_media(v: object) -> str:
    # чтобы file_id не раздувал логи
    return short_media_id(v) if "short_media_id" in globals() else (str(v)[:12] + "…" if v else "—")


@router.message(F.text.regexp(r"^/dup_user_cards(?:\s+.+)?$"), F.chat.type == "private")
@admin_only
async def cmd_dup_user_cards(message: Message):
    """
    Пересечения "биржа + стандарт" ТОЛЬКО для одного и того же пользователя (user_id) и одной и той же card_id.
    /dup_user_cards
    /dup_user_cards <user_id>
    /dup_user_cards card <card_id>
    """
    parts = (message.text or "").split()

    user_id_filter: int | None = None
    card_id_filter: int | None = None

    # парсинг аргументов без истерик
    if len(parts) >= 2:
        if parts[1].isdigit():
            user_id_filter = int(parts[1])
        elif parts[1].lower() == "card" and len(parts) >= 3 and parts[2].isdigit():
            card_id_filter = int(parts[2])

    def _ids_preview(ids: list[int] | None, limit: int = 10) -> str:
        if not ids:
            return "—"
        ids2 = [int(x) for x in ids if x is not None][:limit]
        tail = "" if len(ids) <= limit else f" …(+{len(ids) - limit})"
        return ", ".join(str(x) for x in ids2) + tail

    queries = await ExchangeDiagnosticsQueries.create()
    rows = await queries.duplicate_user_cards(
        user_id=user_id_filter,
        card_id=card_id_filter,
    )

    if not rows:
        extra = ""
        if user_id_filter:
            extra = f" по user_id <code>{user_id_filter}</code>"
        if card_id_filter:
            extra = f" по card_id <code>{card_id_filter}</code>"
        await message.answer(f"✅ Пересечений (тот же пользователь + та же card_id) не найдено{extra}.",
                             parse_mode="HTML")
        return

    # вывод группами по пользователю
    header = (
        "🧨 <b>Дубли: один пользователь продаёт одну и ту же card_id</b>\n"
        "Условие: <b>user_id совпадает</b> + <b>card_id совпадает</b> (биржа + стандарт).\n"
    )
    if user_id_filter:
        header += f"Фильтр user_id: <code>{user_id_filter}</code>\n"
    if card_id_filter:
        header += f"Фильтр card_id: <code>{card_id_filter}</code>\n"
    header += f"Найдено строк: <b>{len(rows)}</b>\n"

    out = header
    cur_uid: int | None = None

    for r in rows:
        uid = int(r.get("user_id") or 0)
        uname = (r.get("username") or "").strip() or None

        if cur_uid != uid:
            cur_uid = uid
            who = _safe_user_mention(uid, uname)
            block = f"\n\n👤 {who} (id:<code>{uid}</code>)\n"
            if len(out) + len(block) > 3500:
                await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
                out = header + block
            else:
                out += block

        cid = int(r.get("card_id") or 0)
        hero = (r.get("hero_name") or "").strip()
        name = (r.get("card_name") or "").strip()
        deck_id = r.get("deck_id")

        title = " — ".join([x for x in [hero, name] if x]) or "—"

        ex_items = int(r.get("ex_items_cnt") or 0)
        ex_batches = int(r.get("ex_batches_cnt") or 0)
        ex_batch_ids = list(r.get("ex_batch_ids") or [])

        std_lots = int(r.get("std_lots_cnt") or 0)
        std_auction_ids = list(r.get("std_auction_ids") or [])

        line = (
            f"• 🎴 <b>{html.escape(title)}</b> | card_id <code>{cid}</code> | deck <code>{deck_id}</code>\n"
            f"   🛒 Биржа: items=<b>{ex_items}</b>, batches=<b>{ex_batches}</b>, batch_id: <code>{html.escape(_ids_preview(ex_batch_ids))}</code>\n"
            f"   ⭐ Стандарт: lots=<b>{std_lots}</b>, auction_id: <code>{html.escape(_ids_preview(std_auction_ids))}</code>\n"
        )

        if len(out) + len(line) > 3500:
            await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
            out = header + line
        else:
            out += line

    await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)


_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{3,})")


def _extract_usernames_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _USERNAME_RE.finditer(text or ""):
        un = (m.group(1) or "").strip()
        if not un:
            continue
        key = un.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(un)
    return out


def _chunk_lines(lines: list[str], max_len: int = 3500) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for line in lines:
        add_len = len(line) + 1
        if cur and (cur_len + add_len) > max_len:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = add_len
        else:
            cur.append(line)
            cur_len += add_len

    if cur:
        chunks.append("\n".join(cur))
    return chunks


@router.message(Command("ex_not_sent"))
@admin_only
async def cmd_ex_not_sent(message: Message):
    # источник текста: реплай на список или сам текст сообщения
    src = message.reply_to_message or message
    raw = (src.text or src.caption or "").strip()

    # если команда не реплаем, и список вставлен после команды
    if src is message and raw.startswith("/ex_not_sent"):
        raw = raw[len("/ex_not_sent"):].strip()

    if not raw:
        await message.answer(
            "Формат:\n"
            "1) пришли список отдельным сообщением\n"
            "2) ответь на него командой <code>/ex_not_sent</code>\n\n"
            "Либо вставь список прямо после команды.",
            parse_mode="HTML",
        )
        return

    usernames = _extract_usernames_from_text(raw)
    if not usernames:
        await message.answer("В тексте не нашёл ни одного @username.", parse_mode="HTML")
        return

    missing: dict[str, list[dict]] = {}
    ok: list[str] = []
    not_found: list[str] = []
    queries = await ExchangeDiagnosticsQueries.create()

    for un in usernames:
        # пытаемся найти user_id по username (на случай, если manual_winner_id заполнялся)
        winner_id = await queries.user_id_by_username(un)

        rows_unsent = await queries.unsent_winner_batches(
            winner_id=winner_id,
            username=un,
        )

        if rows_unsent:
            missing[un] = rows_unsent
            continue

        exists = await queries.has_winner_batches(
            winner_id=winner_id,
            username=un,
        )
        if exists:
            ok.append(f"@{un}")
        else:
            not_found.append(f"@{un}")

    lines: list[str] = []
    lines.append("🛒 <b>Биржа • проверка “не отправили”</b>")
    lines.append(f"Юзеров в списке: <b>{len(usernames)}</b>")
    lines.append("")

    if not missing:
        lines.append("✅ По этому списку <b>не нашёл</b> лотов, где победителю не отправляли (manual_sent_at пустой).")
        if not_found:
            lines.append("")
            lines.append("⚠️ <b>Не нашёл в БД ни одного лота по:</b>")
            lines.extend([f"• {u}" for u in not_found[:50]])
        for chunk in _chunk_lines(lines):
            await message.answer(chunk, parse_mode="HTML")
        return

    # есть “не отправлено”
    lines.append(f"❌ <b>Найдено НЕ отправлено:</b> {len(missing)} пользователей")
    lines.append("")

    msk = ZoneInfo("Europe/Moscow")

    for un, lots in missing.items():
        lines.append(f"• <b>@{un}</b> — лотов: <b>{len(lots)}</b>")
        for r in lots[:20]:
            bid = int(r.get("batch_id") or 0)
            deck_id = int(r.get("deck_id") or 0)
            mode = (r.get("mode") or "—").strip()
            price = int(r.get("price") or 0)
            cur = (r.get("currency") or "алмазы").strip()
            cnt = int(r.get("items_count") or 0)

            dt = r.get("created_at")
            dt_s = "—"
            if isinstance(dt, datetime):
                # created_at обычно без tzinfo, поэтому просто красиво форматируем
                dt_s = dt.strftime("%d.%m %H:%M")

            lines.append(
                f"    └ <code>{bid}</code> • 📚 {deck_id} • 🎛 {mode} • 🃏 {cnt} • 💰 {price} {cur} • 🕒 {dt_s}"
            )
        if len(lots) > 20:
            lines.append(f"    └ …и ещё {len(lots) - 20}")

    if ok:
        lines.append("")
        lines.append(f"✅ <b>ОК (есть лоты, но “неотправленных” нет):</b> {len(ok)}")
        lines.extend([f"• {u}" for u in ok[:60]])
        if len(ok) > 60:
            lines.append(f"• …и ещё {len(ok) - 60}")

    if not_found:
        lines.append("")
        lines.append(f"⚠️ <b>Не нашёл в БД лотов по:</b> {len(not_found)}")
        lines.extend([f"• {u}" for u in not_found[:60]])
        if len(not_found) > 60:
            lines.append(f"• …и ещё {len(not_found) - 60}")

    for chunk in _chunk_lines(lines):
        await message.answer(chunk, parse_mode="HTML")


@router.message(Command("ex_unsent"))
async def cmd_ex_unsent(message: Message) -> None:
    if message.from_user.id not in legacy_config.ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    deck_id: int | None = None
    if len(parts) == 2:
        try:
            deck_id = int(parts[1].strip())
        except Exception:
            await message.answer("Формат: /ex_unsent [deck_id]")
            return
    elif len(parts) > 2:
        await message.answer("Формат: /ex_unsent [deck_id]")
        return

    queries = await ExchangeDiagnosticsQueries.create()
    rows = await queries.approved_unsent_batches(deck_id)

    if not rows:
        await message.answer(
            "✅ Нет одобренных батчей биржи без отметки отправки (manual_sent_at пустой)."
            + (f" Фильтр: колода {deck_id}." if deck_id else "")
        )
        return

    msk = ZoneInfo("Europe/Moscow")

    total_batches = len(rows)
    total_cards = sum(int(r.get("items_count") or 0) for r in rows)

    lines: list[str] = []
    lines.append("🛒 <b>Биржа • НЕ ОТПРАВЛЕНО (approved + manual_sent_at пустой)</b>")
    if deck_id:
        lines.append(f"Фильтр: колода <b>{deck_id}</b>")
    lines.append(f"Батчей: <b>{total_batches}</b>, карт внутри: <b>{total_cards}</b>")
    lines.append("")

    for r in rows:
        batch_id = int(r["batch_id"])
        did = int(r.get("deck_id") or 0)
        mode = (r.get("mode") or "—").strip()

        owner_username = (r.get("owner_username") or "").strip()
        owner = f"@{owner_username}" if owner_username else f"id{int(r['user_id'])}"

        win_id = r.get("manual_winner_id")
        win_un = (r.get("manual_winner_username") or "").strip()
        winner = "—"
        if win_un:
            winner = win_un if win_un.startswith("@") else f"@{win_un}"
        elif win_id:
            winner = f"id{int(win_id)}"

        cnt = int(r.get("items_count") or 0)

        dt = r.get("created_at")
        dt_s = "—"
        if isinstance(dt, datetime):
            # если naive, просто форматируем
            dt_s = dt.strftime("%d.%m %H:%M")

        flag = "⚠️ без победителя" if (not win_id and not win_un) else ""

        lines.append(
            f"• 🆔 <code>{batch_id}</code> • 📚 {did} • 🎛 {mode} • 🃏 {cnt} • 👤 {owner} • 🏆 {winner} • 🕒 {dt_s} {flag}"
        )

    # чанк по лимиту Телеги
    text = "\n".join(lines)
    if len(text) <= 3800:
        await message.answer(text, parse_mode="HTML")
        return

    chunk: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > 3800:
            await message.answer("\n".join(chunk), parse_mode="HTML")
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk), parse_mode="HTML")


_USER_LINE_RE = re.compile(r"^@([A-Za-z0-9_]{3,})(.*)$")
_AUTHOR_TS_RE = re.compile(r"^.+,\s*\[\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}\]\s*$", re.I)


def _norm(s: str) -> str:
    s = (s or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def _parse_qty_and_card(rest: str, cur_card: str | None) -> tuple[int, str | None]:
    """
    rest examples:
      "Граф"
      "2 Граф 2"
      "Мадс 4"
      "5 Нахом Кевин"
      "5 шт"
      "4 карты"
      ""  (then use cur_card)
    """
    rest = (rest or "").strip()
    if not rest:
        return (1, cur_card)

    # вычленяем количество
    qty = None

    # "5 шт" / "5 карт" / "5 карты" / "5 карта"
    m = re.match(r"^(\d+)\s*(шт\.?|штук|карта|карты|карт)?\b(.*)$", rest, flags=re.I)
    if m and m.group(1):
        qty = int(m.group(1))
        rest2 = (m.group(3) or "").strip()
    else:
        rest2 = rest

    # если не нашли qty в начале: пробуем в конце "Граф 2" / "Виктор 5 карт"
    if qty is None:
        m2 = re.match(r"^(.*?)(?:\s+(\d+))\s*(шт\.?|штук|карта|карты|карт)?\s*$", rest2, flags=re.I)
        if m2 and m2.group(2):
            qty = int(m2.group(2))
            rest2 = (m2.group(1) or "").strip()

    if qty is None:
        qty = 1

    # иногда пишут "2 Граф 2" -> уберем хвостовую цифру, если осталась
    tokens = rest2.split()
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    card = " ".join(tokens).strip()

    # чистим мусорные слова, если остались
    card = re.sub(r"\b(шт\.?|штук|карта|карты|карт)\b", "", card, flags=re.I).strip()
    card = re.sub(r"\s+", " ", card).strip()

    if not card:
        card = cur_card

    return qty, card


def _parse_expected_from_text(text: str) -> dict[tuple[str, str], int]:
    """
    returns {(username_lower, card_norm): expected_qty}
    """
    expected: dict[tuple[str, str], int] = defaultdict(int)
    cur_card: str | None = None
    cur_default_qty = 1

    for raw in (text or "").splitlines():
        line = (raw or "").strip()
        if not line:
            continue

        # пропускаем "Имя, [04.02.2026 19:04:08]"
        if _AUTHOR_TS_RE.match(line):
            continue

        low = line.lower()

        # групповый заголовок, не карта
        if _norm(line) in {"золото 18к"}:
            cur_card = None
            cur_default_qty = 1
            continue

        # заголовки вида "Каин и Авель, по одной карте"
        if "по одной" in low:
            card_title = line.split(",")[0].strip()
            if card_title:
                cur_card = card_title
                cur_default_qty = 1
            continue

        # заголовки вида "Джон (с белкой) 21 карта" / "Лилиан 19 карт"
        if not line.startswith("@"):
            hdr = line.rstrip(":").strip()
            hdr = re.sub(r"\s+\d+\s*карт\w*\s*$", "", hdr, flags=re.I).strip()
            # если это выглядит как название карты (короткая строка) - ставим контекст
            if hdr and len(hdr) <= 60:
                cur_card = hdr
                cur_default_qty = 1
            continue

        # строки вида "@user ...."
        m = _USER_LINE_RE.match(line)
        if not m:
            continue
        uname = _norm(m.group(1))
        rest = (m.group(2) or "").strip()

        qty, card = _parse_qty_and_card(rest, cur_card)
        if card is None:
            continue

        # если в rest вообще нет названия карты (например "@yaaziyaa"), берем cur_card
        # qty по умолчанию 1, но если cur_card задан и в rest пусто, ок
        card_norm = _norm(card)

        # если rest пустой, но у нас стоит cur_default_qty (редко нужно), применим
        if not rest and cur_card:
            qty = cur_default_qty

        expected[(uname, card_norm)] += int(qty)

    return dict(expected)


def _chunk(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines = text.splitlines()
    out, cur, size = [], [], 0
    for ln in lines:
        add = len(ln) + 1
        if cur and size + add > limit:
            out.append("\n".join(cur))
            cur, size = [ln], add
        else:
            cur.append(ln)
            size += add
    if cur:
        out.append("\n".join(cur))
    return out


@router.message(Command("ex_check_list"))
@admin_only
async def cmd_ex_check_list(message: types.Message) -> None:
    src = message.reply_to_message or message
    raw = (src.text or src.caption or "").strip()

    # если не реплаем, а вставили после команды
    if src is message and raw.startswith("/ex_check_list"):
        raw = raw[len("/ex_check_list"):].strip()

    if not raw:
        await message.answer(
            "Формат: пришли список одним сообщением и ответь на него /ex_check_list\n"
            "Или вставь список сразу после команды."
        )
        return

    expected = _parse_expected_from_text(raw)
    if not expected:
        await message.answer("Не смог распарсить список (не вижу строк вида @username ...).")
        return

    winners = sorted({u for (u, _) in expected.keys()})
    # подтягиваем user_id (если есть)
    queries = await ExchangeDiagnosticsQueries.create()
    uid_by_uname = await queries.user_ids_by_usernames(winners)

    unames = winners
    uids = [uid_by_uname.get(u, -1) for u in unames]

    rows = await queries.winner_assignment_items(
        usernames=unames,
        user_ids=uids,
    )

    sent_map: dict[tuple[str, str], int] = defaultdict(int)
    assigned_map: dict[tuple[str, str], int] = defaultdict(int)

    for r in (rows or []):
        uname = str(r["uname"]).strip().lower()
        card_norm = str(r["card_norm"] or "").strip().lower()
        if not uname or not card_norm:
            continue
        qty = int(r["qty"] or 0)
        assigned_map[(uname, card_norm)] += qty
        if bool(r["is_sent"]):
            sent_map[(uname, card_norm)] += qty

    # сверка
    missing_by_user: dict[str, list[str]] = defaultdict(list)
    total_expected = 0
    total_sent = 0
    total_missing = 0

    for (uname, card_norm), exp_qty in expected.items():
        exp_qty = int(exp_qty)
        total_expected += exp_qty
        sent = int(sent_map.get((uname, card_norm), 0))
        assigned = int(assigned_map.get((uname, card_norm), 0))
        total_sent += min(sent, exp_qty)

        if sent < exp_qty:
            miss = exp_qty - sent
            total_missing += miss
            # красиво: показываем, назначено ли вообще и висит ли "не отправлено"
            tail = ""
            if assigned > sent:
                tail = f" (назначено {assigned}, отправлено {sent})"
            else:
                tail = f" (в БД отправлено {sent})"
            missing_by_user[uname].append(f"• {card_norm} ×{miss}{tail}")

    lines: list[str] = []
    lines.append("📋 <b>Биржа • сверка по принятому списку</b>")
    lines.append(f"Пользователей: <b>{len(winners)}</b>")
    lines.append(
        f"Ожидаемо по списку: <b>{total_expected}</b> • Отмечено отправленным: <b>{total_sent}</b> • Не добито: <b>{total_missing}</b>")
    lines.append("")

    if not missing_by_user:
        lines.append("✅ По этой сверке всё закрыто: по списку нет недоотправленного (по данным БД).")
        for part in _chunk("\n".join(lines)):
            await message.answer(part, parse_mode="HTML")
        return

    lines.append("❌ <b>Кому по списку НЕ хватает (по данным БД):</b>")
    lines.append("")

    # выводим только проблемных
    for uname in sorted(missing_by_user.keys()):
        lines.append(f"<b>@{uname}</b>")
        lines.extend(missing_by_user[uname])
        lines.append("")

    for part in _chunk("\n".join(lines)):
        await message.answer(part, parse_mode="HTML")
