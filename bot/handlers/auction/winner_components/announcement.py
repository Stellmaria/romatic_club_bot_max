from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.auctions import AuctionKind, Currency, comparison_units
from bot.services.auction_winners import AuctionWinnerService
from bot.core.legacy_config import ADMIN_LOG_CHATS, DISCUSSION_CHAT_ID

from .common import (
    CB_WIN_EDIT_AMT,
    CB_WIN_EDIT_USER,
    CB_WIN_SEND,
    CB_WIN_SKIP,
    PENDING_EDIT,
    WIN_DRAFTS,
    admin_tag,
    build_channel_link,
    emoji_by_currency,
    fmt_msk,
    kb_winner_actions,
    log_admin,
    mention,
    msk_now,
    norm_username,
    user_links_html,
    winner_threshold,
)

try:
    from bot.core.legacy_config import WINNER_NOTIFY_DEADLINE_MINUTES
except Exception:
    WINNER_NOTIFY_DEADLINE_MINUTES = 5

router = Router(name="auction_winner_announcement")


def get_winner(bids: list[Any], auction_kind: str = "standard") -> Any | None:
    if not bids:
        return None
    kind = AuctionKind.from_raw(auction_kind)
    if not kind.is_automatic_bidding:
        return None
    direction = 1 if kind.lowest_bid_wins else -1

    def value(bid: Any) -> int:
        amount = int(bid["amount"] if isinstance(bid, dict) else bid.amount)
        if not kind.lowest_bid_wins:
            return amount
        raw_currency = (
            bid.get("currency") if isinstance(bid, dict) else getattr(bid, "currency", None)
        )
        return comparison_units(amount, Currency.from_raw(raw_currency or "алмазы"))

    return sorted(
        bids,
        key=lambda bid: (
            direction * value(bid),
            bid["placed_at"] if isinstance(bid, dict) else bid.placed_at,
        ),
    )[0]


async def _winner_preview_text(
    service: AuctionWinnerService,
    auction_id: int,
    amount: int,
    winner_id: int,
) -> str:
    auction = await service.auction(auction_id) or {}
    currency_emoji = emoji_by_currency(auction.get("currency"))
    link = build_channel_link(auction.get("message_id")) or "(ссылка недоступна)"
    lot_line = (auction.get("hero_name") or "-") + (
        f" — {auction.get('card_name')}" if auction.get("card_name") else ""
    )

    winner = await service.user(winner_id) or {}
    winner_name = mention(winner_id, winner.get("username"))

    owners = await service.owners(auction_id)
    owner_mentions = ", ".join(
        mention(int(owner["user_id"]), owner.get("username")) for owner in owners
    ) or "—"
    owner_ids = [int(owner["user_id"]) for owner in owners]

    winner_verified = await service.uid_verified(winner_id)
    winner_verification = "✅" if winner_verified else "❌"
    seller_total, _, sellers_verified = await service.uid_verification_counts(owner_ids)
    seller_verification = "✅" if seller_total and sellers_verified else ("❌" if seller_total else "—")

    return (
        "Привет!\n\n"
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amount} {currency_emoji}\n"
        f"Победитель: {winner_name} ({winner_verification})\n"
        f"Продавец вериф.: {seller_verification}\n"
        f"Владелец карты: {owner_mentions}"
    )


async def _autobid_win_note(
    service: AuctionWinnerService,
    *,
    auction_id: int,
    winner_bid: dict[str, Any] | object,
    winner_id: int,
    amount: int,
    winner_name: str,
) -> str:
    try:
        message_id = (
            winner_bid.get("discussion_message_id")
            if isinstance(winner_bid, dict)
            else getattr(winner_bid, "discussion_message_id", None)
        )
    except Exception:
        message_id = None

    if not message_id:
        message_id = await service.bid_message_id(auction_id, winner_id, amount)
    if not message_id:
        return ""
    action = await service.autobid_action(int(message_id))
    if not action:
        return ""
    return f"\n🤖 <i>Платная автоставка для {winner_name}</i>"


async def announce_winner(telegram_bot: Bot, auction: dict[str, Any], bids: list[Any], send_admin_log=None) -> None:
    service = await AuctionWinnerService.create()
    auction_id = int(auction["auction_id"])
    currency = (auction.get("currency") or "").lower()
    currency_emoji = emoji_by_currency(currency)
    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    reply_to_id = auction.get("discussion_message_id") or auction.get("message_id")

    if kind is AuctionKind.FREE:
        text = (
            "⏰ <b>Свободный аукцион завершён!</b>\n"
            "🪶 <i>Итог определит модератор после ручной проверки комментариев.</i>"
        )
        try:
            await telegram_bot.send_message(
                DISCUSSION_CHAT_ID,
                text,
                parse_mode="HTML",
                reply_to_message_id=reply_to_id,
            )
        except Exception:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, text, parse_mode="HTML")
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await telegram_bot.send_message(
                    chat_id,
                    f"🪶 Лот {auction_id}: требуется ручное определение итога свободного аукциона.",
                )
            except Exception:
                continue
        return

    winner_bid = get_winner(bids or [], kind.value)
    amount: int | None = None
    if winner_bid is not None:
        try:
            amount = int(winner_bid["amount"] if isinstance(winner_bid, dict) else winner_bid.amount)
        except Exception:
            winner_bid = None

    if not winner_bid:
        text = "⏰ <b>Аукцион завершён!</b>\n❌ <i>Победителей нет, ставок не было.</i>"
        try:
            await telegram_bot.send_message(
                DISCUSSION_CHAT_ID,
                text,
                parse_mode="HTML",
                reply_to_message_id=reply_to_id,
            )
        except Exception:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, text, parse_mode="HTML")
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await telegram_bot.send_message(chat_id, f"🏁 Лот {auction_id}: ставок не было.", parse_mode="HTML")
            except Exception:
                continue
        return

    try:
        win_message_id = (
            winner_bid.get("discussion_message_id")
            if isinstance(winner_bid, dict)
            else getattr(winner_bid, "discussion_message_id", None)
        )
    except Exception:
        win_message_id = None

    winner_bidder_id: int | None = None
    if not win_message_id:
        top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
        if top_bid:
            winner_bidder_id = int(top_bid["bidder_id"])
            amount = int(top_bid["amount"])
            win_message_id = top_bid.get("discussion_message_id")
    else:
        winner_bidder_id = int(
            winner_bid["bidder_id"] if isinstance(winner_bid, dict) else winner_bid.bidder_id
        )

    if winner_bidder_id is None:
        return

    action = await service.autobid_action(int(win_message_id)) if win_message_id else None
    winner_id = int(action["target_user_id"]) if action and action.get("target_user_id") else winner_bidder_id
    final_amount = int(amount or 0)
    winning_currency = Currency.from_raw(
        (winner_bid.get("currency") if isinstance(winner_bid, dict) else getattr(winner_bid, "currency", None))
        or currency
    )
    currency_emoji = winning_currency.emoji

    winner = await service.user(winner_id) or {}
    winner_name = mention(winner_id, winner.get("username"))
    owners = await service.owners(auction_id)
    owner_mentions = ", ".join(
        mention(int(owner["user_id"]), owner.get("username")) for owner in owners
    ) or "—"
    owner_ids = [int(owner["user_id"]) for owner in owners]

    winner_verified = await service.uid_verified(winner_id)
    winner_tag = "✅" if winner_verified else "❌"
    seller_total, _, sellers_verified = await service.uid_verification_counts(owner_ids)
    if seller_total:
        seller_line = (
            "🔒 Лот от верифицированного продавца ✅"
            if sellers_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌"
        )
    else:
        seller_line = "🔒 Верификация продавца: —"

    deck = await service.deck_for_auction(auction_id) or {}
    if deck.get("deck_id"):
        deck_tag = f" Колода №{deck['deck_id']}"
    elif deck.get("deck_name"):
        deck_tag = f" Колода «{deck['deck_name']}»"
    else:
        deck_tag = ""

    try:
        discussion_message_id = await service.discussion_message_id(auction_id)
        autobid_note = await _autobid_win_note(
            service,
            auction_id=auction_id,
            winner_bid=winner_bid,
            winner_id=winner_id,
            amount=final_amount,
            winner_name=winner_name,
        )
        end_text = (
            "🏁 <b>Аукцион завершён</b>\n"
            f"Победитель: {winner_name} ({winner_tag})\n"
            f"Ставка: <b>{final_amount} {currency_emoji}</b>"
            f"{autobid_note}\n"
            f"{seller_line}"
        )
        await telegram_bot.send_message(
            DISCUSSION_CHAT_ID,
            end_text,
            parse_mode="HTML",
            reply_to_message_id=discussion_message_id,
        )
    except Exception:
        pass

    link = build_channel_link(auction.get("message_id"))
    now = msk_now()
    deadline = now + timedelta(minutes=int(WINNER_NOTIFY_DEADLINE_MINUTES or 10))
    relative_minutes = int((deadline - now).total_seconds() // 60)
    lot_title = (auction.get("hero_name") or "-") + (
        f" — {auction.get('card_name')}" if auction.get("card_name") else ""
    )
    preview = await _winner_preview_text(service, auction_id, final_amount, winner_id)
    threshold = 0 if kind is AuctionKind.REVERSE else winner_threshold(currency)
    review_line = (
        f"\n⚠️ <b>Сумма ≥ порога проверки ({threshold} {currency_emoji}). Рекомендуется сверка ставок.</b>\n"
        if threshold and final_amount >= threshold
        else ""
    )

    admin_text = (
        "🏁 <b>Аукцион завершён</b>\n\n"
        f"Лот: <b>{lot_title}</b>{deck_tag}\n"
        f"Стоимость: <b>{final_amount} {currency_emoji}</b>\n"
        f"Победитель: {winner_name} ({winner_tag})\n"
        f"Владелец(ы): {owner_mentions}\n"
        f"{seller_line}\n"
        f"{'Ссылка: ' + link if link else ''}\n\n"
        "🧾 <b>Отправка уведомлений</b>\n"
        f"• Рекомендуем отправить <b>до {fmt_msk(deadline)} МСК</b> "
        f"(через ~{relative_minutes} мин)."
        f"{review_line}\n"
        "<i>Превью ЛС (как получит победитель/владельцы):</i>\n"
        "<code>" + preview.replace("<", "‹").replace(">", "›") + "</code>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{auction_id}:{winner_id}")],
        [
            InlineKeyboardButton(text="✎ Исправить ставку", callback_data=f"{CB_WIN_EDIT_AMT}:{auction_id}:{winner_id}"),
            InlineKeyboardButton(text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{auction_id}:{winner_id}"),
        ],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{winner_id}")],
    ])
    for chat_id in ADMIN_LOG_CHATS:
        try:
            await telegram_bot.send_message(
                chat_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception:
            continue


async def send_notifications(
    bot: Bot,
    auction_id: int,
    winner_id: int,
    *,
    override_amount: int | None = None,
) -> tuple[int, int, list[dict[str, Any]], int]:
    service = await AuctionWinnerService.create()
    auction = await service.auction(auction_id) or {}
    currency_emoji = emoji_by_currency(auction.get("currency"))
    link = build_channel_link(auction.get("message_id")) or "(ссылка недоступна)"
    lot_line = (auction.get("hero_name") or "-") + (
        f" — {auction.get('card_name')}" if auction.get("card_name") else ""
    )
    has_winner = int(winner_id or 0) > 0
    winner = await service.user(int(winner_id)) if has_winner else None
    winner = winner or {}
    winner_name = mention(int(winner_id), winner.get("username")) if has_winner else "—"
    winner_links = ""
    if has_winner and norm_username(winner.get("username")) is None:
        winner_links = f"\nСсылки победителя: {user_links_html(int(winner_id), winner.get('username'))}"

    owners = await service.owners(auction_id)
    owner_mentions = ", ".join(
        mention(int(owner["user_id"]), owner.get("username")) for owner in owners
    ) or "—"
    if override_amount is not None:
        amount = int(override_amount)
    else:
        kind = AuctionKind.from_raw(auction.get("auction_kind"))
        top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
        amount = int(top_bid["amount"]) if top_bid and top_bid.get("amount") is not None else 0
        if top_bid and top_bid.get("currency"):
            currency_emoji = Currency.from_raw(top_bid["currency"]).emoji

    common_text = (
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amount} {currency_emoji}\n"
        f"Победитель: {winner_name}{winner_links}\n"
        f"Владелец карты: {owner_mentions}"
    )
    owner_text = common_text
    if not has_winner:
        owner_text = (
            "Привет!\n\n"
            f"Аукцион {link} завершён!\n"
            f"Лот: {lot_line}\n\n"
            "Ставок не было, поэтому карта не нашла нового владельца. 🫶\n"
            "Ничего страшного: такое бывает, просто не попали в настроение чата.\n\n"
            f"Владелец карты: {owner_mentions}\n\n"
            "Хочешь, выставь её снова или закинь в биржу."
        )

    ok = 0
    fail = 0
    deliveries: list[dict[str, Any]] = []

    if has_winner:
        try:
            await bot.send_message(
                int(winner_id),
                common_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            ok += 1
            deliveries.append({"role": "winner", "user_id": int(winner_id), "username": winner.get("username"), "ok": True, "err": ""})
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            fail += 1
            deliveries.append({"role": "winner", "user_id": int(winner_id), "username": winner.get("username"), "ok": False, "err": str(error)})
        except Exception as error:
            fail += 1
            deliveries.append({"role": "winner", "user_id": int(winner_id), "username": winner.get("username"), "ok": False, "err": repr(error)})
    else:
        deliveries.append({"role": "winner", "user_id": 0, "username": None, "ok": False, "err": "no_winner"})

    for owner in owners:
        user_id = int(owner["user_id"])
        username = owner.get("username")
        try:
            await bot.send_message(
                user_id,
                owner_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            ok += 1
            deliveries.append({"role": "owner", "user_id": user_id, "username": username, "ok": True, "err": ""})
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            fail += 1
            deliveries.append({"role": "owner", "user_id": user_id, "username": username, "ok": False, "err": str(error)})
        except Exception as error:
            fail += 1
            deliveries.append({"role": "owner", "user_id": user_id, "username": username, "ok": False, "err": repr(error)})

    return ok, fail, deliveries, amount


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_AMT}:"))
async def cb_win_edit_amt(call: types.CallbackQuery) -> None:
    await call.answer()
    try:
        _, _, auction_id_raw, winner_id_raw = call.data.split(":")
        auction_id = int(auction_id_raw)
        winner_id = int(winner_id_raw)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.")
        return
    PENDING_EDIT[call.from_user.id] = {"auction_id": auction_id, "field": "amount", "winner_id": winner_id}
    await call.message.answer(
        f"✎ Введите новую сумму ставки для лота <code>{auction_id}</code> (число).",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_USER}:"))
async def cb_win_edit_user(call: types.CallbackQuery) -> None:
    await call.answer()
    try:
        _, _, auction_id_raw, _ = call.data.split(":")
        auction_id = int(auction_id_raw)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.")
        return
    PENDING_EDIT[call.from_user.id] = {"auction_id": auction_id, "field": "winner"}
    await call.message.answer(
        f"👤 Пришлите нового победителя для лота <code>{auction_id}</code> в формате @username или числовой id.",
        parse_mode="HTML",
    )


@router.message(lambda message: message.from_user and message.from_user.id in PENDING_EDIT)
async def handle_pending_edit(message: types.Message, bot: Bot) -> None:
    pending = PENDING_EDIT.pop(message.from_user.id, None)
    if not pending:
        return
    service = await AuctionWinnerService.create()
    auction_id = int(pending["auction_id"])
    field = str(pending["field"])
    draft = WIN_DRAFTS.get(auction_id, {})
    administrator = admin_tag(message.from_user)

    if field == "amount":
        from .common import parse_amount_text

        value = parse_amount_text(message.text or "")
        if value is None:
            await message.answer("❌ Неверное число.")
            return
        error = service.validate_amount(await service.auction_currency(auction_id), value)
        if error:
            await message.answer(error)
            return
        draft["amount"] = value
        WIN_DRAFTS[auction_id] = draft
        auction = await service.auction(auction_id) or {}
        kind = AuctionKind.from_raw(auction.get("auction_kind"))
        top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
        winner_id = int(draft.get("winner_id") or (top_bid["bidder_id"] if top_bid else 0))
        preview = await _winner_preview_text(service, auction_id, value, winner_id)
        await message.answer("✔︎ Стоимость обновлена в черновике.", parse_mode="HTML")
        await message.answer(
            preview,
            parse_mode="HTML",
            reply_markup=kb_winner_actions(auction_id, winner_id),
            disable_web_page_preview=True,
        )
        await log_admin(
            bot,
            f"✎ Админ {administrator} установил ставку <b>{value}</b> в черновике для лота <b>{auction_id}</b>.",
        )
        return

    raw = (message.text or "").strip()
    winner_id, _ = await service.resolve_user_ref(raw)
    if not winner_id:
        await message.answer("❌ Пользователь не найден.")
        return
    draft["winner_id"] = winner_id
    WIN_DRAFTS[auction_id] = draft
    auction = await service.auction(auction_id) or {}
    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
    amount = int(draft.get("amount") or (top_bid["amount"] if top_bid else 0))
    preview = await _winner_preview_text(service, auction_id, amount, winner_id)
    await message.answer("✔︎ Победитель обновлён в черновике.", parse_mode="HTML")
    await message.answer(
        preview,
        parse_mode="HTML",
        reply_markup=kb_winner_actions(auction_id, winner_id),
        disable_web_page_preview=True,
    )
    await log_admin(
        bot,
        f"👤 Админ {administrator} сменил победителя на <code>{winner_id}</code> в черновике для лота <b>{auction_id}</b>.",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND}:"))
async def cb_winner_send(call: types.CallbackQuery, bot: Bot) -> None:
    await call.answer()
    try:
        _, _, auction_id_raw, winner_id_raw = call.data.split(":")
        auction_id = int(auction_id_raw)
        winner_id = int(winner_id_raw)
    except Exception:
        await call.message.edit_text("❌ Неверные данные кнопки.", parse_mode="HTML")
        return

    draft = WIN_DRAFTS.get(auction_id, {})
    winner_id = int(draft.get("winner_id") or winner_id)
    override_amount = int(draft["amount"]) if draft.get("amount") is not None else None
    ok, fail, deliveries, used_amount = await send_notifications(
        bot,
        auction_id,
        winner_id,
        override_amount=override_amount,
    )
    service = await AuctionWinnerService.create()
    currency_emoji = emoji_by_currency(await service.auction_currency(auction_id))
    lines = [
        f"📨 Рассылка по лоту <b>{auction_id}</b> завершена ({fmt_msk(msk_now())} МСК).",
        f"Ставка: <b>{used_amount} {currency_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]
    for delivery in deliveries:
        tag = "🏆" if delivery["role"] == "winner" else "👑"
        username = f"@{delivery['username']}" if delivery.get("username") else f"id{delivery['user_id']}"
        status = "OK" if delivery["ok"] else f"FAIL: {str(delivery['err'])[:120]}"
        lines.append(f"{tag} {username} — {status}")
    report = "\n".join(lines)
    try:
        await call.message.edit_text(report, parse_mode="HTML")
    except Exception:
        pass
    await log_admin(bot, report)


@router.callback_query(F.data.startswith(f"{CB_WIN_SKIP}:"))
async def cb_winner_skip(call: types.CallbackQuery, bot: Bot) -> None:
    await call.answer("Рассылка отменена.")
    try:
        _, _, auction_id_raw, winner_id_raw = call.data.split(":")
        auction_id = int(auction_id_raw)
        winner_id = int(winner_id_raw)
    except Exception:
        await call.message.edit_text("❌ Неверные данные кнопки.", parse_mode="HTML")
        return
    draft = WIN_DRAFTS.get(auction_id, {})
    winner_id = int(draft.get("winner_id") or winner_id)
    amount = draft.get("amount")
    try:
        await call.message.edit_text(
            f"⛔ Рассылка по лоту <b>{auction_id}</b> отменена админом.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await log_admin(
        bot,
        f"⛔ Админ {admin_tag(call.from_user)} отменил рассылку по лоту <b>{auction_id}</b> "
        f"(winner={winner_id}, amount={amount if amount is not None else '—'}).",
    )
