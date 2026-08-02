from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

from bot.services.exchange_catalog import ExchangeCatalogQueries
from bot.services.exchange_diagnostics import ExchangeDiagnosticsQueries
from bot.services.exchange_moderation import ExchangeModerationQueries
from bot.services.exchange_submission import ExchangeSubmissionQueries


ROOT = Path(__file__).resolve().parents[1]
HANDLERS = (
    "bot/handlers/auction/exchange/__init__.py",
    "bot/handlers/auction/exchange/submission.py",
    "bot/handlers/auction/exchange/moderation.py",
    "bot/handlers/auction/exchange/catalog.py",
    "bot/handlers/auction/exchange/diagnostics/__init__.py",
    "bot/handlers/auction/exchange/diagnostics/media.py",
    "bot/handlers/auction/exchange/diagnostics/delivery.py",
    "bot/handlers/auction/exchange/diagnostics/reports.py",
    "bot/handlers/auction/exchange/diagnostics/reconciliation.py",
)
REPOSITORIES = (
    "bot/repositories/exchange_submission.py",
    "bot/repositories/exchange_moderation.py",
    "bot/repositories/exchange_catalog.py",
    "bot/repositories/exchange_diagnostics.py",
)
SERVICES = (
    "bot/services/exchange_submission.py",
    "bot/services/exchange_moderation.py",
    "bot/services/exchange_catalog.py",
    "bot/services/exchange_diagnostics.py",
)
SQL_STATEMENT = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)\b",
    re.IGNORECASE,
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _imports(relative: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative), filename=relative)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _receiver_tokens(node: ast.expr) -> set[str]:
    tokens: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            tokens.add(child.id.lower())
        elif isinstance(child, ast.Attribute):
            tokens.add(child.attr.lower().lstrip("_"))
    return tokens


def test_exchange_handlers_contain_no_sql_or_pool_access() -> None:
    for relative in HANDLERS:
        tree = ast.parse(_source(relative), filename=relative)
        imports = _imports(relative)
        assert "db.core" not in imports, relative
        assert "db.pool" not in imports, relative
        assert "asyncpg" not in imports, relative

        sql_literals = [
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and SQL_STATEMENT.search(node.value)
        ]
        assert sql_literals == [], relative

        forbidden_calls: set[str] = set()
        sql_receivers = {"pool", "conn", "connection", "cursor", "db", "database", "repo", "repository"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method in {"acquire", "transaction", "fetch", "fetchrow"}:
                forbidden_calls.add(method)
            elif method == "execute" and _receiver_tokens(node.func.value) & sql_receivers:
                forbidden_calls.add(method)
        assert forbidden_calls == set(), relative


def test_exchange_sql_is_owned_by_repositories_and_pool_is_wired_by_services() -> None:
    for relative in REPOSITORIES:
        source = _source(relative)
        assert SQL_STATEMENT.search(source), relative
        assert "self._pool.acquire()" in source, relative
        assert "db.core" not in _imports(relative), relative
        assert not any(
            dependency.startswith("bot.handlers")
            for dependency in _imports(relative)
        ), relative

    for relative in SERVICES:
        imports = _imports(relative)
        assert "db.pool" in imports, relative
        assert "db.core" not in imports, relative
        assert not any(
            dependency.startswith("bot.handlers") for dependency in imports
        ), relative


class FakeSubmissionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def deck_type_for_card(self, card_id: int) -> str:
        self.calls.append(("card", card_id))
        return "resource"

    async def deck_type_for_deck(self, deck_id: int) -> str:
        self.calls.append(("deck", deck_id))
        return "roulette"

    async def deck_type_for_card_identity(self, card_name: str, hero_name: str) -> str:
        self.calls.append(("identity", card_name, hero_name))
        return "resource"

    async def latest_resource_deck_ids(self, limit: int) -> list[int]:
        self.calls.append(("latest", limit))
        return [22, 24, 26]

    async def deck_name(self, deck_id: int) -> str:
        self.calls.append(("name", deck_id))
        return "Deck"


def test_submission_query_service_preserves_lookup_order_and_arguments() -> None:
    async def scenario() -> None:
        repository = FakeSubmissionRepository()
        service = ExchangeSubmissionQueries(repository)  # type: ignore[arg-type]

        assert await service.deck_type_for_card("7") == "resource"  # type: ignore[arg-type]
        assert await service.deck_type_for_deck("8") == "roulette"  # type: ignore[arg-type]
        assert await service.deck_type_for_card_identity("Card", "Hero") == "resource"
        assert await service.latest_resource_deck_ids("3") == [22, 24, 26]  # type: ignore[arg-type]
        assert await service.deck_name("9") == "Deck"  # type: ignore[arg-type]
        assert repository.calls == [
            ("card", 7),
            ("deck", 8),
            ("identity", "Card", "Hero"),
            ("latest", 3),
            ("name", 9),
        ]

    asyncio.run(scenario())


class FakeModerationRepository:
    def __init__(self) -> None:
        self.limit: int | None = None

    async def pending(self, *, limit: int):
        self.limit = limit
        return [{"batch_id": 1}]

    async def user_flags(self, user_id: int):
        return {"user_id": user_id, "is_luxury": True}


def test_moderation_query_service_keeps_historical_limit_guard() -> None:
    async def scenario() -> None:
        repository = FakeModerationRepository()
        service = ExchangeModerationQueries(repository)  # type: ignore[arg-type]

        assert await service.pending(limit=999) == [{"batch_id": 1}]
        assert repository.limit == 200
        assert await service.pending(limit=-1) == [{"batch_id": 1}]
        assert repository.limit == 1
        assert await service.user_flags("42") == {  # type: ignore[arg-type]
            "user_id": 42,
            "is_luxury": True,
        }

    asyncio.run(scenario())


class FakeCatalogRepository:
    def __init__(self) -> None:
        self.batch_call = None
        self.deck_call = None

    async def approved_batch_ids_by_card(self, **kwargs):
        self.batch_call = kwargs
        return [19, 18]

    async def decks_with_approved(self, **kwargs):
        self.deck_call = kwargs
        return [22, 24]


def test_catalog_query_service_preserves_status_modes_and_order() -> None:
    async def scenario() -> None:
        repository = FakeCatalogRepository()
        service = ExchangeCatalogQueries(repository)  # type: ignore[arg-type]

        result = await service.approved_batch_ids_by_card(
            status="approved",
            deck_id="22",  # type: ignore[arg-type]
            card_id="4",  # type: ignore[arg-type]
            modes=("card", "deck_split"),
        )
        assert result == [19, 18]
        assert repository.batch_call == {
            "status": "approved",
            "deck_id": 22,
            "card_id": 4,
            "modes": ("card", "deck_split"),
        }

        assert await service.decks_with_approved(
            status="approved",
            deck_ids=[24, 22],
        ) == [22, 24]
        assert repository.deck_call == {
            "status": "approved",
            "deck_ids": [24, 22],
        }

    asyncio.run(scenario())


class FakeDiagnosticsRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def unsent_winner_batches(self, **kwargs):
        self.calls.append(("unsent", kwargs))
        return [{"batch_id": 5}]

    async def winner_assignment_items(self, **kwargs):
        self.calls.append(("assignment", kwargs))
        return [{"uname": "winner", "qty": 2}]


def test_diagnostics_query_service_keeps_optional_ids_and_list_alignment() -> None:
    async def scenario() -> None:
        repository = FakeDiagnosticsRepository()
        service = ExchangeDiagnosticsQueries(repository)  # type: ignore[arg-type]

        assert await service.unsent_winner_batches(
            winner_id=None,
            username="winner",
        ) == [{"batch_id": 5}]
        assert await service.winner_assignment_items(
            usernames=["a", "b"],
            user_ids=[1, -1],
        ) == [{"uname": "winner", "qty": 2}]
        assert repository.calls == [
            ("unsent", {"winner_id": None, "username": "winner"}),
            (
                "assignment",
                {"usernames": ["a", "b"], "user_ids": [1, -1]},
            ),
        ]

    asyncio.run(scenario())
