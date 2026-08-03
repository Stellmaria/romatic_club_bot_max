"""Transactional outbox integration tests against the full current schema."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from bot.repositories.outbox import TelegramOutboxRepository


pytestmark = pytest.mark.integration


async def _insert_user(pool: asyncpg.Pool, user_id: int) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.users(user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
            f"user_{user_id}",
            f"User {user_id}",
        )


async def _insert_card(pool: asyncpg.Pool, card_id: int) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.decks(id, name)
            VALUES ($1, $2)
            ON CONFLICT (id) DO NOTHING
            """,
            card_id,
            f"Integration Deck {card_id}",
        )
        await connection.execute(
            """
            INSERT INTO public.cards(
                card_id,
                deck_id,
                num,
                hero_name,
                rarity,
                story,
                card_name
            )
            VALUES ($1, $1, 1, 'Integration Hero', 'common', 'Integration', $2)
            ON CONFLICT (card_id) DO NOTHING
            """,
            card_id,
            f"Integration Card {card_id}",
        )


async def _insert_auction(pool: asyncpg.Pool, *, auction_id: int = 1) -> None:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.auctions(
                auction_id,
                card_name,
                start_time,
                end_time,
                status
            )
            VALUES ($1, 'Outbox Card', $2, $3, 'pending')
            """,
            int(auction_id),
            now,
            now + timedelta(minutes=31),
        )


async def test_current_schema_keeps_utc_columns_and_operational_end_time_trigger(
    postgres_pool: asyncpg.Pool,
) -> None:
    async with postgres_pool.acquire() as connection:
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
        start = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
        interval = await connection.fetchval(
            """
            INSERT INTO public.auctions(
                card_name, start_time, end_time, status
            )
            VALUES ('Trigger Card', $1, $1, 'pending')
            RETURNING end_time - start_time
            """,
            start,
        )

    assert data_type == "timestamp with time zone"
    assert trigger_exists
    assert interval.total_seconds() == 30 * 60 + 59


async def test_concurrent_auction_enqueue_has_single_winner(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_auction(postgres_pool)
    repository = TelegramOutboxRepository(postgres_pool)

    results = await asyncio.gather(
        *(
            repository.enqueue_auction_notification(
                auction_id=1,
                event="start",
                messages={101: "start", 102: "start"},
            )
            for _ in range(8)
        )
    )

    assert sum(int(claimed) for claimed, _ in results) == 1
    async with postgres_pool.acquire() as connection:
        count = await connection.fetchval(
            "SELECT count(*) FROM public.telegram_outbox WHERE topic = 'auction'"
        )
    assert int(count) == 2


async def test_card_day_marker_and_message_are_atomic_under_race(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_user(postgres_pool, 501)
    await _insert_card(postgres_pool, 77)
    repository = TelegramOutboxRepository(postgres_pool)

    results = await asyncio.gather(
        *(
            repository.enqueue_card_day_notification(
                user_id=501,
                card_id=77,
                day=date(2026, 7, 14),
                text="card today",
            )
            for _ in range(8)
        )
    )

    assert sum(int(claimed) for claimed, _ in results) == 1
    async with postgres_pool.acquire() as connection:
        marker_count = await connection.fetchval(
            "SELECT count(*) FROM public.card_day_notifications"
        )
        message_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM public.telegram_outbox
            WHERE topic = 'card-day'
            """
        )
    assert (int(marker_count), int(message_count)) == (1, 1)


async def test_skip_locked_claims_do_not_overlap(
    postgres_pool: asyncpg.Pool,
) -> None:
    repository = TelegramOutboxRepository(postgres_pool)
    await repository.enqueue_messages(
        topic="race",
        dedupe_scope="batch",
        messages={user_id: "hello" for user_id in range(1, 11)},
    )

    batches = await asyncio.gather(
        *(repository.claim_batch(limit=3) for _ in range(4))
    )
    ids = [
        int(row["outbox_id"])
        for batch in batches
        for row in batch
    ]

    assert len(ids) == len(set(ids))
    assert len(ids) == 10


async def test_copy_message_broadcast_is_deduplicated(
    postgres_pool: asyncpg.Pool,
) -> None:
    repository = TelegramOutboxRepository(postgres_pool)

    first = await repository.enqueue_copy_message_broadcast(
        topic="admin-broadcast",
        dedupe_scope="100:200",
        recipients=[11, 12, 12],
        from_chat_id=100,
        message_id=200,
    )
    second = await repository.enqueue_copy_message_broadcast(
        topic="admin-broadcast",
        dedupe_scope="100:200",
        recipients=[11, 12],
        from_chat_id=100,
        message_id=200,
    )

    assert (first, second) == (2, 0)
    async with postgres_pool.acquire() as connection:
        methods = await connection.fetch(
            "SELECT DISTINCT method FROM public.telegram_outbox"
        )
    assert {row["method"] for row in methods} == {"copy_message"}


async def test_unknown_crash_state_cannot_be_replayed_automatically(
    postgres_pool: asyncpg.Pool,
) -> None:
    repository = TelegramOutboxRepository(postgres_pool)
    await repository.enqueue_messages(
        topic="crash",
        dedupe_scope="after-send",
        messages={999: "possibly delivered"},
    )
    row = (await repository.claim_batch(limit=1))[0]
    outbox_id = int(row["outbox_id"])

    async with postgres_pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE public.telegram_outbox
            SET locked_at = now() - interval '1 hour'
            WHERE outbox_id = $1
            """,
            outbox_id,
        )

    assert await repository.fail_stale(older_than_minutes=15) == [outbox_id]
    assert not await repository.requeue_confirmed_not_sent(
        outbox_id,
        reviewed_by=1,
    )
    assert await repository.confirm_delivered(outbox_id, reviewed_by=1)
