from __future__ import annotations

from bot.repositories.publication_recovery import (
    AuctionPublicationRecoveryRepository,
)
from db.core import get_db_pool


class AuctionPublicationRecoveryService:
    def __init__(self, repository: AuctionPublicationRecoveryRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionPublicationRecoveryService":
        return cls(AuctionPublicationRecoveryRepository(await get_db_pool()))

    async def mark_awaiting_channel_post(self, auction_id: int) -> bool:
        return await self._repository.mark_awaiting_channel_post(int(auction_id))

    async def confirm_channel_post(
        self,
        auction_id: int,
        *,
        message_id: int,
    ) -> bool:
        return await self._repository.confirm_channel_post(
            int(auction_id),
            message_id=int(message_id),
        )


__all__ = ["AuctionPublicationRecoveryService"]
