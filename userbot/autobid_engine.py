import asyncio
import logging
import time
from datetime import datetime

from db.db import (
    get_lot_by_id,
    get_top_bid_for_auction,
    pick_best_autobid_candidate,
    record_autobid_action,
)
from userbot.utils import get_discussion_peer, now_msk

logger = logging.getLogger("autobid")

# Логика (аукцион обычно заканчивается на секунде 00):
#  - :56 начинаем "typing"
#  - :58-:59 делаем ОДНУ ставку
TYPE_START_BEFORE_END_SEC = 4.0
SEND_BEFORE_END_SEC = 1.89

# Чуть опережаем, чтобы не улетать ровно в 00
CLIENT_TIMER_AHEAD_SEC = 0.15

RECHECK_BEFORE_SEND_SEC = 0.20
FINAL_RECHECK_BEFORE_SEND_SEC = 0.05

# Один таск на лот (снайп)
_SNIPE_TASKS: dict[int, dict] = {}

# Анти-дубль: если уже стреляли по этому end_ts, больше не стреляем повторно
_LAST_SNIPE_FIRE: dict[int, dict] = {}
_LAST_SNIPE_FIRE_TTL_SEC = 20.0

# Локальный маппинг исходящих автоставок (пока БД-лог не успел записаться)
_LOCAL_AUTOBID: dict[int, dict] = {}
_LOCAL_AUTOBID_TTL_SEC = 30.0

# Общий lock на аукцион: и для обычной ставки, и для финального снайпа
_AUCTION_LOCKS: dict[int, asyncio.Lock] = {}


def _auction_lock(auction_id: int) -> asyncio.Lock:
    lock = _AUCTION_LOCKS.get(int(auction_id))
    if lock is None:
        lock = asyncio.Lock()
        _AUCTION_LOCKS[int(auction_id)] = lock
    return lock


def get_auction_lock(auction_id: int) -> asyncio.Lock:
    return _auction_lock(int(auction_id))


def get_local_autobid_action(msg_id: int) -> dict | None:
    d = _LOCAL_AUTOBID.get(int(msg_id))
    if not d:
        return None
    if (time.time() - float(d.get("ts", 0.0))) > _LOCAL_AUTOBID_TTL_SEC:
        _LOCAL_AUTOBID.pop(int(msg_id), None)
        return None
    return d


def pop_local_autobid_action(msg_id: int) -> None:
    _LOCAL_AUTOBID.pop(int(msg_id), None)


def _cleanup_local_autobids() -> None:
    now = time.time()
    for mid, d in list(_LOCAL_AUTOBID.items()):
        if (now - float(d.get("ts", 0.0))) > _LOCAL_AUTOBID_TTL_SEC:
            _LOCAL_AUTOBID.pop(int(mid), None)


def _cleanup_last_fire() -> None:
    now = time.time()
    for aid, d in list(_LAST_SNIPE_FIRE.items()):
        if (now - float(d.get("fired_at", 0.0))) > _LAST_SNIPE_FIRE_TTL_SEC:
            _LAST_SNIPE_FIRE.pop(int(aid), None)


def _already_fired(auction_id: int, end_ts: float) -> bool:
    _cleanup_last_fire()
    d = _LAST_SNIPE_FIRE.get(int(auction_id))
    if not d:
        return False
    return float(d.get("end_ts", -1.0)) == float(end_ts)


def _mark_fired(auction_id: int, end_ts: float) -> None:
    _LAST_SNIPE_FIRE[int(auction_id)] = {"end_ts": float(end_ts), "fired_at": time.time()}
    _cleanup_last_fire()


def _overcap_by_currency(currency: str) -> int:
    c = (currency or "").lower()
    if "алмаз" in c:
        return 90
    if "чаш" in c or "чай" in c:
        return 2
    return 10


def _is_diamonds(currency: str) -> bool:
    return "алмаз" in (currency or "").lower()


def _floor30(x: int) -> int:
    return (int(x) // 30) * 30


def _calc_one_shot_amount(
    *,
    currency: str,
    current_max: int | None,
    base_max: int,
    overcap: int,
) -> int | None:
    """
    РОВНО ОДНА ставка в финале.

    Алмазы (все наши ставки кратны 30):
      - если current < base_max  -> floor30(current) + 30, но не выше base_max (floor30)
      - если current >= base_max -> base_max + overcap (например +90), если это выше current
    """
    base_max = int(base_max)
    cap_add = int(overcap)

    if _is_diamonds(currency):
        base = max(30, _floor30(base_max))
        cap = base + cap_add

        if current_max is None:
            return base

        cur = int(current_max)

        if cur >= cap:
            return None

        if cur >= base:
            return cap if cap > cur else None

        bid = _floor30(cur) + 30
        if bid > base:
            bid = base
        return bid if bid > cur else None

    if current_max is None:
        return base_max

    cur = int(current_max)
    base = base_max
    cap = base + cap_add

    if cur >= cap:
        return None
    if cur >= base:
        return cap if cap > cur else None
    return base if base > cur else None


async def _get_peer(client, discussion_chat_id: int | None):
    if discussion_chat_id:
        try:
            return await client.get_entity(int(discussion_chat_id))
        except Exception:  # noqa: BLE001
            pass
    return await get_discussion_peer(client)


async def _send_bid(
    client,
    *,
    peer,
    auction: dict,
    amount: int,
    target_user_id: int,
    autobid_id: int,
) -> None:
    thread_root_id = int(auction.get("discussion_message_id") or 0)
    if not thread_root_id:
        return

    sent_amount = int(amount)
    msg = await client.send_message(peer, str(sent_amount), reply_to=thread_root_id)

    _LOCAL_AUTOBID[int(msg.id)] = {
        "target_user_id": int(target_user_id),
        "amount": int(sent_amount),
        "autobid_id": int(autobid_id),
        "ts": time.time(),
    }

    try:
        await record_autobid_action(
            autobid_id=int(autobid_id),
            auction_id=int(auction["auction_id"]),
            target_user_id=int(target_user_id),
            amount=int(sent_amount),
            discussion_message_id=int(msg.id),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record autobid action for msg_id=%s", getattr(msg, "id", None))
    finally:
        _cleanup_local_autobids()


async def schedule_snipe(
    client,
    *,
    auction: dict,
    discussion_chat_id: int | None = None,
) -> None:
    auction_id = int(auction["auction_id"])

    end_dt: datetime | None = auction.get("end_time")
    if not end_dt:
        return

    now = now_msk()

    if end_dt.tzinfo is None and now.tzinfo is not None:
        end_dt = end_dt.replace(tzinfo=now.tzinfo)
    elif end_dt.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=end_dt.tzinfo)

    left = (end_dt - now).total_seconds()
    if left <= 0:
        return

    end_ts = end_dt.timestamp()

    if _already_fired(auction_id, end_ts):
        return

    prev = _SNIPE_TASKS.get(auction_id)
    if prev and prev.get("end_ts") == end_ts:
        t = prev.get("task")
        if t and not t.done():
            return

    if prev and prev.get("task") and not prev["task"].done():
        prev["task"].cancel()

    task = asyncio.create_task(
        _snipe_task(
            client,
            discussion_chat_id=discussion_chat_id,
            auction_id=auction_id,
            end_ts=end_ts,
        )
    )
    _SNIPE_TASKS[auction_id] = {"task": task, "end_ts": end_ts}


async def _snipe_task(client, *, discussion_chat_id: int | None, auction_id: int, end_ts: float) -> None:
    try:
        peer = await _get_peer(client, discussion_chat_id)

        type_at_ts = end_ts - TYPE_START_BEFORE_END_SEC - CLIENT_TIMER_AHEAD_SEC
        send_at_ts = end_ts - SEND_BEFORE_END_SEC - CLIENT_TIMER_AHEAD_SEC

        while True:
            now_ts = now_msk().timestamp()
            if now_ts >= type_at_ts:
                break
            await asyncio.sleep(min(1.0, max(0.05, type_at_ts - now_ts)))

        async with client.action(peer, "typing"):
            precheck_ts = send_at_ts - RECHECK_BEFORE_SEND_SEC
            while True:
                now_ts = now_msk().timestamp()
                if now_ts >= precheck_ts:
                    break
                await asyncio.sleep(min(0.2, max(0.02, precheck_ts - now_ts)))

            auction = await get_lot_by_id(int(auction_id))
            if not auction or str(auction.get("status")) != "active":
                _mark_fired(int(auction_id), float(end_ts))
                return

            if now_msk().timestamp() >= end_ts:
                _mark_fired(int(auction_id), float(end_ts))
                return

            currency = auction.get("currency") or ""
            overcap = _overcap_by_currency(currency)

            current_max, leader_id = await get_top_bid_for_auction(int(auction_id))
            cand = await pick_best_autobid_candidate(
                auction_id=int(auction_id),
                current_max=current_max,
                current_leader_id=leader_id,
            )
            if not cand:
                _mark_fired(int(auction_id), float(end_ts))
                return

            target_user_id = int(cand["target_user_id"])
            if leader_id is not None and int(leader_id) == target_user_id:
                _mark_fired(int(auction_id), float(end_ts))
                return

            base_max = int(cand["max_amount"])

            final_ts = send_at_ts - FINAL_RECHECK_BEFORE_SEND_SEC
            while True:
                now_ts = now_msk().timestamp()
                if now_ts >= final_ts:
                    break
                await asyncio.sleep(min(0.02, max(0.005, final_ts - now_ts)))

            async with get_auction_lock(int(auction_id)):
                latest_max, latest_leader_id = await get_top_bid_for_auction(int(auction_id))

                if latest_leader_id is not None and int(latest_leader_id) == target_user_id:
                    _mark_fired(int(auction_id), float(end_ts))
                    return

                amount = _calc_one_shot_amount(
                    currency=currency,
                    current_max=latest_max,
                    base_max=base_max,
                    overcap=overcap,
                )
                if amount is None:
                    _mark_fired(int(auction_id), float(end_ts))
                    return

                autobid_id = int(cand.get("autobid_id") or cand.get("id") or 0)
                _mark_fired(int(auction_id), float(end_ts))

                await _send_bid(
                    client,
                    peer=peer,
                    auction=auction,
                    amount=int(amount),
                    target_user_id=int(target_user_id),
                    autobid_id=autobid_id,
                )

    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Snipe task failed auction_id=%s", auction_id)
    finally:
        cur = _SNIPE_TASKS.get(int(auction_id))
        if cur and cur.get("end_ts") == end_ts:
            _SNIPE_TASKS.pop(int(auction_id), None)


async def maybe_place_autobid(client, *, discussion_chat_id: int | None, auction_id: int) -> None:
    auction = await get_lot_by_id(int(auction_id))
    if not auction or str(auction.get("status")) != "active":
        return

    await schedule_snipe(client, discussion_chat_id=discussion_chat_id, auction=auction)
