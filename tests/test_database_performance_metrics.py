from __future__ import annotations

import asyncio

import pytest

from db.performance import (
    database_performance_snapshot,
    reset_database_performance_metrics,
    track_database_query,
)


class _Pool:
    def __init__(self, size: int, idle: int) -> None:
        self._size = size
        self._idle = idle

    def get_size(self) -> int:
        return self._size

    def get_idle_size(self) -> int:
        return self._idle


@pytest.mark.asyncio
async def test_database_metrics_record_round_trips_p95_and_saturation() -> None:
    reset_database_performance_metrics()
    pool = _Pool(size=10, idle=0)

    for _ in range(5):
        async with track_database_query(
            "admin.users.page",
            pool=pool,
            slow_query_seconds=0,
        ):
            await asyncio.sleep(0)

    snapshot = database_performance_snapshot()["admin.users.page"]

    assert snapshot["round_trips"] == 5
    assert snapshot["slow_queries"] == 5
    assert snapshot["failures"] == 0
    assert snapshot["p95_ms"] >= 0
    assert snapshot["max_pool_utilization"] == 1.0


@pytest.mark.asyncio
async def test_database_metrics_count_failed_operations_without_sql_payloads() -> None:
    reset_database_performance_metrics()

    with pytest.raises(RuntimeError, match="broken"):
        async with track_database_query("users.profile.sync"):
            raise RuntimeError("broken")

    snapshot = database_performance_snapshot()["users.profile.sync"]
    assert snapshot["round_trips"] == 1
    assert snapshot["failures"] == 1
