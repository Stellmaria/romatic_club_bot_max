from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg
import pytest

from bot.core.errors import PersistenceError, PersistenceUnavailableError
from db import admin, auctions, core, reliable_mutations, users


class FakeTransaction:
    def __init__(self) -> None:
        self.entered = False
        self.exit_exception: BaseException | None = None

    async def __aenter__(self) -> "FakeTransaction":
        self.entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        self.exit_exception = exc
        return False


class AcquireContext:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def __aenter__(self) -> Any:
        return self.connection

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakePool:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def acquire(self) -> AcquireContext:
        return AcquireContext(self.connection)


class UnavailableConnection:
    async def fetch(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OSError("postgresql://user:secret@private-host/database")

    async def fetchrow(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OSError("postgresql://user:secret@private-host/database")

    async def fetchval(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OSError("postgresql://user:secret@private-host/database")

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OSError("postgresql://user:secret@private-host/database")

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_state = FakeTransaction()
        self.fail_owner_insert = False

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        if self.fail_owner_insert and "INSERT INTO auction_owners" in query:
            raise asyncpg.PostgresError("private SQL failure")
        return "UPDATE 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append((query, args))
        if "RETURNING auction_id" in query:
            return {"auction_id": 17}
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return None

    def transaction(self) -> FakeTransaction:
        return self.transaction_state


@pytest.fixture(autouse=True)
def reset_pool() -> None:
    core.db_pool.clear()
    yield
    core.db_pool.clear()


@pytest.mark.asyncio
async def test_user_read_does_not_mask_database_outage_as_missing_user() -> None:
    core.db_pool.bind(FakePool(UnavailableConnection()))

    with pytest.raises(PersistenceUnavailableError) as caught:
        await users.get_user(100)

    assert "secret" not in str(caught.value)
    assert caught.value.error_code == "database_unavailable"


@pytest.mark.asyncio
async def test_legacy_broad_catch_cannot_return_empty_collection_on_db_failure() -> None:
    core.db_pool.bind(FakePool(UnavailableConnection()))

    with pytest.raises(PersistenceUnavailableError):
        await auctions.get_lots_by_owner(100)


@pytest.mark.asyncio
async def test_admin_check_does_not_turn_database_failure_into_access_denied() -> None:
    core.db_pool.bind(FakePool(UnavailableConnection()))

    with pytest.raises(PersistenceUnavailableError):
        await admin.is_admin(100)


@pytest.mark.asyncio
async def test_trusted_status_sync_is_one_atomic_database_statement() -> None:
    connection = RecordingConnection()
    core.db_pool.bind(FakePool(connection))

    await users.sync_trusted_status(42, "@Alice")

    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    assert "SET is_trusted = EXISTS" in query
    assert "trusted_usernames" in query
    assert args == (42, "Alice")


@pytest.mark.asyncio
async def test_legacy_auction_creation_rolls_back_when_owner_link_fails() -> None:
    connection = RecordingConnection()
    connection.fail_owner_insert = True
    core.db_pool.bind(FakePool(connection))

    with pytest.raises(PersistenceError):
        await reliable_mutations.add_auction(
            "Card",
            "Hero",
            "file-id",
            42,
            100,
            "diamonds",
            datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc),
            "pending",
            "",
        )

    assert connection.transaction_state.entered is True
    assert isinstance(connection.transaction_state.exit_exception, PersistenceError)


def test_compatibility_facade_exports_atomic_auction_creation() -> None:
    from db import db as facade

    assert facade.add_auction is reliable_mutations.add_auction
