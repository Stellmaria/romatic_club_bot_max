from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from bot.repositories.privacy_cleanup import (
    PrivacyCleanupConflict,
    PrivacyCleanupRepository,
)
from bot.services.privacy_cleanup import PrivacyCleanupService

pytestmark = pytest.mark.integration


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


async def _insert_session(
    connection: asyncpg.Connection,
    *,
    user_id: int,
    updated_at: datetime,
    active: bool = False,
) -> None:
    await connection.execute(
        "INSERT INTO public.users (user_id, username) VALUES ($1, $2)",
        user_id,
        f"privacy_cleanup_{user_id}",
    )
    await connection.execute(
        """
        INSERT INTO public.schedule_setup_sessions (
            user_id, active, validation_summary, opened_at, updated_at
        ) VALUES ($1, $2, '{}'::jsonb, $3, $3)
        """,
        user_id,
        active,
        updated_at,
    )


@pytest.mark.asyncio
async def test_approved_cleanup_deletes_only_old_inactive_rows_in_bounded_batch(
    postgres_pool: asyncpg.Pool,
) -> None:
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    oldest_user = 930000001
    stale_user = 930000002
    fresh_user = 930000003
    active_stale_user = 930000004

    async with postgres_pool.acquire() as connection:
        await _insert_session(
            connection,
            user_id=oldest_user,
            updated_at=now - timedelta(days=10),
        )
        await _insert_session(
            connection,
            user_id=stale_user,
            updated_at=now - timedelta(days=9),
        )
        await _insert_session(
            connection,
            user_id=fresh_user,
            updated_at=now - timedelta(days=2),
        )
        await _insert_session(
            connection,
            user_id=active_stale_user,
            updated_at=now - timedelta(days=30),
            active=True,
        )

    service = PrivacyCleanupService(
        PrivacyCleanupRepository(postgres_pool),
        clock=FixedClock(now),
    )
    plan = await service.build_plan(batch_limit=1)
    assert plan.eligible_rows == 2
    assert plan.to_dict()["planned_deletions"] == 1

    result = await service.apply_plan(
        plan,
        confirmation=plan.confirmation_token,
    )
    assert result.status == "applied"
    assert result.deleted_rows == 1
    assert result.audit_id is not None

    async with postgres_pool.acquire() as connection:
        remaining = {
            int(row["user_id"])
            for row in await connection.fetch("SELECT user_id FROM public.schedule_setup_sessions")
        }
        assert oldest_user not in remaining
        assert stale_user in remaining
        assert fresh_user in remaining
        assert active_stale_user in remaining

        audit = await connection.fetchrow(
            """
            SELECT action_type, user_id, details
            FROM public.audit_logs
            WHERE id = $1
            """,
            result.audit_id,
        )
        assert audit is not None
        assert audit["action_type"] == "privacy.cleanup.applied"
        assert audit["user_id"] is None
        details = json.loads(audit["details"])
        assert details["deleted_rows"] == 1
        assert details["eligible_rows"] == 2
        assert details["rule_id"] == "expired_schedule_setup_sessions"
        rendered = json.dumps(details, sort_keys=True)
        assert str(oldest_user) not in rendered
        assert str(stale_user) not in rendered

        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "UPDATE public.audit_logs SET details = 'changed' WHERE id = $1",
                result.audit_id,
            )
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "DELETE FROM public.audit_logs WHERE id = $1",
                result.audit_id,
            )


@pytest.mark.asyncio
async def test_cleanup_plan_drift_rolls_back_without_deleting_rows(
    postgres_pool: asyncpg.Pool,
) -> None:
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    first_user = 930000011
    late_user = 930000012

    async with postgres_pool.acquire() as connection:
        await _insert_session(
            connection,
            user_id=first_user,
            updated_at=now - timedelta(days=9),
        )

    service = PrivacyCleanupService(
        PrivacyCleanupRepository(postgres_pool),
        clock=FixedClock(now),
    )
    plan = await service.build_plan(batch_limit=100)
    assert plan.eligible_rows == 1

    async with postgres_pool.acquire() as connection:
        await _insert_session(
            connection,
            user_id=late_user,
            updated_at=now - timedelta(days=8),
        )

    with pytest.raises(PrivacyCleanupConflict, match="count changed"):
        await service.apply_plan(
            plan,
            confirmation=plan.confirmation_token,
        )

    async with postgres_pool.acquire() as connection:
        assert (
            await connection.fetchval(
                """
                SELECT count(*)::bigint
                FROM public.schedule_setup_sessions
                WHERE user_id = ANY($1::bigint[])
                """,
                [first_user, late_user],
            )
            == 2
        )
        assert await connection.fetchval("""
                SELECT count(*)::bigint
                FROM public.audit_logs
                WHERE action_type = 'privacy.cleanup.applied'
                """) == 0
