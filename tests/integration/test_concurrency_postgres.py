"""Concurrency contracts against real PostgreSQL row and advisory locks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from bot.domain.auctions import AuctionSlotConflict
from bot.repositories.auction_bids import AuctionBidRepository
from bot.repositories.auction_workflows import AuctionWorkflowRepository
from bot.repositories.uid_verification import UIDVerificationRepository
from bot.services.auction_bids import AuctionBidService
from db.core import db_pool
from db.users import set_luxury_status, set_trusted_status


pytestmark = pytest.mark.integration


async def _insert_user(
    pool: asyncpg.Pool,
    user_id: int,
    *,
    username: str | None = None,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.users(user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            int(user_id),
            username or f"user_{user_id}",
            f"User {user_id}",
        )


async def _insert_auction(
    pool: asyncpg.Pool,
    *,
    card_name: str,
    status: str,
    start_time: datetime,
    end_time: datetime,
    discussion_message_id: int | None = None,
    start_price: int = 0,
) -> int:
    async with pool.acquire() as connection:
        value = await connection.fetchval(
            """
            INSERT INTO public.auctions(
                card_name,
                hero_name,
                start_price,
                start_time,
                end_time,
                status,
                currency,
                accepted_currencies,
                auction_kind,
                discussion_message_id
            )
            VALUES (
                $1,
                'Integration Hero',
                $2,
                $3,
                $4,
                $5,
                'алмазы',
                ARRAY['алмазы']::text[],
                'standard',
                $6
            )
            RETURNING auction_id
            """,
            card_name,
            int(start_price),
            start_time,
            end_time,
            status,
            discussion_message_id,
        )
    return int(value)


async def test_parallel_schedule_approval_allows_only_one_shared_slot(
    postgres_pool: asyncpg.Pool,
) -> None:
    owner_id = 91001
    await _insert_user(postgres_pool, owner_id)

    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=2)).replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = start + timedelta(minutes=31)
    first_id = await _insert_auction(
        postgres_pool,
        card_name="Shared Slot Card",
        status="pending",
        start_time=now,
        end_time=now + timedelta(minutes=31),
    )
    second_id = await _insert_auction(
        postgres_pool,
        card_name="Shared Slot Card",
        status="pending",
        start_time=now,
        end_time=now + timedelta(minutes=31),
    )

    async with postgres_pool.acquire() as connection:
        await connection.executemany(
            """
            INSERT INTO public.auction_owners(auction_id, user_id)
            VALUES ($1, $2)
            """,
            [(first_id, owner_id), (second_id, owner_id)],
        )

    repository = AuctionWorkflowRepository(postgres_pool)
    results = await asyncio.gather(
        repository.schedule(first_id, start_time=start, end_time=end),
        repository.schedule(second_id, start_time=start, end_time=end),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, AuctionSlotConflict) for result in results) == 1

    async with postgres_pool.acquire() as connection:
        scheduled = await connection.fetchval(
            """
            SELECT count(*)
            FROM public.auctions
            WHERE auction_id = ANY($1::int[])
              AND status = 'scheduled'
            """,
            [first_id, second_id],
        )
    assert int(scheduled) == 1


async def test_parallel_equal_bids_are_serialized_by_auction_row_lock(
    postgres_pool: asyncpg.Pool,
) -> None:
    now = datetime.now(timezone.utc)
    auction_id = await _insert_auction(
        postgres_pool,
        card_name="Parallel Bid Card",
        status="active",
        start_time=now - timedelta(minutes=5),
        end_time=now + timedelta(minutes=25),
        discussion_message_id=700001,
        start_price=0,
    )
    service = AuctionBidService(AuctionBidRepository(postgres_pool))

    results = await asyncio.gather(
        *(
            service.place_for_auction(
                auction_id=auction_id,
                bid_message_id=710000 + index,
                bidder_id=720000 + index,
                explicit_amount=10,
                now=now,
                check_ban=False,
            )
            for index in range(8)
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, Exception) for result in results) == 7

    async with postgres_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT amount, currency
            FROM public.bids
            WHERE auction_id = $1
            """,
            auction_id,
        )
    assert [(row["amount"], row["currency"]) for row in rows] == [(10, "алмазы")]


async def test_parallel_publication_workers_claim_each_lot_once(
    postgres_pool: asyncpg.Pool,
) -> None:
    now = datetime.now(timezone.utc)
    auction_ids = [
        await _insert_auction(
            postgres_pool,
            card_name=f"Publication Card {index}",
            status="scheduled",
            start_time=now - timedelta(minutes=1),
            end_time=now + timedelta(minutes=30),
        )
        for index in range(15)
    ]
    repositories = [
        AuctionWorkflowRepository(postgres_pool)
        for _ in range(5)
    ]

    batches = await asyncio.gather(
        *(repository.claim_due(now=now, limit=4) for repository in repositories)
    )
    claimed_ids = [
        int(row["auction_id"])
        for batch in batches
        for row in batch
    ]

    assert sorted(claimed_ids) == sorted(auction_ids)
    assert len(claimed_ids) == len(set(claimed_ids))

    async with postgres_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT auction_id, status, publication_attempts
            FROM public.auctions
            WHERE auction_id = ANY($1::int[])
            ORDER BY auction_id
            """,
            auction_ids,
        )
    assert all(row["status"] == "publishing" for row in rows)
    assert all(row["publication_attempts"] == 1 for row in rows)


async def test_parallel_role_updates_preserve_independent_flags(
    postgres_pool: asyncpg.Pool,
) -> None:
    user_id = 93001
    await _insert_user(postgres_pool, user_id)

    db_pool.bind(postgres_pool)
    try:
        await asyncio.gather(
            set_luxury_status(user_id, True),
            set_trusted_status(user_id, True),
        )
    finally:
        db_pool.clear()

    async with postgres_pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT is_luxury, is_trusted
            FROM public.users
            WHERE user_id = $1
            """,
            user_id,
        )
    assert bool(row["is_luxury"])
    assert bool(row["is_trusted"])


async def test_parallel_uid_approvals_keep_uid_globally_unique(
    postgres_pool: asyncpg.Pool,
) -> None:
    first_user = 94001
    second_user = 94002
    await _insert_user(postgres_pool, first_user)
    await _insert_user(postgres_pool, second_user)

    repository = UIDVerificationRepository(postgres_pool)
    shared_uid = "1234567890123456"
    first_request = await repository.create_request(
        user_id=first_user,
        uid=shared_uid,
        verification_code="ABC123",
        profile_proof_file_id="proof:first",
        deal_file_ids=[],
        counterparty_usernames=[],
    )
    second_request = await repository.create_request(
        user_id=second_user,
        uid=shared_uid,
        verification_code="XYZ789",
        profile_proof_file_id="proof:second",
        deal_file_ids=[],
        counterparty_usernames=[],
    )

    results = await asyncio.gather(
        repository.approve_request(request_id=first_request, admin_id=1),
        repository.approve_request(request_id=second_request, admin_id=2),
    )

    assert sum(result.ok for result in results) == 1
    assert sum(result.code.startswith("conflict:") for result in results) == 1

    async with postgres_pool.acquire() as connection:
        uid_rows = await connection.fetchval(
            "SELECT count(*) FROM public.user_uids"
        )
        statuses = await connection.fetch(
            """
            SELECT id, status
            FROM public.uid_verification_requests
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            [first_request, second_request],
        )

    assert int(uid_rows) == 1
    assert {row["status"] for row in statuses} == {"approved", "conflict"}
