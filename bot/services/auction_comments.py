"""Use cases for miscellaneous auction discussion messages."""

from __future__ import annotations

from typing import Any

from bot.repositories.auction_comments import AuctionCommentRepository
from db.pool import get_db_pool


class AuctionCommentService:
    def __init__(self, repository: AuctionCommentRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionCommentService":
        return cls(AuctionCommentRepository(await get_db_pool()))

    async def is_active_lot_owner(self, *, user_id: int, username: str) -> bool:
        return await self._repository.is_active_lot_owner(
            user_id=int(user_id),
            username=(username or "").strip().lower(),
        )

    async def get_current_auction(self) -> dict[str, Any] | None:
        return await self._repository.get_current_auction()

    async def auction_id_for_bid_message(self, discussion_message_id: int) -> int | None:
        return await self._repository.auction_id_for_bid_message(discussion_message_id)


__all__ = ["AuctionCommentService"]
