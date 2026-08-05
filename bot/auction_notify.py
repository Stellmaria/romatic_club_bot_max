import asyncio
import html
import importlib
import logging
import re
from contextlib import suppress
from datetime import date as _date
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Dict, List, Optional, Set, Any, Tuple
from typing import Type
from zoneinfo import ZoneInfo

import pytz
from aiogram import Router, F, types, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dateutil import parser as _parser
from dateutil import tz

from bot.handlers.helper.helpers_users import get_user_ids_from_usernames, format_today_lots_fancy
from bot.services.outbox import TelegramOutboxService
from bot.utils import currency_emoji
from db.legacy import get_settings, set_settings, get_card_by_id, get_card_subscribers, get_auctions_by_date, \
    list_auctions, get_auction_owner_id, get_users_with_pref, list_broadcast_targets, \
    get_auction_winner, subscribers_for_lot_title, get_card_full_by_id, find_card_by_name_hero, subscribers_for_deck, \
    subscribers_for_rarity

router = Router()
logger = logging.getLogger("auction_notificator")

MSK = tz.gettz("Europe/Moscow")
CB_PREFIX = "mkt"


def _resolve_db_error() -> Type[BaseException]:
    try:
        exc_mod = importlib.import_module("asyncpg.exceptions")
        return getattr(exc_mod, "PostgresError")
    except (ImportError, AttributeError):
        pass

    try:
        pkg = importlib.import_module("asyncpg")
        return getattr(pkg, "PostgresError")
    except (ImportError, AttributeError):
        pass

    class _DBError(Exception):
        pass

    return _DBError


DBError = _resolve_db_error()


def to_msk_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = _parser.parse(v)
        except (ValueError, TypeError):
            return None
    if isinstance(v, _date) and not isinstance(v, datetime):
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=MSK)
        return v.astimezone(MSK)
    return None


def extract_usernames(comment: str) -> list[str]:
    return re.findall(r'@(\w+)', comment or "")


def _as_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int,)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"1", "true", "t", "yes", "y", "да", "on"}:
            return True
        if s in {"0", "false", "f", "no", "n", "нет", "off"}:
            return False
    return default


def settings_keyboard(settings: dict) -> types.InlineKeyboardMarkup:
    b = _as_bool  # использовать тот же нормализатор (скопируй функцию сюда или импортни)
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="🔔 О начале аукциона " + ("✅" if b(settings.get("notify_auction_start", True)) else "❌"),
            callback_data="toggle_notify_auction_start")],
        [types.InlineKeyboardButton(
            text="⏰ За минуту до конца " + ("✅" if b(settings.get("notify_bid_reminder", True)) else "❌"),
            callback_data="toggle_notify_bid_reminder")],
        [types.InlineKeyboardButton(
            text="🏁 О завершении " + ("✅" if b(settings.get("notify_auction_end", True)) else "❌"),
            callback_data="toggle_notify_auction_end")],
        [types.InlineKeyboardButton(
            text="📅 Анонс дня в 00:00 " + ("✅" if b(settings.get("notify_daily_today", False)) else "❌"),
            callback_data="toggle_notify_daily_today")],
    ])


@router.message(F.text.in_(['/settings', 'Настройки уведомлений']))
async def user_settings_menu(message: types.Message) -> None:
    user = message.from_user
    if user is None:
        return
    settings = await get_settings(user.id) or {}
    await message.answer("Выберите, какие уведомления хотите получать:", reply_markup=settings_keyboard(settings))


_ALLOWED_TOGGLE_FIELDS = {
    "notify_auction_start",
    "notify_bid_reminder",
    "notify_auction_end",
    "notify_daily_today",
}


@router.callback_query(F.data.startswith("toggle_notify_"))
async def toggle_setting(call: types.CallbackQuery) -> None:
    user = call.from_user
    data_raw = call.data
    if user is None or not isinstance(data_raw, str) or not data_raw.startswith("toggle_notify_"):
        await call.answer()
        return

    field = data_raw.replace("toggle_", "", 1)
    if field not in _ALLOWED_TOGGLE_FIELDS:
        await call.answer("Неизвестная настройка.")
        return

    user_id = user.id
    settings = await get_settings(user_id) or {}
    current = _as_bool(settings.get(field), True if field != "notify_daily_today" else False)
    val = not current
    await set_settings(user_id, **{field: val})
    new_settings = await get_settings(user_id) or {}

    if call.message is not None:
        await call.message.edit_reply_markup(reply_markup=settings_keyboard(new_settings))
    await call.answer("Настройка обновлена!")


async def morning_card_subscribe_notify_loop(bot):
    msk_tz = pytz.timezone("Europe/Moscow")
    while True:
        now = datetime.now(msk_tz)
        target = datetime.combine(now.date(), dtime(hour=10, minute=0, second=0), tzinfo=msk_tz)
        if now > target:
            target = target + timedelta(days=1)
        wait_seconds: float = max(0.0, (target - now).total_seconds())
        await asyncio.sleep(wait_seconds)

        today = datetime.now(msk_tz).date()
        auctions = await get_auctions_by_date(today)
        seen: set[int] = set()

        for lot in auctions:
            raw_cid = lot.get("card_id")
            try:
                card_id = int(raw_cid)
            except (TypeError, ValueError):
                continue

            if card_id in seen:
                continue

            card = await get_card_by_id(card_id)
            subs = await get_card_subscribers(card_id)
            if not subs or not card:
                continue

            st = to_msk_dt(lot.get("start_time"))
            tstr = st.strftime("%H:%M") if st else "-"
            url = f"https://t.me/YourChannelUsername/{lot.get('message_id')}" if lot.get('message_id') else ""
            url_line = f"👉 <a href=\"{url}\">Перейти к лоту</a>" if url else "ℹ️ Лот ещё не опубликован в канале."

            text = (
                f"🌅 <b>Сегодня в аукционе участвует твоя подписка!</b>\n"
                f"<b>{html.escape(card['card_name'])}</b> ({html.escape(card['hero_name'])})\n"
                f"Старт: {tstr}\n"
                f"Стартовая цена: <b>{lot.get('start_price', '-')}</b> {lot.get('currency', '')}\n"
                f"{url_line}"
            )

            for uid in subs:
                try:
                    if card.get("image_id"):
                        await bot.send_photo(uid, photo=card["image_id"], caption=text, parse_mode="HTML")
                    else:
                        await bot.send_message(uid, text, parse_mode="HTML")
                except TelegramAPIError:
                    pass
            seen.add(card_id)

        await asyncio.sleep(60.0)


def build_card_day_text(lot: dict, now: datetime) -> tuple[str, bool]:
    st = to_msk_dt(lot.get("start_time"))
    if st is not None and st < now:
        return "", False
    time_part = f" в {st.strftime('%H:%M')} МСК." if st is not None else "."
    hero = lot.get("hero_name") or ""
    text = (
            f"🔔 Сегодня на канале будет карта <b>{html.escape(lot['card_name'])}</b>"
            + (f" (герой: {html.escape(hero)})" if hero else "")
            + time_part
    )
    return text, True


async def _enqueue_card_day_notification(
    *,
    user_id: int,
    card_id: int,
    day,
    text: str,
) -> None:
    """Atomically claim a card-day reminder and queue it for delivery."""
    try:
        await (await TelegramOutboxService.create()).enqueue_card_day_notification(
            user_id=user_id,
            card_id=card_id,
            day=day,
            text=text,
        )
    except DBError as err:
        logger.warning("card-day outbox enqueue(%s, %s, %s) failed: %s", user_id, card_id, day, err)


async def notify_card_day_subscribers(bot):
    now = datetime.now(MSK)
    today = now.date()
    lots = await get_auctions_by_date(today)

    for lot in lots:
        card_id = lot.get("card_id")
        if not card_id:
            continue
        subs = await get_card_subscribers(card_id)
        if not subs:
            continue

        text, ok = build_card_day_text(lot, now)
        if not ok:
            continue

        for uid in subs:
            await _enqueue_card_day_notification(
                user_id=int(uid), card_id=int(card_id), day=today, text=text
            )


def _to_str(v) -> str:
    return v if isinstance(v, str) else ""


async def notify_new_subscriber_today(bot, user_id: int, card_id: int) -> None:
    now = datetime.now(MSK)
    today = now.date()
    lots = await get_auctions_by_date(today)

    for lot in lots:
        try:
            cid = int(lot.get("card_id"))
        except (TypeError, ValueError):
            continue
        if cid != card_id:
            continue

        text, ok = build_card_day_text(lot, now)
        if not ok:
            continue

        await _enqueue_card_day_notification(
            user_id=user_id, card_id=card_id, day=today, text=text
        )
        break


def _safe(v, default="-"):
    return default if v is None or (isinstance(v, str) and not v.strip()) else v


def _to_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _extract_gifts(obj: dict | None) -> Tuple[int, int]:
    if not obj:
        return 0, 0

    cups = _to_int(obj.get("gift_cups"), 0) or _to_int(obj.get("obtain_tea"), 0) or 0
    dias = _to_int(obj.get("gift_diamonds"), 0) or _to_int(obj.get("obtain_diamonds"), 0) or 0

    otype = (obj.get("obtain_type") or "").strip().lower()
    oamt = _to_int(obj.get("obtain_amount"))
    if oamt is not None:
        if otype in {"tea", "cup", "cups", "чай", "чашка", "чашки"}:
            cups = oamt
        elif otype in {"diamond", "diamonds", "алмаз", "алмазы"}:
            dias = oamt

    return cups, dias


def _fmt_gifts(card: dict | None = None, auction: dict | None = None) -> str:
    cups_a, dias_a = _extract_gifts(auction)
    cups_c, dias_c = _extract_gifts(card)
    cups = cups_a or cups_c or 0
    dias = dias_a or dias_c or 0

    parts: list[str] = []
    if cups > 0:
        parts.append(f"☕{cups}")
    if dias > 0:
        parts.append(f"💎{dias}")
    return " · ".join(parts) if parts else "-"


async def _build_card_info(auction: dict) -> str:
    card: dict | None = None
    cid = _to_int(auction.get("card_id"))
    if cid is not None:
        try:
            card = await get_card_full_by_id(cid)  # c.*, deck_name
        except Exception:
            card = None

    if not card:
        cname = (auction.get("card_name") or "").strip()
        hname = (auction.get("hero_name") or "").strip()
        if cname and hname:
            try:
                card = await find_card_by_name_hero(cname, hname)
            except Exception:
                card = None

    card = card or {}

    deck_id = _to_int(card.get("deck_id")) or _to_int(auction.get("deck_id"))
    deck_name = (card.get("deck_name") or auction.get("deck_name") or "").strip() or "-"
    if deck_id and deck_name != "-":
        deck_label = f"{deck_name} (№{deck_id})"
    elif deck_id:
        deck_label = f"Колода №{deck_id}"
    else:
        deck_label = deck_name

    rarity = (
            (auction.get("rarity") or "").strip()
            or (card.get("rarity") or "").strip()
            or (card.get("tier") or "").strip()
            or (card.get("nominal") or "").strip()
            or "-"
    )
    hero = (auction.get("hero_name") or card.get("hero_name") or "").strip() or "-"

    gifts = _fmt_gifts(card, auction)

    deck_label = html.escape(str(deck_label))
    rarity = html.escape(str(rarity))
    hero = html.escape(str(hero))
    gifts = html.escape(str(gifts))

    lines = [f"Колода: <b>{deck_label}</b>"]
    if rarity != "-":
        lines.append(f"Редкость: <b>{rarity}</b>")
    if hero != "-":
        lines.append(f"Герой: <b>{hero}</b>")
    lines.append(f"При получении даёт: <i>{gifts}</i>")
    return "\n".join(lines)


def _rarity_slug_local(r: Optional[str]) -> Optional[str]:
    r = (r or "").strip().lower()
    map_ = {
        "бронзовая": "bronze", "бронза": "bronze", "bronze": "bronze",
        "серебряная": "silver", "серебро": "silver", "silver": "silver",
        "золотая": "gold", "золото": "gold", "gold": "gold",
        "алмазная": "diamond", "алмазы": "diamond", "алмаз": "diamond",
        "diamond": "diamond", "diamonds": "diamond",
    }
    return map_.get(r, r) if r else None


def confirm_publish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, опубликовать", callback_data=f"{CB_PREFIX}:confirm:yes")],
        [InlineKeyboardButton(text="✖ Отмена", callback_data=f"{CB_PREFIX}:confirm:no")],
    ])


def _format_price_line(cur: str, price: float, cash_code: str | None) -> str:
    if cur == "cash":
        return f"💵 {price:.2f} {cash_code or ''}".strip()
    return f"{currency_emoji(cur)} {int(price)}"


# ---------------------- валюты и форматирование ----------------------

FIAT_FLAGS = {
    "BYN": "🇧🇾",
    "RUB": "🇷🇺",
    "UAH": "🇺🇦",
    "KZT": "🇰🇿",
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
}


def fiat_flag(code: str | None) -> str:
    """Флаг для кода валюты. Если код неизвестен — вернем 💵."""
    if not code:
        return "💵"
    return FIAT_FLAGS.get(code.upper(), "💵")


def _normalize_cash_map(
        cash: dict | tuple | list | str | None,
        price_map: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Приводим фиат к единому виду { 'BYN': 26.0, 'RUB': 100.0 }.
    Допустимые варианты на входе:
      • dict: {'BYN': 26, 'RUB': 100}
      • tuple/list длиной 2: ('BYN', 26)
      • str: 'BYN'  (тогда берем сумму из price_map['cash'], для обратной совместимости)
      • None
    """
    res: dict[str, float] = {}

    if isinstance(cash, dict):
        for k, v in cash.items():
            try:
                amount = float(v)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                res[str(k).upper()] = amount
        return res

    if isinstance(cash, (tuple, list)) and len(cash) == 2:
        code, val = cash
        try:
            amount = float(val)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0 and code:
            res[str(code).upper()] = amount
        return res

    if isinstance(cash, str) and price_map and "cash" in price_map:
        # legacy: передали только код, сумма лежит в price_map['cash']
        try:
            amount = float(price_map.get("cash") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0:
            res[cash.upper()] = amount

    return res


def _build_price_lines(price_map: dict[str, float] | None, cash: dict | tuple | list | str | None) -> str:
    """
    Готовим строковый блок:
      • ☕ 2
      • 💎 40
      • 🇧🇾 26.00 BYN
      • 🇷🇺 100.00 RUB
    """
    pm = {k: float(v) for k, v in (price_map or {}).items() if v not in (None, "", 0, 0.0)}
    lines: list[str] = []

    if pm.get("cups"):
        lines.append(f"• ☕ {int(pm['cups'])}")
    if pm.get("diamonds"):
        lines.append(f"• 💎 {int(pm['diamonds'])}")
    if pm.get("treasures"):
        lines.append(f"• ⚔️ {int(pm['treasures'])}")

    cash_map = _normalize_cash_map(cash, pm)
    # Стабильный порядок вывода
    for code in sorted(cash_map.keys()):
        amt = cash_map[code]
        lines.append(f"• {fiat_flag(code)} {amt:.2f} {code}")

    return "\n".join(lines) if lines else "—"


async def auction_notifications_loop(bot, channel_username: str) -> None:
    while True:
        now = datetime.now(MSK)
        today = now.date()

        try:
            lots: List[Dict[str, Any]] = await get_auctions_by_date(today)
        except DBError as err:
            logger.exception("get_auctions_by_date(%s) failed: %s", today, err)
            lots = []

        for lot in lots:
            cid = _to_int(lot.get("card_id"))
            if cid is None:
                continue

            try:
                subs: List[int] = await get_card_subscribers(cid)
            except DBError as err:
                logger.warning("get_card_subscribers(%s) failed: %s", cid, err)
                subs = []

            if not subs:
                continue

            text, ok = build_card_day_text(lot, now)
            if not ok:
                continue

            for uid in subs:
                await _enqueue_card_day_notification(
                    user_id=int(uid), card_id=cid, day=today, text=text
                )

        try:
            auctions: List[Dict[str, Any]] = await list_auctions(["active"])
        except DBError as err:
            logger.exception("list_auctions(['active']) failed: %s", err)
            auctions = []

        try:
            globally_enabled_users = set(await list_broadcast_targets())
        except DBError as err:
            logger.warning("list_broadcast_targets failed: %s", err)
            globally_enabled_users = set()

        for auction in auctions:
            msg_id = _to_int(auction.get("message_id"))
            if not msg_id:
                continue
            auction_url = f"https://t.me/{channel_username}/{msg_id}"

            recipients: Set[int] = set()
            auction_id = _to_int(auction.get("auction_id"))

            owner_id: Optional[int] = None
            if auction_id is not None:
                try:
                    owner_raw = await get_auction_owner_id(auction_id)
                    owner_id = _to_int(owner_raw)
                    if owner_id:
                        recipients.add(owner_id)
                except DBError as err:
                    logger.warning("get_auction_owner_id(%s) failed: %s", auction_id, err)

            comment = _to_str(auction.get("comment"))
            if comment:
                try:
                    usernames = extract_usernames(comment)
                    rec = await get_user_ids_from_usernames(bot, usernames)
                    recipients.update(int(u) for u in rec if _to_int(u) is not None)
                except (ValueError, TypeError) as err:
                    logger.warning("bad usernames in comment: %s", err)
                except TelegramAPIError as err:
                    logger.warning("get_user_ids_from_usernames failed: %s", err)
                except DBError as err:
                    logger.warning("resolve usernames DB error: %s", err)

            cid = _to_int(auction.get("card_id"))
            if cid is not None:
                try:
                    subs = await get_card_subscribers(cid)
                    recipients.update(int(u) for u in (subs or []) if _to_int(u) is not None)
                except DBError as err:
                    logger.warning("get_card_subscribers(%s) failed: %s", cid, err)

            lot_title = _to_str(auction.get("card_name") or auction.get("lot_name") or "").strip()
            if lot_title:
                try:
                    preset_uids = await subscribers_for_lot_title(lot_title)
                    recipients.update(int(u) for u in (preset_uids or []) if _to_int(u) is not None)
                except DBError as err:
                    logger.warning("subscribers_for_lot_title(%r) failed: %s", lot_title, err)

            card_for_meta: dict | None = None
            cid_for_meta = _to_int(auction.get("card_id"))
            if cid_for_meta is not None:
                with suppress(DBError, Exception):
                    card_for_meta = await get_card_full_by_id(cid_for_meta)
            if not card_for_meta:
                cname = (auction.get("card_name") or "").strip()
                hname = (auction.get("hero_name") or "").strip()
                if cname and hname:
                    with suppress(DBError, Exception):
                        card_for_meta = await find_card_by_name_hero(cname, hname)
            card_for_meta = card_for_meta or {}

            rarity_raw = (
                    (auction.get("rarity") or "").strip()
                    or (card_for_meta.get("rarity") or "").strip()
                    or (card_for_meta.get("tier") or "").strip()
                    or (card_for_meta.get("nominal") or "").strip()
            )
            rarity_slug = _rarity_slug_local(rarity_raw)
            if rarity_slug:
                try:
                    uids_rar = await subscribers_for_rarity(rarity_slug)
                    recipients.update(int(u) for u in (uids_rar or []) if _to_int(u) is not None)
                except DBError as err:
                    logger.warning("subscribers_for_rarity(%r) failed: %s", rarity_slug, err)

            deck_id = _to_int(card_for_meta.get("deck_id")) or _to_int(auction.get("deck_id"))
            deck_name = (card_for_meta.get("deck_name") or auction.get("deck_name") or "").strip()
            if deck_id or deck_name:
                try:
                    uids_deck = await subscribers_for_deck(deck_id, deck_name)
                    recipients.update(int(u) for u in (uids_deck or []) if _to_int(u) is not None)
                except DBError as err:
                    logger.warning("subscribers_for_deck(%r,%r) failed: %s", deck_id, deck_name, err)

            async def pref(name: str) -> Set[int]:
                try:
                    return set(await get_users_with_pref(name)) & globally_enabled_users
                except DBError as db_exc:
                    logger.warning("get_users_with_pref(%s) failed: %s", name, db_exc)
                    return set()

            users_start = await pref("notify_auction_start")
            users_1min = await pref("notify_bid_reminder")
            users_end = await pref("notify_auction_end")

            st_dt: Optional[datetime] = None
            et_dt: Optional[datetime] = None
            try:
                st_dt = to_msk_dt(auction.get("start_time"))
                et_dt = to_msk_dt(auction.get("end_time"))
            except (ValueError, TypeError) as err:
                logger.warning("to_msk_dt failed: %s", err)
            else:
                if st_dt is not None and et_dt is None:
                    et_dt = st_dt + timedelta(minutes=30)

            card_info = await _build_card_info(auction)

            if st_dt is not None and not auction.get("notified_start"):
                window_end = st_dt + timedelta(minutes=30)
                if st_dt <= now <= window_end:
                    text = (
                        "🚨 <b>Аукцион стартовал!</b>\n"
                        f"Карта: <b>{html.escape(auction['card_name'])}</b>\n"
                        f"{card_info}\n"
                        f"Ссылка: <a href='{auction_url}'>Перейти к аукциону</a>"
                    )
                    user_ids = list(users_start & recipients)
                    if auction_id is not None:
                        await (await TelegramOutboxService.create()).enqueue_auction_notification(
                            auction_id=auction_id,
                            event="start",
                            recipients=user_ids,
                            text=text,
                        )

            if et_dt is not None and not auction.get("notified_1min"):
                delta = et_dt - now
                if timedelta(seconds=0) < delta <= timedelta(seconds=90):
                    text = (
                        "⏰ <b>До конца аукциона осталась 1 минута!</b>\n"
                        f"Карта: <b>{html.escape(auction['card_name'])}</b>\n"
                        f"{card_info}\n"
                        f"Ссылка: <a href='{auction_url}'>Успей сделать ставку</a>"
                    )
                    user_ids = list(users_1min & recipients)
                    if auction_id is not None:
                        await (await TelegramOutboxService.create()).enqueue_auction_notification(
                            auction_id=auction_id,
                            event="one_minute",
                            recipients=user_ids,
                            text=text,
                        )

            if et_dt is not None and not auction.get("notified_end") and now >= et_dt:
                winner: Optional[Dict[str, Any]]
                try:
                    winner = await get_auction_winner(auction_id) if auction_id else None
                except DBError as err:
                    logger.warning("get_auction_winner(%s) failed: %s", auction_id, err)
                    winner = None

                currency = auction.get("currency")
                if isinstance(winner, dict):
                    username = (winner.get("username") or "").strip() or "победитель"
                    bid_val = winner.get("bid")
                    winner_uid = _to_int(winner.get("user_id"))
                    winner_line_owner = (
                        "🥇 <b>Победитель:</b> "
                        f"@{html.escape(str(username))} — <b>{bid_val}</b> {currency}"
                    )
                    winner_line_public = "🥇 <b>Победитель</b> будет объявлен администратором."
                else:
                    winner_uid = None
                    winner_line_owner = "🥇 <b>Победитель</b> будет объявлен администратором."
                    winner_line_public = winner_line_owner

                if auction_id is not None:
                    text = (
                        "🏁 <b>Аукцион завершён!</b>\n"
                        f"Карта: <b>{html.escape(auction['card_name'])}</b>\n"
                        f"{winner_line_public}\n"
                        f"Ссылка: <a href='{auction_url}'>Открыть аукцион</a>"
                    )
                    messages = {int(user_id): text for user_id in (users_end & recipients)}
                    if owner_id and owner_id in users_end:
                        messages[owner_id] = text.replace(winner_line_public, winner_line_owner)
                    await (await TelegramOutboxService.create()).enqueue_auction_notification(
                        auction_id=auction_id,
                        event="end",
                        messages=messages,
                    )

        await asyncio.sleep(20.0)


async def send_daily_announce(bot: Bot):
    today = datetime.now(MSK).date()
    try:
        lots = await get_auctions_by_date(today)
    except DBError:
        lots = []

    if not lots:
        return  # нечего слать

    try:
        uids = await get_users_with_pref("notify_daily_today")
    except DBError:
        uids = []

    if not uids:
        return

    try:
        msg = await format_today_lots_fancy(today, lots)
    except Exception:
        items = []
        for a in lots:
            st = to_msk_dt(a.get("start_time"))
            t = st.strftime("%H:%M") if st else "—"
            items.append(f"• {html.escape(a.get('card_name', '-'))} ({html.escape(a.get('hero_name', '-'))}) в {t}")
        msg = "📅 <b>Анонс на сегодня</b>\n" + "\n".join(items)

    for chunk_index, chunk in enumerate(_telegram_text_chunks(msg), start=1):
        await (await TelegramOutboxService.create()).enqueue_messages(
            topic="daily",
            dedupe_scope=f"{today.isoformat()}:{chunk_index}",
            messages={int(uid): chunk for uid in uids},
        )


def _telegram_text_chunks(text: str, *, limit: int = 4096) -> list[str]:
    """Split a Telegram text on line boundaries while preserving every character."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


async def _sleep_until(hour: int, minute: int, tz: ZoneInfo):
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())


async def daily_loop(bot):
    while True:
        await _sleep_until(0, 00, MSK)  # ждём ближайшие 00:00 МСК
        await send_daily_announce(bot)  # шлём анонс
        await asyncio.sleep(2)  # крошечная пауза на всякий


async def card_subscriptions_watch_loop(bot):
    while True:
        try:
            await notify_card_day_subscribers(bot)
        except Exception as e:
            logger.exception("card_subscriptions_watch_loop failed: %s", e)
        await asyncio.sleep(60)


async def collect_recipients_for_auction(auction: Dict[str, Any]) -> Set[int]:
    """
    Собирает получателей для уведомления по аукциону:
    1) по подпискам на точный заголовок лота;
    2) по подпискам на редкость, включая пресеты вида «Любая бронза»
       даже если у лота нет card_id и поле rarity пустое.

    Возвращает множество user_id (int).
    """
    recipients: Set[int] = set()

    # Заголовок лота (карточный или кастомный)
    lot_title = _to_str(
        auction.get("card_name")
        or auction.get("lot_name")
        or auction.get("title")
        or ""
    ).strip()

    # 1) Подписчики на конкретный заголовок
    if lot_title:
        try:
            uids_title = await subscribers_for_lot_title(lot_title)
            if uids_title:
                for u in uids_title:
                    uid = _to_int(u)
                    if uid is not None:
                        recipients.add(uid)
        except DBError as err:
            logger.warning(
                "subscribers_for_lot_title(%r) failed: %s", lot_title, err
            )

    # 2) Подписчики на редкость.
    # Пытаемся взять slug из полей аукциона, если есть
    rarity_slug: str = _to_str(
        auction.get("rarity")
        or auction.get("tier")
        or auction.get("nominal")
        or ""
    ).lower().strip()

    # Нормализуем: допускаем и английские, и русские формы
    if rarity_slug and rarity_slug not in {"bronze", "silver", "gold", "diamond"}:
        rarity_slug = _rarity_slug_local(rarity_slug) or ""

    # Если редкость не нашлась, вытаскиваем её из заголовка для пресетов «Любая ...»
    if not rarity_slug and lot_title:
        t = lot_title.lower()
        # Ловим формы 'любой/любая/любое/любые'
        if "любо" in t:
            for key in (
                    "бронза", "бронзовая",
                    "серебро", "серебряная",
                    "золото", "золотая",
                    "алмаз", "алмазы", "алмазная",
            ):
                if key in t:
                    rarity_slug = _rarity_slug_local(key) or ""
                    break

    if rarity_slug:
        try:
            uids_rar = await subscribers_for_rarity(rarity_slug)
            if uids_rar:
                for u in uids_rar:
                    uid = _to_int(u)
                    if uid is not None:
                        recipients.add(uid)
        except DBError as err:
            logger.warning(
                "subscribers_for_rarity(%r) failed: %s", rarity_slug, err
            )

    return recipients


def _kb_equal(a: InlineKeyboardMarkup | None,
              b: InlineKeyboardMarkup | None) -> bool:
    """Безопасное сравнение инлайн-клавиатур."""
    if a is b:
        return True
    if (a is None) ^ (b is None):
        return False
    try:
        return a.model_dump(exclude_none=True) == b.model_dump(exclude_none=True)
    except Exception:
        return str(a) == str(b)