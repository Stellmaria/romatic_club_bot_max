# ruff: noqa: S311
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import defaultdict, deque
from time import monotonic
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.repositories.outbox import TelegramOutboxRepository
from bot.telegram.outbox_commands import OutboxCommandError, decode_command
from db.core import get_db_pool

logger = logging.getLogger("auction_bot.telegram_outbox")


class TelegramRateLimiter:
    """Small in-process limiter for global and per-chat Telegram pacing."""

    def __init__(
        self,
        *,
        global_rate: float = 25.0,
        per_chat_rate: float = 1.0,
    ) -> None:
        self._global_interval = 1.0 / max(0.1, float(global_rate))
        self._chat_interval = 1.0 / max(0.1, float(per_chat_rate))
        self._global_lock = asyncio.Lock()
        self._chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_global = 0.0
        self._next_chat: dict[int, float] = {}

    async def wait(self, chat_id: int) -> None:
        chat_lock = self._chat_locks[chat_id]
        async with chat_lock, self._global_lock:
            now = monotonic()
            due = max(self._next_global, self._next_chat.get(chat_id, 0.0))
            delay = due - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = monotonic()
            jitter = random.uniform(0.0, 0.02)
            self._next_global = now + self._global_interval + jitter
            self._next_chat[chat_id] = now + self._chat_interval + jitter


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    raise OutboxCommandError("outbox payload must be a JSON object")


async def _refresh_auction_publication(
    bot: Bot,
    *,
    auction_id: int,
) -> int:
    from bot.core.legacy_config import legacy_config
    from bot.handlers.admin.helper.admin_constants import render_auction_caption
    from bot.handlers.auction.publication import _media_id, _publication_context
    from bot.services.auction_workflows import AuctionPublicationService

    service = await AuctionPublicationService.create()
    auction = await service.get_publication(int(auction_id))
    message_id = int(auction.get("message_id") or 0)
    if message_id <= 0:
        raise OutboxCommandError(
            "refresh_auction_publication requires an existing positive message_id"
        )
    full_auction, card, deck, owners_count = await _publication_context(auction)
    caption = render_auction_caption(
        full_auction,
        card=card,
        deck=deck,
        owners_count=owners_count,
        show_min_bid=True,
    )
    chat_id = int(legacy_config.AUCTION_CHANNEL_ID)
    if _media_id(full_auction, card, auction):
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            parse_mode="HTML",
        )
    else:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=caption,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    return message_id


async def _deliver_one(
    bot: Bot,
    repository: TelegramOutboxRepository,
    item: dict[str, Any],
    limiter: TelegramRateLimiter,
) -> bool:
    outbox_id = int(item["outbox_id"])
    chat_id = int(item["chat_id"])
    try:
        command = decode_command(
            _payload(item.get("payload")),
            legacy_method=str(item.get("method") or "") or None,
        )
        payload = dict(command.payload)
        await limiter.wait(chat_id)
        message: Any
        if command.command_type == "send_message":
            text = str(payload.pop("text"))
            message = await bot.send_message(chat_id, text, **payload)
        elif command.command_type == "copy_message":
            message = await bot.copy_message(chat_id, **payload)
        elif command.command_type == "refresh_auction_publication":
            message_id = await _refresh_auction_publication(
                bot,
                auction_id=int(payload["auction_id"]),
            )
            message = type("EditedMessage", (), {"message_id": message_id})()
        else:  # registry and dispatch must stay in lock-step
            raise OutboxCommandError(f"unsupported outbox command type: {command.command_type}")
    except asyncio.CancelledError:
        raise
    except TelegramRetryAfter as exc:
        await repository.retry_after(
            outbox_id,
            delay_seconds=int(getattr(exc, "retry_after", 1)) + random.randint(0, 2),
            error=f"TelegramRetryAfter: {exc}",
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        await repository.mark_failed(
            outbox_id,
            error=f"terminal Telegram rejection: {exc}",
            delivery_state="confirmed_not_sent",
        )
    except OutboxCommandError as exc:
        await repository.mark_failed(
            outbox_id,
            error=f"invalid outbox command; manual review required: {exc}",
            delivery_state="confirmed_not_sent",
        )
    except TelegramAPIError as exc:
        await repository.mark_failed(
            outbox_id,
            error=f"delivery outcome unknown; manual review required: {exc}",
            delivery_state="unknown",
        )
    except Exception as exc:
        logger.exception("Outbox delivery %s failed", outbox_id)
        await repository.mark_failed(
            outbox_id,
            error=f"delivery outcome unknown; manual review required: {exc!r}",
            delivery_state="unknown",
        )
    else:
        if not await repository.mark_sent(
            outbox_id,
            message_id=int(message.message_id),
        ):
            logger.error("Outbox row %s left processing state after delivery", outbox_id)
        return True
    return False


async def deliver_outbox_batch(
    bot: Bot,
    repository: TelegramOutboxRepository,
    *,
    limit: int = 50,
    concurrency: int = 8,
    limiter: TelegramRateLimiter | None = None,
) -> int:
    claimed = await repository.claim_batch(limit=max(1, int(limit)))
    if not claimed:
        return 0

    rate_limiter = limiter or TelegramRateLimiter()
    per_chat: dict[int, deque[dict[str, Any]]] = {}
    for item in claimed:
        per_chat.setdefault(int(item["chat_id"]), deque()).append(item)

    ready = deque(per_chat)
    ready_lock = asyncio.Lock()
    delivered = 0
    delivered_lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal delivered
        while True:
            async with ready_lock:
                if not ready:
                    return
                chat_id = ready.popleft()
                item = per_chat[chat_id].popleft()
                if per_chat[chat_id]:
                    ready.append(chat_id)
            if await _deliver_one(bot, repository, item, rate_limiter):
                async with delivered_lock:
                    delivered += 1

    workers = [
        asyncio.create_task(worker()) for _ in range(min(max(1, int(concurrency)), len(claimed)))
    ]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    return delivered


async def telegram_outbox_loop(bot: Bot) -> None:
    repository = TelegramOutboxRepository(await get_db_pool())
    limiter = TelegramRateLimiter()
    idle_delay = 0.5
    while True:
        try:
            stale = await repository.fail_stale()
            if stale:
                logger.error("Stale outbox deliveries require manual review: %s", stale)
            delivered = await deliver_outbox_batch(
                bot,
                repository,
                limiter=limiter,
            )
            idle_delay = 0.5 if delivered else min(5.0, idle_delay * 1.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram outbox iteration failed")
            idle_delay = min(5.0, idle_delay * 1.5)
        await asyncio.sleep(idle_delay)
