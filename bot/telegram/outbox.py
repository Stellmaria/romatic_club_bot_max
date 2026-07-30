from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.repositories.outbox import TelegramOutboxRepository
from db.core import get_db_pool

logger = logging.getLogger("auction_bot.telegram_outbox")


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    raise ValueError("outbox payload must be a JSON object")


async def deliver_outbox_batch(
    bot: Bot,
    repository: TelegramOutboxRepository,
    *,
    limit: int = 50,
) -> int:
    delivered = 0
    # Claim one command at a time.  A graceful shutdown can then leave at most
    # the currently attempted delivery in the unknown/manual-review state;
    # commands that were never attempted remain pending.
    for _ in range(max(1, int(limit))):
        claimed = await repository.claim_batch(limit=1)
        if not claimed:
            break
        item = claimed[0]
        outbox_id = int(item["outbox_id"])
        try:
            payload = _payload(item.get("payload"))
            method = item.get("method")
            if method == "send_message":
                text = str(payload.pop("text"))
                message = await bot.send_message(int(item["chat_id"]), text, **payload)
            elif method == "copy_message":
                message = await bot.copy_message(int(item["chat_id"]), **payload)
            else:
                raise ValueError(f"unsupported outbox method: {method}")
        except asyncio.CancelledError:
            # Delivery may already have reached Telegram.  Leave the lease for
            # stale-claim handling instead of risking a duplicate on restart.
            raise
        except TelegramRetryAfter as exc:
            await repository.retry_after(
                outbox_id,
                delay_seconds=int(getattr(exc, "retry_after", 1)),
                error=f"TelegramRetryAfter: {exc}",
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            await repository.mark_failed(
                outbox_id,
                error=f"terminal Telegram rejection: {exc}",
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
            delivered += 1
    return delivered


async def telegram_outbox_loop(bot: Bot) -> None:
    repository = TelegramOutboxRepository(await get_db_pool())
    while True:
        try:
            stale = await repository.fail_stale()
            if stale:
                logger.error("Stale outbox deliveries require manual review: %s", stale)
            await deliver_outbox_batch(bot, repository)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram outbox iteration failed")
        await asyncio.sleep(3)
