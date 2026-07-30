import html
from datetime import datetime
from datetime import timezone
from typing import Tuple, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.services.market_constants import BUMP_COOLDOWN, TIER_RE, MAP_PAY
from db.db import market_get_listing, market_add_rate_tiers


async def can_publish_more(_: Bot, user_id: int) -> Tuple[bool, int]:
    return True, 999_999


async def ensure_owner(call: CallbackQuery, listing_id: int) -> bool:
    listing = await market_get_listing(listing_id)
    ok = listing and listing["seller_id"] == call.from_user.id
    if not ok:
        await call.answer("Недостаточно прав.", show_alert=True)
    return bool(ok)


def can_bump_now(listing: dict) -> Tuple[bool, int]:
    last = listing.get("updated_at")
    if last is None:
        return True, 0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    left = BUMP_COOLDOWN - (datetime.now(timezone.utc) - last)
    secs = int(left.total_seconds())
    return secs <= 0, max(0, secs)


def get_selected_ids(data: dict) -> set[int]:
    return set(int(x) for x in (data.get("card_ids") or []))


async def add_selected_id(state: FSMContext, cid: int, limit: Optional[int]) -> bool:
    data = await state.get_data()
    sel = get_selected_ids(data)
    if limit is not None and len(sel) >= limit:
        return False
    sel.add(int(cid))
    await state.update_data(card_ids=sorted(sel))
    return True


async def remove_selected_id(state: FSMContext, cid: int) -> None:
    data = await state.get_data()
    sel = get_selected_ids(data)
    sel.discard(int(cid))
    await state.update_data(card_ids=sorted(sel))


async def safe_delete(obj: Message | None = None, *, bot: Bot | None = None, chat_id: int | None = None,
                      message_id: int | None = None) -> None:
    try:
        if obj is not None:
            await obj.delete()
        elif bot and chat_id and message_id:
            await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "message to delete not found" in s or "message can't be deleted" in s:
            return
        raise
    except Exception:
        return


def _normalize_pay_type(pay_type: str, cash_code: str | None = None) -> tuple[str, str | None]:
    if (pay_type or "").lower() == "tgstars":
        return "cash", "TGS"
    return pay_type, cash_code


async def _upsert_price(lid: int, pay_type: str, price: float | None, cash_code: str | None = None) -> None:
    import importlib
    pay_type, cash_code = _normalize_pay_type(pay_type, cash_code)
    dbmod = importlib.import_module("db.db")
    pool = getattr(dbmod, "db_pool", None)
    if pool is None:
        return

    delete_sql = """
                 DELETE
                 FROM market_rate_tiers
                 WHERE listing_id = $1
                   AND pay_type = $2
                   AND COALESCE(cash_code, '') = COALESCE($3, '') \
                 """
    try:
        await pool.execute(delete_sql, lid, pay_type, cash_code)  # type: ignore[attr-defined]
    except AttributeError:
        async with pool.acquire() as conn:
            await conn.execute(delete_sql, lid, pay_type, cash_code)

    if price is None:
        return

    tier = [{
        "label": None, "qty": None, "pay_type": pay_type,
        "cash_code": cash_code, "price": float(price), "sort_order": 999
    }]
    await market_add_rate_tiers(lid, tier)


def parse_tiers(text: str) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        m = TIER_RE.match(line)
        if not m:
            continue
        qty = m.group("qty")
        label = m.group("label")
        price = float(m.group("price").replace(",", "."))
        token = m.group("pay")
        cash_code = None
        if token:
            low = token.lower()
            if low in MAP_PAY:
                pay_type = MAP_PAY[low]
            elif len(token) == 3 and token.isalpha():
                pay_type, cash_code = "cash", token.upper()
            else:
                pay_type = "cash"
        else:
            pay_type = "cash"
        out.append({
            "label": label.strip() if label else None,
            "qty": int(qty.rstrip("+")) if qty else None,
            "pay_type": pay_type,
            "cash_code": cash_code,
            "price": price,
            "sort_order": i,
        })
    return out


def _distinct_cards_count(data: dict) -> int:
    raw = data.get("card_ids") or data.get("cards") or []
    ids: set[int] = set()
    for x in raw:
        if isinstance(x, dict):
            cid = x.get("card_id") or x.get("id")
        else:
            cid = x
        try:
            ids.add(int(cid))
        except Exception:
            continue
    return len(ids)


def validate_price_by_currency(cur: str, value: float) -> tuple[bool, str | None]:
    if cur == "cups":
        if value < 2 or value % 2 != 0:
            return False, "Для чашек минимально 2 и кратность 2."
    elif cur == "diamonds":
        if value < 30 or value % 10 != 0:
            return False, "Для алмазов минимально 30 и кратность 10."
    elif cur == "treasures":
        if value < 10 or value % 10 != 0:
            return False, "Для сокровищ минимально 10 и кратность 10."
    else:
        if value <= 0:
            return False, "Для денег нужна положительная цена."
    return True, None


async def fetch_deck_card_ids(deck_id: int) -> list[int]:
    import importlib
    dbmod = importlib.import_module("db.db")
    pool = getattr(dbmod, "db_pool", None)
    sql = "SELECT card_id FROM cards WHERE deck_id=$1 ORDER BY card_id"
    if pool is None:
        return []
    try:
        rows = await pool.fetch(sql, deck_id)
    except AttributeError:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, deck_id)
    return [int(r["card_id"]) for r in rows]


async def safe_edit_text(msg: Message, text: str, **kwargs) -> None:
    try:
        await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def safe_edit_reply_markup(msg: Message, *, reply_markup=None) -> None:
    try:
        await msg.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


def _card_title(card: dict) -> str:
    hero = html.escape(card.get("hero_name") or "")
    name = html.escape(card.get("card_name") or "")
    rarity = str(card.get("rarity") or "?")
    return f"{hero} — {name} [{rarity}]"


def fiat_flag(code: str) -> str:
    c = (code or "").strip().upper()

    mapping = {
        "BYN": "🇧🇾", "RUB": "🇷🇺", "UAH": "🇺🇦", "KZT": "🇰🇿",
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "PLN": "🇵🇱",
        "TRY": "🇹🇷", "KGS": "🇰🇬", "AMD": "🇦🇲", "AZN": "🇦🇿",
        "GEL": "🇬🇪", "ILS": "🇮🇱", "AED": "🇦🇪",
        "CNY": "🇨🇳", "JPY": "🇯🇵", "KRW": "🇰🇷",
        "CHF": "🇨🇭", "SEK": "🇸🇪", "NOK": "🇳🇴", "DKK": "🇩🇰",
        "CZK": "🇨🇿", "HUF": "🇭🇺", "RON": "🇷🇴", "BGN": "🇧🇬",
        "UZS": "🇺🇿", "TJS": "🇹🇯", "TMT": "🇹🇲",
        "CAD": "🇨🇦", "AUD": "🇦🇺",
    }

    extra = globals().setdefault("EXTRA_FLAGS", {})
    if c in extra:
        return extra[c]
    return mapping.get(c, "")


def currency_emoji(cur: str) -> str:
    return {"diamonds": "💎", "cups": "☕", "treasures": "🏴‍☠️", "cash": "💵"}.get(cur, "💵")
