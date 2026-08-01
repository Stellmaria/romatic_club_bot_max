"""Print-win context assembly, text rendering, and menu presentation."""

from __future__ import annotations

import html
from typing import Optional

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.auctions import AuctionKind

from bot.core.legacy_config import legacy_config
from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

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
    TG_MAX,
    _admin_tag,
    _build_channel_link,
    _emoji_by_currency,
    _get_owners,
    _is_user_uid_verified,
    _mention,
    _user_links_html,
    _users_uid_verification_counts,
    get_user,
    get_user_by_username,
)
from .resolution import _get_discussion_msg_id


class AuctionWinContext:
    auction_id: int
    hero_name: str
    card_name: str
    currency: str
    channel_message_id: Optional[int]

    owner_user_id: Optional[int]
    owner_mention: str

    winner_user_id: Optional[int]
    winner_mention: str

    final_price: Optional[int]
    sent_total: int
    sent_owner: int
    sent_winner: int


async def _ensure_print_win_tables() -> None:
    await winner_repository.ensure_print_tables(await get_db_pool())


async def _win_mailing_counts(auction_id: int) -> tuple[int, int, int]:
    return await winner_repository.get_mailing_counts(
        await get_db_pool(),
        auction_id,
    )


async def _add_win_mailing(auction_id: int, target: str, admin: types.User) -> None:
    await winner_repository.add_mailing(
        await get_db_pool(),
        auction_id,
        target,
        admin.id,
        admin.username,
    )


async def _get_manual_result(auction_id: int) -> dict | None:
    return await winner_repository.get_manual_result(
        await get_db_pool(),
        auction_id,
    )


async def _upsert_manual_result(
    auction_id: int,
    *,
    winner_user_id: int | None,
    winner_username: str | None,
    owner_user_id: int | None,
    owner_username: str | None,
    amount: int | None,
    updated_by: int,
    moderator_comment: str | None = None,
) -> None:
    await winner_repository.upsert_manual_result(
        await get_db_pool(),
        auction_id,
        winner_user_id=winner_user_id,
        winner_username=winner_username,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        amount=amount,
        updated_by=updated_by,
        moderator_comment=moderator_comment,
    )


async def _resolve_user_ref(raw: str) -> tuple[int | None, str | None]:
    """
    Возвращает (user_id, username_without_at) по:
      - @username
      - числовому id
    Если @username не найден в users — вернём (None, username) чтобы хотя бы текстом показать.
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("@"):
        uname = s.lstrip("@")
        u = await get_user_by_username(uname)
        if u and u.get("user_id"):
            return int(u["user_id"]), uname
        return None, uname
    if s.isdigit():
        return int(s), None
    return None, None


def _mention_soft(user_id: int | None, username: str | None) -> str:
    """
    - если есть username -> @username
    - если только id -> кликабельный id + короткая tg-ссылка
    """
    if username:
        return f"@{username}"
    if user_id:
        uid = int(user_id)
        return (
            f'<a href="tg://user?id={uid}">id{uid}</a>'
            f' (<a href="tg://openmessage?user_id={uid}">tg</a>)'
        )
    return "—"


async def _build_verification_warning_block(
    *,
    winner_user_id: int | None,
    owner_user_ids: list[int] | None,
) -> str:
    owner_ids = [int(x) for x in (owner_user_ids or []) if x]

    seller_unverified = False
    winner_unverified = False

    if owner_ids:
        s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_ids)
        seller_unverified = bool(s_total and not sellers_all_verified)

    if winner_user_id:
        winner_unverified = not await _is_user_uid_verified(int(winner_user_id))

    if not (seller_unverified or winner_unverified):
        return ""

    need = []
    if seller_unverified:
        need.append("продавцу")
    if winner_unverified:
        need.append("победителю")

    need_text = " и ".join(need) if need else "участникам сделки"

    return (
        "\n⚠️ <b>Для вашей безопасности рекомендуем "
        f"{need_text} пройти верификацию в Максе до завершения сделки.</b>\n"
        "Проверить человека можно так:\n"
        "• <code>/who @username</code>\n"
        "• <code>/who 123456789</code>\n"
        "• или reply на сообщение человека с командой <code>/who</code>\n"
    )


async def _compose_user_win_text(
    *,
    auction_id: int,
    link: str,
    lot_line: str,
    amount: int | None,
    cur_emoji: str,
    winner_user_id: int | None,
    winner_username: str | None,
    owner_mentions: str,
    moderator_tag: str,
    owner_user_ids: list[int] | None = None,
    moderator_comment: str | None = None,
) -> str:
    has_winner = bool(winner_user_id or winner_username)
    wname = _mention_soft(winner_user_id, winner_username)

    extra_links = ""
    if winner_user_id and not winner_username:
        extra_links = f"Профиль: {_user_links_html(int(winner_user_id), None)}\n"

    # верификация продавца(ов)
    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(
        owner_user_ids or []
    )
    if s_total:
        seller_verif_line = (
            "🔒 Лот от верифицированного продавца ✅\n"
            if sellers_all_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌\n"
        )
    else:
        seller_verif_line = "🔒 Верификация продавца: —\n"

    # верификация победителя
    if winner_user_id:
        w_verified = await _is_user_uid_verified(int(winner_user_id))
        winner_verif_line = (
            "🛡️ Победитель верифицирован: ✅\n"
            if w_verified
            else "🛡️ Победитель НЕ верифицирован: ❌\n"
        )
    else:
        winner_verif_line = "🛡️ Верификация победителя: —\n"

    verification_warning_block = await _build_verification_warning_block(
        winner_user_id=winner_user_id,
        owner_user_ids=owner_user_ids or [],
    )

    # комментарий модератора
    c = (moderator_comment or "").strip()
    comment_block = ""
    if c:
        comment_block = "\n💬 <b>Комментарий модератора:</b>\n<i>" + html.escape(c) + "</i>\n"

    if not has_winner:
        return (
            "Привет! 👋\n\n"
            f"🏁 Аукцион завершён: {link}\n\n"
            f"🃏 Лот: {lot_line}\n\n"
            f"{seller_verif_line}\n"
            "😿 Ставок не было, так что карта осталась у тебя. 🫶\n"
            "Это не провал. Просто сегодня чат не поймал волну (или цена/валюта/время были не в тему).\n\n"
            f"👑 Владелец карты: {owner_mentions}\n\n"
            f"🛡️ Модератор аукциона: {moderator_tag}\n"
            f"❓ Вопросы об аукционе: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
            f"🧯 Проблемы по ауку: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
            f"{comment_block}\n"
            "🔁 Если хочешь, попробуй ещё раз: иногда решает другое время, другая валюта или чуть мягче старт.\n"
            "⚡️ Или закинь лот в другой формат: быстрый, свободный или чёрный аукцион.\n"
            "🏪 Ну и биржа тоже вариант, если хочется закрыть вопрос побыстрее."
        )

    amt = amount if amount is not None else 0
    return (
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amt} {cur_emoji}\n"
        f"{winner_verif_line}"
        f"{seller_verif_line}"
        f"Победитель: {wname}\n"
        f"{extra_links}"
        f"Владелец карты: {owner_mentions}\n"
        f"{verification_warning_block}\n"
        f"Модератор аукциона: {moderator_tag}\n"
        f"Вопросы об аукционе сюда: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
        f"По проблемам по ауку сюда: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
        f"{comment_block}\n"
        "Если хочешь, можешь сказать спасибо модератору ниже ❤️\n"
    )


def _print_win_menu_kb(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить обоим", callback_data=f"{CB_WIN_SEND_BOTH}:{auction_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Отправить только владельцу",
                    callback_data=f"{CB_WIN_SEND_OWNER}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Отправить только победителю",
                    callback_data=f"{CB_WIN_SEND_WINNER}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Комментарий от модератора",
                    callback_data=f"win:edit_manual_comment:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Сменить победителя",
                    callback_data=f"{CB_WIN_EDIT_MANUAL_WINNER}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Сменить владельца",
                    callback_data=f"{CB_WIN_EDIT_MANUAL_OWNER}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Сменить цену",
                    callback_data=f"{CB_WIN_EDIT_MANUAL_AMOUNT}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Сбросить ручной итог",
                    callback_data=f"{CB_WIN_CLEAR_MANUAL}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Мастер ручного итога (побед/влад/цена)",
                    callback_data=f"{CB_WIN_MANUAL}:{auction_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data=f"{CB_WIN_REFRESH}:{auction_id}"
                )
            ],
        ]
    )


async def _build_print_win_context(auction_id: int) -> dict:
    a = await winner_repository.get_auction_summary(
        await get_db_pool(),
        auction_id,
    )
    if not a:
        return {"ok": False, "err": "Лот не найден."}

    a = dict(a)
    photo = a.get("image_id")
    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (
        f" — {a.get('card_name')}" if a.get("card_name") else ""
    )

    manual = await _get_manual_result(auction_id)
    moderator_comment = (manual or {}).get("moderator_comment")

    kind = AuctionKind.from_raw(a.get("auction_kind"))
    b = await winner_repository.get_top_bid(
        await get_db_pool(),
        auction_id,
        lowest_wins=kind.lowest_bid_wins,
    )

    winner_user_id = int(b["bidder_id"]) if b and b.get("bidder_id") else None
    amount = int(b["amount"]) if b and b.get("amount") is not None else None
    winner_username = None

    owners = await _get_owners(auction_id)
    owner_mentions = (
        ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) if owners else "—"
    )
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    if manual:
        if manual.get("winner_user_id") is not None or manual.get("winner_username") is not None:
            winner_user_id = int(manual["winner_user_id"]) if manual.get("winner_user_id") else None
            winner_username = manual.get("winner_username")
        if manual.get("amount") is not None:
            amount = int(manual["amount"])
        if manual.get("owner_user_id") is not None or manual.get("owner_username") is not None:
            ouid = int(manual["owner_user_id"]) if manual.get("owner_user_id") else None
            oun = manual.get("owner_username")
            owner_mentions = _mention_soft(ouid, oun)
            owner_user_ids = [ouid] if ouid else []

    # попробуем дорезолвить id по username (если руками вводили только @username)
    if winner_username and not winner_user_id:
        u = await get_user_by_username(winner_username) or {}
        if u.get("user_id"):
            winner_user_id = int(u["user_id"])

    if not owner_user_ids:
        # если ручной владелец задан только username, пробуем найти id
        if manual and manual.get("owner_username") and not manual.get("owner_user_id"):
            uo = await get_user_by_username(str(manual["owner_username"])) or {}
            if uo.get("user_id"):
                owner_user_ids = [int(uo["user_id"])]

    if winner_user_id and not winner_username:
        u = await get_user(winner_user_id) or {}
        if u.get("username"):
            winner_username = u["username"]

    total, owner_cnt, winner_cnt = await _win_mailing_counts(auction_id)

    return {
        "ok": True,
        "auction_id": auction_id,
        "link": link,
        "lot_line": lot_line,
        "cur_emoji": cur_emoji,
        "amount": amount,
        "winner_user_id": winner_user_id,
        "winner_username": winner_username,
        "owner_mentions": owner_mentions,
        "owner_user_ids": owner_user_ids,
        "moderator_comment": moderator_comment,
        "sent_total": total,
        "sent_owner": owner_cnt,
        "sent_winner": winner_cnt,
        "has_manual": bool(manual),
        "photo": photo,
    }


async def _post_taken_comment_and_pin_after_print_win(
    bot: Bot, *, auction_id: int
) -> tuple[bool, str | None]:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        return False, str(ctx.get("err") or "ctx_error")

    # не плодим дубли: закрепляем только после первой успешной рассылки /print_win
    if int(ctx.get("sent_total") or 0) > 1:
        return False, "already_posted"

    winner_user_id = ctx.get("winner_user_id")
    winner_username = ctx.get("winner_username")
    amount = ctx.get("amount")

    if not winner_user_id and not winner_username:
        return False, "no_winner"

    dmsg_id = await _get_discussion_msg_id(int(auction_id))
    if not dmsg_id:
        return False, "no_discussion_message_id"

    winner_line = _mention_soft(winner_user_id, winner_username)

    verification_hint = await _build_verification_warning_block(
        winner_user_id=winner_user_id,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
    )

    text = (
        "📌 <b>Карту забрали</b>\n"
        f"🏆 Победитель: {winner_line}\n"
        f"💰 Ставка: <b>{int(amount or 0)} {ctx['cur_emoji']}</b>\n"
        "🔎 Проверить участника в Максе: <code>/who @username</code> или <code>/who 123456789</code>"
        f"{verification_hint}"
    )

    try:
        msg = await bot.send_message(
            legacy_config.DISCUSSION_CHAT_ID,
            text,
            parse_mode="HTML",
            reply_to_message_id=int(dmsg_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        return False, f"send_failed: {e}"

    try:
        await bot.pin_chat_message(
            chat_id=legacy_config.DISCUSSION_CHAT_ID,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        # сообщение отправили, но закрепить не смогли
        return True, "sent_but_not_pinned"

    return True, None


async def _compose_print_win_menu_text(ctx: dict, moderator_tag: str) -> str:
    warns = []
    if ctx.get("winner_user_id") is None and not ctx.get("winner_username"):
        warns.append("⚠️ Победитель не определён (нет ставок и нет ручного результата).")
    if ctx.get("amount") is None:
        warns.append("⚠️ Цена не определена (нет ставок и нет ручного результата).")
    if (ctx.get("owner_mentions") or "—") == "—":
        warns.append("⚠️ Владелец не определён (нет auction_owners и нет ручного результата).")

    warn_block = ("\n" + "\n".join(warns) + "\n") if warns else ""

    preview = await _compose_user_win_text(
        auction_id=int(ctx["auction_id"]),
        link=str(ctx["link"]),
        lot_line=str(ctx["lot_line"]),
        amount=ctx.get("amount"),
        cur_emoji=str(ctx["cur_emoji"]),
        winner_user_id=ctx.get("winner_user_id"),
        winner_username=ctx.get("winner_username"),
        owner_mentions=str(ctx.get("owner_mentions") or "—"),
        moderator_tag=moderator_tag,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
        moderator_comment=ctx.get("moderator_comment"),
    )

    manual_line = "📝 Есть ручной результат.\n" if ctx.get("has_manual") else ""

    text = (
        f"📨 <b>/print_win</b> для лота <b>{ctx['auction_id']}</b>\n"
        f"Отправлено рассылок: <b>{ctx['sent_total']}</b> | 👑 владельцу: <b>{ctx['sent_owner']}</b> | 🏆 победителю: <b>{ctx['sent_winner']}</b>\n"
        f"{manual_line}"
        f"{warn_block}\n"
        f"<b>Превью сообщения (ЛС):</b>\n\n"
        f"{preview}"
    )

    if len(text) > TG_MAX:
        text = text[: TG_MAX - 50] + "\n\n…<i>превью обрезано из-за лимита Telegram</i>"
    return text


async def _send_print_win_menu(message: types.Message, auction_id: int) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        await message.answer(f"❌ {ctx.get('err')}")
        return

    moderator_tag = _admin_tag(message.from_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_print_win_menu_kb(auction_id),
        disable_web_page_preview=True,
    )


async def _edit_print_win_menu(call: types.CallbackQuery, auction_id: int) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        try:
            await call.message.edit_text(f"❌ {ctx.get('err')}", parse_mode="HTML")
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            raise
        return

    moderator_tag = _admin_tag(call.from_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=_print_win_menu_kb(auction_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


async def _refresh_print_win_menu_by_ids(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    auction_id: int,
    admin_user: types.User,
) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ {ctx.get('err')}",
            parse_mode="HTML",
        )
        return

    moderator_tag = _admin_tag(admin_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_print_win_menu_kb(auction_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise
