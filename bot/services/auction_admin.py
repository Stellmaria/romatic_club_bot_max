from __future__ import annotations

from typing import Any

from bot.repositories.auction_admin import AuctionAdminRepository
from db.core import get_db_pool


class AuctionAdminService:
    def __init__(self, repository: AuctionAdminRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionAdminService":
        return cls(AuctionAdminRepository(await get_db_pool()))

    async def delete_bid_with_warning(
        self,
        *,
        discussion_message_id: int,
    ) -> dict[str, Any] | None:
        return await self._repository.delete_bid_with_warning(
            discussion_message_id=int(discussion_message_id),
            reason="delete_bid",
        )
