from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterable

from aiogram import Bot

from bot.handlers.admin.services.market_constants import STAR_DB_CODE, _EXTRAS_HEAD_RE, _EXTRAS_TAIL_RE, _RU_WORD
from bot.handlers.admin.services.market_db_helpers import fetch_card
from bot.handlers.admin.services.market_keyboards import my_listing_actions
from bot.handlers.admin.services.market_utils import fiat_flag
from bot.services.market import (
    market_get_listing,
    market_has_any_proof,
    market_listing_display_tiers,
    market_listing_items,
    market_listing_reload_view,
    market_price_map,
)

try:
    import pymorphy2  # type: ignore
except Exception:
    pymorphy2 = None  # type: ignore


def _morph() -> "pymorphy2.MorphAnalyzer|None":
    """
    Ленивая и безопасная инициализация морфо-анализатора.
    Возвращает None, если pymorphy2 нет или он сломался.
    """
    if pymorphy2 is None:
        return None
    try:
        return pymorphy2.MorphAnalyzer(lang="ru")
    except Exception:
        return None


_MORPH = _morph()


def build_card_preview_caption(
        card: dict,
        per_card_prices: dict | None,
        cash_code: str | None,
        description: str | None,
        *,
        has_proof: bool = False,
        qty_available: int | None = None,
        status: str | None = None,
        created_at: datetime | None = None,  # ← добавили
) -> str:
    CASH_FLAGS: dict[str, str] = {
        "BYN": "🇧🇾", "RUB": "🇷🇺", "UAH": "🇺🇦", "KZT": "🇰🇿",
        "USD": "🇺🇸", "EUR": "🇪🇺", "PLN": "🇵🇱", "TRY": "🇹🇷",
        "GEL": "🇬🇪", "AZN": "🇦🇿", "AMD": "🇦🇲", "KGS": "🇰🇬",
        "UZS": "🇺🇿", "MDL": "🇲🇩",
    }

    TREASURE_ICON = "🪙"  # можно "📦"
    TREASURE_NOTE = " (сокровищ в игре)"
    SHOW_TREASURE_NOTE = True
    STARS_ICON = "⭐"
    STARS_NOTE = " (звёзды TG)"

    title = card.get("title") or card.get("name") or card.get("card_name") or "—"
    hero = card.get("hero") or card.get("hero_name")
    rarity = card.get("rarity") or card.get("tier") or card.get("nominal")

    head = title
    if hero and rarity:
        head = f"{title} — {hero} [{rarity}]"
    elif hero:
        head = f"{title} — {hero}"
    elif rarity:
        head = f"{title} [{rarity}]"

    lines: list[str] = [f"<b>{head}</b>"]

    if per_card_prices:
        lines += ["", "Цены:"]

        def add(pfx: str, val: str | int | float):
            lines.append(f"• {pfx} {val}")

        for k, v in per_card_prices.items():
            key = str(k).lower()

            if key.startswith("cash:"):
                code = str(k).split(":", 1)[1].upper()
                flag = CASH_FLAGS.get(code, "")
                pfx = f"{flag} {code}" if flag else code
                add(pfx, f"{float(v):.2f}")

            elif key == "diamonds":
                add("💎", int(v))

            elif key == "cups":
                add("☕", int(v))

            elif key in ("treasure", "treasures"):
                note = TREASURE_NOTE if SHOW_TREASURE_NOTE else ""
                add(TREASURE_ICON, f"{int(v)}{note}")

            elif key in ("tgstars", "tg_stars", "stars"):
                add(STARS_ICON, f"{int(v)}{STARS_NOTE}")

    if qty_available is not None:
        lines += ["", f"Доступно: {qty_available}"]
    lines.append("Фото подтверждения: есть" if has_proof else "Фото подтверждения: отсутствует")

    if status:
        ru = {"active": "активно", "hidden": "скрыто", "sold": "продано", "archived": "архив"}
        lines += ["", f"<i>Статус: {ru.get(status.lower(), status)}</i>"]

    if created_at:
        def _fmt_age_local(dt: datetime) -> str:
            now = datetime.utcnow() if dt.tzinfo is None else datetime.now(tz=dt.tzinfo)
            secs = int((now - dt).total_seconds())
            d, r = divmod(secs, 86400)
            h, r = divmod(r, 3600)
            m, _ = divmod(r, 60)
            if d: return f"{d}д {h}ч"
            if h: return f"{h}ч {m}м"
            return f"{m}м"

        lines += ["", f"Создано: {created_at:%d.%m %H:%M} • висит {_fmt_age_local(created_at)}"]

    if description and description.strip():
        lines += ["", description.strip()]

    return "\n".join(lines).strip()


def _fmt_tiers_for_view(tiers: list[dict]) -> str:
    out = []
    for t in sorted(tiers, key=lambda x: x.get("sort_order") or 0):
        qty = int(t.get("qty") or 0)
        pay = t.get("pay_type") or t.get("cash_code") or ""
        price = t.get("price")
        out.append(f"• {qty} алм. → {price:g} {pay}")
    return "\n".join(out)


def _fmt_age(dt: datetime) -> str:
    if not dt:
        return "—"
    now = datetime.now(tz=timezone.utc if dt.tzinfo else None)
    delta = now - dt
    secs = int(delta.total_seconds())
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}д")
    if h: parts.append(f"{h}ч")
    if not d and m: parts.append(f"{m}м")
    return " ".join(parts) or "меньше минуты"


def _badge_status(status: str) -> str:
    return "🟢 Актуально" if status == "active" else "⚪ Неактуально"


def _kind_emoji(kind: str) -> str:
    return {
        "cards": "🃏", "whole_deck": "📚", "diamonds": "💎",
        "cups": "☕", "treasures": "🏴‍☠️", "service": "🛠",
    }.get(kind, "🛒")


def render_price_tiers(tiers: list[dict]) -> str:
    lines: list[str] = []
    for t in tiers:
        qty = t.get("qty")
        label = t.get("label")
        left = str(qty) if qty else (label or "")
        pay = t.get("pay_type")
        code = t.get("cash_code")
        emoji = {"diamonds": "💎", "cups": "☕", "treasures": "🏴‍☠️"}.get(pay, "")
        right = f"{t['price']:.2f} {code or ''}".strip() if pay == "cash" else f"{int(t['price'])}"
        lines.append(f"{left} {emoji} — {right}")
    return "\n".join(lines)


def render_cards_summary(deck_id: int | None, items_count: int | None, price_line: str | None) -> str:
    parts = []
    if deck_id:
        parts.append(f"Колода: #{deck_id}")
    if items_count:
        parts.append(f"Позиций: {items_count}")
    if price_line:
        parts.append(f"Цена за штуку: {price_line}")
    return " · ".join(parts)


async def _collect_price_map(listing_id: int) -> dict:
    return await market_price_map(listing_id)


async def _has_any_proof(listing_id: int) -> bool:
    return await market_has_any_proof(listing_id)


async def _reload_listing_inplace(src_msg_or_call, lid: int) -> None:
    message = getattr(src_msg_or_call, "message", None) or src_msg_or_call
    bot = message.bot

    view = await market_listing_reload_view(lid)
    if not view or not view.get("card_id"):
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id, message_id=message.message_id,
                text="Эта запись больше не доступна."
            )
        except Exception:
            pass
        return
    cid = int(view["card_id"])
    status = view.get("status") or ""
    desc = view.get("description")
    created_at = view.get("created_at")
    qty_left = int(view.get("quantity_left") or 0)
    has_proof = bool(view.get("cover_file_id")) or int(view.get("item_proof_count") or 0) > 0
    price_map = await market_price_map(lid)

    card = await fetch_card(cid)
    caption = build_card_preview_caption(
        card,
        price_map,
        None,
        desc,
        has_proof=has_proof,
        qty_available=qty_left,
        status=status,
        created_at=created_at,
    )

    try:
        if message.photo:
            await bot.edit_message_caption(
                chat_id=message.chat.id, message_id=message.message_id,
                caption=caption, parse_mode="HTML",
                reply_markup=my_listing_actions(lid, status)
            )
        else:
            await bot.edit_message_text(
                chat_id=message.chat.id, message_id=message.message_id,
                text=caption, parse_mode="HTML",
                reply_markup=my_listing_actions(lid, status)
            )
        return
    except Exception:
        pass

    if card.get("image_id"):
        await message.answer_photo(
            card["image_id"], caption=caption, parse_mode="HTML",
            reply_markup=my_listing_actions(lid, status)
        )
    else:
        await message.answer(
            caption, parse_mode="HTML",
            reply_markup=my_listing_actions(lid, status)
        )


async def send_listing_preview(bot: Bot, chat_id: int, listing_id: int):
    listing = await market_get_listing(listing_id)
    if not listing:
        await bot.send_message(chat_id, "Лот не найден.")
        return

    items = await market_listing_items(listing_id)
    tiers = await market_listing_display_tiers(listing_id)

    per_card_prices: dict[str, float | int] = {}
    for t in tiers or []:
        if int(t["qty"]) != 1:
            continue
        pt = str(t["pay_type"]).lower()
        if pt == "cash":
            code = (t["cash_code"] or "").upper() or "BYN"
            per_card_prices[f"cash:{code}"] = float(t["price"])
        elif pt in ("cups", "diamonds", "treasure", "treasures", "tgstars"):
            per_card_prices[pt] = int(float(t["price"]))
        else:
            per_card_prices[pt] = float(t["price"])

    if not items:
        card_for_caption = {"title": f"Лот #{listing_id}"}
        qty_left = 0
        image_id = listing["cover_file_id"]
    else:
        qty_left = sum(int(x["quantity"]) for x in items)
        if len(items) == 1:
            it = items[0]
            card_for_caption = {
                "title": it["card_name"],
                "hero": it["hero_name"],
                "rarity": it["rarity"],
            }
            image_id = it["image_id"] or listing["cover_file_id"]
        else:
            card_for_caption = {"title": f"Лот из {len(items)} карт"}
            image_id = listing["cover_file_id"] or items[0]["image_id"]

    has_proof = bool(listing["cover_file_id"])
    if not has_proof:
        pass

    caption = build_card_preview_caption(
        card_for_caption,
        per_card_prices or None,
        cash_code=None,
        description=listing["description"],
        has_proof=has_proof,
        qty_available=qty_left,
        status=listing["status"],
        created_at=listing["created_at"],
    )

    if image_id:
        await bot.send_photo(chat_id, image_id, caption=caption, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML")


def _format_extra_for_summary(item: str) -> str:
    s = (item or "").strip()
    m = _EXTRAS_HEAD_RE.match(s)
    if m:
        return f"• {html.escape(m.group(2).strip())} ×{int(m.group(1))}"
    m = _EXTRAS_TAIL_RE.match(s)
    if m:
        return f"• {html.escape(m.group(1).strip())} ×{int(m.group(2))}"
    return f"• {html.escape(s)}"


def _title_like(src: str, dst: str) -> str:
    if not src:
        return dst
    return dst.capitalize() if src[0].isupper() else dst


def _inflect_word_acc(word: str) -> str:
    if not _RU_WORD.match(word):
        return word

    if _MORPH:
        p = _MORPH.parse(word)[0]
        target = p.inflect({"accs"}) or p
        return _title_like(word, target.word)
    return _fallback_inflect_word_acc(word)


def inflect_phrase_accusative(text: str) -> str:
    if not text:
        return text

    tokens = re.findall(r"[A-Za-zА-Яа-яЁё\-]+|\s+|[^\w\s]", text)
    out = []
    changed_head = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not changed_head and _RU_WORD.match(t):
            j = i
            buf = []
            while j < len(tokens) and _RU_WORD.match(tokens[j]):
                buf.append(tokens[j])
                j += 1
            buf = [_inflect_word_acc(w) for w in buf]
            out.extend(buf)
            changed_head = True
            i = j
            continue
        out.append(t)
        i += 1
    return "".join(out).strip()


def join_human(items: Iterable[str]) -> str:
    parts = [s for s in (s.strip() for s in items) if s]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " и " + parts[-1]


def build_accepts_sentence(raw_items: Iterable[str]) -> str | None:
    items = [inflect_phrase_accusative(x) for x in raw_items if x and str(x).strip()]
    if not items:
        return None
    return f"Дополнительно принимаю: {join_human(items)}"


def build_market_caption(listing: dict, tiers: list[dict] | None, seller_username: str | None) -> str:
    kind = listing.get("offer_kind") or "cards"
    head = _kind_emoji(kind)
    status_line = _badge_status(listing.get("status"))
    seller_line = f"Продавец: @{html.escape(seller_username)}" if seller_username else "Продавец: [кнопка ниже]"

    lines = [f"<b>{head} Объявление #{listing['listing_id']}</b>  ·  {status_line}", seller_line]

    desc = listing.get("description")
    if desc:
        lines += ["", html.escape(desc.strip())[:1000]]

    if tiers:
        def _fmt_tier(t: dict) -> str:
            pay = t.get("pay_type")
            left = t.get("label") or (str(t.get("qty")) if t.get("qty") else "")
            left = (left + " " if left else "")

            if pay == "cups":
                emoji = "☕"
                right = f"{_fmt_num(t.get('price'))} чашки"
            elif pay == "diamonds":
                emoji = "💎"
                right = f"{_fmt_num(t.get('price'))} алмазов"
            elif pay == "treasures":
                emoji = "🏴‍☠️"
                right = f"{_fmt_num(t.get('price'))} сокровищ"
            else:
                emoji = "💰"
                code = str(t.get("cash_code") or "").upper()
                flag = fiat_flag(code)
                price = _fmt_num(t.get("price"))
                right = f"{flag} {price} {code}".strip()

            return f"{left}{emoji} — {right}"

        lines += ["", "<b>Цены:</b>", "<code>" + "\n".join(_fmt_tier(t) for t in tiers) + "</code>"]

    else:
        cur = listing.get("currency_type")
        price = listing.get("price_num")
        cash_code = (listing.get("cash_code") or "").upper()
        if cur == "cups":
            emoji = "☕"
            right = f"{_fmt_num(price)} чашки"
        elif cur == "diamonds":
            emoji = "💎"
            right = f"{_fmt_num(price)} алмазов"
        elif cur == "treasures":
            emoji = "🏴‍☠️"
            right = f"{_fmt_num(price)} сокровищ"
        else:
            emoji = "💰"
            flag = fiat_flag(cash_code)
            right = f"{flag} {_fmt_num(price)} {cash_code}".strip()
        lines += ["", "<b>Цены:</b>", f"<code>{emoji} — {right}</code>"]


def build_price_lines(
        price_map: dict | None = None,
        cash_code: str | None = None,
        tiers: list[dict] | None = None,
) -> list[str]:
    lines: list[str] = []

    def add_cups(v):
        lines.append(f"• ☕ {int(float(v))}")

    def add_diams(v):
        lines.append(f"• 💎 {int(float(v))}")

    def add_treas(v):
        lines.append(f"• ⚒ {int(float(v))}")

    def add_cash(v, code):
        code_up = (code or "").upper()
        flag = fiat_flag(code_up) or ""
        flag_part = f"{flag} " if flag else ""
        amt = f"{float(v):.2f}"
        tail = f"{amt} {code_up}".strip()
        lines.append(f"• {flag_part}{tail}".rstrip())

    if tiers:
        for t in tiers:
            ptype = (t.get("pay_type") or "").lower()
            price = t.get("price")
            if price is None:
                continue
            if ptype == "cups":
                add_cups(price)
            elif ptype == "diamonds":
                add_diams(price)
            elif ptype == "treasures":
                add_treas(price)
            elif ptype == "cash":
                add_cash(price, t.get("cash_code"))
        return lines

    pm = dict(price_map or {})

    if pm.get("cups"):       add_cups(pm["cups"])
    if pm.get("diamonds"):   add_diams(pm["diamonds"])
    if pm.get("treasures"):  add_treas(pm["treasures"])

    if "cash" in pm and pm["cash"]:
        add_cash(pm["cash"], cash_code)

    fiat_map: dict[str, float] = {}
    for k, v in pm.items():
        if isinstance(k, str) and k.startswith("cash:"):
            code = k.split(":", 1)[1].upper()
            try:
                fiat_map[code] = float(v)
            except (TypeError, ValueError):
                continue

    if fiat_map:
        order = ["BYN", "RUB", "UAH", "KZT", "USD"]
        codes = [c for c in order if c in fiat_map] + sorted(c for c in fiat_map if c not in order)
        for code in codes:
            add_cash(fiat_map[code], code)

    return lines


def _fmt_num(val) -> str:
    try:
        n = float(val)
    except Exception:
        return str(val)
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def _fallback_inflect_word_acc(word: str) -> str:
    w = word
    lw = w.lower()

    for a, b in (("ая", "ую"), ("яя", "юю")):
        if lw.endswith(a):
            return _title_like(w, lw[: -len(a)] + b)

    for a, b in (("ка", "ку"), ("га", "гу"), ("ха", "ху"), ("ша", "шу"),
                 ("ча", "чу"), ("жа", "жу"), ("а", "у"), ("я", "ю")):
        if lw.endswith(a):
            return _title_like(w, lw[: -len(a)] + b)
    return w
