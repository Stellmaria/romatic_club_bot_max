from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.admin.helper.new.admin_actions import _safe_user_mention
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.exchange_diagnostics import ExchangeDiagnosticsService
from config import ADMINS
from .common import (
    _chunk,
    _chunk_lines,
    _extract_usernames_from_text,
    _parse_expected_from_text,
)

router = Router(name="auction_exchange_diagnostics_reconciliation")

@router.message(F.text.regexp(r"^/dup_user_cards(?:\s+.+)?$"), F.chat.type == "private")
@admin_only
async def cmd_dup_user_cards(message: Message):
    """Ищет пересечения биржи и стандартных лотов по user_id/card_id."""
    diagnostics = await ExchangeDiagnosticsService.create()
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

    rows = await diagnostics.duplicate_user_cards(
        user_id=user_id_filter,
        card_id=card_id_filter,
        limit=120,
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

@router.message(Command("ex_not_sent"))
@admin_only
async def cmd_ex_not_sent(message: Message):
    diagnostics = await ExchangeDiagnosticsService.create()
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

    for un in usernames:
        rows_unsent, exists = await diagnostics.winner_delivery_state(un)
        if rows_unsent:
            missing[un] = rows_unsent
            continue
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
    diagnostics = await ExchangeDiagnosticsService.create()
    if message.from_user.id not in ADMINS:
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

    rows = await diagnostics.unsent_batches(deck_id=deck_id)


    if not rows:
        await message.answer(
            "✅ Нет одобренных батчей биржи без отметки отправки (manual_sent_at пустой)."
            + (f" Фильтр: колода {deck_id}." if deck_id else "")
        )
        return

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

@router.message(Command("ex_check_list"))
@admin_only
async def cmd_ex_check_list(message: types.Message) -> None:
    diagnostics = await ExchangeDiagnosticsService.create()
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
    rows = await diagnostics.assignment_rows(winners)

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
