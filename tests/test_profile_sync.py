from __future__ import annotations

from typing import Any

import pytest

from db import core
from db.profile_sync import sync_user_profile


class FakeTransaction:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeTransaction":
        self.entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.exited = True
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


class RecordingConnection:
    def __init__(self, changed_user_id: int | None = 42) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.changed_user_id = changed_user_id
        self.transaction_state = FakeTransaction()

    def transaction(self) -> FakeTransaction:
        return self.transaction_state

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "UPDATE 1"

    async def fetchval(self, query: str, *args: Any) -> int | None:
        self.calls.append(("fetchval", query, args))
        return self.changed_user_id


@pytest.fixture(autouse=True)
def reset_pool() -> None:
    core.db_pool.clear()
    yield
    core.db_pool.clear()


@pytest.mark.asyncio
async def test_profile_sync_claims_reused_username_before_upsert() -> None:
    connection = RecordingConnection()
    core.db_pool.bind(FakePool(connection))

    changed = await sync_user_profile(42, " @Krsdtt ", " Test User ")

    assert changed is True
    assert connection.transaction_state.entered is True
    assert connection.transaction_state.exited is True
    assert len(connection.calls) == 3

    lock_call, release_call, upsert_call = connection.calls
    assert lock_call[0] == "execute"
    assert "pg_advisory_xact_lock" in lock_call[1]
    assert release_call[0] == "execute"
    assert "SET username = NULL" in release_call[1]
    assert release_call[2] == (42, "Krsdtt")
    assert upsert_call[0] == "fetchval"
    assert "ON CONFLICT (user_id) DO UPDATE" in upsert_call[1]
    assert upsert_call[2] == (42, "Krsdtt", "Test User")


@pytest.mark.asyncio
async def test_profile_sync_skips_username_release_when_username_is_empty() -> None:
    connection = RecordingConnection(changed_user_id=None)
    core.db_pool.bind(FakePool(connection))

    changed = await sync_user_profile(42, "", "Test User")

    assert changed is False
    assert len(connection.calls) == 2
    assert "pg_advisory_xact_lock" in connection.calls[0][1]
    assert connection.calls[1][0] == "fetchval"
    assert connection.calls[1][2] == (42, "", "Test User")
