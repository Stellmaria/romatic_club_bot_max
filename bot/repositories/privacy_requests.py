from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

_ACTIVE_STATUSES = ("pending_review", "approved")
_TERMINAL_APPEAL_STATUSES = ("resolved", "rejected", "closed")
_NON_FK_IDENTITY_COLUMNS = (
    ("autobids", "target_user_id"),
    ("autobids", "created_by"),
    ("autobid_actions", "target_user_id"),
    ("auction_manual_results", "winner_user_id"),
    ("auction_manual_results", "owner_user_id"),
    ("auction_manual_results", "updated_by"),
    ("auction_posts_backfill", "winner_id"),
    ("auction_posts_stats", "checked_by"),
    ("auction_posts_stats", "manual_winner_id"),
    ("auction_posts_stats", "excluded_by"),
    ("auction_posts_stats", "owner_id"),
    ("auction_win_mailings", "sent_by_user_id"),
    ("bid_duplicate_archive", "bidder_id"),
    ("exchange_batches", "moderator_id"),
    ("exchange_batches", "moderated_by"),
    ("exchange_batches", "manual_winner_id"),
    ("exchange_batches", "manual_set_by"),
    ("exchange_print_stats", "manual_winner_id"),
    ("exchange_print_stats", "updated_by"),
    ("uid_bans", "banned_by"),
    ("uid_verification_requests", "decided_by"),
    ("uid_verification_requests", "revision_by"),
    ("uid_verification_events", "actor_id"),
    ("telegram_outbox", "reviewed_by"),
)


class PrivacyRequestNotFound(LookupError):
    """Raised when a privacy request cannot be resolved."""


class PrivacyRequestConflict(RuntimeError):
    """Raised when request state or the approved plan changed."""


class PrivacyRequestBlocked(RuntimeError):
    """Raised when active business or access holds prevent execution."""


@dataclass(frozen=True, slots=True)
class PrivacyRequestRecord:
    request_id: UUID
    subject_digest: str
    status: str
    policy_sha256: str
    approved_plan_sha256: str | None
    blocking_holds: tuple[str, ...]
    retained_holds: tuple[str, ...]
    outcome_counts: dict[str, int]
    requested_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PrivacyRequestPlan:
    request_id: UUID
    status: str
    policy_sha256: str
    plan_sha256: str
    blocking_holds: tuple[str, ...]
    retained_holds: tuple[str, ...]
    action_counts: dict[str, int]

    @property
    def executable(self) -> bool:
        return not self.blocking_holds and self.status in _ACTIVE_STATUSES


def _row_to_record(row: asyncpg.Record) -> PrivacyRequestRecord:
    raw_counts = row["outcome_counts"] or {}
    return PrivacyRequestRecord(
        request_id=row["request_id"],
        subject_digest=str(row["subject_digest"]),
        status=str(row["status"]),
        policy_sha256=str(row["policy_sha256"]),
        approved_plan_sha256=row["approved_plan_sha256"],
        blocking_holds=tuple(row["blocking_holds"] or ()),
        retained_holds=tuple(row["retained_holds"] or ()),
        outcome_counts={str(key): int(value) for key, value in dict(raw_counts).items()},
        requested_at=row["requested_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _canonical_plan_sha256(
    *,
    request_id: UUID,
    subject_digest: str,
    policy_sha256: str,
    blocking_holds: tuple[str, ...],
    retained_holds: tuple[str, ...],
    action_counts: dict[str, int],
) -> str:
    payload = json.dumps(
        {
            "schema_version": 1,
            "request_id": str(request_id),
            "subject_digest": subject_digest,
            "policy_sha256": policy_sha256,
            "blocking_holds": list(blocking_holds),
            "retained_holds": list(retained_holds),
            "action_counts": action_counts,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class PrivacyRequestRepository:
    """Own privacy-request persistence, planning and transactional mutation boundaries."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    async def _append_audit(
        connection: asyncpg.Connection,
        *,
        action_type: str,
        details: dict[str, Any],
    ) -> None:
        await connection.execute(
            """
            INSERT INTO public.audit_logs (user_id, action_type, auction_id, details)
            VALUES (NULL, $1, NULL, $2)
            """,
            action_type,
            json.dumps(details, sort_keys=True),
        )

    @staticmethod
    async def _fetch_request(
        connection: asyncpg.Connection,
        request_id: UUID,
        *,
        for_update: bool = False,
    ) -> asyncpg.Record:
        query = (
            """
            SELECT request_id, subject_user_id, subject_digest, status, policy_sha256,
                   approved_plan_sha256, approved_by_digest,
                   blocking_holds, retained_holds,
                   outcome_counts, requested_at, updated_at, completed_at
            FROM public.privacy_requests
            WHERE request_id = $1
            FOR UPDATE
            """
            if for_update
            else """
            SELECT request_id, subject_user_id, subject_digest, status, policy_sha256,
                   approved_plan_sha256, approved_by_digest,
                   blocking_holds, retained_holds,
                   outcome_counts, requested_at, updated_at, completed_at
            FROM public.privacy_requests
            WHERE request_id = $1
            """
        )
        row = await connection.fetchrow(query, request_id)
        if row is None:
            raise PrivacyRequestNotFound(str(request_id))
        return row

    @staticmethod
    async def _snapshot(
        connection: asyncpg.Connection,
        *,
        subject_user_id: int,
    ) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, int]]:
        row = await connection.fetchrow(
            """
            WITH subject AS (
                SELECT user_id, username
                FROM public.users
                WHERE user_id = $1
            )
            SELECT
                EXISTS(SELECT 1 FROM public.admins WHERE user_id = $1) AS is_admin,
                (SELECT count(*) FROM public.auction_owners ao
                 JOIN public.auctions a USING (auction_id)
                 WHERE ao.user_id = $1
                   AND a.status IN ('pending', 'scheduled', 'publishing', 'active'))::int
                    AS active_auctions,
                (SELECT count(*) FROM public.market_listings
                 WHERE seller_id = $1 AND status IN ('active', 'hidden'))::int
                    AS active_listings,
                ((SELECT count(*) FROM public.bids b
                  JOIN public.auctions a USING (auction_id)
                  WHERE b.bidder_id = $1
                    AND a.status IN ('pending', 'scheduled', 'publishing', 'active'))
                 + (SELECT count(*) FROM public.autobids ab
                    JOIN public.auctions a USING (auction_id)
                    WHERE ab.target_user_id = $1 AND ab.is_active
                      AND a.status IN ('pending', 'scheduled', 'publishing', 'active')))::int
                    AS active_auction_participation,
                (SELECT count(*) FROM public.exchange_batches
                 WHERE user_id = $1 AND status IN ('pending', 'revision'))::int
                    AS pending_exchanges,
                (SELECT count(*) FROM public.user_appeals
                 WHERE user_id = $1 AND status NOT IN ('resolved', 'rejected', 'closed'))::int
                    AS open_appeals,
                (SELECT count(*) FROM public.uid_verification_requests
                 WHERE user_id = $1 AND status IN ('pending', 'conflict', 'revision'))::int
                    AS unresolved_verifications,
                ((SELECT count(*) FROM public.user_bans
                  WHERE user_id = $1
                    AND (banned_until IS NULL OR banned_until > now()))
                 + (SELECT count(*) FROM public.uid_bans b
                    WHERE (
                        b.uid_hash IN (
                            SELECT u.uid_hash FROM public.user_uids u
                            WHERE u.user_id = $1 AND u.uid_hash IS NOT NULL
                        )
                        OR b.uid IN (
                            SELECT u.uid FROM public.user_uids u WHERE u.user_id = $1
                        )
                    )
                    AND (b.banned_until IS NULL OR b.banned_until > now())))::int
                    AS active_bans,
                (SELECT count(*) FROM public.telegram_outbox
                 WHERE chat_id = $1
                   AND (status IN ('pending', 'processing')
                        OR (status = 'failed' AND reviewed_at IS NULL)))::int
                    AS active_outbox,
                ((SELECT count(*) FROM public.auction_owners WHERE user_id = $1)
                 + (SELECT count(*) FROM public.bids WHERE bidder_id = $1)
                 + (SELECT count(*) FROM public.market_listings WHERE seller_id = $1)
                 + (SELECT count(*) FROM public.exchange_batches WHERE user_id = $1))::int
                    AS business_history,
                ((SELECT count(*) FROM public.user_bans WHERE user_id = $1)
                 + (SELECT count(*) FROM public.user_warnings WHERE user_id = $1)
                 + (SELECT count(*) FROM public.user_appeals WHERE user_id = $1)
                 + (SELECT count(*) FROM public.user_uids WHERE user_id = $1)
                 + (SELECT count(*) FROM public.uid_verification_requests WHERE user_id = $1)
                 + (SELECT count(*) FROM public.audit_logs WHERE user_id = $1))::int
                    AS security_history,
                (SELECT count(*) FROM public.users WHERE user_id = $1)::int AS profile_rows,
                ((SELECT count(*) FROM public.settings WHERE user_id = $1)
                 + (SELECT count(*) FROM public.notifications WHERE user_id = $1)
                 + (SELECT count(*) FROM public.user_subscriptions WHERE user_id = $1)
                 + (SELECT count(*) FROM public.user_preset_subscriptions WHERE user_id = $1)
                 + (SELECT count(*) FROM public.card_day_notifications WHERE user_id = $1)
                 + (SELECT count(*) FROM public.unreachable_users WHERE user_id = $1))::int
                    AS preference_rows,
                ((SELECT count(*) FROM public.guides_thanks WHERE user_id = $1)
                 + (SELECT count(*) FROM public.admin_thanks_users WHERE user_id = $1)
                 + (SELECT count(*) FROM public.admin_thanks_clicks WHERE user_id = $1))::int
                    AS thanks_rows,
                (SELECT count(*) FROM public.audit_logs
                 WHERE user_id = $1 AND action_type NOT LIKE 'privacy.%')::int
                    AS audit_links,
                (SELECT count(*) FROM public.user_uids WHERE user_id = $1)::int
                    AS uid_bindings,
                (SELECT count(*) FROM public.uid_verification_requests WHERE user_id = $1)::int
                    AS uid_requests,
                (SELECT count(*) FROM public.user_appeals
                 WHERE user_id = $1 AND status IN ('resolved', 'rejected', 'closed'))::int
                    AS resolved_appeals,
                (SELECT count(*) FROM public.market_listings WHERE seller_id = $1)::int
                    AS market_listings,
                (SELECT count(*) FROM public.exchange_batches WHERE user_id = $1)::int
                    AS exchange_batches,
                (SELECT count(*) FROM public.telegram_outbox WHERE chat_id = $1)::int
                    AS outbox_rows,
                (SELECT count(*) FROM public.trusted_usernames t
                 JOIN subject s ON lower(t.username) = lower(s.username)
                 WHERE s.username IS NOT NULL)::int AS trusted_usernames
            """,
            int(subject_user_id),
        )
        if row is None or int(row["profile_rows"]) == 0:
            raise PrivacyRequestConflict("subject account no longer exists")

        blocking: list[str] = []
        if bool(row["is_admin"]):
            blocking.append("owner-admin-role")
        for key, code in (
            ("active_auctions", "active-auction"),
            ("active_listings", "active-market-listing"),
            ("active_auction_participation", "active-auction-participation"),
            ("pending_exchanges", "pending-exchange"),
            ("open_appeals", "open-appeal"),
            ("unresolved_verifications", "unresolved-uid-verification"),
            ("active_bans", "active-ban"),
            ("active_outbox", "pending-delivery-or-review"),
        ):
            if int(row[key]) > 0:
                blocking.append(code)

        retained: list[str] = []
        if int(row["business_history"]) > 0:
            retained.append("business-history-retained")
        if int(row["security_history"]) > 0:
            retained.append("security-history-retained")
        if int(row["uid_bindings"]) + int(row["uid_requests"]) > 0:
            retained.append("minimal-uid-digest-retained")

        action_counts = {
            key: int(row[key])
            for key in (
                "profile_rows",
                "preference_rows",
                "thanks_rows",
                "audit_links",
                "uid_bindings",
                "uid_requests",
                "resolved_appeals",
                "market_listings",
                "exchange_batches",
                "outbox_rows",
                "trusted_usernames",
            )
        }
        return tuple(sorted(blocking)), tuple(sorted(retained)), action_counts

    async def append_denied_audit(
        self,
        *,
        action_type: str,
        details: dict[str, Any],
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._append_audit(connection, action_type=action_type, details=details)

    async def create_request(
        self,
        *,
        request_id: UUID,
        subject_user_id: int,
        subject_digest: str,
        policy_sha256: str,
        requested_at: datetime,
    ) -> PrivacyRequestRecord:
        async with self._pool.acquire() as connection, connection.transaction():
            try:
                row = await connection.fetchrow(
                    """
                    INSERT INTO public.privacy_requests (
                        request_id, subject_user_id, subject_digest, status,
                        policy_sha256, requested_at, updated_at
                    )
                    VALUES ($1, $2, $3, 'pending_review', $4, $5, $5)
                    RETURNING request_id, subject_user_id, subject_digest, status,
                              policy_sha256, approved_plan_sha256, blocking_holds,
                              retained_holds, outcome_counts, requested_at,
                              updated_at, completed_at
                    """,
                    request_id,
                    int(subject_user_id),
                    subject_digest,
                    policy_sha256,
                    requested_at,
                )
            except asyncpg.UniqueViolationError as error:
                raise PrivacyRequestConflict("an active privacy request already exists") from error
            if row is None:
                raise PrivacyRequestConflict("privacy request was not created")
            await self._append_audit(
                connection,
                action_type="privacy.request.created",
                details={
                    "schema_version": 1,
                    "request_id": str(request_id),
                    "subject_digest": subject_digest,
                    "policy_sha256": policy_sha256,
                    "outcome": "pending-review",
                    "contains_personal_values": False,
                },
            )
            return _row_to_record(row)

    async def latest_for_subject(self, subject_digest: str) -> PrivacyRequestRecord | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT request_id, subject_digest, status, policy_sha256,
                       approved_plan_sha256, approved_by_digest,
                   blocking_holds, retained_holds,
                       outcome_counts, requested_at, updated_at, completed_at
                FROM public.privacy_requests
                WHERE subject_digest = $1
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                subject_digest,
            )
        return _row_to_record(row) if row else None

    async def cancel_request(
        self,
        *,
        request_id: UUID,
        subject_digest: str,
        cancelled_at: datetime,
    ) -> PrivacyRequestRecord:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                UPDATE public.privacy_requests
                SET status = 'cancelled', subject_user_id = NULL,
                    cancelled_at = $3, updated_at = $3, version = version + 1
                WHERE request_id = $1 AND subject_digest = $2
                  AND status = 'pending_review'
                RETURNING request_id, subject_digest, status, policy_sha256,
                          approved_plan_sha256, approved_by_digest,
                   blocking_holds, retained_holds,
                          outcome_counts, requested_at, updated_at, completed_at
                """,
                request_id,
                subject_digest,
                cancelled_at,
            )
            if row is None:
                raise PrivacyRequestConflict("only a pending request can be cancelled")
            await self._append_audit(
                connection,
                action_type="privacy.request.cancelled",
                details={
                    "schema_version": 1,
                    "request_id": str(request_id),
                    "subject_digest": subject_digest,
                    "outcome": "cancelled",
                    "contains_personal_values": False,
                },
            )
            return _row_to_record(row)

    async def plan_request(self, request_id: UUID) -> PrivacyRequestPlan:
        async with self._pool.acquire() as connection, connection.transaction(readonly=True):
            row = await self._fetch_request(connection, request_id)
            subject_user_id = row["subject_user_id"]
            if subject_user_id is None:
                raise PrivacyRequestConflict("request no longer has an executable subject")
            blocking, retained, counts = await self._snapshot(
                connection,
                subject_user_id=int(subject_user_id),
            )
            plan_sha = _canonical_plan_sha256(
                request_id=request_id,
                subject_digest=str(row["subject_digest"]),
                policy_sha256=str(row["policy_sha256"]),
                blocking_holds=blocking,
                retained_holds=retained,
                action_counts=counts,
            )
            return PrivacyRequestPlan(
                request_id=request_id,
                status=str(row["status"]),
                policy_sha256=str(row["policy_sha256"]),
                plan_sha256=plan_sha,
                blocking_holds=blocking,
                retained_holds=retained,
                action_counts=counts,
            )

    async def approve_request(
        self,
        *,
        request_id: UUID,
        expected_plan_sha256: str,
        operator_digest: str,
        approved_at: datetime,
    ) -> PrivacyRequestPlan:
        async with self._pool.acquire() as connection, connection.transaction(
            isolation="serializable"
        ):
            row = await self._fetch_request(connection, request_id, for_update=True)
            if row["status"] != "pending_review" or row["subject_user_id"] is None:
                raise PrivacyRequestConflict("request is not pending review")
            blocking, retained, counts = await self._snapshot(
                connection,
                subject_user_id=int(row["subject_user_id"]),
            )
            current_sha = _canonical_plan_sha256(
                request_id=request_id,
                subject_digest=str(row["subject_digest"]),
                policy_sha256=str(row["policy_sha256"]),
                blocking_holds=blocking,
                retained_holds=retained,
                action_counts=counts,
            )
            if current_sha != expected_plan_sha256:
                raise PrivacyRequestConflict("privacy plan changed; generate a new plan")
            if blocking:
                raise PrivacyRequestBlocked(",".join(blocking))
            await connection.execute(
                """
                UPDATE public.privacy_requests
                SET status = 'approved', approved_plan_sha256 = $2,
                    approved_by_digest = $3, blocking_holds = $4,
                    retained_holds = $5, approved_at = $6, updated_at = $6,
                    version = version + 1
                WHERE request_id = $1
                """,
                request_id,
                current_sha,
                operator_digest,
                list(blocking),
                list(retained),
                approved_at,
            )
            await self._append_audit(
                connection,
                action_type="privacy.request.approved",
                details={
                    "schema_version": 1,
                    "request_id": str(request_id),
                    "operator_digest": operator_digest,
                    "plan_sha256": current_sha,
                    "retained_holds": list(retained),
                    "outcome": "approved",
                    "contains_personal_values": False,
                },
            )
            return PrivacyRequestPlan(
                request_id=request_id,
                status="approved",
                policy_sha256=str(row["policy_sha256"]),
                plan_sha256=current_sha,
                blocking_holds=blocking,
                retained_holds=retained,
                action_counts=counts,
            )

    @staticmethod
    def _quote_identifier(value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError(f"unsafe SQL identifier: {value!r}")
        return f'"{value}"'

    @classmethod
    async def _pseudonymize_fk_links(
        cls,
        connection: asyncpg.Connection,
        *,
        subject_user_id: int,
        surrogate_user_id: int,
    ) -> int:
        rows = await connection.fetch(
            """
            SELECT ns.nspname AS table_schema, child.relname AS table_name,
                   attribute.attname AS column_name
            FROM pg_catalog.pg_constraint constraint_row
            JOIN pg_catalog.pg_class child
              ON child.oid = constraint_row.conrelid
            JOIN pg_catalog.pg_namespace ns
              ON ns.oid = child.relnamespace
            JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY keys(attnum, ord)
              ON TRUE
            JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = child.oid AND attribute.attnum = keys.attnum
            WHERE constraint_row.contype = 'f'
              AND constraint_row.confrelid = 'public.users'::regclass
              AND array_length(constraint_row.conkey, 1) = 1
              AND array_length(constraint_row.confkey, 1) = 1
              AND ns.nspname = 'public'
            ORDER BY child.relname, attribute.attname
            """
        )
        changed = 0
        for row in rows:
            table = str(row["table_name"])
            column = str(row["column_name"])
            if table == "privacy_requests" and column == "subject_user_id":
                continue
            query = (
                f"UPDATE public.{cls._quote_identifier(table)} "  # noqa: S608
                f"SET {cls._quote_identifier(column)} = $2 "
                f"WHERE {cls._quote_identifier(column)} = $1"
            )
            result = await connection.execute(query, subject_user_id, surrogate_user_id)
            changed += int(result.rsplit(" ", 1)[-1])
        return changed

    @classmethod
    async def _pseudonymize_non_fk_links(
        cls,
        connection: asyncpg.Connection,
        *,
        subject_user_id: int,
        surrogate_user_id: int,
    ) -> int:
        available_rows = await connection.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
        available = {
            (str(row["table_name"]), str(row["column_name"])) for row in available_rows
        }
        changed = 0
        for table, column in _NON_FK_IDENTITY_COLUMNS:
            if (table, column) not in available:
                continue
            query = (
                f"UPDATE public.{cls._quote_identifier(table)} "  # noqa: S608
                f"SET {cls._quote_identifier(column)} = $2 "
                f"WHERE {cls._quote_identifier(column)} = $1"
            )
            result = await connection.execute(query, subject_user_id, surrogate_user_id)
            changed += int(result.rsplit(" ", 1)[-1])
        return changed

    @classmethod
    async def _execute_actions(
        cls,
        connection: asyncpg.Connection,
        *,
        request_id: UUID,
        subject_user_id: int,
    ) -> dict[str, int]:
        username = await connection.fetchval(
            "SELECT username FROM public.users WHERE user_id = $1",
            subject_user_id,
        )
        counts: dict[str, int] = {}

        for key, query in (
            ("settings_deleted", "DELETE FROM public.settings WHERE user_id = $1"),
            ("notifications_deleted", "DELETE FROM public.notifications WHERE user_id = $1"),
            ("subscriptions_deleted", "DELETE FROM public.user_subscriptions WHERE user_id = $1"),
            (
                "preset_subscriptions_deleted",
                "DELETE FROM public.user_preset_subscriptions WHERE user_id = $1",
            ),
            (
                "card_notifications_deleted",
                "DELETE FROM public.card_day_notifications WHERE user_id = $1",
            ),
            (
                "unreachable_deleted",
                "DELETE FROM public.unreachable_users WHERE user_id = $1",
            ),
            ("guide_thanks_deleted", "DELETE FROM public.guides_thanks WHERE user_id = $1"),
            (
                "admin_thanks_users_deleted",
                "DELETE FROM public.admin_thanks_users WHERE user_id = $1",
            ),
            (
                "admin_thanks_clicks_deleted",
                "DELETE FROM public.admin_thanks_clicks WHERE user_id = $1",
            ),
        ):
            result = await connection.execute(query, subject_user_id)
            counts[key] = int(result.rsplit(" ", 1)[-1])

        if username:
            result = await connection.execute(
                "DELETE FROM public.trusted_usernames WHERE lower(username) = lower($1)",
                str(username),
            )
            counts["trusted_usernames_deleted"] = int(result.rsplit(" ", 1)[-1])
        else:
            counts["trusted_usernames_deleted"] = 0

        redacted_uid = f"redacted:{request_id.hex}"
        result = await connection.execute(
            """
            UPDATE public.uid_bans
            SET uid = $2, uid_enc = NULL, uid_last4 = NULL
            WHERE uid_hash IN (
                SELECT uid_hash FROM public.user_uids
                WHERE user_id = $1 AND uid_hash IS NOT NULL
            ) OR uid IN (
                SELECT uid FROM public.user_uids WHERE user_id = $1
            )
            """,
            subject_user_id,
            f"redacted-ban:{request_id.hex}",
        )
        counts["uid_bans_redacted"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.user_uids
            SET uid = $2, uid_enc = NULL, uid_last4 = NULL,
                status = 'revoked', updated_at = now()
            WHERE user_id = $1
            """,
            subject_user_id,
            redacted_uid,
        )
        counts["uid_bindings_redacted"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.uid_verification_requests
            SET uid = $2, challenge_code = 'redacted', profile_file_id = 'redacted',
                deal_file_ids = '{}'::text[], uid_proof_file_id = NULL,
                profile_proof_file_id = NULL, verification_code = 'redacted',
                counterparty_usernames = '{}'::text[], reg_date_proof_file_id = NULL,
                extra_proof_file_ids = '{}'::text[], revision_by_username = NULL,
                uid_enc = NULL, uid_last4 = NULL
            WHERE user_id = $1
            """,
            subject_user_id,
            redacted_uid,
        )
        counts["uid_requests_redacted"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.uid_verification_confirmations c
            SET counterparty_username = 'redacted', message_chat_id = NULL, message_id = NULL
            FROM public.uid_verification_requests r
            WHERE c.request_id = r.id AND r.user_id = $1
            """,
            subject_user_id,
        )
        counts["uid_confirmations_redacted"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.uid_verification_events e
            SET actor_id = CASE WHEN e.actor_id = $1 THEN NULL ELSE e.actor_id END,
                actor_username = CASE WHEN e.actor_id = $1 THEN NULL ELSE e.actor_username END,
                details = '{}'::jsonb
            FROM public.uid_verification_requests r
            WHERE e.request_id = r.id AND r.user_id = $1
            """,
            subject_user_id,
        )
        counts["uid_events_minimized"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.user_appeals
            SET username = NULL, description = '[redacted]', participants = NULL,
                media_message_ids = '{}'::integer[], origin_chat_id = 0
            WHERE user_id = $1 AND status IN ('resolved', 'rejected', 'closed')
            """,
            subject_user_id,
        )
        counts["resolved_appeals_redacted"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.market_listings
            SET description = NULL, cover_file_id = NULL, channel_id = NULL,
                message_id = NULL, proof_file_id = NULL, proof_by_card = '{}'::jsonb
            WHERE seller_id = $1
            """,
            subject_user_id,
        )
        counts["market_listings_minimized"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.market_listing_items i
            SET proof_file_id = NULL
            FROM public.market_listings l
            WHERE i.listing_id = l.listing_id AND l.seller_id = $1
            """,
            subject_user_id,
        )
        counts["market_item_proofs_removed"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.exchange_batches
            SET comment = NULL, proof_photo_id = 'NO_PROOF',
                posted_chat_id = NULL, posted_message_id = NULL
            WHERE user_id = $1
            """,
            subject_user_id,
        )
        counts["exchange_batches_minimized"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.telegram_outbox
            SET chat_id = $2, payload = '{}'::jsonb, telegram_message_id = NULL,
                last_error = NULL, review_note = NULL, updated_at = now()
            WHERE chat_id = $1
              AND status IN ('sent', 'failed')
              AND (status <> 'failed' OR reviewed_at IS NOT NULL)
            """,
            subject_user_id,
            -((request_id.int % ((1 << 62) - 1)) + 1),
        )
        counts["outbox_rows_minimized"] = int(result.rsplit(" ", 1)[-1])

        result = await connection.execute(
            """
            UPDATE public.audit_logs
            SET user_id = NULL
            WHERE user_id = $1 AND action_type NOT LIKE 'privacy.%'
            """,
            subject_user_id,
        )
        counts["audit_links_pseudonymized"] = int(result.rsplit(" ", 1)[-1])

        surrogate_user_id = -((request_id.int % ((1 << 62) - 1)) + 1)
        try:
            result = await connection.execute(
                """
                INSERT INTO public.users (
                    user_id, username, full_name, is_subscribed, is_luxury,
                    warnings_count, created_at, is_trusted, pm_opened,
                    uid_verif_confirmed_count, uid_verif_rejected_count
                )
                SELECT $2, NULL, NULL, FALSE, FALSE, 0, created_at, FALSE, FALSE, 0, 0
                FROM public.users
                WHERE user_id = $1
                """,
                subject_user_id,
                surrogate_user_id,
            )
        except asyncpg.UniqueViolationError as error:
            raise PrivacyRequestConflict("privacy surrogate identity collision") from error
        counts["surrogate_profiles_created"] = int(result.rsplit(" ", 1)[-1])
        if counts["surrogate_profiles_created"] != 1:
            raise PrivacyRequestConflict("subject account no longer exists")

        await connection.execute(
            "UPDATE public.privacy_requests SET subject_user_id = NULL WHERE request_id = $1",
            request_id,
        )
        counts["foreign_key_links_pseudonymized"] = await cls._pseudonymize_fk_links(
            connection,
            subject_user_id=subject_user_id,
            surrogate_user_id=surrogate_user_id,
        )
        counts["non_fk_links_pseudonymized"] = await cls._pseudonymize_non_fk_links(
            connection,
            subject_user_id=subject_user_id,
            surrogate_user_id=surrogate_user_id,
        )

        result = await connection.execute(
            "DELETE FROM public.users WHERE user_id = $1",
            subject_user_id,
        )
        counts["source_profiles_deleted"] = int(result.rsplit(" ", 1)[-1])
        if counts["source_profiles_deleted"] != 1:
            raise PrivacyRequestConflict("source identity was not removed")
        return counts

    async def execute_request(
        self,
        *,
        request_id: UUID,
        expected_plan_sha256: str,
        operator_digest: str,
        completed_at: datetime,
    ) -> PrivacyRequestRecord:
        async with self._pool.acquire() as connection, connection.transaction(
            isolation="serializable"
        ):
            row = await self._fetch_request(connection, request_id, for_update=True)
            if row["status"] != "approved" or row["subject_user_id"] is None:
                raise PrivacyRequestConflict("request is not approved for execution")
            if row["approved_plan_sha256"] != expected_plan_sha256:
                raise PrivacyRequestConflict("approved plan hash does not match")
            if row["approved_by_digest"] == operator_digest:
                raise PrivacyRequestConflict(
                    "privacy execution requires an operator different from the approver"
                )

            subject_user_id = int(row["subject_user_id"])
            blocking, retained, counts = await self._snapshot(
                connection,
                subject_user_id=subject_user_id,
            )
            current_sha = _canonical_plan_sha256(
                request_id=request_id,
                subject_digest=str(row["subject_digest"]),
                policy_sha256=str(row["policy_sha256"]),
                blocking_holds=blocking,
                retained_holds=retained,
                action_counts=counts,
            )
            if current_sha != expected_plan_sha256:
                raise PrivacyRequestConflict("privacy plan changed after approval")
            if blocking:
                raise PrivacyRequestBlocked(",".join(blocking))

            outcomes = await self._execute_actions(
                connection,
                request_id=request_id,
                subject_user_id=subject_user_id,
            )
            status = "completed_with_holds" if retained else "completed"
            result = await connection.fetchrow(
                """
                UPDATE public.privacy_requests
                SET subject_user_id = NULL, status = $2, blocking_holds = $3,
                    retained_holds = $4, outcome_counts = $5::jsonb,
                    completed_at = $6, updated_at = $6, version = version + 1
                WHERE request_id = $1
                RETURNING request_id, subject_digest, status, policy_sha256,
                          approved_plan_sha256, approved_by_digest,
                   blocking_holds, retained_holds,
                          outcome_counts, requested_at, updated_at, completed_at
                """,
                request_id,
                status,
                list(blocking),
                list(retained),
                json.dumps(outcomes, sort_keys=True),
                completed_at,
            )
            if result is None:
                raise PrivacyRequestConflict("privacy request execution was not recorded")
            await self._append_audit(
                connection,
                action_type="privacy.request.completed",
                details={
                    "schema_version": 1,
                    "request_id": str(request_id),
                    "operator_digest": operator_digest,
                    "plan_sha256": current_sha,
                    "outcome": status,
                    "retained_holds": list(retained),
                    "outcome_counts": outcomes,
                    "contains_personal_values": False,
                },
            )
            return _row_to_record(result)


__all__ = [
    "PrivacyRequestBlocked",
    "PrivacyRequestConflict",
    "PrivacyRequestNotFound",
    "PrivacyRequestPlan",
    "PrivacyRequestRecord",
    "PrivacyRequestRepository",
]
