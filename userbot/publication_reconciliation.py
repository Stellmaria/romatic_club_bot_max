from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from bot.core.legacy_config import legacy_config
from bot.services.publication_recovery import AuctionPublicationRecoveryService

logger = logging.getLogger("userbot.publication_reconciliation")

_LOT_ID_RE = re.compile(r"(?i)\bлот\s*№\s*(\d{1,10})\b")


def extract_auction_id(text: str | None) -> int | None:
    match = _LOT_ID_RE.search(text or "")
    if not match:
        return None
    try:
        value = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _configured_channels() -> tuple[int | str, ...]:
    targets: list[int | str] = []
    if legacy_config.AUCTION_CHANNEL_ID:
        targets.append(legacy_config.AUCTION_CHANNEL_ID)
    username = str(legacy_config.AUCTION_CHANNEL_USERNAME or "").strip()
    if username:
        username_target = username if username.startswith("@") else f"@{username}"
        if username_target not in targets:
            targets.append(username_target)
    return tuple(targets)


async def reconcile_recent_auction_publications(
    telegram_client: Any,
    *,
    channel: int | str | None = None,
    limit: int = 100,
    service: AuctionPublicationRecoveryService | None = None,
) -> int:
    targets = (channel,) if channel is not None else _configured_channels()
    if not targets:
        return 0

    scan_limit = max(1, int(limit))
    recovery = service or await AuctionPublicationRecoveryService.create()
    remaining = set(
        await recovery.recoverable_auction_ids(limit=scan_limit)
    )
    if not remaining:
        return 0

    recovered = 0
    for target in targets:
        try:
            async for message in telegram_client.iter_messages(
                target,
                limit=scan_limit,
            ):
                message_id = int(getattr(message, "id", 0) or 0)
                if message_id <= 0:
                    continue
                auction_id = extract_auction_id(getattr(message, "message", None))
                if auction_id is None or auction_id not in remaining:
                    continue
                if await recovery.confirm_channel_post(
                    auction_id,
                    message_id=message_id,
                ):
                    recovered += 1
                    remaining.discard(auction_id)
                    logger.warning(
                        "Recovered auction %s from Telegram channel message %s",
                        auction_id,
                        message_id,
                    )
                    if not remaining:
                        return recovered
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Could not scan auction channel target %r for publication recovery",
                target,
            )
    return recovered


async def publication_reconciliation_watchdog(telegram_client: Any) -> None:
    history_limit = 500
    while True:
        try:
            recovered = await reconcile_recent_auction_publications(
                telegram_client,
                limit=history_limit,
            )
            history_limit = 100
            if recovered:
                logger.warning("Recovered %s auction publication(s)", recovered)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auction publication reconciliation failed")
        await asyncio.sleep(60)


__all__ = [
    "extract_auction_id",
    "publication_reconciliation_watchdog",
    "reconcile_recent_auction_publications",
]
