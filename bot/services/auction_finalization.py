from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import Bot

from bot.core.time import ensure_utc, utc_now
from bot.repositories.auctions import AuctionFinalizationRepository
from db.core import get_db_pool

logger = logging.getLogger("auction_bot.auction_finalization")
WinnerAnnouncer = Callable[
    [Bot, dict[str, Any], list[dict[str, Any]]],
    Awaitable[None],
]


class AuctionFinalizationService:
    def __init__(
        self,
        repository: AuctionFinalizationRepository,
        announcer: WinnerAnnouncer,
    ):
        self._repository = repository
        self._announcer = announcer

    @classmethod
    async def create(
        cls,
        announcer: WinnerAnnouncer,
    ) -> "AuctionFinalizationService":
        return cls(AuctionFinalizationRepository(await get_db_pool()), announcer)

    async def process_due(self, bot: Bot, *, now: datetime | None = None) -> int:
        threshold = ensure_utc(now) if now is not None else utc_now()
        stale = await self._repository.fail_stale_claims(older_than_minutes=15)
        if stale:
            logger.error(
                "Moved stale auction finalization claims to manual review: %s",
                ", ".join(map(str, stale)),
            )

        auctions = await self._repository.claim_due(now=threshold)
        completed = 0

        for auction in auctions:
            auction_id = int(auction["auction_id"])
            try:
                bids = await self._repository.get_bids(auction_id)
                await self._announcer(bot, auction, bids)
                if not await self._repository.mark_finished(auction_id):
                    raise RuntimeError("auction left finalizing state before completion")
                completed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Could not finalize auction %s", auction_id)
                await self._repository.mark_failed(auction_id, repr(exc))

        return completed


async def auction_finalization_loop(
    bot: Bot,
    announcer: WinnerAnnouncer,
) -> None:
    service = await AuctionFinalizationService.create(announcer)
    while True:
        try:
            await service.process_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auction finalization loop failed")
        await asyncio.sleep(30)
