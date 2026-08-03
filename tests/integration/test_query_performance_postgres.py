"""Performance contracts for bounded pages, profile sync and query plans."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import pytest

from db.performance import (
    database_performance_snapshot,
    reset_database_performance_metrics,
)
from db.pool import install_pool_for_testing
from db.user_list_queries import (
    list_admins_page,
    list_trusted_users_page,
    list_users_page,
)
from db.users import sync_user_profile

pytestmark = pytest.mark.integration


def _decode_plan(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def _explain(
    connection: asyncpg.Connection,
    query: str,
    *args: Any,
) -> tuple[str, float]:
    raw = await connection.fetchval(
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}",
        *args,
    )
    plan = _decode_plan(raw)
    return json.dumps(plan), float(plan[0]["Execution Time"])


async def test_keyset_pages_are_bounded_single_round_trip_queries(
    postgres_pool: asyncpg.Pool,
) -> None:
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.users(
                user_id, username, full_name, is_trusted, is_luxury
            )
            SELECT
                value,
                'user_' || lpad(value::text, 8, '0'),
                'Synthetic User ' || value,
                value % 5 = 0,
                value % 17 = 0
            FROM generate_series(1, 50000) AS value
            ON CONFLICT (user_id) DO NOTHING
            """
        )
        await connection.execute(
            """
            INSERT INTO public.admins(user_id, username, added_by)
            SELECT
                value,
                'user_' || lpad(value::text, 8, '0'),
                1
            FROM generate_series(50, 50000, 50) AS value
            ON CONFLICT (user_id) DO NOTHING
            """
        )
        await connection.execute(
            """
            INSERT INTO public.trusted_usernames(username)
            SELECT 'manual_' || lpad(value::text, 6, '0')
            FROM generate_series(1, 1000) AS value
            ON CONFLICT (username) DO NOTHING
            """
        )
        await connection.execute("ANALYZE public.users")
        await connection.execute("ANALYZE public.admins")
        await connection.execute("ANALYZE public.trusted_usernames")

    install_pool_for_testing(postgres_pool)
    try:
        reset_database_performance_metrics()

        users_first = await list_users_page(limit=20)
        assert len(users_first.rows) == 20
        assert users_first.next_cursor is not None
        users_second = await list_users_page(
            limit=20,
            after_user_id=int(users_first.next_cursor.values[0]),
        )
        assert len(users_second.rows) == 20
        assert {row["user_id"] for row in users_first.rows}.isdisjoint(
            row["user_id"] for row in users_second.rows
        )

        admins = await list_admins_page([1, 50000], limit=20)
        trusted = await list_trusted_users_page(limit=20)

        assert len(admins.rows) <= 20
        assert len(trusted.rows) <= 20
        assert admins.rows[0]["is_owner"] is True

        metrics = database_performance_snapshot()
        assert metrics["admin.users.page"]["round_trips"] == 2
        assert metrics["admin.admins.page"]["round_trips"] == 1
        assert metrics["admin.trusted.page"]["round_trips"] == 1
        assert metrics["admin.users.page"]["p95_ms"] >= 0
    finally:
        install_pool_for_testing(None)


async def test_representative_plans_use_audited_indexes_with_budgets(
    postgres_pool: asyncpg.Pool,
) -> None:
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.users(user_id, username, full_name, is_trusted)
            SELECT
                value,
                'plan_' || lpad(value::text, 8, '0'),
                'Plan User ' || value,
                value % 4 = 0
            FROM generate_series(1, 50000) AS value
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                is_trusted = EXCLUDED.is_trusted
            """
        )
        await connection.execute("ANALYZE public.users")

        index_names = {
            str(row["indexname"])
            for row in await connection.fetch(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
        }
        expected = {
            "ix_users_username_ci",
            "ix_users_trusted_username_ci",
            "ix_trusted_usernames_username_ci",
            "ix_auction_owners_user_auction",
            "ix_bids_auction_amount_bid",
            "ix_exchange_batches_status_created",
            "ix_exchange_items_batch_item",
            "idx_auctions_start_time_date",
            "ix_auctions_publication_due",
            "ix_telegram_outbox_pending",
        }
        assert expected <= index_names

        users_plan, users_ms = await _explain(
            connection,
            """
            SELECT user_id, username, is_luxury
            FROM public.users
            WHERE user_id > $1
            ORDER BY user_id
            LIMIT 21
            """,
            25000,
        )
        username_plan, username_ms = await _explain(
            connection,
            """
            SELECT user_id
            FROM public.users
            WHERE username IS NOT NULL
              AND username <> ''
              AND lower(username) = $1
            LIMIT 1
            """,
            "plan_00040000",
        )
        trusted_plan, trusted_ms = await _explain(
            connection,
            """
            SELECT user_id, username
            FROM public.users
            WHERE is_trusted = TRUE
              AND username IS NOT NULL
              AND ltrim(username, '@') <> ''
              AND (lower(ltrim(username, '@')), user_id) > ($1, $2)
            ORDER BY lower(ltrim(username, '@')), user_id
            LIMIT 21
            """,
            "plan_00025000",
            0,
        )

    assert "users_pkey" in users_plan
    assert "ix_users_username_ci" in username_plan
    assert "ix_users_trusted_username_ci" in trusted_plan
    assert max(users_ms, username_ms, trusted_ms) < 250.0


async def test_profile_upsert_reports_no_change_without_physical_update(
    postgres_pool: asyncpg.Pool,
) -> None:
    install_pool_for_testing(postgres_pool)
    try:
        reset_database_performance_metrics()

        assert await sync_user_profile(900_001, "profile_user", "Profile User") is True
        assert await sync_user_profile(900_001, "profile_user", "Profile User") is False
        assert await sync_user_profile(900_001, "profile_user_2", "Profile User") is True

        metrics = database_performance_snapshot()["users.profile.sync"]
        assert metrics["round_trips"] == 3
        assert metrics["p95_ms"] >= 0
    finally:
        install_pool_for_testing(None)
