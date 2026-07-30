from __future__ import annotations

from datetime import date
from typing import Any

from aiogram.types import User

from bot.repositories.auction_winners import AuctionWinnerRepository, MailTarget
from db.core import get_db_pool


class AuctionWinnerService:
    """Application boundary for auction winner and print workflows."""

    def __init__(self, repository: AuctionWinnerRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionWinnerService":
        return cls(AuctionWinnerRepository(await get_db_pool()))

    async def auction(self, auction_id: int) -> dict[str, Any] | None:
        return await self._repository.auction(auction_id)

    async def ensure_print_win_schema(self) -> None:
        await self._repository.ensure_print_win_schema()

    async def ensure_admin_thanks_schema(self) -> None:
        await self._repository.ensure_admin_thanks_schema()

    async def auction_currency(self, auction_id: int) -> str | None:
        return await self._repository.auction_currency(auction_id)

    async def discussion_message_id(self, auction_id: int) -> int | None:
        return await self._repository.discussion_message_id(auction_id)

    async def owners(self, auction_id: int) -> list[dict[str, Any]]:
        return await self._repository.owners(auction_id)

    async def user(self, user_id: int) -> dict[str, Any] | None:
        return await self._repository.user(user_id)

    async def user_by_username(self, username: str) -> dict[str, Any] | None:
        return await self._repository.user_by_username(username)

    async def resolve_user_ref(self, raw: str) -> tuple[int | None, str | None]:
        value = (raw or "").strip()
        if not value:
            return None, None
        if value.isdigit():
            return int(value), None
        if not value.startswith("@"):  # manual winner forms require an explicit username marker
            return None, None
        username = value.lstrip("@").strip()
        if not username:
            return None, None
        user = await self.user_by_username(username)
        if user and user.get("user_id"):
            return int(user["user_id"]), username
        return None, username

    async def uid_verified(self, user_id: int | None) -> bool:
        return await self._repository.uid_verified(user_id)

    async def uid_verification_counts(self, user_ids: list[int] | None) -> tuple[int, int, bool]:
        return await self._repository.uid_verification_counts(user_ids)

    async def top_bid(self, auction_id: int, *, lowest_wins: bool = False) -> dict[str, Any] | None:
        return await self._repository.top_bid(auction_id, lowest_wins=lowest_wins)

    async def ranked_bids(self, auction_id: int, *, limit: int | None = None) -> list[dict[str, Any]]:
        return await self._repository.ranked_bids(auction_id, limit=limit)

    async def bid_message_id(self, auction_id: int, bidder_id: int, amount: int) -> int | None:
        return await self._repository.bid_message_id(auction_id, bidder_id, amount)

    async def autobid_action(self, discussion_message_id: int) -> dict[str, Any] | None:
        return await self._repository.autobid_action(discussion_message_id)

    async def deck_for_auction(self, auction_id: int) -> dict[str, Any] | None:
        return await self._repository.deck_for_auction(auction_id)

    async def manual_result(self, auction_id: int) -> dict[str, Any] | None:
        return await self._repository.manual_result(auction_id)

    async def upsert_manual_result(
        self,
        auction_id: int,
        *,
        winner_user_id: int | None,
        winner_username: str | None,
        owner_user_id: int | None,
        owner_username: str | None,
        amount: int | None,
        updated_by: int,
        moderator_comment: str | None = None,
    ) -> None:
        await self._repository.upsert_manual_result(
            auction_id,
            winner_user_id=winner_user_id,
            winner_username=winner_username,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            amount=amount,
            updated_by=updated_by,
            moderator_comment=moderator_comment,
        )

    async def clear_manual_result(self, auction_id: int) -> None:
        await self._repository.clear_manual_result(auction_id)

    async def mailing_counts(self, auction_id: int) -> tuple[int, int, int]:
        return await self._repository.mailing_counts(auction_id)

    async def add_mailing(self, auction_id: int, target: MailTarget, admin: User) -> None:
        await self._repository.add_mailing(
            auction_id,
            target,
            admin_user_id=int(admin.id),
            admin_username=getattr(admin, "username", None),
        )

    async def missed_mailings_for_day(self, target_date: date) -> list[dict[str, Any]]:
        return await self._repository.missed_mailings_for_day(target_date)

    async def exchange_batches_for_card(self, card_id: int, *, status: str = "approved") -> list[dict[str, Any]]:
        return await self._repository.exchange_batches_for_card(card_id, status=status)

    async def exchange_batch(self, batch_id: int) -> dict[str, Any] | None:
        return await self._repository.exchange_batch(batch_id)

    async def exchange_cards(self, batch_id: int) -> list[dict[str, Any]]:
        return await self._repository.exchange_cards(batch_id)

    async def exchange_print_stats(self, batch_id: int) -> dict[str, Any] | None:
        return await self._repository.exchange_print_stats(batch_id)

    async def upsert_exchange_print_stats(self, batch_id: int, **values: Any) -> None:
        await self._repository.upsert_exchange_print_stats(batch_id, **values)

    async def reset_exchange_print_stats(self, batch_id: int, *, updated_by: int | None = None) -> None:
        await self._repository.reset_exchange_print_stats(batch_id, updated_by=updated_by)

    async def increment_admin_thanks(self, author: str, user_id: int) -> tuple[int, int]:
        return await self._repository.increment_admin_thanks(author, user_id)

    async def admin_thanks_totals(self, author: str) -> tuple[int, int]:
        return await self._repository.admin_thanks_totals(author)

    @staticmethod
    def validate_amount(currency: str | None, amount: int) -> str | None:
        value = int(amount)
        if value < 0:
            return "Цена не может быть отрицательной."
        normalized = (currency or "").strip().lower()
        if normalized in {"алмазы", "diamond", "diamonds"} and value % 10:
            return "Для 💎 ставка/цена должна быть кратной 10."
        if normalized in {"чашки", "tea", "cups"} and value % 2:
            return "Для 🍵 ставка/цена должна быть чётной."
        return None
