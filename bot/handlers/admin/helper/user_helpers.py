import asyncio
from collections import defaultdict
from datetime import datetime
from html import escape
from typing import Optional, Dict, Any

from aiogram import types
from aiogram.exceptions import TelegramAPIError

from bot.core.time import schedule_slot_key, to_moscow_wall
from bot.handlers.admin.helper.date_utils import all_30min_slots_for_date
from bot.handlers.admin.helper.new.utils import auction_kind_label
from bot.handlers.helper.helpers_users import parse_username_userid
from config import LUXURY_CHAT_ID
from db.db import get_all_users, set_luxury_status, get_user, add_user, get_user_by_username, get_card_by_num, \
    get_lot_owners, get_users_by_ids, is_luxury_user


async def luxury_status_sync_loop(bot):
    while True:
        users = await get_all_users()
        for user in users:
            user_id = user['user_id']
            try:
                member = await bot.get_chat_member(LUXURY_CHAT_ID, user_id)
                is_lux = member.status in ("member", "administrator", "creator")
            except TelegramAPIError:
                is_lux = False
            await set_luxury_status(user_id, is_lux)
            await asyncio.sleep(0.1)
        await asyncio.sleep(3600 * 6)


async def ensure_user_by_username_or_id(who: str) -> Optional[Dict[str, Any]]:
    """
    Находит пользователя по username или user_id. Если не найден по user_id, добавляет в БД и возвращает.

    Args:
        who (str): Строка вида '@username', '123456789', или '@username 123456789'.

    Returns:
        Optional[dict]: Словарь пользователя или None если не найден.
    """
    username, user_id = parse_username_userid(who)
    if user_id:
        user = await get_user(user_id)
        if user:
            return user
        await add_user(user_id, username or "", "")
        user = await get_user(user_id)
        return user
    if username:
        user = await get_user_by_username(username)
        return user
    return None


async def resolve_user_identifier(who: str):
    if not who:
        return None
    if who.startswith("@"):
        return await get_user_by_username(who[1:])
    if who.isdigit():
        return await get_user(int(who))
    return None


async def resolve_user_from_message(message: types.Message):
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        return await get_user(user_id)
    who = parse_user_arg(message)
    return await resolve_user_identifier(who) if who else None


def parse_user_arg(message: types.Message) -> str | None:
    parts = message.text.strip().split()
    if len(parts) < 2:
        return None
    who = parts[1]
    if who.startswith("@") or who.isdigit():
        return who
    return None


def format_user_ref(user: dict) -> str:
    """
    Формирует текстовую ссылку на пользователя (plain, без HTML).

    Args:
        user (dict): Словарь с user_id и username.

    Returns:
        str: Ссылка типа '@username' или 'id:123456'.
    """
    return f"@{user['username']}" if user.get("username") else f"id:{user['user_id']}"


def user_display(user):
    crown = "👑 " if user.get("is_luxury") else ""
    user_id = user.get("user_id")
    username = user.get("username")
    if user_id:
        if username:
            uname = username if username.startswith("@") else f"@{username}"
        else:
            uname = f"id:{user_id}"
        safe_uname = escape(uname)
        return f"{crown}<a href='tg://user?id={user_id}'>{safe_uname}</a>"
    elif username:
        uname = username if username.startswith("@") else f"@{username}"
        return f"{crown}{escape(uname)}"
    else:
        return f"{crown}—"


def owners_pretty(users: list) -> str:
    if not users:
        return "-"
    result = []
    for u in users:
        is_luxury = u.get("is_luxury", False)
        crown = "👑 " if is_luxury else ""
        uname = f'@{u["username"]}' if u.get("username") else u.get("full_name", u["user_id"])
        result.append(f'{crown}<a href="tg://user?id={u["user_id"]}">{uname}</a>')
    return ", ".join(result)


async def get_pretty_owners_for_log(auction_id: int):
    owners = await get_lot_owners(int(auction_id))
    if not owners:
        return "-"
    user_ids = [o["user_id"] for o in owners]
    if not user_ids:
        return "-"
    users = await get_users_by_ids(user_ids)
    return owners_pretty(users)


async def resolve_user_id(identifier: str) -> Optional[int]:
    """
    Получить user_id по username или строке user_id.

    Args:
        identifier (str): Username (с @) или user_id (строкой/числом).

    Returns:
        Optional[int]: user_id или None.
    """
    user = await resolve_user_identifier(identifier)
    return user["user_id"] if user else None


async def is_card_num_exists(num: int) -> bool:
    return await get_card_by_num(num) is not None


def parse_date_or_none(text: str):
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


async def get_owner_refs(auction_id, with_luxury_emoji=False):
    owners = await get_lot_owners(int(auction_id))
    refs = []
    for o in owners:
        user = await get_user(o['user_id'])
        is_lux = user and await is_luxury_user(o['user_id'])
        name = f"@{user['username']}" if user and user.get('username') else f"id:{o['user_id']}"
        if with_luxury_emoji and is_lux:
            refs.append(f"👑 {name}")
        else:
            refs.append(name)
    return ", ".join(refs)


async def build_schedule_lines(auctions, lot, current_owner_ids=None):
    auction_ids = [a['auction_id'] for a in auctions]
    auction_owners_map = {}
    for aid in auction_ids:
        auction_owners_map[aid] = await get_lot_owners(aid)
    lines = []
    for a in auctions:
        start = to_moscow_wall(a['start_time']).strftime('%H:%M')
        end = to_moscow_wall(a['end_time']).strftime('%H:%M')
        card_name = a['card_name']
        owners = auction_owners_map[a['auction_id']]
        owner_text_parts = []
        for o in owners:
            if current_owner_ids and o['user_id'] in current_owner_ids and card_name == lot['card_name']:
                owner_text_parts.append(f"❗️@{o['username']}")
            else:
                owner_text_parts.append(f"@{o['username']}")
        owner_text = ', '.join(owner_text_parts)
        prefix = ""
        if card_name == lot['card_name']:
            if current_owner_ids and any(o['user_id'] in current_owner_ids for o in owners):
                prefix = ""
            else:
                prefix = "🟡"
        kind_key = str(a.get("auction_kind") or "standard").strip().lower()
        kind_text = {
            "standard": "⭐ Стандартный",
            "reverse": "✨ Обратный",
            "fast": "⚡ Быстрый",
            "free": "🪶 Свободный",
            "black": "👑 Чёрный",
            "exchange": "🛍 Биржа",
        }.get(kind_key, kind_key)
        lines.append(
            f"{prefix}⏰ {start}–{end} | <b>{card_name}</b> | "
            f"⚙️ {kind_text} | {owner_text}"
        )
    return lines


async def find_free_slots(auctions, lot, auction_id, selected_date):
    """Возвращает свободные позиции на получасовой сетке.

    ``end_time`` включает последнюю секунду приёма ставок и поэтому почти
    на минуту заходит за границу отображаемого получасового слота. Для
    расписания это не пересечение: занятым считается только одинаковое
    время начала у той же карты и того же владельца.
    """
    current_owners = await get_lot_owners(auction_id)
    current_owner_ids = {int(o['user_id']) for o in current_owners}
    auction_ids = [a['auction_id'] for a in auctions]
    auction_owners_map = {}
    for aid in auction_ids:
        auction_owners_map[aid] = await get_lot_owners(aid)

    card_name = str(lot.get('card_name') or '').strip().casefold()
    busy_starts = set()
    for auction in auctions:
        if int(auction.get('auction_id') or 0) == int(auction_id):
            continue
        if str(auction.get('card_name') or '').strip().casefold() != card_name:
            continue

        owner_ids = {
            int(owner['user_id'])
            for owner in auction_owners_map.get(auction['auction_id'], [])
        }
        if not current_owner_ids.intersection(owner_ids):
            continue

        start = auction.get('start_time')
        if start is not None:
            busy_starts.add(schedule_slot_key(start))

    return [
        slot
        for slot in all_30min_slots_for_date(selected_date)
        if schedule_slot_key(slot) not in busy_starts
    ]


def filter_slots_by_user_type(free_slots, is_luxury):
    if is_luxury:
        return [slot for slot in free_slots if 11 <= slot.hour < 23 or (slot.hour == 22 and slot.minute == 30)]
    else:
        return [slot for slot in free_slots if
                12 <= slot.hour < 20 or (slot.hour == 20 and (slot.minute == 0 or slot.minute == 30))]

async def build_grouped_schedule_lines_with_prefixes(auctions, lot, current_owner_ids=None):
    auction_ids = [a['auction_id'] for a in auctions]
    auction_owners_map = {}
    for aid in auction_ids:
        auction_owners_map[aid] = await get_lot_owners(aid)
    slots_dict = defaultdict(list)
    for a in auctions:
        start_msk = to_moscow_wall(a['start_time'])
        end_msk = to_moscow_wall(a['end_time'])
        kind_key = str(a.get("auction_kind") or "standard").strip().lower()
        key = (start_msk, end_msk, a['card_name'], kind_key)
        owners = auction_owners_map[a['auction_id']]
        slots_dict[key].append((a['auction_id'], [o['user_id'] for o in owners], owners))
    lines = []
    for (start, end, card_name, kind_key), group in sorted(
        slots_dict.items(), key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3])
    ):
        all_owner_ids = set()
        owner_names = []
        current_owner_in_slot = False
        for auction_id, user_ids, owners in group:
            for o in owners:
                uid = o['user_id']
                if uid not in all_owner_ids:
                    all_owner_ids.add(uid)
                    name = f'@{o["username"]}' if o.get("username") else f'id:{uid}'
                    if current_owner_ids and uid in current_owner_ids and card_name == lot['card_name']:
                        current_owner_in_slot = True
                        name = f'❗️{name}'
                    owner_names.append(name)
        time_prefix = ""
        if card_name == lot['card_name']:
            if not current_owner_in_slot and len(all_owner_ids) > 0:
                time_prefix = "🟡"
        start_str = start.strftime('%H:%M')
        end_str = end.strftime('%H:%M')
        owners_str = ", ".join(owner_names) if owner_names else "-"
        kind_text = {
            "standard": "⭐ Стандартный",
            "reverse": "✨ Обратный",
            "fast": "⚡ Быстрый",
            "free": "🪶 Свободный",
            "black": "👑 Чёрный",
            "exchange": "🛍 Биржа",
        }.get(kind_key, kind_key)
        lines.append(
            f"{time_prefix}⏰ {start_str}–{end_str} | <b>{card_name}</b> | "
            f"⚙️ {kind_text} | {owners_str}"
        )
    return lines
