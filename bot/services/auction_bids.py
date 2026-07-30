from __future__ import annotations

from datetime import datetime

from bot.domain.auctions import (
    Auction,
    AuctionEnded,
    AuctionNotActive,
    Bid,
    BidOwnershipError,
    BidPlacement,
    BidRevision,
    BidRevisionWindowExpired,
    BidderBanned,
    BidderNotEligible,
)
from bot.domain.auctions.rules import parse_bid_amount, validate_bid_for_kind
from bot.core.time import ensure_utc, utc_now
from bot.repositories.auction_bids import AuctionBidRepository, AuctionBidTransaction
from db.core import get_db_pool


class AuctionBidService:
    def __init__(self, repository: AuctionBidRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionBidService":
        return cls(AuctionBidRepository(await get_db_pool()))

    @staticmethod
    def _assert_active(auction: Auction, now: datetime) -> None:
        if auction.has_ended_at(now):
            raise AuctionEnded(f"auction {auction.auction_id} has ended")
        if not auction.is_active_at(now):
            raise AuctionNotActive(auction.status)

    async def place_for_discussion(
        self,
        *,
        discussion_root_message_id: int,
        bid_message_id: int,
        bidder_id: int,
        bid_text: str,
        username: str | None = None,
        full_name: str | None = None,
        now: datetime | None = None,
        check_ban: bool = True,
    ) -> BidPlacement:
        async with self._repository.transaction() as tx:
            auction = await tx.get_auction_by_discussion_message(
                discussion_root_message_id,
                for_update=True,
            )
            return await self._place_locked(
                tx,
                auction=auction,
                bid_message_id=bid_message_id,
                bidder_id=bidder_id,
                bid_text=bid_text,
                explicit_amount=None,
                username=username,
                full_name=full_name,
                now=now,
                check_ban=check_ban,
            )

    async def place_for_auction(
        self,
        *,
        auction_id: int,
        bid_message_id: int,
        bidder_id: int,
        bid_text: str = "",
        explicit_amount: int | None = None,
        username: str | None = None,
        full_name: str | None = None,
        now: datetime | None = None,
        check_ban: bool = True,
    ) -> BidPlacement:
        async with self._repository.transaction() as tx:
            auction = await tx.get_auction_by_id(auction_id, for_update=True)
            return await self._place_locked(
                tx,
                auction=auction,
                bid_message_id=bid_message_id,
                bidder_id=bidder_id,
                bid_text=bid_text,
                explicit_amount=explicit_amount,
                username=username,
                full_name=full_name,
                now=now,
                check_ban=check_ban,
            )

    async def _place_locked(
        self,
        tx: AuctionBidTransaction,
        *,
        auction: Auction,
        bid_message_id: int,
        bidder_id: int,
        bid_text: str,
        explicit_amount: int | None,
        username: str | None,
        full_name: str | None,
        now: datetime | None,
        check_ban: bool,
    ) -> BidPlacement:
        current_time = ensure_utc(now) if now is not None else utc_now()
        self._assert_active(auction, current_time)

        await tx.ensure_user(
            user_id=bidder_id,
            username=username,
            full_name=full_name,
        )
        if check_ban and await tx.is_user_banned(bidder_id):
            raise BidderBanned(f"bidder {bidder_id} is banned")
        if auction.auction_kind.requires_luxury_bidder and not await tx.is_user_luxury(bidder_id):
            raise BidderNotEligible("black auctions accept bids only from Luxury users")

        amount = int(explicit_amount) if explicit_amount is not None else parse_bid_amount(bid_text)
        previous_max = await tx.get_best_bid(
            auction.auction_id,
            lowest_wins=auction.lowest_bid_wins,
        )
        minimum = validate_bid_for_kind(
            amount=amount,
            currency=auction.currency,
            start_price=auction.start_price,
            current_best=previous_max,
            auction_kind=auction.auction_kind,
        )
        bid = await tx.insert_bid(
            auction_id=auction.auction_id,
            bidder_id=bidder_id,
            amount=amount,
            discussion_message_id=bid_message_id,
        )
        return BidPlacement(
            auction=auction,
            bid=bid,
            previous_max=previous_max,
            minimum_required=minimum,
        )

    async def revise_bid(
        self,
        *,
        bid_message_id: int,
        actor_user_id: int,
        new_bid_text: str | None,
        now: datetime | None = None,
        revision_window_seconds: int = 60,
    ) -> BidRevision:
        current_time = ensure_utc(now) if now is not None else utc_now()
        async with self._repository.transaction() as tx:
            bid = await tx.get_bid_by_message(bid_message_id, for_update=True)
            auction = await tx.get_auction_by_id(bid.auction_id, for_update=True)
            self._assert_active(auction, current_time)

            if bid.bidder_id != int(actor_user_id):
                raise BidOwnershipError("only the bidder can revise a bid")

            created_at = bid.created_at or bid.placed_at
            if created_at is not None:
                if (current_time - ensure_utc(created_at)).total_seconds() > revision_window_seconds:
                    raise BidRevisionWindowExpired(revision_window_seconds)

            previous_amount = bid.amount
            if not (new_bid_text or "").strip():
                deleted = await tx.delete_bid(bid.bid_id)
                return BidRevision(
                    auction=auction,
                    bid=deleted,
                    previous_amount=previous_amount,
                    cancelled=True,
                )

            amount = parse_bid_amount(new_bid_text or "")
            other_max = await tx.get_best_bid(
                auction.auction_id,
                lowest_wins=auction.lowest_bid_wins,
                excluding_bid_id=bid.bid_id,
            )
            minimum = validate_bid_for_kind(
                amount=amount,
                currency=auction.currency,
                start_price=auction.start_price,
                current_best=other_max,
                auction_kind=auction.auction_kind,
            )
            updated = await tx.update_bid_amount(bid.bid_id, amount)
            return BidRevision(
                auction=auction,
                bid=updated,
                previous_amount=previous_amount,
                cancelled=False,
                minimum_required=minimum,
            )

    async def remove_edited_bid(
        self,
        *,
        bid_message_id: int,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> Bid:
        current_time = ensure_utc(now) if now is not None else utc_now()
        async with self._repository.transaction() as tx:
            bid = await tx.get_bid_by_message(bid_message_id, for_update=True)
            auction = await tx.get_auction_by_id(bid.auction_id, for_update=True)
            self._assert_active(auction, current_time)
            if bid.bidder_id != int(actor_user_id):
                raise BidOwnershipError("edited bid belongs to another user")
            return await tx.delete_bid(bid.bid_id)
