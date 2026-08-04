from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import asyncpg

from bot.domain.auctions.publication_repair import (
    PublicationRepairAction,
    PublicationRepairError,
    PublicationRepairResult,
)

_PUBLISHED_STATES = frozenset(
    {
        "active",
        "finalizing",
        "finalization_failed",
        "finished",
    }
)
_CONFIRMABLE_STATES = frozenset(
    {
        "scheduled",
        "publishing",
        "publication_deferred",
        "publication_failed",
        *_PUBLISHED_STATES,
    }
)
_CONSTRAINTS = (
    "chk_auctions_message_id_positive",
    "chk_auctions_unpublished_state_has_no_message",
)


class AuctionPublicationRepairRepository:
    """Transactional persistence for verified publication recovery."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def list_targets(self, auction_ids: Sequence[int]) -> list[dict[str, Any]]:
        normalized = sorted({int(auction_id) for auction_id in auction_ids})
        if not normalized:
            return []
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT auction_id,
                       status,
                       message_id,
                       discussion_message_id,
                       start_time,
                       end_time,
                       publication_started_at,
                       publication_finished_at,
                       publication_error,
                       publication_next_attempt_at,
                       finalization_started_at,
                       finalization_finished_at,
                       finalization_error
                FROM public.auctions
                WHERE auction_id = ANY($1::bigint[])
                ORDER BY auction_id
                """,
                normalized,
            )
        return [dict(row) for row in rows]

    async def constraint_status(self) -> dict[str, bool]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE conrelid = 'public.auctions'::regclass
                  AND conname = ANY($1::text[])
                ORDER BY conname
                """,
                list(_CONSTRAINTS),
            )
        return {str(row["conname"]): bool(row["convalidated"]) for row in rows}

    async def validate_constraints(self) -> dict[str, bool]:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._assert_global_postconditions(connection)
            await self._validate_constraints(connection)
        return await self.constraint_status()

    async def repair(
        self,
        actions: Sequence[PublicationRepairAction],
        *,
        dry_run: bool,
    ) -> PublicationRepairResult:
        normalized = tuple(sorted(actions, key=lambda item: item.auction_id))
        if not normalized:
            raise PublicationRepairError("at least one verified repair action is required")

        async with self._pool.acquire() as connection:
            transaction = connection.transaction(isolation="repeatable_read")
            await transaction.start()
            finished = False
            try:
                target_ids = [action.auction_id for action in normalized]
                protected_before = await self._protected_snapshot(connection, target_ids)
                reports_list: list[dict[str, Any]] = []
                for action in normalized:
                    reports_list.append(await self._apply_action(connection, action))
                reports = tuple(reports_list)
                await self._assert_global_postconditions(connection)
                protected_after = await self._protected_snapshot(connection, target_ids)
                if protected_before != protected_after:
                    raise PublicationRepairError(
                        "protected bids/owners/audit/outbox history changed during repair"
                    )

                constraints_validated = False
                if dry_run:
                    await transaction.rollback()
                else:
                    await self._validate_constraints(connection)
                    constraints_validated = True
                    await transaction.commit()
                finished = True
                return PublicationRepairResult(
                    reports=reports,
                    dry_run=dry_run,
                    constraints_validated=constraints_validated,
                    protected_snapshot=protected_after,
                )
            except BaseException:
                if not finished:
                    await transaction.rollback()
                raise

    @staticmethod
    def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "auction_id",
            "status",
            "message_id",
            "discussion_message_id",
            "publication_started_at",
            "publication_finished_at",
            "publication_error",
            "publication_next_attempt_at",
            "finalization_started_at",
            "finalization_finished_at",
            "finalization_error",
        )
        return {field: row.get(field) for field in fields}

    async def _apply_action(
        self,
        connection: asyncpg.Connection,
        action: PublicationRepairAction,
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            "SELECT * FROM public.auctions WHERE auction_id = $1 FOR UPDATE",
            int(action.auction_id),
        )
        if row is None:
            raise PublicationRepairError(f"auction {action.auction_id} does not exist")
        before = dict(row)
        status = str(before.get("status") or "")
        existing_message_id = before.get("message_id")
        existing_discussion_id = before.get("discussion_message_id")

        if action.action == "requeue":
            if not action.post_verified_absent:
                raise PublicationRepairError(
                    f"requeue for {action.auction_id} requires verified post absence"
                )
            if existing_message_id is not None and int(existing_message_id) > 0:
                raise PublicationRepairError(
                    f"auction {action.auction_id} has a positive message_id; requeue is unsafe"
                )
            if status not in {
                "scheduled",
                "publishing",
                "publication_deferred",
                "publication_failed",
            }:
                raise PublicationRepairError(
                    f"auction {action.auction_id} cannot be requeued from {status!r}"
                )
            await connection.execute(
                """
                UPDATE public.auctions
                SET status = 'scheduled',
                    message_id = NULL,
                    discussion_message_id = NULL,
                    publication_started_at = NULL,
                    publication_finished_at = NULL,
                    publication_attempts = 0,
                    publication_error = NULL,
                    publication_next_attempt_at = NULL,
                    notified_start = FALSE,
                    notified_1min = FALSE,
                    notified_end = FALSE
                WHERE auction_id = $1
                """,
                int(action.auction_id),
            )
        else:
            if action.action not in {
                "confirm",
                "normalize_published",
                "replace_published",
            }:
                raise PublicationRepairError(
                    f"unsupported repair action for {action.auction_id}: {action.action!r}"
                )
            if action.channel_message_id is None or int(action.channel_message_id) <= 0:
                raise PublicationRepairError(
                    f"{action.action} for {action.auction_id} requires a positive channel ID"
                )
            channel_message_id = int(action.channel_message_id)
            discussion_message_id = (
                int(action.discussion_message_id)
                if action.discussion_message_id is not None
                else None
            )
            if discussion_message_id is not None and discussion_message_id <= 0:
                raise PublicationRepairError("discussion_message_id must be positive")
            if status not in _CONFIRMABLE_STATES:
                raise PublicationRepairError(
                    f"auction {action.auction_id} cannot be confirmed from {status!r}"
                )
            if action.action in {"normalize_published", "replace_published"} and (
                existing_message_id is None or int(existing_message_id) <= 0
            ):
                raise PublicationRepairError(
                    f"auction {action.auction_id} has no published message to normalize"
                )
            if action.action == "replace_published":
                expected_previous = action.expected_previous_channel_message_id
                if expected_previous is None or int(expected_previous) <= 0:
                    raise PublicationRepairError(
                        f"replace_published for {action.auction_id} requires a positive "
                        "expected previous channel ID"
                    )
                if int(existing_message_id) not in {
                    int(expected_previous),
                    channel_message_id,
                }:
                    raise PublicationRepairError(
                        f"auction {action.auction_id} expected message_id "
                        f"{expected_previous}, found {existing_message_id}"
                    )
            elif (
                existing_message_id is not None
                and int(existing_message_id) > 0
                and int(existing_message_id) != channel_message_id
            ):
                raise PublicationRepairError(
                    f"auction {action.auction_id} has conflicting message_id "
                    f"{existing_message_id}"
                )
            if (
                existing_discussion_id is not None
                and discussion_message_id is not None
                and int(existing_discussion_id) != discussion_message_id
            ):
                raise PublicationRepairError(
                    f"auction {action.auction_id} has conflicting discussion_message_id"
                )

            await connection.execute(
                """
                UPDATE public.auctions
                SET status = CASE
                        WHEN status IN (
                            'finalizing',
                            'finalization_failed',
                            'finished'
                        ) THEN status
                        ELSE 'active'
                    END,
                    message_id = $2,
                    discussion_message_id = COALESCE($3, discussion_message_id),
                    publication_finished_at = COALESCE(publication_finished_at, NOW()),
                    publication_error = NULL,
                    publication_next_attempt_at = NULL
                WHERE auction_id = $1
                """,
                int(action.auction_id),
                channel_message_id,
                discussion_message_id,
            )

        after_row = await connection.fetchrow(
            "SELECT * FROM public.auctions WHERE auction_id = $1",
            int(action.auction_id),
        )
        if after_row is None:
            raise PublicationRepairError(f"auction {action.auction_id} disappeared during repair")
        return {
            "action": action.action,
            "before": self._snapshot(before),
            "after": self._snapshot(dict(after_row)),
        }

    @staticmethod
    async def _assert_global_postconditions(connection: asyncpg.Connection) -> None:
        invalid_message_ids = int(
            await connection.fetchval("SELECT count(*) FROM public.auctions WHERE message_id <= 0")
            or 0
        )
        incompatible = int(await connection.fetchval("""
                SELECT count(*)
                FROM public.auctions
                WHERE status IN (
                    'scheduled',
                    'publishing',
                    'publication_deferred'
                )
                  AND message_id IS NOT NULL
                """) or 0)
        if invalid_message_ids or incompatible:
            raise PublicationRepairError(
                "publication post-condition failed: "
                f"message_id<=0={invalid_message_ids}, incompatible={incompatible}"
            )

    @staticmethod
    async def _validate_constraints(connection: asyncpg.Connection) -> None:
        await connection.execute("""
            ALTER TABLE public.auctions
                VALIDATE CONSTRAINT chk_auctions_message_id_positive
            """)
        await connection.execute("""
            ALTER TABLE public.auctions
                VALIDATE CONSTRAINT chk_auctions_unpublished_state_has_no_message
            """)

    @staticmethod
    async def _protected_snapshot(
        connection: asyncpg.Connection,
        auction_ids: Sequence[int],
    ) -> dict[str, Any]:
        normalized = sorted({int(auction_id) for auction_id in auction_ids})
        bids = [
            dict(row)
            for row in await connection.fetch(
                """
                SELECT *
                FROM public.bids
                WHERE auction_id = ANY($1::bigint[])
                ORDER BY auction_id, bid_id
                """,
                normalized,
            )
        ]
        owners = [
            dict(row)
            for row in await connection.fetch(
                """
                SELECT *
                FROM public.auction_owners
                WHERE auction_id = ANY($1::bigint[])
                ORDER BY auction_id, user_id
                """,
                normalized,
            )
        ]
        audits = [
            dict(row)
            for row in await connection.fetch(
                """
                SELECT *
                FROM public.audit_logs
                WHERE auction_id = ANY($1::bigint[])
                ORDER BY auction_id, id
                """,
                normalized,
            )
        ]
        outbox_count = int(
            await connection.fetchval("SELECT count(*) FROM public.telegram_outbox") or 0
        )

        def digest(rows: list[dict[str, Any]]) -> str:
            payload = json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False)
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return {
            "bid_count": len(bids),
            "bid_digest": digest(bids),
            "owner_count": len(owners),
            "owner_digest": digest(owners),
            "audit_count": len(audits),
            "audit_digest": digest(audits),
            "outbox_count": outbox_count,
        }


class SingleConnectionPool:
    """Expose one maintenance connection through the repository pool contract."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection

    class _Acquire:
        def __init__(self, connection: asyncpg.Connection) -> None:
            self._connection = connection

        async def __aenter__(self) -> asyncpg.Connection:
            return self._connection

        async def __aexit__(self, *_args: object) -> None:
            return None

    def acquire(self) -> SingleConnectionPool._Acquire:
        return self._Acquire(self._connection)


__all__ = [
    "AuctionPublicationRepairRepository",
    "SingleConnectionPool",
]
