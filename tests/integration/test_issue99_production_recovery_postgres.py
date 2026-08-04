from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from bot.domain.auctions.publication_repair import PublicationRepairAction
from bot.repositories.auction_publication_repair import (
    AuctionPublicationRepairRepository,
)
from bot.repositories.auctions import AuctionFinalizationRepository
from bot.services.auction_publication_repair import Issue99PublicationRepairService

pytestmark = pytest.mark.integration

_TARGET_IDS = (3797, 7523, 9210, 9217, 9221, 9243)


def _actions() -> tuple[PublicationRepairAction, ...]:
    return (
        PublicationRepairAction(3797, "normalize_published", 5927),
        PublicationRepairAction(7523, "normalize_published", 10139),
        PublicationRepairAction(9210, "confirm", 12010, 1148772),
        PublicationRepairAction(9217, "confirm", 12017, 1149339),
        PublicationRepairAction(9221, "confirm", 12021, 1149326),
        PublicationRepairAction(9243, "confirm", 12043),
    )


async def _seed_damaged_rows(pool: asyncpg.Pool) -> None:
    start = datetime.now(UTC) - timedelta(days=2)
    end = start + timedelta(minutes=31)
    rows = (
        (3797, "scheduled", 5927, None),
        (7523, "scheduled", 10139, None),
        (9210, "finished", 0, 1148772),
        (9217, "publication_failed", None, 1149339),
        (9221, "publication_failed", None, 1149326),
        (9243, "publication_failed", None, None),
    )
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("""
            ALTER TABLE public.auctions
                DROP CONSTRAINT chk_auctions_message_id_positive,
                DROP CONSTRAINT chk_auctions_unpublished_state_has_no_message
            """)
        await connection.executemany(
            """
            INSERT INTO public.auctions(
                auction_id,
                card_name,
                start_price,
                start_time,
                end_time,
                status,
                message_id,
                discussion_message_id,
                publication_started_at,
                publication_error
            )
            VALUES ($1, $2, 10, $3, $4, $5, $6, $7, $3, 'issue-99 fixture')
            """,
            [
                (
                    auction_id,
                    f"Issue 99 production row {auction_id}",
                    start,
                    end,
                    status,
                    message_id,
                    discussion_message_id,
                )
                for auction_id, status, message_id, discussion_message_id in rows
            ],
        )
        await connection.execute("""
            ALTER TABLE public.auctions
                ADD CONSTRAINT chk_auctions_message_id_positive
                CHECK (message_id IS NULL OR message_id > 0) NOT VALID,
                ADD CONSTRAINT chk_auctions_unpublished_state_has_no_message
                CHECK (
                    status NOT IN (
                        'scheduled',
                        'publishing',
                        'publication_deferred'
                    )
                    OR message_id IS NULL
                ) NOT VALID
            """)
        await connection.execute(
            "INSERT INTO public.users(user_id, username) VALUES (990099, 'issue99')"
        )
        await connection.execute("""
            INSERT INTO public.auction_owners(auction_id, user_id)
            VALUES (9210, 990099)
            """)
        await connection.execute("""
            INSERT INTO public.bids(auction_id, bidder_id, amount, discussion_message_id)
            VALUES (9210, 990099, 10, 779210)
            """)
        await connection.execute("""
            INSERT INTO public.audit_logs(user_id, action_type, auction_id, details)
            VALUES (990099, 'issue99_fixture', 9210, 'must survive repair')
            """)
        await connection.execute("""
            INSERT INTO public.telegram_outbox(dedupe_key, method, chat_id, payload)
            VALUES (
                'issue99:protected',
                'send_message',
                -100123,
                '{"command_type":"send_message","version":1,"payload":{"text":"keep"}}'
            )
            """)


async def _publication_rows(pool: asyncpg.Pool) -> list[dict[str, object]]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT auction_id, status, message_id, discussion_message_id
            FROM public.auctions
            WHERE auction_id = ANY($1::bigint[])
            ORDER BY auction_id
            """,
            list(_TARGET_IDS),
        )
    return [dict(row) for row in rows]


async def test_issue99_recovery_is_atomic_idempotent_and_finalizable(
    postgres_pool: asyncpg.Pool,
) -> None:
    await _seed_damaged_rows(postgres_pool)
    repository = AuctionPublicationRepairRepository(postgres_pool)
    damaged = await _publication_rows(postgres_pool)

    dry_run = await repository.repair(_actions(), dry_run=True)

    assert dry_run.dry_run is True
    assert await _publication_rows(postgres_pool) == damaged

    applied = await repository.repair(_actions(), dry_run=False)
    first_repaired = await _publication_rows(postgres_pool)

    assert applied.constraints_validated is True
    by_id = {int(row["auction_id"]): row for row in first_repaired}
    assert by_id[9210]["status"] == "finished"
    assert by_id[9210]["message_id"] == 12010
    assert by_id[9210]["discussion_message_id"] == 1148772
    for auction_id in (3797, 7523, 9217, 9221, 9243):
        assert by_id[auction_id]["status"] == "active"
        assert int(by_id[auction_id]["message_id"] or 0) > 0

    repeated = await repository.repair(_actions(), dry_run=False)

    assert await _publication_rows(postgres_pool) == first_repaired
    assert repeated.protected_snapshot == applied.protected_snapshot
    assert await repository.constraint_status() == {
        "chk_auctions_message_id_positive": True,
        "chk_auctions_unpublished_state_has_no_message": True,
    }

    service = Issue99PublicationRepairService(repository)
    before_finalization = await service.status()
    assert before_finalization["completed"] is False
    assert before_finalization["unresolved"] == [3797, 7523, 9217, 9221, 9243]

    finalizer = AuctionFinalizationRepository(postgres_pool)
    claimed = await finalizer.claim_due(now=datetime.now(UTC), limit=20)
    assert {int(row["auction_id"]) for row in claimed} == {
        3797,
        7523,
        9217,
        9221,
        9243,
    }
    for row in claimed:
        assert await finalizer.mark_finished(int(row["auction_id"]))

    final = await service.status()
    assert final["completed"] is True
    assert final["unresolved"] == []
