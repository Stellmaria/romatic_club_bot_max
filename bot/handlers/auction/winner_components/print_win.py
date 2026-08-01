from __future__ import annotations

import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.time import to_moscow
from bot.domain.auctions import AuctionKind
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.auction_winners import AuctionWinnerService
from bot.core.legacy_config import legacy_config

from .common import (
    AUCTION_PROBLEMS_CONTACT,
    AUCTION_SUPPORT_CONTACT,
    AUCTION_SUPPORT_CONTACT_2,
    CB_WIN_CLEAR_MANUAL,
    CB_WIN_EDIT_MANUAL_AMOUNT,
    CB_WIN_EDIT_MANUAL_OWNER,
    CB_WIN_EDIT_MANUAL_WINNER,
    CB_WIN_MANUAL,
    CB_WIN_REFRESH,
    CB_WIN_SEND_BOTH,
    CB_WIN_SEND_OWNER,
    CB_WIN_SEND_WINNER,
    PENDING_WIN_FIELD_EDIT,
    PENDING_WIN_MANUAL,
    TG_MAX,
    admin_tag,
    build_channel_link,
    cb_last_int,
    emoji_by_currency,
    log_admin,
    mention,
    mention_soft,
    parse_amount_text,
    safe_pin_pm_message,
    send_media_any,
    user_links_html,
)
from .thanks import build_thanks_kb

router = Router(name="auction_winner_print_win")


async def _build_verification_warning_block(
    service: AuctionWinnerService,
    *,
    winner_user_id: int | None,
    owner_user_ids: list[int] | None,
) -> str:
    owner_ids = [int(value) for value in (owner_user_ids or []) if value]
    seller_unverified = False
    winner_unverified = False
    if owner_ids:
        total, _, all_verified = await service.uid_verification_counts(owner_ids)
        seller_unverified = bool(total and not all_verified)
    if winner_user_id:
        winner_unverified = not await service.uid_verified(int(winner_user_id))
    if not (seller_unverified or winner_unverified):
        return ""

    subjects = []
    if seller_unverified:
        subjects.append("продавцу")
    if winner_unverified:
        subjects.append("победителю")
    who = " и ".join(subjects) if subjects else "участникам сделки"
    return (
        "\n⚠️ <b>Для вашей безопасности рекомендуем "
        f"{who} пройти верификацию в Максе до завершения сделки.</b>\n"
        "Проверить человека можно так:\n"
        "• <code>/who @username</code>\n"
        "• <code>/who 123456789</code>\n"
        "• или reply на сообщение человека с командой <code>/who</code>\n"
    )


async def _compose_user_win_text(
    service: AuctionWinnerService,
    *,
    auction_id: int,
    link: str,
    lot_line: str,
    amount: int | None,
    currency_emoji: str,
    winner_user_id: int | None,
    winner_username: str | None,
    owner_mentions: str,
    moderator_tag: str,
    owner_user_ids: list[int] | None = None,
    moderator_comment: str | None = None,
) -> str:
    has_winner = bool(winner_user_id or winner_username)
    winner_name = mention_soft(winner_user_id, winner_username)
    extra_links = ""
    if winner_user_id and not winner_username:
        extra_links = f"Профиль: {user_links_html(int(winner_user_id), None)}\n"

    seller_total, _, sellers_verified = await service.uid_verification_counts(owner_user_ids or [])
    if seller_total:
        seller_line = (
            "🔒 Лот от верифицированного продавца ✅\n"
            if sellers_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌\n"
        )
    else:
        seller_line = "🔒 Верификация продавца: —\n"

    if winner_user_id:
        winner_verified = await service.uid_verified(int(winner_user_id))
        winner_line = (
            "🛡️ Победитель верифицирован: ✅\n"
            if winner_verified
            else "🛡️ Победитель НЕ верифицирован: ❌\n"
        )
    else:
        winner_line = "🛡️ Верификация победителя: —\n"

    warning = await _build_verification_warning_block(
        service,
        winner_user_id=winner_user_id,
        owner_user_ids=owner_user_ids,
    )
    comment = (moderator_comment or "").strip()
    comment_block = (
        "\n💬 <b>Комментарий модератора:</b>\n<i>" + html.escape(comment) + "</i>\n"
        if comment
        else ""
    )

    if not has_winner:
        return (
            "Привет! 👋\n\n"
            f"🏁 Аукцион завершён: {link}\n\n"
            f"🃏 Лот: {lot_line}\n\n"
            f"{seller_line}\n"
            "😿 Ставок не было, так что карта осталась у тебя. 🫶\n"
            "Это не провал. Просто сегодня чат не поймал волну.\n\n"
            f"👑 Владелец карты: {owner_mentions}\n\n"
            f"🛡️ Модератор аукциона: {moderator_tag}\n"
            f"❓ Вопросы об аукционе: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
            f"🧯 Проблемы по ауку: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
            f"{comment_block}\n"
            "🔁 Можно выставить лот снова, сменить время, валюту или формат.\n"
            "🏪 Биржа тоже вариант, если хочется закрыть вопрос быстрее."
        )

    final_amount = int(amount or 0)
    return (
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {final_amount} {currency_emoji}\n"
        f"{winner_line}"
        f"{seller_line}"
        f"Победитель: {winner_name}\n"
        f"{extra_links}"
        f"Владелец карты: {owner_mentions}\n"
        f"{warning}\n"
        f"Модератор аукциона: {moderator_tag}\n"
        f"Вопросы об аукционе сюда: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
        f"По проблемам по ауку сюда: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
        f"{comment_block}\n"
        "Если хочешь, можешь сказать спасибо модератору ниже ❤️\n"
    )


def _print_win_menu_kb(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить обоим", callback_data=f"{CB_WIN_SEND_BOTH}:{auction_id}")],
        [InlineKeyboardButton(text="👑 Отправить только владельцу", callback_data=f"{CB_WIN_SEND_OWNER}:{auction_id}")],
        [InlineKeyboardButton(text="🏆 Отправить только победителю", callback_data=f"{CB_WIN_SEND_WINNER}:{auction_id}")],
        [InlineKeyboardButton(text="💬 Комментарий от модератора", callback_data=f"win:edit_manual_comment:{auction_id}")],
        [InlineKeyboardButton(text="🏆 Сменить победителя", callback_data=f"{CB_WIN_EDIT_MANUAL_WINNER}:{auction_id}")],
        [InlineKeyboardButton(text="👑 Сменить владельца", callback_data=f"{CB_WIN_EDIT_MANUAL_OWNER}:{auction_id}")],
        [InlineKeyboardButton(text="💰 Сменить цену", callback_data=f"{CB_WIN_EDIT_MANUAL_AMOUNT}:{auction_id}")],
        [InlineKeyboardButton(text="🧹 Сбросить ручной итог", callback_data=f"{CB_WIN_CLEAR_MANUAL}:{auction_id}")],
        [InlineKeyboardButton(text="✍️ Мастер ручного итога", callback_data=f"{CB_WIN_MANUAL}:{auction_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB_WIN_REFRESH}:{auction_id}")],
    ])


async def _build_print_win_context(service: AuctionWinnerService, auction_id: int) -> dict:
    auction = await service.auction(auction_id)
    if not auction:
        return {"ok": False, "err": "Лот не найден."}

    currency_emoji = emoji_by_currency(auction.get("currency"))
    link = build_channel_link(auction.get("message_id")) or "(ссылка недоступна)"
    lot_line = (auction.get("hero_name") or "-") + (
        f" — {auction.get('card_name')}" if auction.get("card_name") else ""
    )
    manual = await service.manual_result(auction_id)
    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)

    winner_user_id = int(top_bid["bidder_id"]) if top_bid and top_bid.get("bidder_id") else None
    amount = int(top_bid["amount"]) if top_bid and top_bid.get("amount") is not None else None
    winner_username = None

    owners = await service.owners(auction_id)
    owner_mentions = ", ".join(
        mention(int(owner["user_id"]), owner.get("username")) for owner in owners
    ) if owners else "—"
    owner_user_ids = [int(owner["user_id"]) for owner in owners]

    if manual:
        if manual.get("winner_user_id") is not None or manual.get("winner_username") is not None:
            winner_user_id = int(manual["winner_user_id"]) if manual.get("winner_user_id") else None
            winner_username = manual.get("winner_username")
        if manual.get("amount") is not None:
            amount = int(manual["amount"])
        if manual.get("owner_user_id") is not None or manual.get("owner_username") is not None:
            owner_id = int(manual["owner_user_id"]) if manual.get("owner_user_id") else None
            owner_username = manual.get("owner_username")
            owner_mentions = mention_soft(owner_id, owner_username)
            owner_user_ids = [owner_id] if owner_id else []

    if winner_username and not winner_user_id:
        user = await service.user_by_username(str(winner_username))
        if user:
            winner_user_id = int(user["user_id"])
    if manual and manual.get("owner_username") and not owner_user_ids:
        owner = await service.user_by_username(str(manual["owner_username"]))
        if owner:
            owner_user_ids = [int(owner["user_id"])]
    if winner_user_id and not winner_username:
        winner = await service.user(winner_user_id)
        if winner and winner.get("username"):
            winner_username = winner["username"]

    sent_total, sent_owner, sent_winner = await service.mailing_counts(auction_id)
    return {
        "ok": True,
        "auction_id": int(auction_id),
        "link": link,
        "lot_line": lot_line,
        "currency_emoji": currency_emoji,
        "amount": amount,
        "winner_user_id": winner_user_id,
        "winner_username": winner_username,
        "owner_mentions": owner_mentions,
        "owner_user_ids": owner_user_ids,
        "moderator_comment": (manual or {}).get("moderator_comment"),
        "sent_total": sent_total,
        "sent_owner": sent_owner,
        "sent_winner": sent_winner,
        "has_manual": bool(manual),
        "photo": auction.get("image_id"),
    }


async def _compose_print_win_menu_text(service: AuctionWinnerService, context: dict, moderator: str) -> str:
    warnings = []
    if context.get("winner_user_id") is None and not context.get("winner_username"):
        warnings.append("⚠️ Победитель не определён (нет ставок и нет ручного результата).")
    if context.get("amount") is None:
        warnings.append("⚠️ Цена не определена (нет ставок и нет ручного результата).")
    if (context.get("owner_mentions") or "—") == "—":
        warnings.append("⚠️ Владелец не определён (нет auction_owners и нет ручного результата).")
    warning_block = ("\n" + "\n".join(warnings) + "\n") if warnings else ""

    preview = await _compose_user_win_text(
        service,
        auction_id=int(context["auction_id"]),
        link=str(context["link"]),
        lot_line=str(context["lot_line"]),
        amount=context.get("amount"),
        currency_emoji=str(context["currency_emoji"]),
        winner_user_id=context.get("winner_user_id"),
        winner_username=context.get("winner_username"),
        owner_mentions=str(context.get("owner_mentions") or "—"),
        moderator_tag=moderator,
        owner_user_ids=list(context.get("owner_user_ids") or []),
        moderator_comment=context.get("moderator_comment"),
    )
    manual_line = "📝 Есть ручной результат.\n" if context.get("has_manual") else ""
    text = (
        f"📨 <b>/print_win</b> для лота <b>{context['auction_id']}</b>\n"
        f"Отправлено рассылок: <b>{context['sent_total']}</b> | "
        f"👑 владельцу: <b>{context['sent_owner']}</b> | "
        f"🏆 победителю: <b>{context['sent_winner']}</b>\n"
        f"{manual_line}{warning_block}\n"
        "<b>Превью сообщения (ЛС):</b>\n\n"
        f"{preview}"
    )
    if len(text) > TG_MAX:
        text = text[:TG_MAX - 50] + "\n\n…<i>превью обрезано из-за лимита Telegram</i>"
    return text


async def _send_print_win_menu(message: Message, auction_id: int) -> None:
    service = await AuctionWinnerService.create()
    context = await _build_print_win_context(service, auction_id)
    if not context.get("ok"):
        await message.answer(f"❌ {context.get('err')}")
        return
    await message.answer(
        await _compose_print_win_menu_text(service, context, admin_tag(message.from_user)),
        parse_mode="HTML",
        reply_markup=_print_win_menu_kb(auction_id),
        disable_web_page_preview=True,
    )


async def _edit_print_win_menu(call: types.CallbackQuery, auction_id: int) -> None:
    service = await AuctionWinnerService.create()
    context = await _build_print_win_context(service, auction_id)
    if not context.get("ok"):
        text = f"❌ {context.get('err')}"
        markup = None
    else:
        text = await _compose_print_win_menu_text(service, context, admin_tag(call.from_user))
        markup = _print_win_menu_kb(auction_id)
    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def _refresh_print_win_menu_by_ids(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    auction_id: int,
    admin_user: types.User,
) -> None:
    service = await AuctionWinnerService.create()
    context = await _build_print_win_context(service, auction_id)
    if not context.get("ok"):
        text = f"❌ {context.get('err')}"
        markup = None
    else:
        text = await _compose_print_win_menu_text(service, context, admin_tag(admin_user))
        markup = _print_win_menu_kb(auction_id)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def _post_taken_comment_and_pin_after_print_win(bot: Bot, *, auction_id: int) -> tuple[bool, str | None]:
    service = await AuctionWinnerService.create()
    context = await _build_print_win_context(service, auction_id)
    if not context.get("ok"):
        return False, str(context.get("err") or "context_error")
    if int(context.get("sent_total") or 0) > 1:
        return False, "already_posted"
    if not context.get("winner_user_id") and not context.get("winner_username"):
        return False, "no_winner"
    discussion_message_id = await service.discussion_message_id(auction_id)
    if not discussion_message_id:
        return False, "no_discussion_message_id"

    warning = await _build_verification_warning_block(
        service,
        winner_user_id=context.get("winner_user_id"),
        owner_user_ids=list(context.get("owner_user_ids") or []),
    )
    text = (
        "📌 <b>Карту забрали</b>\n"
        f"🏆 Победитель: {mention_soft(context.get('winner_user_id'), context.get('winner_username'))}\n"
        f"💰 Ставка: <b>{int(context.get('amount') or 0)} {context['currency_emoji']}</b>\n"
        "🔎 Проверить участника в Максе: <code>/who @username</code> или <code>/who 123456789</code>"
        f"{warning}"
    )
    try:
        sent = await bot.send_message(
            legacy_config.DISCUSSION_CHAT_ID,
            text,
            parse_mode="HTML",
            reply_to_message_id=int(discussion_message_id),
            disable_web_page_preview=True,
        )
    except Exception as error:
        return False, f"send_failed: {error}"
    try:
        await bot.pin_chat_message(
            chat_id=legacy_config.DISCUSSION_CHAT_ID,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        return True, "sent_but_not_pinned"
    return True, None


async def _send_win_dm_to_targets(
    bot: Bot,
    *,
    auction_id: int,
    target: str,
    admin_user: types.User,
) -> tuple[int, int, list[dict], int | None]:
    service = await AuctionWinnerService.create()
    context = await _build_print_win_context(service, auction_id)
    if not context.get("ok"):
        return 0, 1, [{"role": "error", "user_id": 0, "username": None, "ok": False, "err": context.get("err"), "pinned": False}], None

    moderator = admin_tag(admin_user)
    text = await _compose_user_win_text(
        service,
        auction_id=auction_id,
        link=str(context["link"]),
        lot_line=str(context["lot_line"]),
        amount=context.get("amount"),
        currency_emoji=str(context["currency_emoji"]),
        winner_user_id=context.get("winner_user_id"),
        winner_username=context.get("winner_username"),
        owner_mentions=str(context.get("owner_mentions") or "—"),
        moderator_tag=moderator,
        owner_user_ids=list(context.get("owner_user_ids") or []),
        moderator_comment=context.get("moderator_comment"),
    )
    keyboard = await build_thanks_kb(auction_id, moderator)
    ok = 0
    fail = 0
    deliveries: list[dict] = []

    async def send(user_id: int, role: str, username: str | None) -> None:
        nonlocal ok, fail
        try:
            photo = context.get("photo")
            if photo and len(text) <= 900:
                sent = await send_media_any(bot, user_id, str(photo), text, reply_markup=keyboard)
            else:
                sent = await bot.send_message(
                    user_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            pinned = bool(sent and getattr(sent, "message_id", None) and await safe_pin_pm_message(bot, user_id, sent.message_id))
            try:
                await service.add_mailing(auction_id, role, admin_user)
            except Exception:
                pass
            ok += 1
            deliveries.append({"role": role, "user_id": user_id, "username": username, "ok": True, "err": None, "pinned": pinned})
        except Exception as error:
            fail += 1
            deliveries.append({"role": role, "user_id": user_id, "username": username, "ok": False, "err": str(error), "pinned": False})

    if target in {"winner", "both"}:
        winner_id = context.get("winner_user_id")
        if winner_id:
            winner = await service.user(int(winner_id)) or {}
            await send(int(winner_id), "winner", winner.get("username"))
        else:
            fail += 1
            deliveries.append({"role": "winner", "user_id": 0, "username": None, "ok": False, "err": "winner not set", "pinned": False})

    if target in {"owner", "both"}:
        owner_ids = list(context.get("owner_user_ids") or [])
        if not owner_ids:
            fail += 1
            deliveries.append({"role": "owner", "user_id": 0, "username": None, "ok": False, "err": "owner not set", "pinned": False})
        for owner_id in owner_ids:
            owner = await service.user(int(owner_id)) or {}
            await send(int(owner_id), "owner", owner.get("username"))

    return ok, fail, deliveries, context.get("amount")


def _delivery_report(title: str, auction_id: int, amount: int | None, currency_emoji: str, ok: int, fail: int, deliveries: list[dict]) -> str:
    lines = [
        f"{title} по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{int(amount or 0)} {currency_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]
    for delivery in deliveries:
        tag = "🏆" if delivery["role"] == "winner" else ("👑" if delivery["role"] == "owner" else "⚠️")
        username = f"@{delivery['username']}" if delivery.get("username") else (f"id{delivery['user_id']}" if delivery.get("user_id") else "—")
        pin = " 📌" if delivery.get("pinned") else ""
        status = "OK" if delivery.get("ok") else f"FAIL: {str(delivery.get('err') or '')[:120]}"
        lines.append(f"{tag} {username} — {status}{pin}")
    return "\n".join(lines)


@router.message(Command("print_win"))
async def cmd_print_win(message: Message, bot: Bot) -> None:
    if message.from_user.id not in legacy_config.ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /print_win <auction_id>")
        return
    auction_id = int(parts[1])
    await _send_print_win_menu(message, auction_id)
    await log_admin(bot, f"🔎 Админ {admin_tag(message.from_user)} открыл /print_win для лота <b>{auction_id}</b>.")


@router.message(Command("print_win_missed"))
@admin_only
async def cmd_print_win_missed(message: types.Message) -> None:
    arguments = (message.text or "").split(maxsplit=1)
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    target_date: date = today
    if len(arguments) > 1:
        raw = arguments[1].strip()
        parsed = None
        for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                if fmt == "%d.%m":
                    parsed = parsed.replace(year=today.year)
                break
            except ValueError:
                continue
        if not parsed:
            await message.answer("❌ Неверный формат даты. Примеры: 20.01.2026 или 20.01")
            return
        target_date = parsed

    service = await AuctionWinnerService.create()
    rows = await service.missed_mailings_for_day(target_date)
    if not rows:
        await message.answer(f"✅ За {target_date.strftime('%d.%m.%Y')} пропусков /print_win не найдено.")
        return

    lines = [f"⚠️ За {target_date.strftime('%d.%m.%Y')} НЕ было рассылок /print_win:", ""]
    for row in rows:
        started = row.get("start_time")
        time_text = to_moscow(started).strftime("%H:%M") if isinstance(started, datetime) else "??:??"
        no_bids = " 😿 без ставок" if int(row.get("bids_count") or 0) == 0 else ""
        hero = (row.get("hero_name") or "").strip()
        card = (row.get("card_name") or "").strip()
        lot_name = f" — {hero} • {card}" if hero or card else ""
        lines.append(f"{time_text} — {int(row['auction_id'])}{no_bids}{lot_name}")

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > 3800:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    for chunk in chunks:
        await message.answer(chunk)


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_WINNER}:"))
async def cb_print_win_edit_manual_winner(call: types.CallbackQuery) -> None:
    await call.answer()
    auction_id = cb_last_int(call.data)
    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "winner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }
    await call.message.answer(
        "🏆 <b>Сменить победителя</b>\n\nПришли <code>@username</code> или числовой <code>id</code>.\nЕсли победителя нет — пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_OWNER}:"))
async def cb_print_win_edit_manual_owner(call: types.CallbackQuery) -> None:
    await call.answer()
    auction_id = cb_last_int(call.data)
    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "owner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }
    await call.message.answer(
        "👑 <b>Сменить владельца</b>\n\nПришли <code>@username</code> или числовой <code>id</code>.\nДля сброса пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_AMOUNT}:"))
async def cb_print_win_edit_manual_amount(call: types.CallbackQuery) -> None:
    await call.answer()
    auction_id = cb_last_int(call.data)
    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "amount",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }
    await call.message.answer(
        "💰 <b>Сменить цену</b>\n\nПришли число, например <code>6700</code> или <code>6k</code>. Для сброса пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("win:edit_manual_comment:"))
async def cb_print_win_edit_manual_comment(call: types.CallbackQuery) -> None:
    await call.answer()
    auction_id = cb_last_int(call.data)
    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "comment",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }
    await call.message.answer(
        "💬 <b>Комментарий от модератора</b>\n\nПришли текст. Чтобы очистить комментарий — пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_CLEAR_MANUAL}:"))
async def cb_print_win_clear_manual(call: types.CallbackQuery, bot: Bot) -> None:
    auction_id = cb_last_int(call.data)
    service = await AuctionWinnerService.create()
    await service.clear_manual_result(auction_id)
    await call.answer("🧹 Ручной итог сброшен.")
    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        auction_id=auction_id,
        admin_user=call.from_user,
    )


@router.message(lambda message: message.from_user and message.from_user.id in PENDING_WIN_FIELD_EDIT)
async def msg_print_win_edit_single_field(message: Message, bot: Bot) -> None:
    pending = PENDING_WIN_FIELD_EDIT.pop(message.from_user.id, None)
    if not pending:
        return
    service = await AuctionWinnerService.create()
    auction_id = int(pending["auction_id"])
    field = str(pending["field"])
    raw = (message.text or "").strip()
    previous = await service.manual_result(auction_id) or {}

    winner_user_id = previous.get("winner_user_id")
    winner_username = previous.get("winner_username")
    owner_user_id = previous.get("owner_user_id")
    owner_username = previous.get("owner_username")
    amount = previous.get("amount")
    comment: str | None = None

    if field in {"winner", "owner"}:
        if raw == "-":
            user_id, username = None, None
        else:
            user_id, username = await service.resolve_user_ref(raw)
            if user_id is None and username is None:
                await message.answer("❌ Дай @username, числовой id или '-'.", parse_mode="HTML")
                return
        if field == "winner":
            winner_user_id, winner_username = user_id, username
        else:
            owner_user_id, owner_username = user_id, username
    elif field == "amount":
        if raw == "-":
            amount = None
        else:
            value = parse_amount_text(raw)
            if value is None:
                await message.answer("❌ Цена должна быть числом или '-'.", parse_mode="HTML")
                return
            validation_error = service.validate_amount(await service.auction_currency(auction_id), value)
            if validation_error:
                await message.answer(validation_error, parse_mode="HTML")
                return
            amount = value
    elif field == "comment":
        comment = "" if raw == "-" else raw
        if len(comment) > 900:
            await message.answer("❌ Комментарий должен быть до 900 символов.", parse_mode="HTML")
            return

    await service.upsert_manual_result(
        auction_id,
        winner_user_id=int(winner_user_id) if winner_user_id else None,
        winner_username=winner_username,
        owner_user_id=int(owner_user_id) if owner_user_id else None,
        owner_username=owner_username,
        amount=int(amount) if amount is not None else None,
        updated_by=int(message.from_user.id),
        moderator_comment=comment,
    )
    await message.answer("✅ Обновлено.", parse_mode="HTML")
    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=int(pending["menu_chat_id"]),
        message_id=int(pending["menu_message_id"]),
        auction_id=auction_id,
        admin_user=message.from_user,
    )
    await log_admin(bot, f"✎ Админ {admin_tag(message.from_user)} обновил поле <b>{field}</b> для лота <b>{auction_id}</b>.")


@router.callback_query(F.data.startswith(f"{CB_WIN_REFRESH}:"))
async def cb_print_win_refresh(call: types.CallbackQuery) -> None:
    try:
        auction_id = cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return
    await call.answer()
    await _edit_print_win_menu(call, auction_id)


async def _send_target_callback(call: types.CallbackQuery, bot: Bot, *, target: str, title: str) -> None:
    try:
        auction_id = cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return
    await call.answer()
    ok, fail, deliveries, amount = await _send_win_dm_to_targets(
        bot,
        auction_id=auction_id,
        target=target,
        admin_user=call.from_user,
    )
    service = await AuctionWinnerService.create()
    report = _delivery_report(
        title,
        auction_id,
        amount,
        emoji_by_currency(await service.auction_currency(auction_id)),
        ok,
        fail,
        deliveries,
    )
    await call.message.answer(report, parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_OWNER}:"))
async def cb_print_win_send_owner(call: types.CallbackQuery, bot: Bot) -> None:
    await _send_target_callback(call, bot, target="owner", title="👑 Рассылка владельцу")


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_WINNER}:"))
async def cb_print_win_send_winner(call: types.CallbackQuery, bot: Bot) -> None:
    await _send_target_callback(call, bot, target="winner", title="🏆 Рассылка победителю")


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_BOTH}:"))
async def cb_print_win_send_both(call: types.CallbackQuery, bot: Bot) -> None:
    await _send_target_callback(call, bot, target="both", title="📨 Рассылка обоим")


@router.callback_query(F.data.startswith(f"{CB_WIN_MANUAL}:"))
async def cb_print_win_manual(call: types.CallbackQuery) -> None:
    await call.answer()
    auction_id = cb_last_int(call.data)
    PENDING_WIN_MANUAL[call.from_user.id] = {
        "auction_id": auction_id,
        "step": "winner",
        "winner_user_id": None,
        "winner_username": None,
        "owner_user_id": None,
        "owner_username": None,
        "amount": None,
    }
    await call.message.answer(
        "✍️ <b>Ручной итог</b>\n\n1) Пришли победителя: <code>@username</code> или <code>id</code>. Если победителя нет — <code>-</code>.",
        parse_mode="HTML",
    )


@router.message(lambda message: message.from_user and message.from_user.id in PENDING_WIN_MANUAL)
async def msg_print_win_manual(message: Message, bot: Bot) -> None:
    pending = PENDING_WIN_MANUAL.get(message.from_user.id)
    if not pending:
        return
    service = await AuctionWinnerService.create()
    auction_id = int(pending["auction_id"])
    step = str(pending["step"])
    raw = (message.text or "").strip()

    if step == "winner":
        if raw != "-":
            pending["winner_user_id"], pending["winner_username"] = await service.resolve_user_ref(raw)
        pending["step"] = "owner"
        await message.answer("2) Пришли владельца карты: <code>@username</code> или <code>id</code>. Для auction_owners — <code>-</code>.", parse_mode="HTML")
        return
    if step == "owner":
        if raw != "-":
            pending["owner_user_id"], pending["owner_username"] = await service.resolve_user_ref(raw)
        pending["step"] = "amount"
        await message.answer("3) Пришли цену числом. Для цены из ставок — <code>-</code>.", parse_mode="HTML")
        return

    if raw != "-":
        amount = parse_amount_text(raw)
        if amount is None:
            await message.answer("❌ Цена должна быть числом или <code>-</code>.", parse_mode="HTML")
            return
        validation_error = service.validate_amount(await service.auction_currency(auction_id), amount)
        if validation_error:
            await message.answer(validation_error, parse_mode="HTML")
            return
        pending["amount"] = amount

    await service.upsert_manual_result(
        auction_id,
        winner_user_id=pending.get("winner_user_id"),
        winner_username=pending.get("winner_username"),
        owner_user_id=pending.get("owner_user_id"),
        owner_username=pending.get("owner_username"),
        amount=pending.get("amount"),
        updated_by=int(message.from_user.id),
    )
    PENDING_WIN_MANUAL.pop(message.from_user.id, None)
    await _send_print_win_menu(message, auction_id)
    await log_admin(bot, f"✍️ Админ {admin_tag(message.from_user)} задал ручной итог для лота <b>{auction_id}</b>.")
