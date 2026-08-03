"""Real PostgreSQL tests for the complete migration lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from db.migrator import (
    MIGRATION_TABLE,
    _load_migrations,
    apply_migrations,
)


pytestmark = pytest.mark.integration


async def test_clean_install_is_complete_idempotent_and_checksummed(
    empty_pool: asyncpg.Pool,
) -> None:
    migrations = _load_migrations()

    first = await apply_migrations(empty_pool)
    second = await apply_migrations(empty_pool)

    assert first == [migration.filename for migration in migrations]
    assert second == []

    async with empty_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT filename, version, checksum, postgres_version
            FROM public.schema_migrations
            ORDER BY version
            """
        )
        table_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """
        )

        naive_timestamp_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND data_type = 'timestamp without time zone'
            """
        )

    assert [row["filename"] for row in rows] == first
    assert [row["version"] for row in rows] == [
        migration.version for migration in migrations
    ]
    assert [row["checksum"] for row in rows] == [
        migration.checksum for migration in migrations
    ]
    assert all(str(row["postgres_version"]).split(".", 1)[0] == "17" for row in rows)
    assert int(table_count) >= 20
    assert int(naive_timestamp_count) == 0


async def test_concurrent_migration_runners_are_serialized_by_advisory_lock(
    empty_pool: asyncpg.Pool,
) -> None:
    migrations = _load_migrations()

    results = await asyncio.gather(
        *(apply_migrations(empty_pool) for _ in range(3))
    )

    assert sum(bool(result) for result in results) == 1
    assert sorted(len(result) for result in results) == [0, 0, len(migrations)]

    async with empty_pool.acquire() as connection:
        journal_count = await connection.fetchval(
            "SELECT count(*) FROM public.schema_migrations"
        )
        duplicate_versions = await connection.fetchval(
            """
            SELECT count(*)
            FROM (
                SELECT version
                FROM public.schema_migrations
                GROUP BY version
                HAVING count(*) > 1
            ) duplicates
            """
        )

    assert int(journal_count) == len(migrations)
    assert int(duplicate_versions) == 0


async def test_failed_migration_rolls_back_schema_and_journal(
    empty_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    migration = tmp_path / "001_intentional_failure.sql"
    migration.write_text(
        """
        CREATE TABLE public.rollback_probe (
            id integer PRIMARY KEY
        );
        INSERT INTO public.rollback_probe(id) VALUES (1);
        DO $$
        BEGIN
            RAISE EXCEPTION 'intentional migration failure';
        END
        $$;
        """,
        encoding="utf-8",
    )

    with pytest.raises(asyncpg.RaiseError, match="intentional migration failure"):
        await apply_migrations(empty_pool, directory=tmp_path)

    async with empty_pool.acquire() as connection:
        probe = await connection.fetchval(
            "SELECT to_regclass('public.rollback_probe')"
        )
        journal_count = await connection.fetchval(
            f"SELECT count(*) FROM public.{MIGRATION_TABLE}"
        )

    assert probe is None
    assert int(journal_count) == 0


async def test_changed_applied_migration_checksum_is_rejected(
    empty_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    migration = tmp_path / "001_checksum_probe.sql"
    migration.write_text(
        "CREATE TABLE public.checksum_probe(id integer PRIMARY KEY);",
        encoding="utf-8",
    )
    assert await apply_migrations(empty_pool, directory=tmp_path) == [
        migration.name
    ]

    migration.write_text(
        "CREATE TABLE public.checksum_probe(id bigint PRIMARY KEY);",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Уже применённая миграция была изменена"):
        await apply_migrations(empty_pool, directory=tmp_path)


async def test_minimal_legacy_snapshot_is_upgraded_without_data_loss(
    empty_pool: asyncpg.Pool,
) -> None:
    async with empty_pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE public.schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamp without time zone DEFAULT now()
            );
            INSERT INTO public.schema_migrations(version) VALUES ('legacy');

            CREATE TABLE public.legacy_restore_probe (
                id integer PRIMARY KEY,
                payload text NOT NULL
            );
            INSERT INTO public.legacy_restore_probe(id, payload)
            VALUES (1, 'preserve-me');
            """
        )

    applied = await apply_migrations(empty_pool)

    async with empty_pool.acquire() as connection:
        payload = await connection.fetchval(
            "SELECT payload FROM public.legacy_restore_probe WHERE id = 1"
        )
        archived = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name LIKE 'schema_migrations_legacy_%'
            """
        )
        current_columns = {
            row["column_name"]
            for row in await connection.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'schema_migrations'
                """
            )
        }

    assert applied == [migration.filename for migration in _load_migrations()]
    assert payload == "preserve-me"
    assert int(archived) == 1
    assert {
        "filename",
        "version",
        "checksum",
        "applied_at",
        "execution_ms",
        "postgres_version",
    } <= current_columns


async def test_current_schema_enforces_unique_check_and_foreign_key_constraints(
    postgres_pool: asyncpg.Pool,
) -> None:
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.telegram_outbox(
                dedupe_key, topic, method, chat_id, payload
            )
            VALUES ('constraint:dedupe', 'test', 'send_message', 1, '{}'::jsonb)
            """
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO public.telegram_outbox(
                    dedupe_key, topic, method, chat_id, payload
                )
                VALUES ('constraint:dedupe', 'test', 'send_message', 2, '{}'::jsonb)
                """
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO public.auctions(
                    card_name, start_time, end_time, status
                )
                VALUES (
                    'invalid-status',
                    now(),
                    now() + interval '31 minutes',
                    'definitely_invalid'
                )
                """
            )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO public.auction_owners(auction_id, user_id)
                VALUES (987654321, 123)
                """
            )


async def test_timestamptz_round_trip_preserves_the_same_instant(
    postgres_pool: asyncpg.Pool,
) -> None:
    source = datetime(
        2026,
        8,
        3,
        16,
        45,
        tzinfo=timezone(timedelta(hours=3)),
    )

    async with postgres_pool.acquire() as connection:
        stored = await connection.fetchval(
            """
            INSERT INTO public.telegram_outbox(
                dedupe_key, topic, method, chat_id, payload, available_at
            )
            VALUES (
                'time-policy:round-trip',
                'test',
                'send_message',
                1,
                '{}'::jsonb,
                $1
            )
            RETURNING available_at
            """,
            source,
        )

    assert stored.tzinfo is not None
    assert stored == datetime(2026, 8, 3, 13, 45, tzinfo=timezone.utc)
