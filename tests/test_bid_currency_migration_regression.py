from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "db/migrations/012_bid_currency_and_deadline_contract.sql"
DEPLOY_PATH = ROOT / "deploy/server/deploy.sh"


def test_bid_currency_backfill_suspends_legacy_check_before_update() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    drop_position = sql.index("DROP CONSTRAINT %I")
    update_position = sql.index("UPDATE public.bids AS b")
    restore_position = sql.index("ADD CONSTRAINT %I %s NOT VALID")

    assert "pg_get_constraintdef" in sql
    assert drop_position < update_position < restore_position
    assert "DELETE FROM public.bids" not in sql
    assert "SET amount" not in sql


def test_deployment_smoke_rejects_restart_loops() -> None:
    script = DEPLOY_PATH.read_text(encoding="utf-8")

    assert "ROMATIC_HEALTH_STABLE_POLLS" in script
    assert "restart_count > 0" in script
    assert "restarting|exited|dead|removing" in script
    assert "stable_polls >= HEALTH_STABLE_POLLS" in script
    assert "rolling application code back" in script


@pytest.mark.asyncio
async def test_migration_preserves_legacy_bid_and_remains_idempotent() -> None:
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    connection = await asyncpg.connect(database_url)
    transaction = connection.transaction()
    await transaction.start()

    try:
        await connection.execute(
            """
            CREATE TABLE public.auctions (
                auction_id integer PRIMARY KEY,
                currency text NOT NULL,
                start_price integer NOT NULL
            );

            CREATE OR REPLACE FUNCTION public.is_valid_bid(
                p_auction_id integer,
                p_amount integer
            ) RETURNS boolean
            STABLE
            LANGUAGE plpgsql
            AS $$
            DECLARE
                required_minimum integer;
            BEGIN
                SELECT start_price
                  INTO required_minimum
                FROM public.auctions
                WHERE auction_id = p_auction_id;

                RETURN required_minimum IS NOT NULL
                   AND p_amount >= required_minimum;
            END;
            $$;

            CREATE TABLE public.bids (
                bid_id serial PRIMARY KEY,
                auction_id integer NOT NULL,
                bidder_id bigint NOT NULL,
                amount integer NOT NULL,
                placed_at timestamp DEFAULT CURRENT_TIMESTAMP,
                discussion_message_id bigint,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT chk_bids_step_and_min_by_currency
                    CHECK (public.is_valid_bid(auction_id, amount))
            );
            """
        )

        await connection.execute(
            """
            INSERT INTO public.auctions (auction_id, currency, start_price)
            VALUES (2039, 'алмазы', 30);

            INSERT INTO public.bids (bid_id, auction_id, bidder_id, amount)
            VALUES (3811, 2039, 1331750526, 30);

            UPDATE public.auctions
            SET start_price = 100
            WHERE auction_id = 2039;
            """
        )

        await connection.execute(migration_sql)
        await connection.execute(migration_sql)

        row = await connection.fetchrow(
            """
            SELECT bid_id, auction_id, bidder_id, amount, currency
            FROM public.bids
            WHERE bid_id = 3811
            """
        )
        assert row is not None
        assert dict(row) == {
            "bid_id": 3811,
            "auction_id": 2039,
            "bidder_id": 1331750526,
            "amount": 30,
            "currency": "алмазы",
        }

        validated = await connection.fetchval(
            """
            SELECT convalidated
            FROM pg_catalog.pg_constraint
            WHERE conrelid = 'public.bids'::regclass
              AND conname = 'chk_bids_step_and_min_by_currency'
            """
        )
        assert validated is False

        with pytest.raises(asyncpg.CheckViolationError):
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO public.bids (
                        auction_id,
                        bidder_id,
                        amount,
                        currency
                    ) VALUES (2039, 200, 31, 'алмазы')
                    """
                )

        await connection.execute(
            """
            INSERT INTO public.bids (
                auction_id,
                bidder_id,
                amount,
                currency
            ) VALUES (2039, 201, 100, 'алмазы')
            """
        )
    finally:
        await transaction.rollback()
        await connection.close()
