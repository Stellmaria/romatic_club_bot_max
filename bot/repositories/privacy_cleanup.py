"""Persistence adapter for approved temporary privacy cleanup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

_PRIVACY_CLEANUP_LOCK_KEY = 4_504_501_021

_COUNT_EXPIRED_SESSIONS = """
    SELECT count(*)::bigint
    FROM public.schedule_setup_sessions
    WHERE active IS FALSE
      AND updated_at < $1
"""

_DELETE_EXPIRED_SESSIONS = """
    WITH candidates AS (
        SELECT ctid
        FROM public.schedule_setup_sessions
        WHERE active IS FALSE
      AND updated_at < $1
        ORDER BY updated_at, user_id
        LIMIT $2
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM public.schedule_setup_sessions AS target
    USING candidates
    WHERE target.ctid = candidates.ctid
    RETURNING target.user_id
"""


class PrivacyCleanupConflict(RuntimeError):
    """Raised when the database no longer matches the reviewed cleanup plan."""


class PrivacyCleanupLockUnavailable(RuntimeError):
    """Raised when another cleanup executor already owns the global lock."""


@dataclass(frozen=True, slots=True)
class PrivacyCleanupExecution:
    deleted_rows: int
    audit_id: int


class PrivacyCleanupRepository:
    """Execute one statically allowlisted temporary-data rule."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def count_expired_schedule_sessions(self, *, cutoff: datetime) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(_COUNT_EXPIRED_SESSIONS, cutoff)
        return int(value or 0)

    async def apply_expired_schedule_sessions(
        self,
        *,
        cutoff: datetime,
        expected_eligible_rows: int,
        delete_limit: int,
        policy_sha256: str,
        plan_sha256: str,
        run_id: str,
    ) -> PrivacyCleanupExecution:
        async with self._pool.acquire() as connection:
            async with connection.transaction(isolation="serializable"):
                locked = await connection.fetchval(
                    "SELECT pg_try_advisory_xact_lock($1)",
                    _PRIVACY_CLEANUP_LOCK_KEY,
                )
                if locked is not True:
                    raise PrivacyCleanupLockUnavailable(
                        "another privacy cleanup execution owns the advisory lock"
                    )

                current_count = int(
                    await connection.fetchval(_COUNT_EXPIRED_SESSIONS, cutoff) or 0
                )
                if current_count != expected_eligible_rows:
                    raise PrivacyCleanupConflict(
                        "eligible row count changed after the cleanup plan was generated"
                    )

                expected_delete_count = min(current_count, delete_limit)
                deleted = await connection.fetch(
                    _DELETE_EXPIRED_SESSIONS,
                    cutoff,
                    delete_limit,
                )
                if len(deleted) != expected_delete_count:
                    raise PrivacyCleanupConflict(
                        "deleted row count differs from the approved cleanup plan"
                    )

                details = json.dumps(
                    {
                        "batch_limit": delete_limit,
                        "cutoff": cutoff.isoformat(),
                        "deleted_rows": len(deleted),
                        "eligible_rows": current_count,
                        "plan_sha256": plan_sha256,
                        "policy_sha256": policy_sha256,
                        "rule_id": "expired_schedule_setup_sessions",
                        "run_id": run_id,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                audit_id = await connection.fetchval(
                    """
                    INSERT INTO public.audit_logs (user_id, action_type, details)
                    VALUES (NULL, 'privacy.cleanup.applied', $1)
                    RETURNING id
                    """,
                    details,
                )
                return PrivacyCleanupExecution(
                    deleted_rows=len(deleted),
                    audit_id=int(audit_id),
                )


__all__ = [
    "PrivacyCleanupConflict",
    "PrivacyCleanupExecution",
    "PrivacyCleanupLockUnavailable",
    "PrivacyCleanupRepository",
]
