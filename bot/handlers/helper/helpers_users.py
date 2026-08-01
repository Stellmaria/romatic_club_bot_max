from datetime import date, timedelta
from typing import Iterable, List, Set
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound

from bot.core.time import ensure_utc, to_moscow, utc_now
from bot.handlers.admin.helper.new.utils import is_luxury_member
from bot.core.legacy_config import legacy_config
from db.legacy import (
    add_user, set_luxury_status, log_admin_action, get_user_by_username, get_user
)


def parse_username_userid(text: str) -> tuple[Optional[str], Optional[int]]:
    """
    Парсит строку и извлекает username и/или user_id.

    Args:
        text (str): Строка вида '@username', '123456789', или '@username 123456789'.

    Returns:
        tuple: (username: Optional[str], user_id: Optional[int])
    """
    username, user_id = None, None
    parts = text.strip().split()
    for part in parts:
        if part.startswith("@"):
            username = part[1:]
        elif part.isdigit():
            user_id = int(part)
    return username, user_id


async def register_user(user, bot):
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    await add_user(user.id, user.username, full_name)
    is_lux = await is_luxury_member(bot, user.id, legacy_config.LUXURY_CHAT_ID)
    await set_luxury_status(user.id, is_lux)
    await log_admin_action(
        user.id,
        "register",
        None,
        f"Пользователь {user.username or user.id} зарегистрировался. Лакшери: {'да' if is_lux else 'нет'}"
    )
    return is_lux, full_name


async def check_luxury(user_id, bot):
    is_lux = await is_luxury_member(bot, user_id, legacy_config.LUXURY_CHAT_ID)
    await set_luxury_status(user_id, is_lux)
    await log_admin_action(
        user_id,
        "luxury_check",
        None,
        f"Проверка статуса Лакшери — {'да' if is_lux else 'нет'}"
    )
    return is_lux


def build_lot_keyboard(lot):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    lot_id = lot["auction_id"]
    buttons = [[InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"useredit|{lot_id}")]]
    if lot["status"] == "pending":
        buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_lot|{lot_id}")])
    elif lot["status"] in ["scheduled", "active"]:
        buttons.append([InlineKeyboardButton(text="🗑️ Запросить удаление", callback_data=f"delete_lot|{lot_id}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


CARD_EMOJI = "🃏"
CROWN = "👑"
PRICE_EMOJI = {"алмазы": "💎", "чашки": "🍵"}


def format_card_count(n):
    if n is None or n <= 1:
        return ""
    if 11 <= n % 100 <= 19:
        suffix = "карт"
    else:
        last = n % 10
        if last == 1:
            suffix = "карта"
        elif last in (2, 3, 4):
            suffix = "карты"
        else:
            suffix = "карт"
    return f"{n} {suffix}"


async def short_card_line(lot: dict, finished: bool = False) -> str:
    """
    Формат: 12:00 🃏(Герой) №12 Название +2 🍵 150 💎
    """
    start_time = to_moscow(lot["start_time"])
    time_str = start_time.strftime("%H:%M")

    hero = lot.get("hero_name") or "-"
    card_name = lot.get("card_name") or lot.get("title") or "-"

    # deck_id может отсутствовать — аккуратно попробуем найти через карту
    deck_id = lot.get("deck_id")
    if not deck_id:
        card = await _find_card_for_lot(lot)
        if card:
            deck_id = card.get("deck_id")
    deck_part = f" {_deck_tag(deck_id)}" if deck_id else ""

    # бонус при получении
    bonus_amt, bonus_type = await _get_card_reward(lot)
    bonus_part = f" +{bonus_amt} {_emoji_by_currency(bonus_type)}" if bonus_amt and bonus_type else ""

    # стартовая цена
    price = lot.get("start_price")
    cur = lot.get("currency") or "алмазы"
    price_part = f" {price} {_emoji_by_currency(cur)}" if isinstance(price, int) else ""

    status_part = " ✅" if finished else ""

    return f"{time_str} 🃏({hero}){deck_part} {card_name}{bonus_part}{price_part}{status_part}"


def _emoji_by_cur(currency: str | None) -> str:
    cur = (currency or "").strip().lower()
    return {
        "алмазы": "💎",
        "кристаллы": "💎",
        "чашки": "🍵",
        "чай": "🍵",
        "сокровища": "🪙",
    }.get(cur, "💎")


def _step_by_cur(currency: str | None) -> int:
    cur = (currency or "").strip().lower()
    return 2 if cur in {"чашки", "чай"} else 10


def _emoji_by_currency(currency: str | None) -> str:
    return _emoji_by_cur(currency)

def _norm_reward_type(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    # поддерживаем обе локали и варианты
    if s in {"tea", "cup", "cups", "чашки", "чай"}:
        return "чашки"
    if s in {"diamond", "diamonds", "алмазы", "алмаз"}:
        return "алмазы"
    return None


async def _find_card_for_lot(lot: dict) -> Optional[dict]:
    """
    Находим карту для лота:
      1) по card_id
      2) по (deck_id + hero_name) или (deck_id + card_name)
    """
    from db.legacy import get_card_by_id, get_cards_by_deck

    cid = lot.get("card_id")
    if cid:
        try:
            card = await get_card_by_id(cid)
            if card:
                return card
        except Exception:
            pass

    deck_id = lot.get("deck_id")
    if not deck_id:
        return None

    hero = (lot.get("hero_name") or "").strip().lower()
    title = (lot.get("card_name") or lot.get("title") or "").strip().lower()
    if not hero and not title:
        return None

    try:
        cards = await get_cards_by_deck(deck_id)
        for c in cards or []:
            h = (c.get("hero_name") or "").strip().lower()
            n = (c.get("card_name") or c.get("title") or "").strip().lower()
            if hero and h == hero:
                return c
            if title and (n == title or h == title):
                return c
    except Exception:
        pass
    return None


async def _get_card_reward(lot: dict) -> tuple[Optional[int], Optional[str]]:
    """
    Возвращает (amount, type) где type ∈ {'чашки','алмазы'}.
    Ищем сначала в самом лоте, затем в карточке. Поддерживаем кучу названий полей,
    ВКЛЮЧАЯ obtain_type/obtain_amount (то, что у тебя в БД).
    """
    # 1) прямо в лоте
    candidates_pairs = [
        ("obtain_type", "obtain_amount"),  # ← ДОБАВЛЕНО: твои реальные поля
        ("gift_type", "gift_amount"),
        ("reward_type", "reward_amount"),
        ("receive_type", "receive_amount"),
        ("gives_type", "gives_amount"),
        ("bonus_type", "bonus_amount"),
        ("donate_type", "donate_amount"),
    ]
    for t_key, a_key in candidates_pairs:
        t = _norm_reward_type(lot.get(t_key))
        a = lot.get(a_key)
        if t and isinstance(a, int) and a > 0:
            return a, t

    # разделённые поля количеством для каждой валюты
    split_amounts = [
        ("gift_cups", "gift_diamonds"),
        ("cups_on_gift", "diamonds_on_gift"),
        ("tea_amount", "diamond_amount"),
        ("receive_cups", "receive_diamonds"),
        ("obtain_cups", "obtain_diamonds"),  # на всякий случай
    ]
    for cups_key, dia_key in split_amounts:
        cups = lot.get(cups_key)
        dias = lot.get(dia_key)
        if isinstance(cups, int) and cups > 0:
            return cups, "чашки"
        if isinstance(dias, int) and dias > 0:
            return dias, "алмазы"

    # 2) смотрим в карте
    card = await _find_card_for_lot(lot)
    if card:
        for t_key, a_key in candidates_pairs:
            t = _norm_reward_type(card.get(t_key))
            a = card.get(a_key)
            if t and isinstance(a, int) and a > 0:
                return a, t
        for cups_key, dia_key in split_amounts:
            cups = card.get(cups_key)
            dias = card.get(dia_key)
            if isinstance(cups, int) and cups > 0:
                return cups, "чашки"
            if isinstance(dias, int) and dias > 0:
                return dias, "алмазы"

    return None, None


def _deck_tag(deck_id: Optional[int]) -> str:
    """Вместо 💜 — требуемый вид: №12"""
    return f"№{deck_id}" if deck_id else ""


async def format_today_lots_fancy(today: date, lots: list[dict]) -> str:
    msg = f"🛜АНОНС НА СЕГОДНЯ ({today.strftime('%d.%m.%Y')})🛜\n\n"
    completed, active = [], []
    now = utc_now()

    for lot in lots:
        start_time = ensure_utc(lot["start_time"])
        raw_end_time = lot.get("end_time")
        end_time = (
            ensure_utc(raw_end_time)
            if raw_end_time is not None
            else start_time + timedelta(minutes=30)
        )
        (completed if end_time < now else active).append(lot)

    # Sort by a single timezone-aware scale.  The database can still contain
    # legacy naive Moscow timestamps next to newer timestamptz values.
    # Sorting the raw values makes Python compare naive and aware datetimes
    # and raises TypeError even though classification above was normalized.
    sort_key = lambda lot: ensure_utc(lot["start_time"])

    if completed:
        msg += "<b>⏳ Завершённые лоты:</b>\n"
        for lot in sorted(completed, key=sort_key):
            msg += await short_card_line(lot, finished=True) + "\n"
        msg += "\n"

    if active:
        msg += "<b>🟢 Актуальные лоты:</b>\n"
        for lot in sorted(active, key=sort_key):
            msg += await short_card_line(lot) + "\n"

    return msg


def user_edit_lot_keyboard(lot_id: int):
    import aiogram.types
    return aiogram.types.InlineKeyboardMarkup(inline_keyboard=[
        [aiogram.types.InlineKeyboardButton(text="✏️ Стартовая цена", callback_data=f"user_edit_price|{lot_id}")],
        [aiogram.types.InlineKeyboardButton(text="✏️ Валюта", callback_data=f"user_edit_currency|{lot_id}")],
        [aiogram.types.InlineKeyboardButton(text="✏️ Комментарий", callback_data=f"user_edit_comment|{lot_id}")],
        [aiogram.types.InlineKeyboardButton(text="❌ Отмена", callback_data="user_edit_cancel")],
    ])


def format_user_lot_card(lot):
    status_map = {
        "pending": "📝 На модерации",
        "scheduled": "📅 В расписании",
        "active": "🔥 Активен",
        "finished": "✅ Завершён",
        "rejected": "❌ Отклонён",
        "deleted": "🗑️ Удалён",
    }
    status_ru = status_map.get(lot.get("status", ""), lot.get("status", "—"))
    start, end = lot.get("start_time"), lot.get("end_time")
    if start and end:
        try:
            start_fmt = start.strftime("%d.%m %H:%M")
            end_fmt = end.strftime("%H:%M")
            time_str = f"{start_fmt} - {end_fmt} (МСК)"
        except (ValueError, AttributeError):
            time_str = "-"
    else:
        time_str = "-"
    currency = lot.get("currency", "чашки").lower()
    emoji = "💎" if currency in ["алмазы", "кристаллы"] else "🍵"
    price = lot.get("start_price", 0)
    price_str = f"{price} {emoji}"
    msg = (
        f"🎴 <b>{lot.get('card_name', '—')}</b>\n"
        f"👤 <b>Герой:</b> {lot.get('hero_name', '—')}\n"
        f"💰 <b>Цена:</b> {price_str}\n"
        f"⏰ <b>Время:</b> {time_str}\n"
        f"📌 <b>Статус:</b> {status_ru}\n"
    )
    return msg


async def resolve_user_identifier(who: str) -> Optional[dict]:
    """
    Получить пользователя по username (с @) или user_id (строкой/числом).

    Args:
        who (str): Username (с @) или user_id (строкой/числом).

    Returns:
        Optional[dict]: Словарь пользователя или None, если не найден.
    """
    if not who:
        return None
    try:
        user_id = int(who)
        user = await get_user(user_id)
        if user:
            return user
    except ValueError:
        pass
    username = who.lstrip("@")
    user = await get_user_by_username(username)
    return user


async def get_user_ids_from_usernames(bot: Bot, usernames: Iterable[str]) -> List[int]:
    result: Set[int] = set()
    for raw in usernames or []:
        username = raw.lstrip("@").strip()
        if not username:
            continue
        try:
            chat = await bot.get_chat(username)
            if chat and getattr(chat, "id", None):
                result.add(chat.id)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound):
            continue
        except Exception as e:
            print(f"[get_user_ids_from_usernames] can't resolve @{username}: {e}")
            continue
    return list(result)


async def notify_lot_owner(bot, user_id, lot, text):
    image_id = lot.get("image_id")
    if image_id and image_id != "DEFAULT_PHOTO_ID":
        try:
            await bot.send_photo(
                user_id,
                photo=image_id,
                caption=text,
                parse_mode="HTML"
            )
        except TelegramAPIError as e:
            print(f"ERROR send_photo to {user_id}: {e}")
            await bot.send_message(user_id, text + "\n[⚠️ Не удалось отправить фото]", parse_mode="HTML")
    else:
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
        except TelegramAPIError as e:
            print(f"ERROR send_message to {user_id}: {e}")
