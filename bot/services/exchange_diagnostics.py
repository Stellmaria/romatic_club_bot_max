from __future__ import annotations

from typing import Any, Sequence

from bot.repositories.exchange_diagnostics import ExchangeDiagnosticsRepository
from db.pool import get_db_pool


class ExchangeDiagnosticsService:
    """Application boundary for admin diagnostics and reconciliation flows."""

    def __init__(self, repository: ExchangeDiagnosticsRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ExchangeDiagnosticsService":
        return cls(ExchangeDiagnosticsRepository(await get_db_pool()))

    async def is_admin(self, user_id: int) -> bool:
        return await self._repository.is_admin(user_id)

    async def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return await self._repository.user_by_id(user_id)

    async def user_by_username(self, username: str) -> dict[str, Any] | None:
        return await self._repository.user_by_username(username)

    async def deck(self, deck_id: int) -> dict[str, Any] | None:
        return await self._repository.deck(deck_id)

    async def batch(self, batch_id: int) -> dict[str, Any] | None:
        return await self._repository.batch(batch_id)

    async def batch_items(self, batch_id: int) -> list[dict[str, Any]]:
        return await self._repository.batch_items(batch_id)

    async def mark_batches_dispatched(
        self,
        batch_ids: Sequence[int],
        *,
        winner_id: int,
        winner_username: str | None,
        admin_id: int,
    ) -> None:
        await self._repository.mark_batches_dispatched(
            batch_ids,
            winner_id=winner_id,
            winner_username=winner_username,
            admin_id=admin_id,
        )

    async def standard_lots_by_owner(self, owner_id: int) -> list[dict[str, Any]]:
        return await self._repository.standard_lots_by_owner(owner_id)

    async def user_card_stats(self, user_id: int) -> list[dict[str, Any]]:
        return await self._repository.user_card_stats(user_id)

    async def user_batch_stats(self, user_id: int) -> list[dict[str, Any]]:
        return await self._repository.user_batch_stats(user_id)

    async def recent_user_batches(self, user_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
        return await self._repository.recent_user_batches(user_id, limit=limit)

    async def dump_group_count(self) -> int:
        return await self._repository.dump_group_count()

    async def dump_groups(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        return await self._repository.dump_groups(limit=limit, offset=offset)

    async def duplicate_user_cards(
        self,
        *,
        user_id: int | None = None,
        card_id: int | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        return await self._repository.duplicate_user_cards(
            user_id=user_id,
            card_id=card_id,
            limit=limit,
        )

    async def winner_delivery_state(self, username: str) -> tuple[list[dict[str, Any]], bool]:
        user = await self._repository.user_by_username(username)
        user_id = int(user["user_id"]) if user else None
        unsent = await self._repository.unsent_for_winner(
            username=username,
            user_id=user_id,
        )
        exists = bool(unsent) or await self._repository.has_lots_for_winner(
            username=username,
            user_id=user_id,
        )
        return unsent, exists

    async def unsent_batches(self, *, deck_id: int | None = None) -> list[dict[str, Any]]:
        return await self._repository.unsent_batches(deck_id=deck_id)

    async def assignment_rows(self, usernames: Sequence[str]) -> list[dict[str, Any]]:
        normalized = [str(name).strip().lstrip("@").lower() for name in usernames]
        user_ids = await self._repository.users_by_usernames(normalized)
        ids = [user_ids.get(name, -1) for name in normalized]
        return await self._repository.assigned_items_for_winners(normalized, ids)


class ExchangeDiagnosticsQueries(ExchangeDiagnosticsService):
    """Compatibility facade for diagnostics handlers and their query contract."""

    async def unsent_winner_batches(
        self, *, winner_id: int | None, username: str | None
    ) -> list[dict[str, Any]]:
        return await self._repository.unsent_winner_batches(
            winner_id=winner_id,
            username=username,
        )

    async def winner_assignment_items(
        self, *, usernames: Sequence[str], user_ids: Sequence[int]
    ) -> list[dict[str, Any]]:
        return await self._repository.winner_assignment_items(
            usernames=list(usernames), user_ids=list(user_ids)
        )
