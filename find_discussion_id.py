import html
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from asyncpg import CheckViolationError, UniqueViolationError
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import ChannelParticipantsAdmins

from db.db import get_autobid_action_by_msg_id, list_autobids
from db.db import init_db, close_db

# -----------------------------
# ENV
# -----------------------------
load_dotenv()


async def _fetch(q: str, *args):
    pool = await _db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(q, *args)


API_ID = int(os.getenv("USERBOT_API_ID") or os.getenv("TELETHON_API_ID") or 0)
API_HASH = (os.getenv("USERBOT_API_HASH") or os.getenv("TELETHON_API_HASH") or "").strip()

AUCTION_CHANNEL_ID = int(os.getenv("AUCTION_CHANNEL_ID") or 0)
DISCUSSION_CHAT_ID = int(os.getenv("DISCUSSION_CHAT_ID") or 0)
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

ADMINS = {
    int(x.strip())
    for x in (os.getenv("ADMINS") or "").split(",")
    if x.strip().isdigit()
}

ADMIN_LOG_CHATS = [
    int(x.strip())
    for x in (os.getenv("ADMIN_LOG_CHATS") or "").split(",")
    if x.strip()
]

LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID") or 0)

if not API_ID or not API_HASH:
    raise RuntimeError("USERBOT_API_ID/USERBOT_API_HASH (или TELETHON_API_ID/TELETHON_API_HASH) не заданы в env")
if not DISCUSSION_CHAT_ID:
    raise RuntimeError("DISCUSSION_CHAT_ID не задан в env")


def _user_link(user_id: int, username: Optional[str] = None) -> str:
    """
    Кликабельная ссылка на пользователя.
    Если username есть — показываем @username, иначе показываем id.
    """
    label = f"@{username}" if username else str(user_id)
    label = html.escape(label)
    return f'<a href="tg://user?id={int(user_id)}">{label}</a>'


# timezone-aware UTC now (Telethon prefers UTC for until_date)
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_thread_root_msg_id(msg) -> int | None:
    """
    Возвращает id корневого сообщения ветки (поста), к которому относится комментарий.
    Работает и для прямых реплаев на пост, и для реплаев на другие комментарии внутри ветки.
    """
    r = getattr(msg, "reply_to", None)
    if not r:
        return None

    # Telethon чаще всего использует reply_to_top_id для "корня" ветки
    top_id = getattr(r, "reply_to_top_id", None)
    if top_id:
        return int(top_id)

    # fallback: прямой reply на пост
    mid = getattr(r, "reply_to_msg_id", None)
    if mid:
        return int(mid)

    return None


import asyncio

AUTO_DELETE_BOT_NOTICE_SEC = 0


async def reply_not_counted(event, text: str):
    m = await event.reply(text)
    if AUTO_DELETE_BOT_NOTICE_SEC and AUTO_DELETE_BOT_NOTICE_SEC > 0:
        await asyncio.sleep(AUTO_DELETE_BOT_NOTICE_SEC)
        try:
            await m.delete()
        except Exception:
            pass


# -----------------------------
# LOGGING
# -----------------------------
logger = logging.getLogger("userbot")
logging.basicConfig(level=logging.INFO)


def _mention(username: Optional[str], user_id: int) -> str:
    # @username если есть, иначе кликабельный id
    return f"@{username}" if username else f'<a href="tg://user?id={user_id}">{user_id}</a>'


# -----------------------------
# CONSTANTS
# -----------------------------
RULES_TEXT = (
    "📌 <b>ПРАВИЛА СТАВОК</b>\n\n"
    "1) Ставка = <b>только ответом на пост лота</b>.\n"
    "   Ответы на ставки/флуд → удаление + мут 1 мин.\n\n"
    "2) Формат: <code>300</code>, <code>1 000</code>, <code>10k</code>/<code>10к</code>.\n\n"
    "3) Шаг валюты:\n"
    "   • 💎/🪙 → <b>кратно 10</b>\n"
    "   • 🍵 → <b>кратно 2</b>\n\n"
    "4) Обычный аукцион: новая ставка выше текущей. Обратный: ниже текущей минимум на шаг.\n\n"
    "5) Нельзя редактировать/удалять ставку вручную → предупреждение.\n\n"
    "🛠 <b>Исправить ошибку (60 сек)</b> (ответом на свою ставку):\n"
    "• <code>/oops</code> — отменить\n"
    "• <code>/oops 810</code> — исправить сумму в учёте\n\n"
    "Подробнее: https://teletype.in/@velassya/karty_kr_pravila"
)

WARN_TEXTS = [
    "@{username}, предупреждение. (предов: {warnings}/4)",
    "@{username}, правила читать полезно. (предов: {warnings}/4)",
]

from datetime import timedelta
import re

_BOT_DELETED: dict[int, float] = {}
_BOT_DELETED_TTL = 300.0
ACCEPTED_BIDS: dict[tuple[int, int], dict] = {}
# key: (chat_id, msg_id)
# val: {"root_id": int, "amount": int, "user_id": int, "text": str, "auction_id": int}
# кеш админов чата
_CHAT_ADMINS_CACHE: dict[int, tuple[set[int], float]] = {}
_CHAT_ADMINS_TTL = 300.0

# -----------------------------
# TELETHON
# -----------------------------
client = TelegramClient("userbot_session", API_ID, API_HASH)

# -----------------------------
# DB
# -----------------------------
db_pool: Optional[asyncpg.pool.Pool] = None

_BID_RE = re.compile(r"^\s*([\d\s_]+)\s*([кk])?\s*$", re.IGNORECASE)


def _parse_bid_amount(text: str) -> Optional[int]:
    """
    300 / 1 000 / 1_000 / 10k / 10к -> int
    """
    s = (text or "").strip()
    if not s:
        return None

    s = s.replace(" ", "").replace("_", "").lower()
    mult = 1
    if s.endswith(("k", "к")):
        mult = 1000
        s = s[:-1]

    if not s.isdigit():
        return None

    try:
        val = int(s) * mult
    except ValueError:
        return None

    return val if val > 0 else None


def _ceil_to_step(value: int, step: int) -> int:
    if step <= 1:
        return value
    return ((value + step - 1) // step) * step


def _calc_min_next(start_price: int, max_bid: Optional[int], step: int) -> int:
    """
    Минимальная следующая:
      - если ставок нет: start_price
      - иначе: max_bid + step
    """
    if max_bid is None:
        return int(start_price)
    base = max(int(start_price), int(max_bid))
    return base + int(step)


from typing import cast


async def _db_pool() -> asyncpg.pool.Pool:
    global db_pool
    if db_pool is None:
        db_pool = cast(
            asyncpg.pool.Pool,
            await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10),
        )
    return db_pool


async def _fetchrow(q: str, *args):
    pool = await _db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(q, *args)


async def _fetchval(q: str, *args):
    pool = await _db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(q, *args)


async def _execute(q: str, *args):
    pool = await _db_pool()
    async with pool.acquire() as conn:
        return await conn.execute(q, *args)


# -----------------------------
# HELPERS
async def _delete_later(msg_id: int, delay_sec: int = 25) -> None:
    await asyncio.sleep(int(delay_sec))
    _BOT_DELETED[int(msg_id)] = _now_ts() + _BOT_DELETED_TTL
    try:
        await client.delete_messages(DISCUSSION_CHAT_ID, [int(msg_id)])
    except Exception:
        pass


async def _send_reply_or_plain(
        text: str,
        *,
        reply_to: int | None = None,
        ttl: int | None = None,
) -> None:
    """Пишем ответ в обсуждение. TTL по умолчанию выключен (ничего не удаляем)."""
    try:
        m = await client.send_message(
            DISCUSSION_CHAT_ID,
            text,
            reply_to=reply_to,
            parse_mode="html",
            link_preview=False,
        )
    except Exception:  # noqa: BLE001
        m = await client.send_message(
            DISCUSSION_CHAT_ID,
            text,
            parse_mode="html",
            link_preview=False,
        )

    if ttl is not None and ttl > 0:
        asyncio.create_task(_delete_later(int(m.id), int(ttl)))


def _now_ts() -> float:
    return time.time()


def _is_recent_bot_delete(msg_id: int) -> bool:
    exp = _BOT_DELETED.get(int(msg_id))
    if not exp:
        return False
    if exp < _now_ts():
        _BOT_DELETED.pop(int(msg_id), None)
        return False
    return True


def _random_warn(username: Optional[str], uid: int, warnings: int) -> str:
    base = random.choice(WARN_TEXTS)
    return base.format(username=(username or f"id{uid}"), warnings=warnings)


async def _get_chat_admin_ids(chat_id: int) -> set[int]:
    now = _now_ts()
    cached = _CHAT_ADMINS_CACHE.get(int(chat_id))
    if cached and cached[1] > now:
        return cached[0]

    ids: set[int] = set(ADMINS)

    try:
        admins = await client.get_participants(chat_id, filter=ChannelParticipantsAdmins)
        for a in admins:
            uid = getattr(a, "id", None)
            if uid:
                ids.add(int(uid))
    except Exception:
        pass

    _CHAT_ADMINS_CACHE[int(chat_id)] = (ids, now + _CHAT_ADMINS_TTL)
    return ids


async def _is_chat_admin(chat_id: int, user_id: int) -> bool:
    return int(user_id) in await _get_chat_admin_ids(chat_id)


async def _mute_1m(chat_id: int, user_id: int) -> None:
    """Мут на 1 минуту в конкретном чате."""
    try:
        until = datetime.now(timezone.utc) + timedelta(minutes=1)
        await client.edit_permissions(
            int(chat_id),
            int(user_id),
            send_messages=False,
            until_date=until,
        )
    except Exception:  # noqa: BLE001
        pass


def _get_root_id(msg) -> Optional[int]:
    # для комментариев в обсуждении root обычно лежит в message_thread_id
    root = getattr(msg, "message_thread_id", None)
    if root:
        return int(root)
    # fallback
    if getattr(msg, "reply_to", None):
        top = getattr(msg.reply_to, "reply_to_top_id", None)
        mid = getattr(msg.reply_to, "reply_to_msg_id", None)
        return int(top or mid) if (top or mid) else None
    return None


def _is_direct_reply_to_root(msg, root_id: int) -> bool:
    # Разрешаем:
    # 1) сообщение в треде без reply_to (не отвечает на ставку)
    # 2) reply_to ровно на root пост
    rt = getattr(msg, "reply_to_msg_id", None)
    if rt is None:
        return True
    return int(rt) == int(root_id)


async def _fetch_auction_by_root(root_id: int) -> Optional[dict]:
    row = await _fetchrow(
        """
        SELECT auction_id,
               start_price,
               currency,
               auction_kind,
               accepted_currencies,
               start_time,
               end_time,
               status,
               message_id,
               discussion_message_id
        FROM public.auctions
        WHERE discussion_message_id = $1
           OR message_id = $1
        ORDER BY auction_id DESC
        LIMIT 1
        """,
        int(root_id),
    )
    return dict(row) if row else None


def _to_utc(dt: datetime) -> datetime:
    # если из БД пришло naive - считаем, что это локальная “naive” шкала (как и datetime.now())
    # если aware - приводим к UTC
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc)


async def _is_auction_active(a: dict) -> bool:
    start_time = a.get("start_time")
    end_time = a.get("end_time")
    if not start_time or not end_time:
        return False

    # оба naive -> сравниваем naive с naive
    if start_time.tzinfo is None and end_time.tzinfo is None:
        now = datetime.now()
        return start_time <= now <= end_time

    # оба aware (или один почему-то aware) -> сравниваем в UTC
    start_utc = _to_utc(start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc))
    end_utc = _to_utc(end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc))
    now_utc = _utcnow()
    return start_utc <= now_utc <= end_utc


async def _fetch_best_bid(auction_id: int, *, excluding_bid_id: int | None = None) -> Optional[int]:
    if excluding_bid_id is None:
        row = await _fetchrow(
            """
            SELECT CASE
                       WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse'
                           THEN MIN(b.amount)
                       ELSE MAX(b.amount)
                   END AS best_amount
            FROM public.auctions a
            LEFT JOIN public.bids b ON b.auction_id = a.auction_id
            WHERE a.auction_id = $1
            GROUP BY a.auction_kind
            """,
            int(auction_id),
        )
    else:
        row = await _fetchrow(
            """
            SELECT CASE
                       WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse'
                           THEN MIN(b.amount)
                       ELSE MAX(b.amount)
                   END AS best_amount
            FROM public.auctions a
            LEFT JOIN public.bids b
                   ON b.auction_id = a.auction_id
                  AND b.bid_id <> $2
            WHERE a.auction_id = $1
            GROUP BY a.auction_kind
            """,
            int(auction_id),
            int(excluding_bid_id),
        )
    value = row.get("best_amount") if row else None
    return int(value) if value is not None else None


async def _fetch_max_bid(auction_id: int) -> Optional[int]:
    """Compatibility alias: returns the best bid, MIN for reverse auctions."""
    return await _fetch_best_bid(auction_id)


CURRENCY_EMOJI = {"алмазы": "💎", "чай": "🍵", "сокровища": "🪙"}
CURRENCY_STEP = {"алмазы": 10, "чай": 2, "сокровища": 10}


def _norm_currency(raw: str) -> str:
    c = (raw or "").strip().lower()
    # на всякий случай: эмодзи/синонимы
    if c in {"💎", "алмаз", "алмазы"}:
        return "алмазы"
    if c in {"🪙", "сокровища", "сокры", "сокр"}:
        return "сокровища"
    if c in {"🍵", "чай", "чашки", "cups"}:
        return "чай"
    return c


async def _ensure_user(user_id: int, username: Optional[str], full_name: Optional[str]) -> None:
    uname = (username or "").strip().lstrip("@") or None
    fname = (full_name or "").strip() or None

    await _execute(
        """
        INSERT INTO public.users (user_id, username, full_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE
            SET username  = EXCLUDED.username,
                full_name = EXCLUDED.full_name
        """,
        int(user_id),
        (uname[:32] if uname else None),
        (fname[:255] if fname else None),
    )



from asyncpg.exceptions import ForeignKeyViolationError

async def _insert_bid(auction_id: int, bidder_id: int, amount: int, msg_id: int,
                      *, username: str | None = None, full_name: str | None = None) -> dict:
    try:
        await _execute(
            """
            INSERT INTO public.bids (auction_id, bidder_id, amount, discussion_message_id)
            VALUES ($1, $2, $3, $4)
            """,
            int(auction_id), int(bidder_id), int(amount), int(msg_id),
        )
        return {"ok": True}
    except ForeignKeyViolationError:
        # пользователь не создан -> создаём и пробуем 1 раз ещё
        await _ensure_user(int(bidder_id), username, full_name)
        await _execute(
            """
            INSERT INTO public.bids (auction_id, bidder_id, amount, discussion_message_id)
            VALUES ($1, $2, $3, $4)
            """,
            int(auction_id), int(bidder_id), int(amount), int(msg_id),
        )
        return {"ok": True}



def _compute_min_next(start_price: int, max_bid: int | None, step: int) -> int:
    sp = int(start_price or 0)
    st = int(step or 1)
    if max_bid is None:
        return sp
    return max(sp, int(max_bid)) + st


async def _get_bid_by_msg_id(msg_id: int) -> Optional[dict]:
    row = await _fetchrow(
        """
        SELECT bid_id, auction_id, bidder_id, amount, discussion_message_id, created_at
        FROM public.bids
        WHERE discussion_message_id = $1
        ORDER BY bid_id DESC
        LIMIT 1
        """,
        int(msg_id),
    )
    return dict(row) if row else None


async def _update_bid_amount(bid_id: int, new_amount: int) -> None:
    await _execute(
        "UPDATE public.bids SET amount=$1 WHERE bid_id=$2",
        int(new_amount),
        int(bid_id),
    )


def _seconds_since(dt: datetime) -> float:
    # created_at у тебя naive, сравниваем с datetime.now()
    if not dt:
        return 10 ** 9
    return (datetime.now() - dt).total_seconds()


async def _delete_bid_by_id(bid_id: int) -> None:
    await _execute("DELETE FROM public.bids WHERE bid_id=$1", int(bid_id))


async def _warnings_count(user_id: int) -> int:
    v = await _fetchval("SELECT warnings_count FROM public.users WHERE user_id=$1", int(user_id))
    return int(v or 0)


async def _add_warning(user_id: int, reason: str, details: str = "") -> int:
    await _execute(
        "INSERT INTO public.user_warnings (user_id, reason, details) VALUES ($1, $2, $3)",
        int(user_id),
        reason,
        details,
    )
    await _execute(
        "UPDATE public.users SET warnings_count = COALESCE(warnings_count,0) + 1 WHERE user_id=$1",
        int(user_id),
    )
    return await _warnings_count(int(user_id))


async def _ban_user(user_id: int, reason: str) -> None:
    banned_until = datetime.now() + timedelta(days=3650)
    await _execute(
        "INSERT INTO public.user_bans (user_id, banned_until, reason) VALUES ($1, $2, $3)",
        int(user_id),
        banned_until,
        reason,
    )
    # в чате тоже режем (на всякий)
    try:
        until = _utcnow() + timedelta(days=3650)
        await client.edit_permissions(DISCUSSION_CHAT_ID, int(user_id), send_messages=False, until_date=until)
    except Exception:
        pass


async def _auction_thread_root(auction_id: int) -> Optional[int]:
    val = await _fetchval(
        "SELECT discussion_message_id FROM public.auctions WHERE auction_id=$1",
        int(auction_id),
    )
    return int(val) if val else None


async def _fetch_max_bid_excluding(auction_id: int, bid_id: int) -> Optional[int]:
    """Compatibility alias: returns the best remaining bid for the auction kind."""
    return await _fetch_best_bid(auction_id, excluding_bid_id=bid_id)


async def _maybe_punish(user_id: int, username: Optional[str], warnings: int, root_id: int) -> None:
    # 3 преда = мут (дольше), 4 = бан
    if warnings >= 4:
        await _ban_user(int(user_id), "4 warnings")
        await _send_reply_or_plain(
            f"🚫 {_mention(username, user_id)} получил(а) <b>бан</b> за 4 предупреждения.",
            reply_to=root_id,
            ttl=35,
        )
        return

    if warnings == 3:
        try:
            until = _utcnow() + timedelta(minutes=10)
            await client.edit_permissions(DISCUSSION_CHAT_ID, int(user_id), send_messages=False, until_date=until)
        except Exception:
            pass
        await _send_reply_or_plain(
            f"⏳ {_mention(username, user_id)}: 3 предупреждения → мут на 10 минут.",
            reply_to=root_id,
            ttl=35,
        )


async def _post_rules_under_lot(root_id: int) -> None:
    try:
        await client.send_message(
            entity=DISCUSSION_CHAT_ID,
            message=RULES_TEXT,
            reply_to=int(root_id),
            parse_mode="html",
            link_preview=False,
        )
    except Exception:
        try:
            await client.send_message(
                entity=DISCUSSION_CHAT_ID,
                message=RULES_TEXT,
                parse_mode="html",
                link_preview=False,
            )
        except Exception:
            pass


async def _auction_thread_root(auction_id: int) -> Optional[int]:
    v = await _fetchval("SELECT discussion_message_id FROM public.auctions WHERE auction_id=$1", int(auction_id))
    return int(v) if v else None


async def _fetch_auction_meta(auction_id: int) -> Optional[dict]:
    row = await _fetchrow(
        """
        SELECT auction_id, status, start_time, end_time, discussion_message_id, auction_kind
        FROM public.auctions
        WHERE auction_id = $1
        LIMIT 1
        """,
        int(auction_id),
    )
    return dict(row) if row else None


def _is_auction_closed_row(auction: Optional[dict]) -> bool:
    if not auction:
        return False

    status = str(auction.get("status") or "").strip().lower()
    if status in {"finished", "closed", "completed", "ended", "cancelled", "canceled"}:
        return True

    end_time = auction.get("end_time")
    if not end_time:
        return False

    try:
        if end_time.tzinfo is None:
            return datetime.now() > end_time
        return _utcnow() > _to_utc(end_time)
    except Exception:
        return False


def _bid_change_root_id(prev: Optional[dict], msg, auction: Optional[dict]) -> int:
    if prev and prev.get("root_id"):
        return int(prev["root_id"])
    if auction and auction.get("discussion_message_id"):
        return int(auction["discussion_message_id"])
    return int(_get_root_id(msg) or getattr(msg, "reply_to_msg_id", None) or msg.id)


# -------------------------
# Root bind (discussion root -> auction row)
# -------------------------

_LOT_ID_RE = re.compile(r"(?i)\bлот\s*№\s*(\d{1,10})\b")


def _looks_like_auction_post(text_low: str) -> bool:
    """Грубая эвристика, чтобы правила не улетали под любой пост."""
    if not text_low:
        return False
    # обязательные маркеры
    if "лот" not in text_low:
        return False
    has_value_marker = any(
        marker in text_low
        for marker in ("цена", "валюта ставок", "принимаются предложения")
    )
    if not has_value_marker:
        return False
    has_action_marker = any(
        marker in text_low
        for marker in ("принимаются ставки", "ставки", "предложения")
    )
    return has_action_marker


def _extract_lot_id(text: str) -> Optional[int]:
    if not text:
        return None
    m = _LOT_ID_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def _norm_channel_id(x: Optional[int]) -> Optional[int]:
    """Нормализует -100123.. (Bot API) и 123.. (Telethon channel_id) к одному виду."""
    if x is None:
        return None
    try:
        n = int(x)
    except Exception:  # noqa: BLE001
        return None

    if n < 0:
        s = str(n)
        if s.startswith("-100") and s[4:].isdigit():
            return int(s[4:])
        return abs(n)
    return n


async def _try_bind_root_message(msg) -> Optional[int]:
    """Если msg похож на корневой пост аукциона в обсуждении — привязать и вернуть auction_id."""

    # 1) Нормальный путь: forwarded из канала (fwd_from.channel_post)
    fwd = getattr(msg, "fwd_from", None)
    channel_post = getattr(fwd, "channel_post", None) if fwd else None

    if channel_post:
        # защита: forward должен быть именно из канала аукционов
        src_channel_id = getattr(getattr(fwd, "from_id", None), "channel_id", None)
        if AUCTION_CHANNEL_ID and src_channel_id:
            if _norm_channel_id(src_channel_id) != _norm_channel_id(AUCTION_CHANNEL_ID):
                channel_post = None

    if channel_post:
        try:
            row = await _fetchrow(
                """
                UPDATE public.auctions
                SET discussion_message_id = $1
                WHERE message_id = $2
                RETURNING auction_id
                """,
                int(msg.id),
                int(channel_post),
            )
            if row and row.get("auction_id"):
                return int(row["auction_id"])
        except Exception:  # noqa: BLE001
            pass

    # 2) Fallback: если телега не даёт fwd_from, цепляемся по тексту “Лот №1234”
    text_raw = (getattr(msg, "message", None) or "").strip()
    text_low = text_raw.lower()
    if not _looks_like_auction_post(text_low):
        return None

    lot_id = _extract_lot_id(text_raw)
    if not lot_id:
        return None

    try:
        row = await _fetchrow(
            """
            UPDATE public.auctions
            SET discussion_message_id = $1
            WHERE auction_id = $2
            RETURNING auction_id
            """,
            int(msg.id),
            int(lot_id),
        )
        if row and row.get("auction_id"):
            return int(row["auction_id"])
    except Exception:  # noqa: BLE001
        pass
    return None


async def _resolve_autobid_mapping(msg_id: int, *, wait_for_race: bool = False, attempts: int = 8, delay: float = 0.15) -> Optional[dict]:
    """
    Пытаемся сопоставить исходящее сообщение юзербота с autobid_actions или локальным кэшем.
    Нужен из-за гонки: событие NewMessage может прилететь раньше, чем _send_bid() успеет
    положить msg.id в _LOCAL_AUTOBID или записать autobid_actions в БД.
    """
    mapped = await get_autobid_action_by_msg_id(int(msg_id))
    if not mapped:
        mapped = get_local_autobid_action(int(msg_id))
    if mapped or not wait_for_race:
        return mapped

    for _ in range(max(1, int(attempts))):
        await asyncio.sleep(float(delay))
        mapped = await get_autobid_action_by_msg_id(int(msg_id))
        if not mapped:
            mapped = get_local_autobid_action(int(msg_id))
        if mapped:
            return mapped
    return None


OOPS_EDIT_WINDOW_SEC = 60

# добавь рядом с другими imports (сверху файла)

from userbot.autobid_engine import maybe_place_autobid, get_local_autobid_action, pop_local_autobid_action, get_auction_lock

@client.on(events.NewMessage(chats=DISCUSSION_CHAT_ID))
async def on_new_message(event: events.NewMessage.Event):
    msg = event.message

    # -------------------------
    # 0) Корневой пост лота -> bind + правила (только если реально есть лот в БД)
    # -------------------------
    auction_id = await _try_bind_root_message(msg)
    if auction_id:
        await _post_rules_under_lot(int(msg.id))
        return

    # -------------------------
    # 1) Автоставки: проверяем маппинг по msg.id ДО любых фильтров msg.out
    # -------------------------
    mapped = await _resolve_autobid_mapping(int(msg.id))
    is_autobid_msg = bool(mapped)

    # -------------------------
    # 2) Базовые фильтры
    # -------------------------
    sender_id = getattr(msg, "sender_id", None)
    if not sender_id:
        return

    # Наши исходящие обычно игнорим,
    # НО для автоставки ждём гонку между событием, локальным кэшем и записью autobid_actions.
    if getattr(msg, "out", False) and not is_autobid_msg:
        mapped = await _resolve_autobid_mapping(int(msg.id), wait_for_race=True, attempts=8, delay=0.15)
        is_autobid_msg = bool(mapped)

    # Если это всё ещё не автоставка, но текст = чистое число, считаем это обычной исходящей ставкой юзербота.
    if getattr(msg, "out", False) and not is_autobid_msg:
        text_probe = (getattr(msg, "message", None) or "").strip()
        if _parse_bid_amount(text_probe) is None:
            return

    if getattr(msg, "sender_chat", None) is not None:
        return

    # Если это автоставка, фактический "участник" = target_user_id.
    bidder_id = int(mapped["target_user_id"]) if mapped else int(sender_id)
    actor_id = int(bidder_id) if is_autobid_msg else int(sender_id)
    actor_username = (mapped.get("target_username") if mapped else None) or None

    # is_admin нужен для модерации (удаления/мутов). Для автоставок модерацию выключаем.
    is_admin = True if is_autobid_msg else await _is_chat_admin(int(event.chat_id), int(sender_id))

    text_raw = (msg.message or "").strip()
    text_low = text_raw.lower()

    # -------------------------
    # 3) Админские команды (только для реальных людей, не для автоставок)
    # -------------------------
    if not is_autobid_msg:
        if text_low.startswith("/unwarn"):
            if not is_admin:
                return
            parts = text_raw.split()
            if len(parts) < 2:
                return
            uid = int(parts[1])
            n = int(parts[2]) if len(parts) >= 3 else 1
            left = await _remove_last_warnings(uid, n)
            await _send_reply_or_plain(
                f"✅ Преды сняты: {_user_link(uid)}. Теперь предов: <b>{left}</b>.",
                reply_to=_get_root_id(msg) or msg.id,
            )
            return

        if text_low.startswith("/recalc_lot"):
            if not is_admin:
                return
            parts = text_raw.split()
            if len(parts) < 2:
                return
            aid = int(parts[1])
            removed = await _prune_missing_bid_messages(aid)
            max_bid = await _fetch_max_bid(aid)
            await _send_reply_or_plain(
                f"♻️ Пересчёт лота <b>{aid}</b>: удалено “призрачных” ставок: <b>{removed}</b>.\n"
                f"Текущая лучшая ставка в БД: <b>{max_bid or 0}</b>.",
                reply_to=_get_root_id(msg) or msg.id,
            )
            return

    # -------------------------
    # 4) /oops ... (только для реальных людей, автоставки сюда не пускаем)
    # -------------------------
    if (not is_autobid_msg) and msg.reply_to_msg_id and (
            text_low.startswith("/oops")
            or text_low.startswith("oops")
            or text_low.startswith("опс")
            or text_low.startswith("упс")
            or text_low.startswith("макс отмена")
            or text_low.startswith("макс отменить")
            or text_low.startswith("макс отмена ")
            or text_low.startswith("макс отменить ")
    ):
        # ---- ТВОЙ БЛОК /oops ОСТАЁТСЯ КАК ЕСТЬ ----
        # Я не переписываю его сейчас, чтобы не устроить тебе «внезапный рефакторинг на 900 строк».
        # Просто оставь тут весь твой текущий код /oops без изменений.
        #
        # ВАЖНО: этот блок должен завершаться return (как у тебя и было).
        #
        # (Вставь сюда твой исходный блок /oops из файла)
        pass

    # -------------------------
    # 5) Обычные сообщения: ставки/флуд
    # -------------------------
    root_id = _get_root_id(msg)
    if not root_id:
        return

    auction = await _fetch_auction_by_root(int(root_id))
    if not auction:
        return
    if not await _is_auction_active(auction):
        return

    thread_root_id = int(auction.get("discussion_message_id") or root_id)

    # флуд: не прямой ответ на пост лота
    # (для автоставок тоже проверим, но у тебя send_message делается reply_to root, так что ок)
    if not _is_direct_reply_to_root(msg, thread_root_id):
        if is_admin:
            return  # админам можно, просто не считаем как ставку

        # автоставки сюда не должны попадать, но на всякий случай не модерируем mapped
        if is_autobid_msg:
            return

        try:
            await client.delete_messages(DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:  # noqa: BLE001
            pass

        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, сообщение удалено.\n"
            f"В комментариях лота разрешены только <b>ставки</b> и только <b>ответом на пост лота</b>.",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(DISCUSSION_CHAT_ID), int(sender_id))
        return

    # Валюта, направление ставок и текущая лучшая ставка.
    kind_key = str(auction.get("auction_kind") or "standard").strip().lower()
    lowest_wins = kind_key == "reverse"

    # Свободный аукцион принимает текстовые предложения, а не автоматические
    # числовые ставки. Юзербот не должен удалять такие комментарии как флуд.
    if kind_key in {"free", "exchange"}:
        return

    currency = _norm_currency((auction.get("currency") or "").strip())
    step = int(CURRENCY_STEP.get(currency, 1))
    emoji = CURRENCY_EMOJI.get(currency, "💰")
    start_price = int(auction.get("start_price") or 0)

    current_best = await _fetch_best_bid(int(auction["auction_id"]))
    if lowest_wins:
        boundary = None if current_best is None else max(1, int(current_best) - step)
        requirement_text = (
            f"Первая ставка должна быть положительной и кратной <b>{step}</b> {emoji}."
            if boundary is None
            else f"Текущая лучшая ставка: <b>{current_best}</b> {emoji}. "
                 f"Следующая должна быть не больше <b>{boundary}</b> {emoji}."
        )
    else:
        min_required = start_price if current_best is None else max(start_price, int(current_best)) + step
        requirement_text = f"Минимум сейчас: <b>{min_required}</b> {emoji} (валюта: <b>{currency}</b>)"

    # сумма ставки:
    # - если это автоставка, берём amount из mapped (чтобы не зависеть от текста/формата)
    # - иначе парсим текст
    if is_autobid_msg:
        amount = int(mapped["amount"])
    else:
        amount = _parse_bid_amount(text_raw)

    # не ставка (только для реальных людей)
    if amount is None:
        if is_admin:
            return

        try:
            await client.delete_messages(DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:  # noqa: BLE001
            pass

        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, это не ставка.\n"
            f"Пиши числом или с K/К (например <code>10к</code>).\n"
            f"{requirement_text}",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(DISCUSSION_CHAT_ID), int(sender_id))
        return

    if lowest_wins:
        maximum_allowed = None if current_best is None else int(current_best) - step
        if int(amount) < step or (maximum_allowed is not None and int(amount) > maximum_allowed):
            await _send_reply_or_plain(
                f"⚠️ {_mention(actor_username, actor_id)}, ставка не принята.\n"
                f"В обратном аукционе выигрывает меньшая ставка.\n"
                f"{requirement_text}",
                reply_to=thread_root_id,
            )
            return
        invalid_step = step > 1 and int(amount) % step != 0
        step_text = f"Нужна сумма, кратная <b>{step}</b>."
    else:
        if int(amount) < int(min_required):
            await _send_reply_or_plain(
                f"⚠️ {_mention(actor_username, actor_id)}, ставка не принята.\n"
                f"{requirement_text}",
                reply_to=thread_root_id,
            )
            return
        invalid_step = step > 1 and ((int(amount) - start_price) % step) != 0
        step_text = f"Нужен шаг <b>{step}</b> от старта <b>{start_price}</b>."

    if invalid_step:
        if is_admin:
            await _send_reply_or_plain(
                f"⚠️ {_mention(actor_username, actor_id)}, ставка не засчитана. {step_text}\n"
                f"{requirement_text}",
                reply_to=thread_root_id,
            )
            return

        if is_autobid_msg:
            return

        try:
            await client.delete_messages(DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:  # noqa: BLE001
            pass

        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, ставка удалена.\n"
            f"{step_text}\n{requirement_text}",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(DISCUSSION_CHAT_ID), int(sender_id))
        return

    # username для /mention (для автоставки это username юзербота, это норм, мы не обязаны светить target)
    sender_obj = await event.get_sender()
    uname = getattr(sender_obj, "username", None)

    # гарантируем пользователя в БД:
    # - для автоставки: ensure по bidder_id (target_user_id)
    # - для обычной: bidder_id = sender_id
    try:
        await _ensure_user(int(bidder_id), uname if not is_autobid_msg else None, None)
    except Exception:  # noqa: BLE001
        pass

    async with get_auction_lock(int(auction["auction_id"])):
        live_best = await _fetch_best_bid(int(auction["auction_id"]))
        if lowest_wins:
            live_boundary = None if live_best is None else int(live_best) - step
            live_ok = int(amount) >= step and (live_boundary is None or int(amount) <= live_boundary)
            live_requirement = (
                f"Первая ставка должна быть кратной <b>{step}</b> {emoji}."
                if live_boundary is None
                else f"Текущая лучшая: <b>{live_best}</b> {emoji}; "
                     f"нужно не больше <b>{max(1, live_boundary)}</b> {emoji}."
            )
        else:
            live_min_required = start_price if live_best is None else max(start_price, int(live_best)) + step
            live_ok = int(amount) >= int(live_min_required)
            live_requirement = f"Минимум сейчас: <b>{live_min_required}</b> {emoji}."

        if not live_ok:
            await _send_reply_or_plain(
                f"⚠️ {_mention(actor_username, actor_id)}, ставка не принята.\n"
                f"{live_requirement}",
                reply_to=thread_root_id,
            )
            return

        res = await _insert_bid(int(auction["auction_id"]), int(bidder_id), int(amount), int(msg.id))
        if not res.get("ok"):
            await _send_reply_or_plain(
                f"⚠️ {_mention(None, sender_id)}, ставка не засчитана: отклонена БД-правилами.\n"
                f"Сумма: <b>{amount}</b> {emoji}\n"
                f"Валюта: <b>{currency}</b>\n"
                f"{live_requirement}",
                reply_to=thread_root_id,
            )
            return

        if is_autobid_msg:
            pop_local_autobid_action(int(msg.id))
        ACCEPTED_BIDS[(int(event.chat_id), int(msg.id))] = {
            "root_id": int(thread_root_id),
            "amount": int(amount),
            "user_id": int(bidder_id),
            "text": text_raw,
            "auction_id": int(auction["auction_id"]),
        }

    # -------------------------
    # 6) Триггер автоставки: только если это была НЕ автоставка
    # -------------------------
    if not is_autobid_msg and kind_key in {"standard", "fast", "black"}:
        try:
            await maybe_place_autobid(
                client,
                discussion_chat_id=int(DISCUSSION_CHAT_ID),
                auction_id=int(auction["auction_id"]),
            )
        except Exception:
            logger.exception("Autobid engine failed for auction_id=%s", auction.get("auction_id"))


async def _remove_last_warnings(user_id: int, n: int = 1) -> int:
    n = max(1, int(n))
    await _execute(
        "DELETE FROM public.user_warnings WHERE id IN (SELECT id FROM public.user_warnings WHERE user_id=$1 ORDER BY id DESC LIMIT $2)",
        int(user_id),
        int(n),
    )
    await _execute(
        "UPDATE public.users SET warnings_count = GREATEST(COALESCE(warnings_count,0) - $2, 0) WHERE user_id=$1",
        int(user_id),
        int(n),
    )
    return await _warnings_count(int(user_id))


async def _prune_missing_bid_messages(auction_id: int) -> int:
    rows = await _fetch(
        "SELECT bid_id, discussion_message_id FROM public.bids WHERE auction_id=$1",
        int(auction_id),
    )
    removed = 0
    for r in rows:
        mid = r["discussion_message_id"]
        if not mid:
            continue
        try:
            m = await client.get_messages(DISCUSSION_CHAT_ID, ids=int(mid))
        except Exception:
            m = None
        if not m:
            try:
                await _delete_bid_by_id(int(r["bid_id"]))
                removed += 1
            except Exception:
                pass
    return removed


@client.on(events.MessageEdited(chats=DISCUSSION_CHAT_ID))
async def on_edited(event: events.MessageEdited.Event):
    msg = event.message
    sender_id = getattr(msg, "sender_id", None)
    if not sender_id:
        return
    if getattr(msg, "out", False):
        return
    if getattr(msg, "sender_chat", None) is not None:
        return

    key = (int(event.chat_id), int(msg.id))
    prev = ACCEPTED_BIDS.get(key)
    new_text = (msg.message or "").strip()

    bid = await _get_bid_by_msg_id(int(msg.id))
    if not prev and not bid:
        return

    if prev and new_text == (prev.get("text") or ""):
        return

    is_admin = await _is_chat_admin(int(event.chat_id), int(sender_id))
    sender_obj = await event.get_sender()
    uname = getattr(sender_obj, "username", None)

    auction_id = None
    if bid and bid.get("auction_id"):
        auction_id = int(bid["auction_id"])
    elif prev and prev.get("auction_id"):
        auction_id = int(prev["auction_id"])

    auction = await _fetch_auction_meta(int(auction_id)) if auction_id else None
    auction_closed = _is_auction_closed_row(auction)
    root_id = _bid_change_root_id(prev, msg, auction)

    if bid and not auction_closed:
        try:
            await _delete_bid_by_id(int(bid["bid_id"]))
        except Exception:  # noqa: BLE001
            pass

    ACCEPTED_BIDS.pop(key, None)

    if is_admin:
        action_text = (
            "была отредактирована после завершения аукциона, запись в БД сохранена"
            if auction_closed else
            "была отредактирована и <b>не засчитана</b>"
        )
        await _send_reply_or_plain(
            f"⚠️ {_mention(uname, sender_id)}: ставка {action_text}.",
            reply_to=root_id,
        )
        return

    details = f"msg_id={msg.id}"
    if bid:
        details += f" amount={bid.get('amount')} auction_id={bid.get('auction_id')}"
    if auction_closed:
        details += " closed=1"

    warnings = await _add_warning(int(sender_id), "edit_bid", details)
    if auction_closed:
        await _send_reply_or_plain(
            f"⛔ {_mention(uname, sender_id)}: редактирование ставки после завершения аукциона запрещено.\n"
            f"Ставка в БД сохранена, предупреждение выдано.\n"
            f"{_random_warn(uname, sender_id, warnings)}",
            reply_to=root_id,
        )
    else:
        await _send_reply_or_plain(
            f"⛔ {_mention(uname, sender_id)}: редактирование ставки запрещено.\n"
            f"{_random_warn(uname, sender_id, warnings)}",
            reply_to=root_id,
        )
    await _maybe_punish(int(sender_id), uname, warnings, int(root_id))


@client.on(events.MessageDeleted(chats=DISCUSSION_CHAT_ID))
async def on_deleted(event: events.MessageDeleted.Event):
    for mid in event.deleted_ids:
        if _is_recent_bot_delete(int(mid)):
            continue

        bid = await _get_bid_by_msg_id(int(mid))
        if not bid:
            continue

        bidder_id = int(bid["bidder_id"])
        auction_id = int(bid["auction_id"])
        auction = await _fetch_auction_meta(auction_id)
        auction_closed = _is_auction_closed_row(auction)

        if not auction_closed:
            try:
                await _delete_bid_by_id(int(bid["bid_id"]))
            except Exception:  # noqa: BLE001
                pass

        ACCEPTED_BIDS.pop((int(DISCUSSION_CHAT_ID), int(mid)), None)

        if await _is_chat_admin(DISCUSSION_CHAT_ID, bidder_id):
            continue

        warnings = await _add_warning(
            bidder_id,
            "delete_bid",
            f"msg_id={mid} amount={bid.get('amount')} auction_id={auction_id} closed={int(auction_closed)}",
        )
        thread_root_id = int((auction or {}).get("discussion_message_id") or await _auction_thread_root(auction_id) or int(mid))

        if auction_closed:
            await _send_reply_or_plain(
                f"⚠️ {_mention(None, bidder_id)}: предупреждение за удаление ставки после завершения аукциона.\n"
                f"Ставка в БД сохранена. (предов: {warnings}/4)",
                reply_to=thread_root_id,
            )
        else:
            await _send_reply_or_plain(
                f"⚠️ {_mention(None, bidder_id)}: предупреждение за удаление ставки.\n"
                f"(предов: {warnings}/4)",
                reply_to=thread_root_id,
            )
        await _maybe_punish(bidder_id, None, warnings, int(thread_root_id))


async def autobid_watchdog():
    while True:
        try:
            rows = await list_autobids(auction_id=None, only_active=True)
            auction_ids = {int(r["auction_id"]) for r in rows}
            for aid in auction_ids:
                auction = await _fetch_auction_meta(aid)
                kind_key = str((auction or {}).get("auction_kind") or "standard").strip().lower()
                if kind_key not in {"standard", "fast", "black"}:
                    continue
                await maybe_place_autobid(
                    client,
                    discussion_chat_id=int(DISCUSSION_CHAT_ID),
                    auction_id=aid,
                )
        except Exception:
            logger.exception("Autobid watchdog failed")
        await asyncio.sleep(15)
async def main():
    # ВАЖНО: userbot тоже должен поднять db_pool, иначе @require_db_pool падает.
    await init_db()

    try:
        await client.connect()
        if not await client.is_user_authorized():
            phone = input("Введите телефон (+7...): ").strip()
            await client.send_code_request(phone)
            code = input("Введите код: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = input("Введите пароль 2FA: ").strip()
                await client.sign_in(phone=phone, password=password)
        asyncio.create_task(autobid_watchdog())
        me = await client.get_me()
        logger.info(f"Userbot logged in as @{me.username or me.id}")
        logger.info("Listening discussion chat for bids/moderation/rules…")
        await client.run_until_disconnected()

    finally:
        # аккуратно закрываем пул, чтобы не висели соединения
        await close_db()



if __name__ == "__main__":
    asyncio.run(main())
