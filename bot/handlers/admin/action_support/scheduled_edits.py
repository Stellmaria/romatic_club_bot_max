"""Scheduled-auction edit application, owner notification and audit logging."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from bot.core.time import to_moscow
from bot.handlers.admin.action_support.exchange import _cur_emoji
from bot.handlers.admin.helper.admin_constants import RARITY_EMOJI
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text, get_lot_owners_with_levels
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.auction_workflows import AuctionModerationService
from db.admin import log_admin_action
from db.auctions import get_lot_by_id

EX_WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")

async def _update_auction_field(auction_id: int, field: str, value: Any) -> dict[str, Any]:
    service = await AuctionModerationService.create()
    return await service.update_field(auction_id, field=field, value=value)


def _short_media_id(v: object) -> str:
    """Чтобы логи/сообщения не превращались в простыню file_id."""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    if len(s) <= 22:
        return s
    return f"{s[:12]}…{s[-8:]}"


def _fmt_dt_msk(dt: object) -> str:
    """Форматируем как 28.02 22:30 (без споров про TZ в тексте)."""
    if not dt:
        return "—"
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return to_moscow(dt).strftime("%d.%m %H:%M")
    return str(dt)


def _fmt_window_msk(start: object, end: object) -> str:
    s = _fmt_dt_msk(start)
    e = _fmt_dt_msk(end)
    # если обе даты ок и одинаковый день, красиво: 28.02 22:30–23:00
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            start_msk = to_moscow(start)
            end_msk = to_moscow(end)
            if start_msk.date() == end_msk.date():
                return f"{start_msk.strftime('%d.%m %H:%M')}–{end_msk.strftime('%H:%M')}"
    except Exception:
        pass
    return f"{s}–{e}"


def _obtain_emoji(obtain_type: str | None) -> str:
    t = (obtain_type or "").strip().lower()
    return {
        "diamonds": "💎",
        "diamond": "💎",
        "cups": "🍵",
        "cup": "🍵",
        "treasures": "🪙",
        "treasure": "🪙",
    }.get(t, "🎁")


def _yn_uid(v: object) -> str:
    if v is True:
        return "🆔 ✅ Да"
    if v is False:
        return "🆔 ❌ Нет"
    return "🆔 —"


def _rarity_line(rarity: str | None) -> str:
    r = (rarity or "").strip().lower()
    emo = RARITY_EMOJI.get(r, "🏷️")
    return f"🏷️ {emo} {rarity or '—'}"


def _pick_sold_count(lot: dict) -> int | None:
    # под разные названия колонок/алиасов, потому что жизнь боль
    for k in ("sold_count", "sold_before", "sold_prev", "sold_total"):
        v = lot.get(k)
        if isinstance(v, int):
            return v
        try:
            if v is not None and str(v).isdigit():
                return int(v)
        except Exception:
            pass
    return None


def _user_status_label(owner_row: dict) -> str:
    # ожидаемые варианты: luxury_level / luxury_tier / is_luxury
    lvl = owner_row.get("luxury_level") or owner_row.get("luxury_tier") or owner_row.get("luxury_lvl")
    if lvl is not None:
        try:
            lvl_i = int(lvl)
            return f"👑 Лакшери {lvl_i}"
        except Exception:
            return "👑 Лакшери"
    is_lux = owner_row.get("is_luxury")
    if is_lux is True:
        return "👑 Лакшери"
    return "🙂 Обычный"


def _format_change_lines(lot_before: dict, changes: list[tuple[str, Any, Any]]) -> list[str]:
    """
    changes: [(field, old, new), ...]
    field: auction_kind | craft_uid_possible | time_window | start_price | currency | comment | image_id
    """
    lines: list[str] = []
    for field, old, new in changes:
        f = (field or "").strip().lower()

        if f in {"time", "time_window", "schedule_time"}:
            # old/new ожидаем как (start, end) tuple
            try:
                old_s, old_e = old
            except Exception:
                old_s, old_e = None, None
            try:
                new_s, new_e = new
            except Exception:
                new_s, new_e = None, None
            lines.append(f"🕒 <b>Время:</b> {_fmt_window_msk(old_s, old_e)} → {_fmt_window_msk(new_s, new_e)} (МСК)")
            continue

        if f in {"start_price", "price"}:
            cur = lot_before.get("currency")
            ce = _cur_emoji(cur)
            old_s = "—" if old is None else f"{old} {ce}"
            new_s = "—" if new is None else f"{new} {ce}"
            lines.append(f"💰 <b>Цена:</b> {old_s} → {new_s}")
            continue

        if f == "currency":
            lines.append(f"💱 <b>Валюта:</b> {old or '—'} → {new or '—'}")
            continue

        if f in {"comment", "note"}:
            o = (old or "—").strip() if isinstance(old, str) else (old or "—")
            n = (new or "—").strip() if isinstance(new, str) else (new or "—")
            lines.append(f"💬 <b>Комментарий:</b> {o} → {n}")
            continue

        if f in {"image_id", "photo", "media"}:
            lines.append(f"🖼 <b>Фото:</b> {_short_media_id(old)} → {_short_media_id(new)}")
            continue

        if f in {"craft_uid_possible", "craft_uid", "uid"}:
            lines.append(f"🆔 <b>Крафт на UID:</b> {_yn_uid(old)} → {_yn_uid(new)}")
            continue

        if f in {"auction_kind", "kind", "type"}:
            lines.append(f"⚙️ <b>Тип аука:</b> {old or '—'} → {new or '—'}")
            continue

        # fallback
        lines.append(f"✏️ <b>{field}:</b> {old if old is not None else '—'} → {new if new is not None else '—'}")

    return lines


def _build_owner_notice_text(
        *,
        title: str,
        lot_after: dict,
        lot_before: dict,
        owners_for_status: list[dict],
        changes: list[tuple[str, Any, Any]],
        moderator: types.User,
) -> str:
    card_name = lot_after.get("card_name") or "—"
    hero_name = lot_after.get("hero_name") or "—"
    auction_id = lot_after.get("auction_id") or lot_before.get("auction_id") or "—"

    # время текущего слота (после изменения)
    cur_window = _fmt_window_msk(lot_after.get("start_time"), lot_after.get("end_time"))

    # статус пользователя: если несколько владельцев, показываем статус первого (обычно один владелец)
    status_label = _user_status_label(owners_for_status[0]) if owners_for_status else "—"

    deck_id = lot_after.get("deck_id") or lot_after.get("deck_num") or "—"
    deck_name = lot_after.get("deck_name") or lot_after.get("deck") or "—"

    rarity = lot_after.get("rarity") or "—"
    obtain_type = lot_after.get("obtain_type")
    obtain_amount = lot_after.get("obtain_amount")
    obtain_line = "🎁 —"
    if obtain_amount is not None:
        obtain_line = f"🎁 +{obtain_amount} {_obtain_emoji(obtain_type)}"

    sold_cnt = _pick_sold_count(lot_after)
    sold_line = f"📊 {sold_cnt}" if sold_cnt is not None else "📊 —"

    story = lot_after.get("story") or "—"
    quote = lot_after.get("quote") or "—"

    change_lines = _format_change_lines(lot_before, changes)
    changes_block = "\n".join(change_lines) if change_lines else "—"

    # тот самый “как при подаче заявки” блок
    meta_block = (
        f"👤 <b>Статус пользователя:</b> {status_label}\n"
        f"Колода: 🃏 {deck_id} колода — {deck_name}\n"
        f"Редкость: {_rarity_line(rarity)}\n"
        f"Крафт на UID возможен: {_yn_uid(lot_after.get('craft_uid_possible'))}\n"
        f"Продано ранее: {sold_line}\n"
        f"При получении в подарок даёт: {obtain_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
        f"Оплата ставки в течение месяца."
    )

    mod_line = f"Лот изменён модератором: {admin_tag(moderator)}"
    thanks_line = "Если хочешь, можешь сказать спасибо ниже ❤️\n"

    return (
        f"<b>{title}</b>\n\n"
        f"Лот: <b>{card_name}</b> — <i>{hero_name}</i>\n"
        f"ID: <code>{auction_id}</code>\n"
        f"Текущее время аукциона: {cur_window} (МСК)\n\n"
        f"<b>Изменения:</b>\n{changes_block}\n\n"
        f"{meta_block}\n\n"
        f"{mod_line}\n"
        f"{thanks_line}"
    )


async def _bot_send_media_any(
        bot,
        *,
        chat_id: int,
        file_id: str | None,
        caption: str,
        reply_markup,
) -> None:
    """Пробуем фото -> видео -> анимация -> текст."""
    if not file_id:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=reply_markup)
        return

    try:
        await bot.send_photo(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    try:
        await bot.send_video(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    try:
        await bot.send_animation(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except Exception:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=reply_markup)


async def _notify_owners_and_log(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        title: str,
        changes: list[tuple[str, Any, Any]],
        action_type: str,
) -> None:
    lot_before = await get_lot_by_id(auction_id)
    lot_after = await get_lot_by_id(auction_id)  # после апдейта вызовут ещё раз, но пусть будет безопасно

    # владельцы (и статусы/уровни если есть)
    owners_rows = await get_lot_owners_with_levels(bot, auction_id)
    owners_text = await get_lot_owners_text(auction_id)

    # owner notice text
    owner_caption = _build_owner_notice_text(
        title=title,
        lot_after=lot_after,
        lot_before=lot_before,
        owners_for_status=owners_rows or [],
        changes=changes,
        moderator=admin_user,
    )

    # кнопка спасибо (по аналогии с удалением)
    thanks_kb = await build_thanks_kb(auction_id, admin_tag(admin_user))

    # рассылаем владельцам
    sent_to: set[int] = set()
    for row in owners_rows or []:
        try:
            uid = int(row.get("user_id"))
        except Exception:
            continue
        if uid in sent_to:
            continue
        sent_to.add(uid)
        try:
            await _bot_send_media_any(
                bot,
                chat_id=uid,
                file_id=(lot_after.get("image_id") or lot_after.get("photo_id")),
                caption=owner_caption,
                reply_markup=thanks_kb,
            )
        except Exception:
            # владелец мог закрыть ЛС, заблокировать бота, etc.
            pass

    # лог в админ-чаты
    log_lines = _format_change_lines(lot_before, changes)
    log_text = (
            f"✏️ <b>Изменение лота в расписании</b>\n"
            f"Лот <code>{auction_id}</code>: <b>{lot_after.get('card_name') or '—'}</b> — <i>{lot_after.get('hero_name') or '—'}</i>\n"
            f"Модератор: {admin_tag(admin_user)}\n\n"
            f"<b>Изменения:</b>\n" + ("\n".join(log_lines) if log_lines else "—") + "\n\n"
                                                                                    f"<b>Владельцы:</b> {owners_text}"
    )
    try:
        await send_admin_log(bot, log_text)
    except Exception:
        pass

    # audit в БД
    try:
        await log_admin_action(
            admin_user.id,
            action_type,
            auction_id,
            f"title={title}; changes={[(a, str(b), str(c)) for a, b, c in changes]}",
        )
    except Exception:
        pass


async def apply_scheduled_time_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_start: datetime,
        new_end: datetime,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_start = lot.get("start_time")
    old_end = lot.get("end_time")

    # обновляем расписание
    moderation_service = await AuctionModerationService.create()
    await moderation_service.reschedule(
        auction_id,
        start_time=new_start,
        end_time=new_end,
    )

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="⏳ Лот перенесён",
        changes=[("time_window", (old_start, old_end), (new_start, new_end))],
        action_type="schedule_edit_time",
    )


async def apply_scheduled_price_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_price: int,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_price = lot.get("start_price")
    await _update_auction_field(auction_id, "start_price", int(new_price))

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💰 Цена лота изменена",
        changes=[("start_price", old_price, int(new_price))],
        action_type="schedule_edit_price",
    )


async def apply_scheduled_currency_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_currency: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_currency = lot.get("currency")
    await _update_auction_field(auction_id, "currency", (new_currency or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💱 Валюта лота изменена",
        changes=[("currency", old_currency, (new_currency or "").strip())],
        action_type="schedule_edit_currency",
    )


async def apply_scheduled_comment_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_comment: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_comment = lot.get("comment")
    await _update_auction_field(auction_id, "comment", (new_comment or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💬 Комментарий к лоту изменён",
        changes=[("comment", old_comment, (new_comment or "").strip())],
        action_type="schedule_edit_comment",
    )


async def apply_scheduled_photo_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_image_id: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_image_id = lot.get("image_id")
    await _update_auction_field(auction_id, "image_id", (new_image_id or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="🖼 Фото лота изменено",
        changes=[("image_id", old_image_id, (new_image_id or "").strip())],
        action_type="schedule_edit_photo",
    )


async def apply_scheduled_auction_kind_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_kind: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_kind = lot.get("auction_kind")
    await _update_auction_field(auction_id, "auction_kind", (new_kind or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="⚙️ Тип аука изменён",
        changes=[("auction_kind", old_kind, (new_kind or "").strip())],
        action_type="schedule_edit_kind",
    )


async def apply_scheduled_craft_uid_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_value: bool,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_val = lot.get("craft_uid_possible")
    await _update_auction_field(auction_id, "craft_uid_possible", bool(new_value))

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="🆔 Крафт на UID изменён",
        changes=[("craft_uid_possible", old_val, bool(new_value))],
        action_type="schedule_edit_craft_uid",
    )


__all__ = (
    'EX_WHOLE_DECK_MODES',
    '_update_auction_field',
    '_short_media_id',
    '_fmt_dt_msk',
    '_fmt_window_msk',
    '_obtain_emoji',
    '_yn_uid',
    '_rarity_line',
    '_pick_sold_count',
    '_user_status_label',
    '_format_change_lines',
    '_build_owner_notice_text',
    '_bot_send_media_any',
    '_notify_owners_and_log',
    'apply_scheduled_time_change',
    'apply_scheduled_price_change',
    'apply_scheduled_currency_change',
    'apply_scheduled_comment_change',
    'apply_scheduled_photo_change',
    'apply_scheduled_auction_kind_change',
    'apply_scheduled_craft_uid_change',
)

