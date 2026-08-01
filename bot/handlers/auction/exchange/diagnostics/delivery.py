from __future__ import annotations

from collections import defaultdict

from aiogram import Router, types
from aiogram.filters import Command

from bot.handlers.admin.action_support.compat import safe_user_mention
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.exchange_diagnostics import ExchangeDiagnosticsService
from ..common import currency_to_emoji
from .common import cards_preview, exchange_mode_label, parse_batch_ids

router = Router(name="auction_exchange_diagnostics_delivery")

@router.message(Command("print_ex_multi"))
@admin_only
async def cmd_print_ex_multi(message: types.Message):
    diagnostics = await ExchangeDiagnosticsService.create()
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
            u = await diagnostics.user_by_username(uname)
            if not u:
                await message.answer("Победитель по @username не найден в БД.")
                return
            winner_id = int(u["user_id"])
            winner_un = u.get("username") or str(winner_id)
        elif winner_token.isdigit():
            winner_id = int(winner_token)
            u = await diagnostics.user_by_id(winner_id)
            winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)
        else:
            await message.answer("Победитель должен быть @username или числовой id.")
            return
    else:
        batch_tokens = args

    batch_ids = parse_batch_ids(batch_tokens)
    if not batch_ids:
        await message.answer("Не вижу batch-id. Пример: /print_ex_multi 123456 149 143 122")
        return

    # нормализуем имя победителя
    if not winner_un and winner_id:
        u = await diagnostics.user_by_id(winner_id)
        winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)

    winner_mention = safe_user_mention(winner_id, winner_un or str(winner_id))
    moderator = admin_tag(message.from_user)
    thanks_kb = await build_thanks_kb(batch_ids[0], moderator)

    missing: list[int] = []
    lots: list[dict] = []
    owner_username: dict[int, str] = {}

    for bid in batch_ids:
        batch = await diagnostics.batch(bid)
        if not batch:
            missing.append(bid)
            continue

        items = await diagnostics.batch_items(bid)
        owner_id = int(batch.get("user_id") or 0)

        uo = await diagnostics.user_by_id(owner_id)
        owner_un = (uo.get("username") or uo.get("full_name")) if uo else str(owner_id)
        owner_username[owner_id] = owner_un

        deck_id = int(batch.get("deck_id") or 0)
        deck = await diagnostics.deck(deck_id) if deck_id else None
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
                "owner_mention": safe_user_mention(owner_id, owner_un),
                "deck_name": deck_name,
                "mode": mode,
                "mode_label": exchange_mode_label(mode),
                "currency": cur,
                "price": price,
                "items": items,
                "cards_preview": cards_preview(items),
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
        om = safe_user_mention(oid, owner_username.get(oid, str(oid)))
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

    # Фиксируем победителя и факт рассылки одной транзакцией.
    try:
        await diagnostics.mark_batches_dispatched(
            [int(lot["batch_id"]) for lot in lots],
            winner_id=int(winner_id),
            winner_username=(winner_un or str(winner_id)),
            admin_id=message.from_user.id,
        )
    except Exception:
        # Telegram уже мог доставить сообщения, поэтому не маскируем проблему в отчёте.
        await message.answer("⚠️ Сообщения отправлены, но БД не смогла зафиксировать рассылку.")

    report = f"✅ /print_ex_multi готово. winner_ok={ok_winner}, owners_ok={owners_ok}, owners_fail={owners_fail}"
    if missing:
        report += f"\n⚠️ Не найдено batch-id: {', '.join(map(str, missing))}"
    await message.answer(report)
