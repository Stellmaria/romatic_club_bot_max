from __future__ import annotations

from bot.domain.auctions import (
    AuctionNotActive,
    AuctionKindNotBiddable,
    AuctionStatus,
    Autobid,
    AutobidLimitTooLow,
    AutobidTargetNotFound,
)
from bot.domain.auctions.rules import minimum_next_bid
from bot.repositories.auction_autobids import AuctionAutobidRepository
from db.core import get_db_pool


class AuctionAutobidService:
    def __init__(self, repository: AuctionAutobidRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionAutobidService":
        return cls(AuctionAutobidRepository(await get_db_pool()))

    async def configure(
        self,
        *,
        auction_id: int,
        target_username: str,
        max_amount: int,
        created_by: int,
    ) -> Autobid:
        auction = await self._repository.get_auction(auction_id)
        if auction.normalized_status not in {
            AuctionStatus.PENDING,
            AuctionStatus.APPROVED,
            AuctionStatus.SCHEDULED,
            AuctionStatus.ACTIVE,
        }:
            raise AuctionNotActive(auction.status)
        if not auction.auction_kind.supports_autobid:
            raise AuctionKindNotBiddable(auction.auction_kind.value)

        normalized_username = (target_username or "").strip().lstrip("@")
        target = await self._repository.get_user_by_username(normalized_username)
        if not target:
            raise AutobidTargetNotFound(normalized_username)

        current_max = await self._repository.get_max_bid(auction_id)
        minimum = minimum_next_bid(
            start_price=auction.start_price,
            current_max=current_max,
            step=auction.currency.bid_step,
        )
        if int(max_amount) < minimum:
            raise AutobidLimitTooLow(minimum=minimum)

        return await self._repository.upsert(
            auction_id=auction_id,
            target_user_id=int(target["user_id"]),
            target_username=normalized_username,
            max_amount=int(max_amount),
            step=auction.currency.autobid_step,
            created_by=created_by,
        )

    async def disable(self, *, auction_id: int, target_username: str) -> bool:
        normalized_username = (target_username or "").strip().lstrip("@")
        target = await self._repository.get_user_by_username(normalized_username)
        if not target:
            raise AutobidTargetNotFound(normalized_username)
        return await self._repository.disable(
            auction_id=auction_id,
            target_user_id=int(target["user_id"]),
        )

    async def list_active(self, *, auction_id: int | None = None) -> list[Autobid]:
        return await self._repository.list(auction_id=auction_id, only_active=True)
