from __future__ import annotations

import ast
import asyncio
import re
from collections import deque
from pathlib import Path
from typing import Any

from bot.repositories.card_economy import CardEconomyRepository
from bot.repositories.card_subscriptions import CardSubscriptionsRepository
from bot.services.card_subscriptions import CardSubscriptionsService


ROOT = Path(__file__).resolve().parents[1]
HANDLERS = (
    ROOT / "bot/handlers/card_subscribe.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy_shared.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy_mutation.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy_luxury.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy_subscriptions.py",
    ROOT / "bot/handlers/admin/helper/new/card_economy_winner_print.py",
)
SQL_STATEMENT = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b[\s\S]*?"
    r"\b(?:FROM|INTO|SET|WHERE|RETURNING)\b",
    re.IGNORECASE,
)


class FakeTransaction:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.events.append("transaction:enter")

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._connection.events.append("transaction:exit")


class FakeAcquire:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self) -> "FakeConnection":
        self._connection.events.append("acquire:enter")
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._connection.events.append("acquire:exit")


class FakeConnection:
    def __init__(
        self,
        *,
        fetchvals: list[Any] | None = None,
        fetchrows: list[Any] | None = None,
        fetch_results: list[list[Any]] | None = None,
    ) -> None:
        self.fetchvals = deque(fetchvals or [])
        self.fetchrows = deque(fetchrows or [])
        self.fetch_results = deque(fetch_results or [])
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.events: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def fetchval(self, query: str, *arguments: Any) -> Any:
        self.calls.append(("fetchval", query, arguments))
        return self.fetchvals.popleft() if self.fetchvals else None

    async def fetchrow(self, query: str, *arguments: Any) -> Any:
        self.calls.append(("fetchrow", query, arguments))
        return self.fetchrows.popleft() if self.fetchrows else None

    async def fetch(self, query: str, *arguments: Any) -> list[Any]:
        self.calls.append(("fetch", query, arguments))
        return self.fetch_results.popleft() if self.fetch_results else []

    async def execute(self, query: str, *arguments: Any) -> str:
        self.calls.append(("execute", query, arguments))
        return "OK"


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


def _imports(path: Path) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_card_handlers_contain_no_sql_or_pool_access() -> None:
    for path in HANDLERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        string_literals = (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        assert not any(SQL_STATEMENT.search(value) for value in string_literals), path

        imports = _imports(path)
        assert "db.core" not in imports
        assert "db.pool" not in imports
        assert "db.db" not in imports

        forbidden_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not ({"acquire", "transaction"} & forbidden_calls)


def test_card_layers_do_not_depend_on_handlers_or_legacy_database_facades() -> None:
    repositories = (
        ROOT / "bot/repositories/card_subscriptions.py",
        ROOT / "bot/repositories/card_economy.py",
    )
    services = (
        ROOT / "bot/services/card_subscriptions.py",
        ROOT / "bot/services/card_economy.py",
    )

    for path in repositories + services:
        imports = _imports(path)
        assert "db.core" not in imports
        assert "db.db" not in imports
        assert not any(module.startswith("bot.handlers") for module in imports)

    for path in services:
        assert "db.pool" in _imports(path)


def test_preset_toggle_is_atomic_and_keeps_service_result_contract() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetchvals=[7], fetchrows=[None])
        repository = CardSubscriptionsRepository(FakePool(connection))  # type: ignore[arg-type]
        service = CardSubscriptionsService(repository)

        result = await service.toggle_preset(42, "any_card")

        assert result == (True, "Подключено")
        assert connection.events == [
            "acquire:enter",
            "transaction:enter",
            "transaction:exit",
            "acquire:exit",
        ]
        assert [call[0] for call in connection.calls] == [
            "fetchval",
            "fetchrow",
            "execute",
        ]
        assert connection.calls[0][2] == ("any_card",)
        assert connection.calls[1][2] == (42, 7)
        assert connection.calls[2][2] == (42, 7)

    asyncio.run(scenario())


def test_preset_toggle_deletes_existing_subscription_without_reinserting() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetchvals=[3], fetchrows=[{"id": 11}])
        repository = CardSubscriptionsRepository(FakePool(connection))  # type: ignore[arg-type]
        service = CardSubscriptionsService(repository)

        assert await service.toggle_preset(8, "any_gold") == (False, "Отключено")
        assert [call[0] for call in connection.calls] == ["fetchval", "fetchrow"]

    asyncio.run(scenario())


def test_missing_preset_preserves_user_facing_message() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fetchvals=[None])
        service = CardSubscriptionsService(
            CardSubscriptionsRepository(FakePool(connection))  # type: ignore[arg-type]
        )

        assert await service.toggle_preset(1, "missing") == (
            False,
            "Пресет не найден",
        )
        assert [call[0] for call in connection.calls] == ["fetchval"]

    asyncio.run(scenario())


def test_luxury_top_preserves_query_parameters_and_shapes_rows() -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            fetchrows=[{"total": 4}],
            fetch_results=[[{"card_id": 9, "subs_count": 5}]],
        )
        repository = CardEconomyRepository(FakePool(connection))  # type: ignore[arg-type]

        rows, total = await repository.luxury_top(
            limit=20,
            offset=40,
            rarity="gold",
        )

        assert rows == [{"card_id": 9, "subs_count": 5}]
        assert total == 4
        assert connection.calls[0][0] == "fetchrow"
        assert connection.calls[0][2] == ("gold",)
        assert connection.calls[1][0] == "fetch"
        assert connection.calls[1][2] == (20, 40, "gold")

    asyncio.run(scenario())


def test_auction_print_repository_normalizes_usernames_and_fallback() -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            fetchrows=[{"username": "@winner", "amount": 125}],
            fetch_results=[[{"username": "@owner"}, {"username": ""}]],
        )
        repository = CardEconomyRepository(FakePool(connection))  # type: ignore[arg-type]

        assert await repository.auction_owner_usernames(17) == ["@owner"]
        assert await repository.fallback_winner(17) == ("@winner", 125)

    asyncio.run(scenario())
