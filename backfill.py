import asyncio
import csv
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, MsgIdInvalidError, SessionPasswordNeededError

from bot.core.legacy_config import (
    AUCTION_CHANNEL_ID,
    AUCTION_CHANNEL_USERNAME,
    TG_API_ID,
    TG_API_HASH,
    TG_SESSION,
    BACKFILL_LIMIT_POSTS,
)

# ---------- настройки ----------
TZ_MSK = ZoneInfo("Europe/Moscow")

COMMENTS_LIMIT_PER_POST = 5000  # максимум сообщений в обсуждении, которые читаем на 1 пост
WINDOW_BEFORE = timedelta(minutes=5)  # окно до поста
WINDOW_AFTER = timedelta(minutes=5)  # окно после дедлайна

OUTPUT_DIR = Path(__file__).parent
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
BIDS_CSV = OUTPUT_DIR / f"backfill_bids_{TS}.csv"
POSTS_CSV = OUTPUT_DIR / f"backfill_posts_{TS}.csv"

# число (поддержка "8к", "8кк", "8 тыс")
NUM_RE = re.compile(r"(?i)\b(\d{1,3}(?:[ .,_]\d{3})*|\d+)\s*(кк|kk|к|k|тыс|тысяч)?\b")


# ---------- утилиты ----------
def to_msk(dt: datetime) -> datetime:
    """Telethon отдаёт UTC aware; в CSV пишем MSK naive."""
    return dt.astimezone(TZ_MSK).replace(tzinfo=None)


def parse_amount(text: str) -> int | None:
    """
    Достаём ставку из текста.
    Поддерживает:
      8000
      8 000
      8к / 8k  -> 8000
      8кк / 8kk -> 8_000_000
      8 тыс -> 8000
    """
    if not text:
        return None

    m = NUM_RE.search(text.strip().lower())
    if not m:
        return None

    num_raw = m.group(1)
    suffix = (m.group(2) or "").lower()

    num_clean = re.sub(r"[ .,_]", "", num_raw)
    if not num_clean.isdigit():
        return None

    val = int(num_clean)
    if suffix in ("к", "k", "тыс", "тысяч"):
        val *= 1_000
    elif suffix in ("кк", "kk"):
        val *= 1_000_000
    return val


def get_channel_ref() -> str | int:
    if AUCTION_CHANNEL_USERNAME:
        return f"@{AUCTION_CHANNEL_USERNAME.lstrip('@')}"
    return AUCTION_CHANNEL_ID


def make_post_link(channel_id: int, channel_username: str | None, post_id: int) -> str:
    if channel_username:
        u = channel_username.lstrip("@")
        return f"https://t.me/{u}/{post_id}"

    internal = str(channel_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    return f"https://t.me/c/{internal}/{post_id}"


def make_msg_link(chat, msg_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"

    internal = str(getattr(chat, "id", ""))
    if internal.startswith("-100"):
        internal = internal[4:]
    return f"https://t.me/c/{internal}/{msg_id}"


def get_reply_ids(msg) -> tuple[int | None, int | None]:
    """
    Возвращает (reply_to_msg_id, reply_to_top_id).
    В обсуждениях Telegram reply_to_top_id часто указывает на корень треда.
    """
    rh = getattr(msg, "reply_to", None)
    if not rh:
        return None, None

    reply_to_msg_id = getattr(rh, "reply_to_msg_id", None) or getattr(rh, "msg_id", None)
    reply_to_top_id = (
        getattr(rh, "reply_to_top_id", None)
        or getattr(rh, "top_msg_id", None)
        or getattr(rh, "reply_to_top_msg_id", None)
    )
    return reply_to_msg_id, reply_to_top_id


async def ensure_login(client: TelegramClient) -> None:
    if await client.is_user_authorized():
        return

    phone = input("Phone: ").strip()
    await client.send_code_request(phone)
    code = input("Code: ").strip()
    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        pwd = input("Cloud password (2FA): ").strip()
        await client.sign_in(password=pwd)


@dataclass
class PostRow:
    post_id: int
    post_link: str
    post_date_msk: str
    end_time_msk: str
    deadline_msk: str
    root_id: int | None
    discussion_id: int | None

    msgs_scanned: int
    numeric_msgs: int

    thread_bids: int
    thread_valid: int
    max_thread_valid: int | None
    winner_id: int | None

    any_valid: int
    max_any_valid: int | None

    note: str


# ---------- main ----------
async def main():
    if not TG_API_ID or not TG_API_HASH:
        raise SystemExit("Нет TG_API_ID/TG_API_HASH в .env (или они пустые).")

    channel_ref = get_channel_ref()
    if not channel_ref:
        raise SystemExit("Нет AUCTION_CHANNEL_USERNAME или AUCTION_CHANNEL_ID в .env")

    with open(BIDS_CSV, "w", newline="", encoding="utf-8-sig") as f_bids, open(
        POSTS_CSV, "w", newline="", encoding="utf-8-sig"
    ) as f_posts:
        bids_writer = csv.DictWriter(
            f_bids,
            fieldnames=[
                "post_id",
                "post_link",
                "post_date_msk",
                "root_id",
                "comment_id",
                "comment_link",
                "sender_id",
                "amount",
                "comment_date_msk",
                "is_valid_time",
                "in_thread",
                "reply_to_msg_id",
                "reply_to_top_id",
                "raw_text",
            ],
        )
        bids_writer.writeheader()

        posts_writer = csv.DictWriter(f_posts, fieldnames=[f.name for f in PostRow.__dataclass_fields__.values()])
        posts_writer.writeheader()

        client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)
        await client.connect()
        try:
            await ensure_login(client)

            channel = await client.get_entity(channel_ref)

            # linked discussion chat берём только через GetFullChannel
            full = await client(functions.channels.GetFullChannelRequest(channel))
            linked_id = full.full_chat.linked_chat_id
            if not linked_id:
                raise SystemExit("У канала нет linked_chat_id (нет привязанного обсуждения).")

            discussion = next(c for c in full.chats if c.id == linked_id)

            channel_id_for_links = AUCTION_CHANNEL_ID or getattr(channel, "id", 0)
            channel_username = AUCTION_CHANNEL_USERNAME if AUCTION_CHANNEL_USERNAME else None

            post_idx = 0
            async for post in client.iter_messages(channel, limit=BACKFILL_LIMIT_POSTS):
                post_idx += 1
                print(f"[{post_idx}/{BACKFILL_LIMIT_POSTS}] scanning post_id={post.id} ...", flush=True)

                post_link = make_post_link(channel_id_for_links, channel_username, post.id)

                # UTC aware (как Telethon отдаёт)
                post_date_utc = post.date
                end_time_utc = post_date_utc + timedelta(minutes=30)      # TODO: заменить на реальное end_time
                deadline_utc = end_time_utc + timedelta(minutes=1)       # “валидно до +1 минуты”

                # для CSV
                post_date_msk = to_msk(post_date_utc)
                end_time_msk = to_msk(end_time_utc)
                deadline_msk = to_msk(deadline_utc)

                root_id = None
                note = "ok"

                msgs_scanned = 0
                numeric_msgs = 0

                thread_bids = 0
                thread_valid = 0
                max_thread_valid = None
                winner_id = None
                winner_time_utc = None

                any_valid = 0
                max_any_valid = None

                # 1) маппинг пост -> discussion root
                try:
                    dm = await client(
                        functions.messages.GetDiscussionMessageRequest(
                            peer=channel,
                            msg_id=post.id,
                        )
                    )
                except FloodWaitError as e:
                    note = f"flood_wait_{e.seconds}s"
                    await asyncio.sleep(e.seconds + 1)
                    posts_writer.writerow(
                        asdict(
                            PostRow(
                                post_id=post.id,
                                post_link=post_link,
                                post_date_msk=str(post_date_msk),
                                end_time_msk=str(end_time_msk),
                                deadline_msk=str(deadline_msk),
                                root_id=None,
                                discussion_id=getattr(discussion, "id", None),
                                msgs_scanned=0,
                                numeric_msgs=0,
                                thread_bids=0,
                                thread_valid=0,
                                max_thread_valid=None,
                                winner_id=None,
                                any_valid=0,
                                max_any_valid=None,
                                note=note,
                            )
                        )
                    )
                    f_posts.flush()
                    print(f"  -> {note}", flush=True)
                    continue
                except MsgIdInvalidError:
                    note = "get_discussion_failed:MsgIdInvalidError"
                    posts_writer.writerow(
                        asdict(
                            PostRow(
                                post_id=post.id,
                                post_link=post_link,
                                post_date_msk=str(post_date_msk),
                                end_time_msk=str(end_time_msk),
                                deadline_msk=str(deadline_msk),
                                root_id=None,
                                discussion_id=getattr(discussion, "id", None),
                                msgs_scanned=0,
                                numeric_msgs=0,
                                thread_bids=0,
                                thread_valid=0,
                                max_thread_valid=None,
                                winner_id=None,
                                any_valid=0,
                                max_any_valid=None,
                                note=note,
                            )
                        )
                    )
                    f_posts.flush()
                    print(f"  -> {note}", flush=True)
                    continue
                except Exception as e:
                    note = f"get_discussion_failed:{type(e).__name__}"
                    posts_writer.writerow(
                        asdict(
                            PostRow(
                                post_id=post.id,
                                post_link=post_link,
                                post_date_msk=str(post_date_msk),
                                end_time_msk=str(end_time_msk),
                                deadline_msk=str(deadline_msk),
                                root_id=None,
                                discussion_id=getattr(discussion, "id", None),
                                msgs_scanned=0,
                                numeric_msgs=0,
                                thread_bids=0,
                                thread_valid=0,
                                max_thread_valid=None,
                                winner_id=None,
                                any_valid=0,
                                max_any_valid=None,
                                note=note,
                            )
                        )
                    )
                    f_posts.flush()
                    print(f"  -> {note}", flush=True)
                    continue

                if not getattr(dm, "messages", None):
                    note = "discussion_empty"
                    posts_writer.writerow(
                        asdict(
                            PostRow(
                                post_id=post.id,
                                post_link=post_link,
                                post_date_msk=str(post_date_msk),
                                end_time_msk=str(end_time_msk),
                                deadline_msk=str(deadline_msk),
                                root_id=None,
                                discussion_id=getattr(discussion, "id", None),
                                msgs_scanned=0,
                                numeric_msgs=0,
                                thread_bids=0,
                                thread_valid=0,
                                max_thread_valid=None,
                                winner_id=None,
                                any_valid=0,
                                max_any_valid=None,
                                note=note,
                            )
                        )
                    )
                    f_posts.flush()
                    print(f"  -> {note}", flush=True)
                    continue

                root = dm.messages[0]
                root_id = root.id

                # discussion_peer: по умолчанию linked discussion, но иногда dm.chats точнее
                discussion_peer = discussion
                try:
                    peer = getattr(root, "peer_id", None)
                    target_id = None
                    if peer is not None:
                        target_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
                    if target_id and getattr(dm, "chats", None):
                        for ch in dm.chats:
                            if getattr(ch, "id", None) == target_id:
                                discussion_peer = ch
                                break
                except Exception:
                    pass

                # 2) читаем сообщения в обсуждении в окне времени
                start_window_utc = post_date_utc - WINDOW_BEFORE
                end_window_utc = deadline_utc + WINDOW_AFTER

                try:
                    async for c in client.iter_messages(
                        discussion_peer,
                        offset_date=end_window_utc,   # ВАЖНО: UTC aware
                        limit=COMMENTS_LIMIT_PER_POST,
                    ):
                        # iter_messages идёт от новых к старым
                        if c.date < start_window_utc:
                            break

                        if c.date > end_window_utc:
                            # крайне редко, но на всякий случай
                            continue

                        msgs_scanned += 1

                        raw = c.message or ""
                        amt = parse_amount(raw)
                        if amt is None or c.sender_id is None:
                            continue

                        numeric_msgs += 1

                        reply_to_msg_id, reply_to_top_id = get_reply_ids(c)
                        in_thread = (reply_to_top_id == root_id) or (reply_to_msg_id == root_id)

                        is_valid_time = c.date <= deadline_utc
                        if is_valid_time:
                            any_valid += 1
                            if max_any_valid is None or amt > max_any_valid:
                                max_any_valid = amt

                        bids_writer.writerow(
                            {
                                "post_id": post.id,
                                "post_link": post_link,
                                "post_date_msk": str(post_date_msk),
                                "root_id": root_id,
                                "comment_id": c.id,
                                "comment_link": make_msg_link(discussion_peer, c.id),
                                "sender_id": int(c.sender_id),
                                "amount": amt,
                                "comment_date_msk": str(to_msk(c.date)),
                                "is_valid_time": int(is_valid_time),
                                "in_thread": int(in_thread),
                                "reply_to_msg_id": reply_to_msg_id,
                                "reply_to_top_id": reply_to_top_id,
                                "raw_text": raw.replace("\n", "\\n"),
                            }
                        )

                        if in_thread:
                            thread_bids += 1
                            if is_valid_time:
                                thread_valid += 1
                                # winner: max ставка, при равенстве раньше по времени
                                if (
                                    max_thread_valid is None
                                    or amt > max_thread_valid
                                    or (amt == max_thread_valid and winner_time_utc and c.date < winner_time_utc)
                                ):
                                    max_thread_valid = amt
                                    winner_id = int(c.sender_id)
                                    winner_time_utc = c.date

                except FloodWaitError as e:
                    note = f"flood_wait_{e.seconds}s"
                    await asyncio.sleep(e.seconds + 1)
                except MsgIdInvalidError:
                    note = "msg_id_invalid_thread"
                except Exception as e:
                    note = f"replies_failed:{type(e).__name__}"

                posts_writer.writerow(
                    asdict(
                        PostRow(
                            post_id=post.id,
                            post_link=post_link,
                            post_date_msk=str(post_date_msk),
                            end_time_msk=str(end_time_msk),
                            deadline_msk=str(deadline_msk),
                            root_id=root_id,
                            discussion_id=getattr(discussion_peer, "id", None),
                            msgs_scanned=msgs_scanned,
                            numeric_msgs=numeric_msgs,
                            thread_bids=thread_bids,
                            thread_valid=thread_valid,
                            max_thread_valid=max_thread_valid,
                            winner_id=winner_id,
                            any_valid=any_valid,
                            max_any_valid=max_any_valid,
                            note=note,
                        )
                    )
                )

                f_bids.flush()
                f_posts.flush()

                print(
                    f"  -> scanned={msgs_scanned} numeric={numeric_msgs} "
                    f"thread_bids={thread_bids} thread_valid={thread_valid} "
                    f"max_thread={max_thread_valid} winner={winner_id} "
                    f"max_any={max_any_valid} note={note}",
                    flush=True,
                )

        finally:
            await client.disconnect()

    print(f"\nDONE\nBIDS:  {BIDS_CSV}\nPOSTS: {POSTS_CSV}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
