from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from bot.repositories.admin_schedule import AdminScheduleRepository
from bot.repositories.exchange_moderation import ExchangeModerationRepository
from bot.repositories.users import UserRepository
from bot.services.admin_auctions import AdminAuctionContextService
from bot.services.admin_diagnostics import AdminDiagnosticsQueries
from bot.services.auction_workflows import AuctionLifecycleService


class FakeAcquire:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> "FakeConnection":
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakePool:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


class FakeConnection:
    def __init__(
        self,
        *,
        fetchrows: list[Any] | None = None,
        fetchvals: list[Any] | None = None,
    ) -> None:
        self.fetchrows = deque(fetchrows or [])
        self.fetchvals = deque(fetchvals or [])
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *arguments: Any) -> str:
        self.calls.append(("execute", query, arguments))
        return "UPDATE 1"

    async def fetchrow(self, query: str, *arguments: Any) -> Any:
        self.calls.append(("fetchrow", query, arguments))
        result = self.fetchrows.popleft() if self.fetchrows else None
        if isinstance(result, BaseException):
            raise result
        return result

    async def fetchval(self, query: str, *arguments: Any) -> Any:
        self.calls.append(("fetchval", query, arguments))
        return self.fetchvals.popleft() if self.fetchvals else None


def test_user_repository_preserves_private_chat_timestamps() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        repository = UserRepository(FakePool(connection))  # type: ignore[arg-type]

        await repository.mark_private_chat_opened(42)
        await repository.mark_private_chat_closed(42)

        assert [call[0] for call in connection.calls] == ["execute", "execute"]
        assert "first_pm_at = COALESCE(first_pm_at, NOW())" in connection.calls[0][1]
        assert "pm_opened = TRUE" in connection.calls[0][1]
        assert "pm_opened = FALSE" in connection.calls[1][1]
        assert connection.calls[0][2] == (42,)
        assert connection.calls[1][2] == (42,)

    asyncio.run(scenario())


def test_exchange_media_read_model_keeps_legacy_column_fallback() -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            fetchrows=[RuntimeError("media_type is absent"), {"image_id": "file-1"}]
        )
        repository = ExchangeModerationRepository(
            FakePool(connection)  # type: ignore[arg-type]
        )

        media = await repository.first_card_media(17)

        assert media == ("file-1", "photo")
        assert len(connection.calls) == 2
        assert "media_type" in connection.calls[0][1]
        assert "media_type" not in connection.calls[1][1]
        assert connection.calls[0][2] == (17,)
        assert connection.calls[1][2] == (17,)

    asyncio.run(scenario())


def test_schedule_repository_returns_scalar_maximum() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetchvals=[29])
        repository = AdminScheduleRepository(
            FakePool(connection)  # type: ignore[arg-type]
        )

        assert await repository.last_nonempty_deck_id() == 29
        assert connection.calls[0][0] == "fetchval"
        assert "MAX(deck_id)" in connection.calls[0][1]

    asyncio.run(scenario())


class FakeAuctionContextRepository:
    async def full_context_row(self, auction_id: int) -> dict[str, Any]:
        assert auction_id == 7
        return {
            "auction_id": 7,
            "hero_name": "Hero",
            "card_name": "Card",
            "start_price": 100,
            "currency": "diamonds",
            "end_time": None,
            "comment": "note",
            "status": "scheduled",
            "owners_count": 2,
            "auction_kind": "standard",
            "craft_uid_possible": True,
            "sellers_total": 2,
            "sellers_verified": 2,
            "card_id": 3,
            "c_hero": "Hero",
            "c_name": "Card",
            "num": 1,
            "rarity": "gold",
            "obtain_type": "cups",
            "obtain_amount": 5,
            "story": "Story",
            "quote": "Quote",
            "card_image_id": "image",
            "deck_id": 4,
            "deck_name": "Deck",
        }


def test_admin_auction_context_keeps_public_shape_and_verification_flag() -> None:
    async def scenario() -> None:
        service = AdminAuctionContextService(
            FakeAuctionContextRepository()  # type: ignore[arg-type]
        )

        context = await service.load_full_context(7)

        assert set(context) == {"auction", "card", "deck"}
        assert context["auction"]["seller_verified"] is True
        assert context["auction"]["sellers_total"] == 2
        assert context["card"]["image_id"] == "image"
        assert context["deck"] == {"deck_id": 4, "name": "Deck"}

    asyncio.run(scenario())


class FakeDiagnosticsRepository:
    def __init__(self) -> None:
        self.after: datetime | None = None

    async def database_metadata(self) -> dict[str, Any]:
        return {"db": "test"}

    async def delayed_luxury_count(self) -> int:
        return 3

    async def owners_with_multiple_future_lots(
        self,
        *,
        after: datetime,
    ) -> list[dict[str, Any]]:
        self.after = after
        return [{"user_id": 1, "cnt": 2}]


def test_admin_diagnostics_service_preserves_query_order_and_cutoff() -> None:
    async def scenario() -> None:
        repository = FakeDiagnosticsRepository()
        queries = AdminDiagnosticsQueries(repository)  # type: ignore[arg-type]
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)

        metadata, count = await queries.database_overview()
        rows = await queries.owners_with_multiple_future_lots(after=cutoff)

        assert metadata == {"db": "test"}
        assert count == 3
        assert rows == [{"user_id": 1, "cnt": 2}]
        assert repository.after is cutoff

    asyncio.run(scenario())


class FakeLifecycleRepository:
    def __init__(self) -> None:
        self.message_id: int | None = None

    async def auction_id_by_bid_message(self, message_id: int) -> int | None:
        self.message_id = message_id
        return 91


def test_lifecycle_service_resolves_bid_message_through_repository() -> None:
    async def scenario() -> None:
        repository = FakeLifecycleRepository()
        service = AuctionLifecycleService(repository)  # type: ignore[arg-type]

        assert await service.auction_id_by_bid_message(55) == 91
        assert repository.message_id == 55

    asyncio.run(scenario())
