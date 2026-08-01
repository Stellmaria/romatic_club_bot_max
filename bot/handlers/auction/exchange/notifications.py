from __future__ import annotations

"""Exchange flow component extracted during refactoring phase 7."""

from html import escape as escape_html
from aiogram import Bot
from aiogram.types import Message
from bot.services.luxury import get_user_luxury_level, is_luxury_member
from bot.telegram.media import answer_media_any as _answer_media_any
from bot.core.legacy_config import legacy_config
from db.legacy import count_sold_by_card_id, count_sold_same_card, get_deck_by_id, get_user, is_admin

from .common import (
    exchange_gain_for_card,
    exchange_gift_for_card,
    gift_emoji,
    escape_html,
    rarity_badge,
    rarity_norm,
    currency_to_emoji,
    h,
)

async def _uid_verification_badge(user_id: int) -> str:
    try:
        from db.legacy import get_user_verified_uid, is_user_uid_banned
        if await is_user_uid_banned(int(user_id)):
            return "⛔️ UID в ЧС"
        uid = await get_user_verified_uid(int(user_id))
        return "✅ UID верифицирован" if uid else "❌ НЕТ ВЕРИФИКАЦИИ"
    except Exception:
        return "❌ НЕТ ВЕРИФИКАЦИИ"


async def _format_user_status(bot: Bot, user_id: int) -> str:
    # 1) админ
    try:
        if await is_admin(int(user_id)):
            return "🛡 Админ"
    except Exception:
        pass

    # 2) лакшери по чатам (самый надёжный источник)
    try:
        if legacy_config.LUXURY_CHAT_ID_LVL2 and await is_luxury_member(bot, user_id, legacy_config.LUXURY_CHAT_ID_LVL2):
            return "👑 Лакшери 2"
        if legacy_config.LUXURY_CHAT_ID and await is_luxury_member(bot, user_id, legacy_config.LUXURY_CHAT_ID):
            return "👑 Лакшери"
    except Exception:
        pass

    # 3) fallback на БД
    try:
        row = await get_user(int(user_id))
        if row:
            if bool(row.get("is_luxury")):
                return "👑 Лакшери"
            if bool(row.get("is_trusted")):
                return "🤝 Доверенный"
    except Exception:
        pass

    badge = await _uid_verification_badge(int(user_id))
    return f"👤 Обычный • {badge}"


async def _send_user_exchange_confirmation(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = escape_html(preview.get("hero_name") or "—")
    card_name = escape_html(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            deck_line = f"🧩 {int(deck_id)} колода — {name}" if name else f"🧩 {int(deck_id)} колода"
        except Exception:
            deck_line = f"🧩 {int(deck_id)} колода"

    # редкость
    rn = rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    # подарок/профит
    obtain_type, obtain_amount = exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = escape_html(preview.get("story") or "—")
    quote = escape_html(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {escape_html(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅ можно пересылать/скринить
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


async def _send_user_exchange_confirmation_multi(
        message: Message,
        *,
        user_id: int,
        created: list[dict],  # [{"batch_id": int, "card": dict, "price": int, "gain": int}]
        currency: str,
        comment: str,
        deck_id: int | None,
        mode: str,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # режим по-русски
    mode_key = (mode or "").strip().lower()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, mode or "—")

    # колода
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            if name:
                if name.lower().startswith(str(int(deck_id))):
                    deck_line = f"🧩 {h(name)}"
                else:
                    deck_line = f"🧩 {int(deck_id)} колода — {h(name)}"
        except Exception:
            pass

    # превью для медиа
    preview_card = (created[0].get("card") or {}) if created else {}
    file_id = (preview_card.get("image_id") or "").strip()

    # определяем: это “копии одной карты”?
    same_card = False
    if created:
        c0 = created[0].get("card") or {}
        cid0 = c0.get("card_id")
        same_card = all(((x.get("card") or {}).get("card_id") == cid0) for x in created)

    caption = (
        "✅ <b>Заявки отправлены на модерацию</b>\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Режим: <b>{escape_html(mode_ru)}</b>\n\n"
    )

    if same_card and created:
        c = created[0]["card"]
        hero = escape_html(c.get("hero_name"))
        name = escape_html(c.get("card_name"))
        price = int(created[0].get("price") or 0)
        caption += (
                f"Карта: <b>{hero} — {name}</b>\n"
                f"Экземпляров: <b>{len(created)}</b>\n"
                f"Стоимость (фикс.) за 1: <b>{price}</b> {cur_emoji}\n\n"
                "IDs лотов: " + ", ".join(f"<code>{int(x['batch_id'])}</code>" for x in created) + "\n"
        )
    else:
        caption += f"Создано лотов: <b>{len(created)}</b>\n\n"
        for x in created:
            bid = int(x["batch_id"])
            c = x.get("card") or {}
            hero = escape_html(c.get("hero_name"))
            name = escape_html(c.get("card_name"))
            rn = rarity_norm(c.get("rarity") or c.get("rarity_norm"))
            price = int(x.get("price") or 0)
            caption += f"• <b>{hero} — {name}</b> ({escape_html(rn)}) → №<code>{bid}</code> • <b>{price}</b> {cur_emoji}\n"

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {escape_html(comment)}"

    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


async def _send_user_exchange_confirmation_copies(
        message: Message,
        *,
        batch_ids: list[int],
        user_id: int,
        card: dict,
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    status_line = await _format_user_status(message.bot, int(user_id))

    hero = h(card.get("hero_name") or "—")
    name = h(card.get("card_name") or "—")

    rn = rarity_norm(card.get("rarity") or card.get("rarity_norm"))
    rarity_line = f"{rarity_badge(rn)} {h(rn or '—')}"

    sold = "—"
    try:
        if card.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(card["card_id"])) or 0))
    except Exception:
        pass

    ot, oa = exchange_gain_for_card(card)
    gift_line = f"🎁 +{int(oa)} {gift_emoji(ot)}" if oa else "—"

    story = h(card.get("story") or "—")
    quote = h(card.get("quote") or "—")

    # колода красиво
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            nm = (d.get("name") or "").strip() if d else ""
            if nm:
                deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                    str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
        except Exception:
            pass

    ids_line = ", ".join(str(int(x)) for x in batch_ids)

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лоты биржи №<b>{h(ids_line)}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {name}\n"
        f"Экземпляров: <b>{len(batch_ids)}</b>\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {h(sold)}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {h(comment.strip())}"

    file_id = (card.get("image_id") or "").strip()
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )
        if sent:
            return

    await message.answer(caption, parse_mode="HTML")


async def _send_user_exchange_confirmation_deck_split(
        message: Message,
        *,
        created: list[tuple[int, dict, int]],  # (batch_id, card, price)
        user_id: int,
        deck_id: int,
) -> None:
    status_line = await _format_user_status(message.bot, int(user_id))

    deck_line = f"🧩 {int(deck_id)} колода"
    try:
        d = await get_deck_by_id(int(deck_id))
        nm = (d.get("name") or "").strip() if d else ""
        if nm:
            deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
    except Exception:
        pass

    lines = [
        "✅ <b>Заявки отправлены на модерацию</b>\n",
        f"Статус пользователя: {status_line}",
        f"Колода: {deck_line}",
        "Режим: <b>Разбор колоды</b>\n",
        f"Создано лотов: <b>{len(created)}</b>\n",
    ]

    for bid, c, price in created:
        hero = h(c.get("hero_name") or "—")
        name = h(c.get("card_name") or "—")
        rn = rarity_norm(c.get("rarity") or c.get("rarity_norm"))
        ot, oa = exchange_gain_for_card(c)
        gain = f"+{int(oa)}{gift_emoji(ot)}" if oa else "—"
        lines.append(f"• №<code>{int(bid)}</code> {hero} — {name} ({h(rn)}) • <b>{int(price)}</b>💎 • {gain}")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _send_user_exchange_confirmation_card(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    # Это твоя старая логика “по карте” (Ливий, редкость, цитата, история…)
    # Можно просто перенести сюда код из старого дубля, который сейчас у тебя в auctions.py.
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = escape_html(preview.get("hero_name") or "—")
    card_name = escape_html(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя
    try:
        lux = int(await get_user_luxury_level(message.bot, user_id) or 0)
    except Exception:
        lux = 0
    status_line = f"👑 Лакшери {lux}" if lux > 0 else "👤 Обычный"

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            if d and d.get("name"):
                deck_line = f"🧩 {deck_id} колода — {d['name']}"
            else:
                deck_line = f"🧩 {deck_id} колода"
        except Exception:
            deck_line = f"🧩 {deck_id} колода"

    # редкость
    rn = rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    obtain_type, obtain_amount = exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = escape_html(preview.get("story") or "—")
    quote = escape_html(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {escape_html(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        try:
            sent = await _answer_media_any(message, file_id, caption=caption, reply_markup=None)
        except Exception:
            sent = None

    if not sent:
        await message.answer(caption, parse_mode="HTML")

# Public feature contracts. Private names remain temporary local aliases.
send_user_exchange_confirmation = _send_user_exchange_confirmation
send_user_exchange_confirmation_copies = _send_user_exchange_confirmation_copies
send_user_exchange_confirmation_deck_split = _send_user_exchange_confirmation_deck_split
