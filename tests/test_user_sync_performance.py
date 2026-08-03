from __future__ import annotations

import asyncio
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import pytest

from bot.middlewares.user_sync import UserSyncMiddleware


def _user(*, username: str = "alice", first_name: str = "Alice") -> Any:
    return SimpleNamespace(
        id=101,
        username=username,
        first_name=first_name,
        last_name="Example",
        is_bot=False,
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * 0.95)]


@pytest.mark.asyncio
async def test_repeated_unchanged_updates_create_one_database_round_trip() -> None:
    calls: list[tuple[int, str, str]] = []

    async def sync(user_id: int, username: str, full_name: str) -> bool:
        calls.append((user_id, username, full_name))
        return True

    middleware = UserSyncMiddleware(
        profile_sync=sync,
        debounce_seconds=0,
        timeout_seconds=1,
    )

    async def handler(event: Any, data: dict[str, Any]) -> str:
        return "handled"

    user = _user()
    event = SimpleNamespace(from_user=user)
    for _ in range(500):
        assert await middleware(handler, event, {"event_from_user": user}) == "handled"

    await middleware.drain()

    assert calls == [(101, "alice", "Alice Example")]

    changed = _user(username="alice_new")
    await middleware(handler, SimpleNamespace(from_user=changed), {"event_from_user": changed})
    await middleware.drain()
    assert calls[-1] == (101, "alice_new", "Alice Example")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_profile_write_is_outside_handler_latency_and_improves_p95() -> None:
    database_delay = 0.004
    calls = 0

    async def sync(user_id: int, username: str, full_name: str) -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(database_delay)
        return True

    async def handler(event: Any, data: dict[str, Any]) -> None:
        return None

    legacy_durations: list[float] = []
    for _ in range(30):
        started = perf_counter()
        await asyncio.sleep(database_delay)
        await handler(None, {})
        legacy_durations.append(perf_counter() - started)

    middleware = UserSyncMiddleware(
        profile_sync=sync,
        debounce_seconds=0,
        timeout_seconds=1,
    )
    user = _user()
    event = SimpleNamespace(from_user=user)
    optimized_durations: list[float] = []
    for _ in range(30):
        started = perf_counter()
        await middleware(handler, event, {"event_from_user": user})
        optimized_durations.append(perf_counter() - started)

    await middleware.drain()

    assert calls == 1
    assert _p95(optimized_durations) < _p95(legacy_durations) * 0.25


@pytest.mark.asyncio
async def test_failed_profile_sync_is_retried_on_the_next_update() -> None:
    attempts = 0

    async def flaky_sync(user_id: int, username: str, full_name: str) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        return True

    middleware = UserSyncMiddleware(
        profile_sync=flaky_sync,
        debounce_seconds=0,
        timeout_seconds=1,
    )
    user = _user()

    middleware.schedule(user)
    await middleware.drain()
    middleware.schedule(user)
    await middleware.drain()

    assert attempts == 2
