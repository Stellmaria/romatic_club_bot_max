from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from bot.repositories.auction_workflows import AuctionWorkflowRepository

pytestmark = pytest.mark.integration


async def _insert_auction(
    pool: asyncpg.Pool,
    *,
    auction_id: int,
    status: str,
    message_id: int | None = None,
    end_offset: timedelta = timedelta(minutes=31),
) -> None:
    now = datetime.now(UTC)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.auctions(
                auction_id,
                card_name,
                start_time,
                end_time,
                status,
                message_id,
                publication_started_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            """,
            auction_id,
            f"Issue 99 card {auction_id}",
            now,
            now + end_offset,
            status,
            message_id,
        )


async def test_non_positive_message_id_is_rejected_for_new_rows(
    postgres_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_auction(
            postgres_pool,
            auction_id=9901,
            status="finished",
            message_id=0,
        )


async def test_deferred_confirmation_is_concurrent_and_idempotent(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_auction(
        postgres_pool,
        auction_id=9902,
        status="publishing",
    )
    repository = AuctionWorkflowRepository(postgres_pool)
    assert await repository.mark_deferred(9902)

    results = await asyncio.gather(
        *(
            repository.confirm_deferred_publication(
                9902,
                channel_message_id=55001,
                discussion_message_id=66001,
            )
            for _ in range(8)
        )
    )

    assert {int(row["message_id"]) for row in results} == {55001}
    async with postgres_pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, message_id, discussion_message_id FROM public.auctions "
            "WHERE auction_id = 9902"
        )
    assert row is not None
    assert row["status"] == "active"
    assert row["message_id"] == 55001
    assert row["discussion_message_id"] == 66001


async def test_unique_positive_message_id_remains_protected(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_auction(postgres_pool, auction_id=9903, status="publication_deferred")
    await _insert_auction(postgres_pool, auction_id=9904, status="publication_deferred")
    repository = AuctionWorkflowRepository(postgres_pool)
    await repository.confirm_deferred_publication(9903, channel_message_id=55002)

    with pytest.raises(asyncpg.UniqueViolationError):
        await repository.confirm_deferred_publication(9904, channel_message_id=55002)


async def test_stale_recovery_does_not_fail_deferred_rows(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_auction(postgres_pool, auction_id=9905, status="publication_deferred")
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            "UPDATE public.auctions SET publication_started_at = NOW() - INTERVAL '1 day' "
            "WHERE auction_id = 9905"
        )
    repository = AuctionWorkflowRepository(postgres_pool)

    stale = await repository.fail_stale_publications(older_than_minutes=1)

    assert 9905 not in stale
    async with postgres_pool.acquire() as connection:
        status = await connection.fetchval(
            "SELECT status FROM public.auctions WHERE auction_id = 9905"
        )
    assert status == "publication_deferred"


async def test_reschedule_published_lot_keeps_binding_and_queues_refresh(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _insert_auction(
        postgres_pool,
        auction_id=9906,
        status="active",
        message_id=55006,
    )
    repository = AuctionWorkflowRepository(postgres_pool)
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start += timedelta(hours=2)

    row = await repository.reschedule(
        9906,
        start_time=start,
        end_time=start + timedelta(minutes=31),
        publication_chat_id=-100123456,
    )

    assert row["status"] == "active"
    assert row["message_id"] == 55006
    async with postgres_pool.acquire() as connection:
        queued = await connection.fetchrow("""
            SELECT method, payload
            FROM public.telegram_outbox
            WHERE topic = 'auction'
              AND payload -> 'payload' ->> 'auction_id' = '9906'
            """)
    assert queued is not None
    assert queued["method"] == "refresh_auction_publication"
