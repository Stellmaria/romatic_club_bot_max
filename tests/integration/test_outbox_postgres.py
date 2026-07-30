"""Opt-in PostgreSQL integration tests for the Phase 6 outbox.

These tests intentionally alter ``public`` and therefore run only against a
clearly disposable database.  Example:

    TEST_DATABASE_URL=postgresql://.../auction_test \
    OUTBOX_INTEGRATION_CONFIRM=1 \
    python -m unittest tests.integration.test_outbox_postgres
"""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

try:
    import asyncpg
except ImportError:  # pragma: no cover - deployment dependency is optional locally
    asyncpg = None

if asyncpg:
    from bot.repositories.outbox import TelegramOutboxRepository
else:  # pragma: no cover
    TelegramOutboxRepository = None

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
CONFIRMED = os.getenv("OUTBOX_INTEGRATION_CONFIRM") == "1"
DATABASE_NAME = urlparse(DATABASE_URL).path.rsplit("/", 1)[-1].lower()
SAFE_DATABASE = "test" in DATABASE_NAME


@unittest.skipUnless(asyncpg and DATABASE_URL and CONFIRMED and SAFE_DATABASE, "disposable PostgreSQL test DB required")
class OutboxPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    pool: "asyncpg.Pool"

    async def asyncSetUp(self) -> None:
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=6)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                DROP TABLE IF EXISTS public.telegram_outbox;
                DROP TABLE IF EXISTS public.card_day_notifications;
                DROP TABLE IF EXISTS public.auctions;
                CREATE TABLE public.auctions (
                    auction_id integer PRIMARY KEY,
                    start_time timestamp without time zone,
                    end_time timestamp without time zone,
                    created_at timestamp without time zone DEFAULT now(),
                    notified_start boolean NOT NULL DEFAULT false,
                    notified_1min boolean NOT NULL DEFAULT false,
                    notified_end boolean NOT NULL DEFAULT false
                );
                CREATE TABLE public.card_day_notifications (
                    id bigserial PRIMARY KEY,
                    user_id bigint NOT NULL,
                    card_id bigint NOT NULL,
                    day date NOT NULL,
                    sent_at timestamp without time zone NOT NULL DEFAULT now(),
                    UNIQUE (user_id, card_id, day)
                );
                CREATE OR REPLACE FUNCTION public.auctions_fix_end_time()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.end_time IS NULL OR NEW.end_time <= NEW.start_time THEN
                        NEW.end_time := NEW.start_time + interval '31 minutes';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER trg_auctions_fix_end_time
                    BEFORE INSERT OR UPDATE OF start_time ON public.auctions
                    FOR EACH ROW
                    EXECUTE FUNCTION public.auctions_fix_end_time();
                """
            )
            await connection.execute((ROOT / "migrations/005_transactional_outbox_and_utc.sql").read_text())
            await connection.execute((ROOT / "migrations/006_outbox_delivery_control.sql").read_text())
            await connection.execute(
                "INSERT INTO public.auctions (auction_id) VALUES (1), (2)"
            )
        self.repository = TelegramOutboxRepository(self.pool)

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def test_timezone_migration_preserves_start_time_trigger(self) -> None:
        async with self.pool.acquire() as connection:
            data_type = await connection.fetchval(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'auctions'
                  AND column_name = 'start_time'
                """
            )
            trigger_exists = await connection.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_trigger
                    WHERE tgrelid = 'public.auctions'::regclass
                      AND tgname = 'trg_auctions_fix_end_time'
                      AND NOT tgisinternal
                )
                """
            )
            interval = await connection.fetchval(
                """
                INSERT INTO public.auctions (auction_id, start_time)
                VALUES (3, '2026-07-14 12:00:00+00'::timestamptz)
                RETURNING end_time - start_time
                """
            )

        self.assertEqual(data_type, "timestamp with time zone")
        self.assertTrue(trigger_exists)
        self.assertEqual(interval.total_seconds(), 31 * 60)

    async def test_concurrent_auction_enqueue_has_single_winner(self) -> None:
        results = await asyncio.gather(
            *[
                self.repository.enqueue_auction_notification(
                    auction_id=1,
                    event="start",
                    messages={101: "start", 102: "start"},
                )
                for _ in range(8)
            ]
        )
        self.assertEqual(sum(int(claimed) for claimed, _ in results), 1)
        async with self.pool.acquire() as connection:
            count = await connection.fetchval(
                "SELECT COUNT(*) FROM public.telegram_outbox WHERE topic='auction'"
            )
        self.assertEqual(count, 2)

    async def test_card_day_marker_and_message_are_atomic_under_race(self) -> None:
        results = await asyncio.gather(
            *[
                self.repository.enqueue_card_day_notification(
                    user_id=501,
                    card_id=77,
                    day=date(2026, 7, 14),
                    text="card today",
                )
                for _ in range(8)
            ]
        )
        self.assertEqual(sum(int(claimed) for claimed, _ in results), 1)
        async with self.pool.acquire() as connection:
            marker_count = await connection.fetchval(
                "SELECT COUNT(*) FROM public.card_day_notifications"
            )
            message_count = await connection.fetchval(
                "SELECT COUNT(*) FROM public.telegram_outbox WHERE topic='card-day'"
            )
        self.assertEqual((marker_count, message_count), (1, 1))

    async def test_skip_locked_claims_do_not_overlap(self) -> None:
        await self.repository.enqueue_messages(
            topic="race",
            dedupe_scope="batch",
            messages={user_id: "hello" for user_id in range(1, 11)},
        )
        batches = await asyncio.gather(
            *[self.repository.claim_batch(limit=3) for _ in range(4)]
        )
        ids = [int(row["outbox_id"]) for batch in batches for row in batch]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 10)

    async def test_copy_message_broadcast_is_deduplicated(self) -> None:
        first = await self.repository.enqueue_copy_message_broadcast(
            topic="admin-broadcast",
            dedupe_scope="100:200",
            recipients=[11, 12, 12],
            from_chat_id=100,
            message_id=200,
        )
        second = await self.repository.enqueue_copy_message_broadcast(
            topic="admin-broadcast",
            dedupe_scope="100:200",
            recipients=[11, 12],
            from_chat_id=100,
            message_id=200,
        )
        self.assertEqual((first, second), (2, 0))
        async with self.pool.acquire() as connection:
            methods = await connection.fetch(
                "SELECT DISTINCT method FROM public.telegram_outbox"
            )
        self.assertEqual({row["method"] for row in methods}, {"copy_message"})

    async def test_unknown_crash_state_cannot_be_replayed(self) -> None:
        await self.repository.enqueue_messages(
            topic="crash",
            dedupe_scope="after-send",
            messages={999: "possibly delivered"},
        )
        row = (await self.repository.claim_batch(limit=1))[0]
        outbox_id = int(row["outbox_id"])
        async with self.pool.acquire() as connection:
            await connection.execute(
                "UPDATE public.telegram_outbox SET locked_at=now()-interval '1 hour' WHERE outbox_id=$1",
                outbox_id,
            )
        await self.repository.fail_stale(older_than_minutes=15)
        replayed = await self.repository.requeue_confirmed_not_sent(
            outbox_id,
            reviewed_by=1,
        )
        self.assertFalse(replayed)
        confirmed = await self.repository.confirm_delivered(outbox_id, reviewed_by=1)
        self.assertTrue(confirmed)
