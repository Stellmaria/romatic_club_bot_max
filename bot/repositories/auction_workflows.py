from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from bot.domain.auctions import (
    AuctionNotFound,
    AuctionOwnerPermissionDenied,
    AuctionSlotConflict,
    InvalidAuctionTransition,
)
from bot.domain.auctions.workflows import AuctionDraft, PublicationFailure


class AuctionWorkflowRepository:
    """Persistence boundary for creation, moderation and publication."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    _MODERATABLE_FIELDS = frozenset(
        {
            "start_price",
            "currency",
            "comment",
            "image_id",
            "auction_kind",
            "craft_uid_possible",
        }
    )
    _OWNER_EDITABLE_FIELDS = frozenset({"start_price", "currency", "comment"})

    @staticmethod
    async def _has_prohibited_slot_overlap(
        conn: asyncpg.Connection,
        *,
        auction_id: int,
        start_time: datetime,
    ) -> bool:
        """Return whether the selected half-hour grid slot is forbidden.

        Auction deadlines intentionally extend through second 59 of the
        displayed ending minute (for example 18:00 -> 18:30:59).  That extra
        minute belongs to bid acceptance and must not reserve the next
        schedule position at 18:30.

        The moderation UI policy only forbids the same card at the same start
        minute when at least one owner is shared.  A different card or the same
        card owned by somebody else may use the same publication slot.
        """
        normalized_start = start_time.replace(second=0, microsecond=0)
        if normalized_start.minute not in (0, 30):
            raise ValueError(
                "auction start_time must be aligned to a :00 or :30 slot"
            )

        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.auctions AS current_lot
                    JOIN public.auction_owners AS current_owner
                      ON current_owner.auction_id = current_lot.auction_id
                    JOIN public.auctions AS existing
                      ON existing.auction_id <> current_lot.auction_id
                    JOIN public.auction_owners AS existing_owner
                      ON existing_owner.auction_id = existing.auction_id
                     AND existing_owner.user_id = current_owner.user_id
                    WHERE current_lot.auction_id = $1
                      AND existing.status IN (
                          'approved', 'scheduled', 'publishing', 'active'
                      )
                      AND date_trunc('minute', existing.start_time)
                          = date_trunc('minute', $2::timestamptz)
                      AND (
                          (
                              current_lot.card_id IS NOT NULL
                              AND existing.card_id = current_lot.card_id
                          )
                          OR (
                              lower(btrim(existing.card_name))
                                  = lower(btrim(current_lot.card_name))
                              AND lower(btrim(coalesce(existing.hero_name, '')))
                                  = lower(btrim(coalesce(current_lot.hero_name, '')))
                          )
                      )
                )
                """,
                int(auction_id),
                normalized_start,
            )
        )

    async def create_pending(self, draft: AuctionDraft) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                card = None
                if draft.card_id is not None:
                    card = await conn.fetchrow(
                        """
                        SELECT card_id, card_name, hero_name, image_id
                        FROM public.cards
                        WHERE card_id = $1
                        FOR SHARE
                        """,
                        int(draft.card_id),
                    )
                    if not card:
                        raise ValueError(f"card not found: {draft.card_id}")

                card_name = str(
                    (card["card_name"] if card else draft.card_name) or ""
                ).strip()
                hero_name = str(
                    (card["hero_name"] if card else draft.hero_name) or ""
                ).strip()
                image_id = (draft.image_id or (card["image_id"] if card else None))
                if not card_name:
                    raise ValueError("card_name is required")

                row = await conn.fetchrow(
                    """
                    INSERT INTO public.auctions (
                        card_name,
                        hero_name,
                        image_id,
                        start_price,
                        start_time,
                        end_time,
                        status,
                        created_at,
                        currency,
                        accepted_currencies,
                        custom_offer_terms,
                        comment,
                        auction_kind,
                        proof_photo_id,
                        craft_uid_possible,
                        card_id
                    )
                    VALUES (
                        $1, $2, $3, $4, NOW(), NOW() + INTERVAL '31 minutes',
                        'pending', NOW(),
                        $5, $6, $7, $8, $9, $10, $11, $12
                    )
                    RETURNING *
                    """,
                    card_name,
                    hero_name or None,
                    image_id,
                    int(draft.start_price),
                    draft.currency.value,
                    [currency.value for currency in (draft.accepted_currencies or (draft.currency,))],
                    draft.custom_offer_terms,
                    draft.comment,
                    draft.auction_kind.value,
                    draft.proof_photo_id,
                    draft.craft_uid_possible,
                    int(draft.card_id) if draft.card_id is not None else None,
                )
                await conn.execute(
                    """
                    INSERT INTO public.auction_owners (auction_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    int(row["auction_id"]),
                    int(draft.owner_id),
                )
        return dict(row)

    async def get(self, auction_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM public.auctions WHERE auction_id = $1",
                int(auction_id),
            )
        if not row:
            raise AuctionNotFound(f"auction {auction_id} not found")
        return dict(row)

    async def schedule(
        self,
        auction_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        if end_time <= start_time:
            raise ValueError("end_time must be greater than start_time")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Serialize approvals for one calendar day. This closes the race
                # between the free-slot screen and the final confirmation click.
                for schedule_day in sorted(
                    {start_time.date().toordinal(), end_time.date().toordinal()}
                ):
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock($1, $2)",
                        0x41554354,
                        int(schedule_day),
                    )
                conflict = await self._has_prohibited_slot_overlap(
                    conn,
                    auction_id=int(auction_id),
                    start_time=start_time,
                )
                if conflict:
                    raise AuctionSlotConflict(
                        f"auction slot {start_time!s} - {end_time!s} is occupied"
                    )

                row = await conn.fetchrow(
                    """
                    UPDATE public.auctions
                    SET start_time = $2,
                        end_time = $3,
                        status = 'scheduled',
                        publication_error = NULL,
                        publication_next_attempt_at = NULL
                    WHERE auction_id = $1
                      AND status IN ('pending', 'approved', 'moderation')
                    RETURNING *
                    """,
                    int(auction_id),
                    start_time,
                    end_time,
                )
                if not row:
                    current = await conn.fetchval(
                        "SELECT status FROM public.auctions WHERE auction_id = $1",
                        int(auction_id),
                    )
                    if current is None:
                        raise AuctionNotFound(f"auction {auction_id} not found")
                    raise InvalidAuctionTransition(
                        current=str(current),
                        target="scheduled",
                    )
        return dict(row)

    async def reject(self, auction_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'rejected'
                WHERE auction_id = $1
                  AND status IN ('pending', 'approved', 'moderation')
                RETURNING *
                """,
                int(auction_id),
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.auctions WHERE auction_id = $1",
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(current=str(current), target="rejected")
        return dict(row)

    async def reschedule(
        self,
        auction_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        if end_time <= start_time:
            raise ValueError("end_time must be greater than start_time")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for schedule_day in sorted(
                    {start_time.date().toordinal(), end_time.date().toordinal()}
                ):
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock($1, $2)",
                        0x41554354,
                        int(schedule_day),
                    )
                conflict = await self._has_prohibited_slot_overlap(
                    conn,
                    auction_id=int(auction_id),
                    start_time=start_time,
                )
                if conflict:
                    raise AuctionSlotConflict(
                        f"auction slot {start_time!s} - {end_time!s} is occupied"
                    )
                row = await conn.fetchrow(
                    """
                    UPDATE public.auctions
                    SET start_time = $2,
                        end_time = $3,
                        notified_start = FALSE,
                        notified_1min = FALSE,
                        notified_end = FALSE
                    WHERE auction_id = $1
                      AND status = 'scheduled'
                      AND message_id IS NULL
                    RETURNING *
                    """,
                    int(auction_id),
                    start_time,
                    end_time,
                )
                if not row:
                    current = await conn.fetchval(
                        "SELECT status FROM public.auctions WHERE auction_id = $1",
                        int(auction_id),
                    )
                    if current is None:
                        raise AuctionNotFound(f"auction {auction_id} not found")
                    raise InvalidAuctionTransition(
                        current=str(current),
                        target="scheduled",
                    )
        return dict(row)

    async def update_moderatable_field(
        self,
        auction_id: int,
        *,
        field: str,
        value: Any,
    ) -> dict[str, Any]:
        return await self.update_moderatable_fields(
            auction_id,
            changes={field: value},
        )

    async def update_moderatable_fields(
        self,
        auction_id: int,
        *,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        if not changes:
            raise ValueError("at least one field is required")
        unknown = set(changes) - self._MODERATABLE_FIELDS
        if unknown:
            raise ValueError(f"fields are not moderatable: {sorted(unknown)}")
        ordered = sorted(changes)
        assignments = ", ".join(
            f"{field} = ${index}"
            for index, field in enumerate(ordered, start=2)
        )
        values = [changes[field] for field in ordered]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    UPDATE public.auctions
                    SET {assignments}
                    WHERE auction_id = $1
                      AND status IN (
                          'draft', 'moderation', 'pending', 'approved',
                          'scheduled', 'publication_failed'
                      )
                      AND message_id IS NULL
                    RETURNING *
                    """,
                    int(auction_id),
                    *values,
                )
                if not row:
                    current = await conn.fetchval(
                        "SELECT status FROM public.auctions WHERE auction_id = $1",
                        int(auction_id),
                    )
                    if current is None:
                        raise AuctionNotFound(f"auction {auction_id} not found")
                    raise InvalidAuctionTransition(
                        current=str(current),
                        target="edit:" + ",".join(ordered),
                    )
                if changes.get("auction_kind") in {"reverse", "free"}:
                    await conn.execute(
                        """
                        UPDATE public.autobids
                        SET is_active = FALSE,
                            updated_at = NOW()
                        WHERE auction_id = $1
                          AND is_active = TRUE
                        """,
                        int(auction_id),
                    )
        return dict(row)

    async def update_owner_fields(
        self,
        auction_id: int,
        *,
        owner_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        if not changes:
            raise ValueError("at least one field is required")
        unknown = set(changes) - self._OWNER_EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"fields are not owner-editable: {sorted(unknown)}")
        ordered = sorted(changes)
        assignments = ", ".join(
            f"{field} = ${index}"
            for index, field in enumerate(ordered, start=2)
        )
        owner_parameter = len(ordered) + 2
        values = [changes[field] for field in ordered]
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.auctions a
                SET {assignments}
                WHERE a.auction_id = $1
                  AND a.status = 'pending'
                  AND a.message_id IS NULL
                  AND EXISTS (
                      SELECT 1
                      FROM public.auction_owners ao
                      WHERE ao.auction_id = a.auction_id
                        AND ao.user_id = ${owner_parameter}
                  )
                RETURNING a.*
                """,
                int(auction_id),
                *values,
                int(owner_id),
            )
            if not row:
                current = await conn.fetchrow(
                    """
                    SELECT a.status,
                           EXISTS (
                               SELECT 1 FROM public.auction_owners ao
                               WHERE ao.auction_id = a.auction_id
                                 AND ao.user_id = $2
                           ) AS is_owner
                    FROM public.auctions a
                    WHERE a.auction_id = $1
                    """,
                    int(auction_id),
                    int(owner_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                if not current["is_owner"]:
                    raise AuctionOwnerPermissionDenied(
                        f"user {owner_id} does not own auction {auction_id}"
                    )
                raise InvalidAuctionTransition(
                    current=str(current["status"]),
                    target="owner_edit",
                )
        return dict(row)

    async def get_owned(self, auction_id: int, *, owner_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.*
                FROM public.auctions a
                JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                WHERE a.auction_id = $1
                  AND ao.user_id = $2
                """,
                int(auction_id),
                int(owner_id),
            )
            if not row:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.auctions WHERE auction_id = $1)",
                    int(auction_id),
                )
                if not exists:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise AuctionOwnerPermissionDenied(
                    f"user {owner_id} does not own auction {auction_id}"
                )
        return dict(row)

    async def cancel_by_owner(self, auction_id: int, *, owner_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions a
                SET status = 'cancelled'
                WHERE a.auction_id = $1
                  AND a.status IN (
                      'draft', 'moderation', 'pending', 'approved',
                      'publication_failed'
                  )
                  AND a.message_id IS NULL
                  AND EXISTS (
                      SELECT 1 FROM public.auction_owners ao
                      WHERE ao.auction_id = a.auction_id
                        AND ao.user_id = $2
                  )
                RETURNING a.*
                """,
                int(auction_id),
                int(owner_id),
            )
            if not row:
                current = await conn.fetchrow(
                    """
                    SELECT a.status,
                           EXISTS (
                               SELECT 1 FROM public.auction_owners ao
                               WHERE ao.auction_id = a.auction_id
                                 AND ao.user_id = $2
                           ) AS is_owner
                    FROM public.auctions a
                    WHERE a.auction_id = $1
                    """,
                    int(auction_id),
                    int(owner_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                if not current["is_owner"]:
                    raise AuctionOwnerPermissionDenied(
                        f"user {owner_id} does not own auction {auction_id}"
                    )
                raise InvalidAuctionTransition(
                    current=str(current["status"]),
                    target="cancelled",
                )
        return dict(row)

    async def cancel_by_moderator(self, auction_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'cancelled'
                WHERE auction_id = $1
                  AND status IN (
                      'draft', 'moderation', 'pending', 'approved', 'scheduled',
                      'publication_failed', 'active', 'finalization_failed'
                  )
                RETURNING *
                """,
                int(auction_id),
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.auctions WHERE auction_id = $1",
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(
                    current=str(current),
                    target="cancelled",
                )
        return dict(row)

    async def requeue_publication(self, auction_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'scheduled',
                    publication_started_at = NULL,
                    publication_finished_at = NULL,
                    publication_attempts = 0,
                    publication_error = NULL,
                    publication_next_attempt_at = NULL
                WHERE auction_id = $1
                  AND status IN ('scheduled', 'publication_failed')
                  AND message_id IS NULL
                RETURNING *
                """,
                int(auction_id),
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.auctions WHERE auction_id = $1",
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(current=str(current), target="scheduled")
        return dict(row)

    async def restart(self, auction_id: int, *, end_time: datetime) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'active',
                    end_time = $2,
                    finalization_started_at = NULL,
                    finalization_finished_at = NULL,
                    finalization_error = NULL,
                    notified_end = FALSE
                WHERE auction_id = $1
                  AND status IN ('active', 'finished', 'finalization_failed')
                  AND message_id IS NOT NULL
                RETURNING *
                """,
                int(auction_id),
                end_time,
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.auctions WHERE auction_id = $1",
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(current=str(current), target="active")
        return dict(row)

    async def finish_now(self, auction_id: int, *, end_time: datetime) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'finished',
                    end_time = $2,
                    finalization_finished_at = NOW(),
                    finalization_error = NULL
                WHERE auction_id = $1
                  AND status = 'active'
                RETURNING *
                """,
                int(auction_id),
                end_time,
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.auctions WHERE auction_id = $1",
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(current=str(current), target="finished")
        return dict(row)

    async def bind_discussion_by_message(
        self,
        *,
        channel_message_id: int,
        discussion_message_id: int,
    ) -> int | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET discussion_message_id = $1
                WHERE message_id = $2
                  AND (
                      discussion_message_id IS NULL
                      OR discussion_message_id = $1
                  )
                RETURNING auction_id
                """,
                int(discussion_message_id),
                int(channel_message_id),
            )
        return int(row["auction_id"]) if row else None

    async def bind_discussion_by_auction(
        self,
        *,
        auction_id: int,
        discussion_message_id: int,
    ) -> int | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET discussion_message_id = $2
                WHERE auction_id = $1
                  AND (
                      discussion_message_id IS NULL
                      OR discussion_message_id = $2
                  )
                RETURNING auction_id
                """,
                int(auction_id),
                int(discussion_message_id),
            )
        return int(row["auction_id"]) if row else None

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    WITH due AS (
                        SELECT auction_id
                        FROM public.auctions
                        WHERE status = 'scheduled'
                          AND message_id IS NULL
                          AND start_time <= $1
                          AND (
                              publication_next_attempt_at IS NULL
                              OR publication_next_attempt_at <= NOW()
                          )
                        ORDER BY start_time, auction_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT $2
                    )
                    UPDATE public.auctions a
                    SET status = 'publishing',
                        publication_started_at = NOW(),
                        publication_finished_at = NULL,
                        publication_error = NULL,
                        publication_attempts = COALESCE(publication_attempts, 0) + 1
                    FROM due
                    WHERE a.auction_id = due.auction_id
                    RETURNING a.*
                    """,
                    now,
                    max(1, int(limit)),
                )
        return [dict(row) for row in rows]

    async def claim_one(self, auction_id: int) -> dict[str, Any]:
        """Claim one scheduled lot for an explicit admin publication."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'publishing',
                    publication_started_at = NOW(),
                    publication_finished_at = NULL,
                    publication_error = NULL,
                    publication_attempts = COALESCE(publication_attempts, 0) + 1
                WHERE auction_id = $1
                  AND status = 'scheduled'
                  AND message_id IS NULL
                RETURNING *
                """,
                int(auction_id),
            )
            if not row:
                current = await conn.fetchrow(
                    """
                    SELECT status, message_id
                    FROM public.auctions
                    WHERE auction_id = $1
                    """,
                    int(auction_id),
                )
                if current is None:
                    raise AuctionNotFound(f"auction {auction_id} not found")
                raise InvalidAuctionTransition(
                    current=str(current["status"]),
                    target="publishing",
                )
        return dict(row)

    async def mark_published(self, auction_id: int, *, message_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'active',
                    message_id = $2,
                    publication_finished_at = NOW(),
                    publication_error = NULL,
                    publication_next_attempt_at = NULL
                WHERE auction_id = $1
                  AND status = 'publishing'
                  AND message_id IS NULL
                RETURNING auction_id
                """,
                int(auction_id),
                int(message_id),
            )
        return bool(row)

    async def mark_publication_failed(
        self,
        auction_id: int,
        *,
        error: str,
        max_attempts: int = 3,
        retry_after_seconds: int = 60,
    ) -> PublicationFailure:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = CASE
                        WHEN COALESCE(publication_attempts, 0) >= $3
                            THEN 'publication_failed'
                        ELSE 'scheduled'
                    END,
                    publication_error = $2,
                    publication_finished_at = CASE
                        WHEN COALESCE(publication_attempts, 0) >= $3 THEN NOW()
                        ELSE NULL
                    END,
                    publication_next_attempt_at = CASE
                        WHEN COALESCE(publication_attempts, 0) >= $3 THEN NULL
                        ELSE NOW() + make_interval(secs => $4::int)
                    END
                WHERE auction_id = $1
                  AND status = 'publishing'
                  AND message_id IS NULL
                RETURNING status, publication_attempts
                """,
                int(auction_id),
                (error or "unknown publication error")[:2000],
                max(1, int(max_attempts)),
                max(1, int(retry_after_seconds)),
            )
        attempts = int(row["publication_attempts"] or 0) if row else 0
        return PublicationFailure(
            auction_id=int(auction_id),
            terminal=bool(row and row["status"] == "publication_failed"),
            attempts=attempts,
        )

    async def fail_stale_publications(self, *, older_than_minutes: int = 15) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE public.auctions
                SET status = 'publication_failed',
                    publication_finished_at = NOW(),
                    publication_error = 'publisher lease expired; manual review required'
                WHERE status = 'publishing'
                  AND message_id IS NULL
                  AND publication_started_at <=
                      NOW() - make_interval(mins => $1::int)
                RETURNING auction_id
                """,
                max(1, int(older_than_minutes)),
            )
        return [int(row["auction_id"]) for row in rows]
