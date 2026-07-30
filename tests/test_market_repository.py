from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from bot.repositories.market import MarketRepository


class FakeTransaction:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.events.append("transaction:enter")

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.connection.events.append("transaction:exit")


class FakeAcquire:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> "FakeConnection":
        self.connection.events.append("acquire:enter")
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.connection.events.append("acquire:exit")


class FakeConnection:
    def __init__(
        self,
        *,
        fetchrows: list[Any] | None = None,
        fetch_results: list[list[Any]] | None = None,
    ) -> None:
        self.fetchrows = deque(fetchrows or [])
        self.fetch_results = deque(fetch_results or [])
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.events: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, query: str, *arguments: Any) -> str:
        self.calls.append(("execute", query, arguments))
        return "OK"

    async def fetchrow(self, query: str, *arguments: Any) -> Any:
        self.calls.append(("fetchrow", query, arguments))
        return self.fetchrows.popleft() if self.fetchrows else None

    async def fetch(self, query: str, *arguments: Any) -> list[Any]:
        self.calls.append(("fetch", query, arguments))
        return self.fetch_results.popleft() if self.fetch_results else []


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


def test_decrement_quantity_locks_and_updates_in_one_transaction() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetchrows=[{"quantity": 5}, {"quantity": 3}])
        repository = MarketRepository(FakePool(connection))  # type: ignore[arg-type]

        remaining = await repository.decrement_item_quantity(17, 2)

        assert remaining == 3
        assert connection.events == [
            "acquire:enter",
            "transaction:enter",
            "transaction:exit",
            "acquire:exit",
        ]
        assert [call[0] for call in connection.calls] == ["fetchrow", "fetchrow"]
        assert "FOR UPDATE" in connection.calls[0][1]
        assert connection.calls[1][2] == (17, 3)

    asyncio.run(scenario())


def test_replace_price_deletes_and_inserts_atomically() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        repository = MarketRepository(FakePool(connection))  # type: ignore[arg-type]

        await repository.replace_price(
            9,
            pay_type="cash",
            cash_code="BYN",
            price=12.5,
        )

        assert connection.events[1:3] == ["transaction:enter", "transaction:exit"]
        assert len(connection.calls) == 2
        assert "DELETE FROM public.market_rate_tiers" in connection.calls[0][1]
        assert connection.calls[0][2] == (9, "cash", "BYN")
        assert "INSERT INTO public.market_rate_tiers" in connection.calls[1][1]
        assert connection.calls[1][2] == (9, "cash", "BYN", 12.5)

    asyncio.run(scenario())


def test_search_keeps_user_input_in_bound_parameters() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetch_results=[[{"listing_id": 3}]])
        repository = MarketRepository(FakePool(connection))  # type: ignore[arg-type]

        rows = await repository.search(
            query="100%_card",
            rarity="legendary",
            limit=7,
            offset=2,
        )

        assert rows == [{"listing_id": 3}]
        method, statement, arguments = connection.calls[0]
        assert method == "fetch"
        assert "100%_card" not in statement
        assert "legendary" not in statement
        assert arguments == (
            "legendary",
            "%100%_card%",
            "%100%_card%",
            "%100%_card%",
            7,
            2,
        )
        assert "LIMIT $5 OFFSET $6" in statement

    asyncio.run(scenario())

