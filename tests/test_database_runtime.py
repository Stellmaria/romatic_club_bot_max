from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from bot.core.settings import DatabaseSettings
from db import core
from db.lifecycle import close_db, init_db
from db.pool import DatabaseRuntime


class FakePool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class RecordingPoolFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.pools: list[FakePool] = []

    async def __call__(
        self,
        url: str,
        *,
        min_size: int,
        max_size: int,
    ) -> FakePool:
        self.calls.append((url, min_size, max_size))
        await asyncio.sleep(0)
        pool = FakePool(f"pool-{len(self.calls)}")
        self.pools.append(pool)
        return pool


@pytest.fixture(autouse=True)
def reset_runtime_adapter() -> None:
    core.db_pool.clear()
    yield
    core.db_pool.clear()


@pytest.mark.asyncio
async def test_runtime_creates_one_pool_under_concurrent_start() -> None:
    factory = RecordingPoolFactory()
    settings = DatabaseSettings(
        "postgresql://one",
        auto_migrate=False,
        pool_min_size=2,
        pool_max_size=7,
    )
    runtime = DatabaseRuntime(settings, pool_factory=factory)

    first, second = await asyncio.gather(runtime.start(), runtime.start())

    assert first is second
    assert factory.calls == [("postgresql://one", 2, 7)]
    await runtime.close()
    assert first.closed is True
    assert runtime.pool is None


@pytest.mark.asyncio
async def test_multiple_runtimes_are_independent_in_one_process() -> None:
    first_factory = RecordingPoolFactory()
    second_factory = RecordingPoolFactory()
    first = DatabaseRuntime(
        DatabaseSettings("postgresql://first", auto_migrate=False),
        pool_factory=first_factory,
    )
    second = DatabaseRuntime(
        DatabaseSettings("postgresql://second", auto_migrate=False),
        pool_factory=second_factory,
    )

    first_pool, second_pool = await asyncio.gather(first.start(), second.start())
    await first.close()

    assert first_pool is not second_pool
    assert first_pool.closed is True
    assert second_pool.closed is False
    assert second.pool is second_pool
    await second.close()


@pytest.mark.asyncio
async def test_runtime_can_restart_without_stale_pool_reference() -> None:
    factory = RecordingPoolFactory()
    runtime = DatabaseRuntime(
        DatabaseSettings("postgresql://restart", auto_migrate=False),
        pool_factory=factory,
    )

    first = await runtime.start()
    await runtime.close()
    second = await runtime.start()

    assert first is not second
    assert first.closed is True
    assert second.closed is False
    assert runtime.pool is second
    assert len(factory.calls) == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_lifecycle_installs_and_uninstalls_the_same_runtime() -> None:
    factory = RecordingPoolFactory()
    runtime = DatabaseRuntime(
        DatabaseSettings("postgresql://lifecycle", auto_migrate=False),
        pool_factory=factory,
    )

    returned = await init_db(runtime)
    assert returned is runtime
    assert core.current_database_runtime() is runtime
    assert core.db_pool.pool is runtime.pool

    await close_db(runtime)
    assert core.current_database_runtime() is None
    assert runtime.pool is None


@pytest.mark.asyncio
async def test_active_runtime_cannot_be_replaced_before_shutdown() -> None:
    first = DatabaseRuntime.for_testing(FakePool("first"))
    second = DatabaseRuntime.for_testing(FakePool("second"))
    core.install_database_runtime(first)

    with pytest.raises(RuntimeError, match="already active"):
        core.install_database_runtime(second)

    await close_db(first)
    core.install_database_runtime(second)
    await close_db(second)


def test_legacy_global_pool_implementation_is_removed() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "db/legacy_impl.py").exists()

    core_source = (root / "db/core.py").read_text(encoding="utf-8")
    lifecycle_source = (root / "db/lifecycle.py").read_text(encoding="utf-8")
    assert "class PoolProxy" not in core_source
    assert "legacy_impl.db_pool" not in core_source
    assert "legacy_impl.db_pool" not in lifecycle_source


@pytest.mark.asyncio
async def test_real_postgres_runtime_survives_start_stop_start() -> None:
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    runtime = DatabaseRuntime(
        DatabaseSettings(
            database_url,
            auto_migrate=False,
            pool_min_size=1,
            pool_max_size=2,
        )
    )
    try:
        first_pool = await runtime.start()
        async with first_pool.acquire() as connection:
            assert await connection.fetchval("SELECT 1") == 1
        await runtime.close()

        second_pool = await runtime.start()
        assert second_pool is not first_pool
        async with second_pool.acquire() as connection:
            assert await connection.fetchval("SELECT 2") == 2
    finally:
        await runtime.close()
