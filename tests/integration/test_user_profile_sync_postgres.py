"""PostgreSQL regressions for Telegram username ownership transfer."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from db.pool import install_pool_for_testing
from db.profile_sync import sync_user_profile

pytestmark = pytest.mark.integration


async def test_profile_sync_transfers_case_insensitive_username(
    postgres_pool: asyncpg.Pool,
) -> None:
    install_pool_for_testing(postgres_pool)
    try:
        assert (
            await sync_user_profile(910_001, "Krsdtt", "Old Owner")
        ) is True
        assert (
            await sync_user_profile(910_002, "krsdtt", "Current Owner")
        ) is True

        async with postgres_pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id, username
                FROM public.users
                WHERE user_id = ANY($1::bigint[])
                ORDER BY user_id
                """,
                [910_001, 910_002],
            )

        assert [dict(row) for row in rows] == [
            {"user_id": 910_001, "username": None},
            {"user_id": 910_002, "username": "krsdtt"},
        ]
    finally:
        install_pool_for_testing(None)


async def test_concurrent_username_claims_leave_exactly_one_owner(
    postgres_pool: asyncpg.Pool,
) -> None:
    install_pool_for_testing(postgres_pool)
    try:
        results = await asyncio.gather(
            sync_user_profile(920_001, "SharedName", "First"),
            sync_user_profile(920_002, "sharedname", "Second"),
        )
        assert results == [True, True]

        async with postgres_pool.acquire() as connection:
            owners = await connection.fetch(
                """
                SELECT user_id, username
                FROM public.users
                WHERE username IS NOT NULL
                  AND lower(username) = 'sharedname'
                """
            )
            stored = await connection.fetch(
                """
                SELECT user_id, username
                FROM public.users
                WHERE user_id = ANY($1::bigint[])
                ORDER BY user_id
                """,
                [920_001, 920_002],
            )

        assert len(owners) == 1
        assert len(stored) == 2
        assert sum(row["username"] is not None for row in stored) == 1
    finally:
        install_pool_for_testing(None)
