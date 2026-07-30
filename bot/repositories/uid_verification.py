from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from bot.uid_crypto import norm_uid, uid_decrypt, uid_encrypt, uid_hash, uid_last4


@dataclass(slots=True, frozen=True)
class UIDApprovalResult:
    ok: bool
    code: str = ""
    user_id: int | None = None


class UIDVerificationRepository:
    """Persistence boundary for UID verification.

    The legacy `uid` columns are retained for schema compatibility, but new
    writes store an HMAC digest there, never the plaintext UID. Reversible data
    is stored only in `uid_enc`.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create_request(
        self,
        *,
        user_id: int,
        uid: str,
        verification_code: str,
        profile_proof_file_id: str,
        deal_file_ids: list[str],
        counterparty_usernames: list[str],
        status: str = "pending",
        uid_proof_file_id: str | None = None,
        reg_date_proof_file_id: str | None = None,
        extra_proof_file_ids: list[str] | None = None,
    ) -> int:
        normalized_uid = norm_uid(uid)
        digest = uid_hash(normalized_uid)
        encrypted = uid_encrypt(normalized_uid)
        last4 = uid_last4(normalized_uid)
        code = (verification_code or "").strip().upper()
        profile_proof = (profile_proof_file_id or "").strip()
        reg_proof = (reg_date_proof_file_id or "").strip() or profile_proof

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.uid_verification_requests (
                    user_id,
                    uid,
                    uid_hash,
                    uid_enc,
                    uid_last4,
                    challenge_code,
                    verification_code,
                    profile_file_id,
                    profile_proof_file_id,
                    uid_proof_file_id,
                    reg_date_proof_file_id,
                    deal_file_ids,
                    counterparty_usernames,
                    extra_proof_file_ids,
                    status
                )
                VALUES (
                    $1, $2, $2, $3, $4, $5, $5, $6, $6, $7, $8, $9, $10, $11, $12
                )
                RETURNING id
                """,
                int(user_id),
                digest,
                encrypted,
                last4,
                code,
                profile_proof,
                uid_proof_file_id,
                reg_proof,
                list(deal_file_ids or []),
                [str(x).strip().lstrip("@").lower() for x in (counterparty_usernames or [])],
                list(extra_proof_file_ids or []),
                (status or "pending").strip().lower(),
            )
        if not row:
            raise RuntimeError("UID verification request was not created")
        return int(row["id"])

    async def get_verified_uid_for_user(self, user_id: int) -> str | None:
        """Return the verified UID without losing legacy bindings.

        Older verified rows predate ``uid_enc`` and keep the plaintext value in
        ``uid``.  Treating a missing encrypted column as "not verified" made a
        valid binding appear to have vanished after the refactor.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT uid, uid_enc
                FROM public.user_uids
                WHERE user_id=$1 AND status='verified'
                LIMIT 1
                """,
                int(user_id),
            )
        if not row:
            return None

        encrypted = str(row.get("uid_enc") or "").strip()
        if encrypted:
            try:
                return uid_decrypt(encrypted)
            except Exception:
                # Do not erase the verification fact merely because a legacy
                # deployment has a different encryption key.  A plaintext
                # legacy value can still be used safely as a fallback.
                pass

        legacy_uid = str(row.get("uid") or "").strip()
        if legacy_uid:
            # New rows store a 64-character digest in ``uid``.  Returning that
            # as if it were the real UID would be misleading; legacy plaintext
            # values have a different shape and remain recoverable.
            is_digest = len(legacy_uid) == 64 and all(
                ch in "0123456789abcdefABCDEF" for ch in legacy_uid
            )
            if not is_digest:
                return legacy_uid
        return None

    async def get_uid_owner(self, uid: str) -> dict[str, Any] | None:
        digest = uid_hash(uid)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, status, verified_at, verified_by, uid_last4
                FROM public.user_uids
                WHERE uid_hash=$1
                LIMIT 1
                """,
                digest,
            )
        return dict(row) if row else None

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.*, u.username, u.full_name
                FROM public.uid_verification_requests r
                LEFT JOIN public.users u ON u.user_id=r.user_id
                WHERE r.id=$1
                """,
                int(request_id),
            )
            if not row:
                return None
            confirmations = await conn.fetch(
                """
                SELECT *
                FROM public.uid_verification_confirmations
                WHERE request_id=$1
                ORDER BY id
                """,
                int(request_id),
            )
        result = dict(row)
        result["confirmations"] = [dict(item) for item in confirmations]
        return result

    async def revision_flags(self, request_id: int) -> list[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT revision_flags FROM public.uid_verification_requests WHERE id=$1",
                int(request_id),
            )
        return list(row["revision_flags"] or []) if row else []

    async def delete_confirmation_for_counterparty(self, request_id: int, username: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.uid_verification_confirmations WHERE request_id=$1 AND counterparty_username=$2",
                int(request_id),
                (username or "").strip().lower(),
            )

    async def confirmation_request_id(self, confirmation_id: int) -> int | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT request_id FROM public.uid_verification_confirmations WHERE id=$1",
                int(confirmation_id),
            )
        return int(value) if value is not None else None

    async def get_latest_request(self, user_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, status, revision_flags, revision_reason,
                       admin_comment, created_at, decided_at, uid_last4
                FROM public.uid_verification_requests
                WHERE user_id=$1
                ORDER BY id DESC
                LIMIT 1
                """,
                int(user_id),
            )
        return dict(row) if row else None

    async def progress(self, request_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.user_id, r.created_at,
                       count(c.id) FILTER (WHERE c.status = 'confirmed')::int AS confirmed_cnt,
                       count(c.id) FILTER (WHERE c.status IN ('pending', 'confirmed', 'rejected', 'unreachable'))::int AS total_cnt
                FROM public.uid_verification_requests r
                LEFT JOIN public.uid_verification_confirmations c ON c.request_id = r.id
                WHERE r.id = $1
                GROUP BY r.id
                """,
                int(request_id),
            )
        return dict(row) if row else None

    async def approve_request(
        self,
        *,
        request_id: int,
        admin_id: int,
        admin_username: str | None = None,
    ) -> UIDApprovalResult:
        rid = int(request_id)
        aid = int(admin_id)
        if rid <= 0 or aid <= 0:
            return UIDApprovalResult(False, "bad_args")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow(
                    """
                    SELECT id, user_id, uid_hash, uid_enc, uid_last4, status
                    FROM public.uid_verification_requests
                    WHERE id=$1
                    FOR UPDATE
                    """,
                    rid,
                )
                if not req:
                    return UIDApprovalResult(False, "not_found")
                if str(req["status"] or "").lower() != "pending":
                    return UIDApprovalResult(False, "already_processed")

                user_id = int(req["user_id"] or 0)
                digest = str(req["uid_hash"] or "").strip()
                encrypted = str(req["uid_enc"] or "").strip()
                last4 = str(req["uid_last4"] or "").strip()
                if user_id <= 0:
                    return UIDApprovalResult(False, "user_id_empty")
                if not digest or not encrypted:
                    return UIDApprovalResult(False, "uid_encryption_missing", user_id)

                # Integrity check protects against manually corrupted rows.
                try:
                    plain = uid_decrypt(encrypted)
                except Exception:
                    return UIDApprovalResult(False, "uid_decrypt_failed", user_id)
                if uid_hash(plain) != digest:
                    return UIDApprovalResult(False, "uid_integrity_failed", user_id)

                # Serialize approvals for both the user and the UID. Request-row
                # locks alone do not protect two different requests submitted by
                # the same person or two people claiming the same UID.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"uid-user:{user_id}",
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"uid-value:{digest}",
                )

                owner = await conn.fetchrow(
                    """
                    SELECT user_id
                    FROM public.user_uids
                    WHERE uid_hash=$1 OR uid=$1
                    FOR UPDATE
                    """,
                    digest,
                )
                if owner and int(owner["user_id"]) != user_id:
                    conflict_owner = int(owner["user_id"])
                    await self._set_request_status(
                        conn,
                        request_id=rid,
                        status="conflict",
                        admin_id=aid,
                        comment=f"UID already verified for user_id={conflict_owner}",
                    )
                    await self._add_event(
                        conn,
                        request_id=rid,
                        actor_id=aid,
                        actor_username=admin_username,
                        event_type="request_conflict",
                        details={"owner_user_id": conflict_owner},
                    )
                    return UIDApprovalResult(False, f"conflict:{conflict_owner}", user_id)

                inserted = await conn.fetchrow(
                    """
                    INSERT INTO public.user_uids (
                        uid, uid_hash, uid_enc, uid_last4,
                        user_id, status, verified_by, verified_at, updated_at
                    )
                    VALUES ($1, $1, $2, $3, $4, 'verified', $5, now(), now())
                    ON CONFLICT (user_id) DO UPDATE
                    SET uid=EXCLUDED.uid,
                        uid_hash=EXCLUDED.uid_hash,
                        uid_enc=EXCLUDED.uid_enc,
                        uid_last4=EXCLUDED.uid_last4,
                        status='verified',
                        verified_by=EXCLUDED.verified_by,
                        verified_at=now(),
                        updated_at=now()
                    RETURNING user_id
                    """,
                    digest,
                    encrypted,
                    last4,
                    user_id,
                    aid,
                )
                if not inserted:
                    raise RuntimeError("UID verification upsert returned no row")

                changed = await self._set_request_status(
                    conn,
                    request_id=rid,
                    status="approved",
                    admin_id=aid,
                    comment="",
                )
                if not changed:
                    raise RuntimeError("UID request status changed during approval")
                await self._add_event(
                    conn,
                    request_id=rid,
                    actor_id=aid,
                    actor_username=admin_username,
                    event_type="request_approved",
                    details={},
                )
                return UIDApprovalResult(True, user_id=user_id)

    async def reject_request(
        self,
        *,
        request_id: int,
        admin_id: int,
        admin_username: str | None = None,
        comment: str = "",
    ) -> UIDApprovalResult:
        rid = int(request_id)
        aid = int(admin_id)
        if rid <= 0 or aid <= 0:
            return UIDApprovalResult(False, "bad_args")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow(
                    "SELECT user_id, status FROM public.uid_verification_requests WHERE id=$1 FOR UPDATE",
                    rid,
                )
                if not req:
                    return UIDApprovalResult(False, "not_found")
                if str(req["status"] or "").lower() != "pending":
                    return UIDApprovalResult(False, "already_processed")

                changed = await self._set_request_status(
                    conn,
                    request_id=rid,
                    status="rejected",
                    admin_id=aid,
                    comment=(comment or "").strip(),
                )
                if not changed:
                    return UIDApprovalResult(False, "db_failed", int(req["user_id"]))
                await self._add_event(
                    conn,
                    request_id=rid,
                    actor_id=aid,
                    actor_username=admin_username,
                    event_type="request_rejected",
                    details={"comment": (comment or "").strip()},
                )
                return UIDApprovalResult(True, user_id=int(req["user_id"]))

    async def claim_due_reminders(self, *, stage_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        if stage_h <= 0:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH due AS (
                    SELECT r.id AS request_id,
                           r.user_id,
                           count(c.id) FILTER (WHERE c.status='confirmed')::int AS confirmed_cnt
                    FROM public.uid_verification_requests r
                    LEFT JOIN public.uid_verification_confirmations c ON c.request_id=r.id
                    WHERE r.status='pending'
                      AND r.created_at <= now() - make_interval(hours => $1::int)
                      AND r.created_at > now() - make_interval(hours => ($1::int + 24))
                    GROUP BY r.id, r.user_id
                    HAVING count(c.id) FILTER (WHERE c.status='confirmed') < $2
                ), claimed AS (
                    INSERT INTO public.uid_verification_request_reminders(request_id, stage_h)
                    SELECT request_id, $1 FROM due
                    ON CONFLICT DO NOTHING
                    RETURNING request_id
                )
                SELECT due.request_id, due.user_id, due.confirmed_cnt
                FROM due
                JOIN claimed USING (request_id)
                ORDER BY due.request_id
                """,
                int(stage_h),
                int(minimum_confirmations),
            )
        return [dict(row) for row in rows]

    async def expire_due_requests(self, *, ttl_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    WITH confirmation_counts AS (
                        SELECT r.id,
                               count(c.id) FILTER (WHERE c.status='confirmed')::int AS confirmed_cnt
                        FROM public.uid_verification_requests r
                        LEFT JOIN public.uid_verification_confirmations c ON c.request_id=r.id
                        GROUP BY r.id
                    ), due AS (
                        SELECT r.id, r.user_id, cc.confirmed_cnt
                        FROM public.uid_verification_requests r
                        JOIN confirmation_counts cc ON cc.id=r.id
                        WHERE r.status='pending'
                          AND r.created_at <= now() - make_interval(hours => $1::int)
                          AND cc.confirmed_cnt < $2
                        FOR UPDATE OF r SKIP LOCKED
                    ), expired AS (
                        UPDATE public.uid_verification_requests r
                        SET status='expired', decided_at=now(), admin_comment='confirmation timeout'
                        FROM due
                        WHERE r.id=due.id
                        RETURNING r.id, r.user_id
                    )
                    SELECT expired.id AS request_id,
                           expired.user_id,
                           due.confirmed_cnt
                    FROM expired
                    JOIN due ON due.id=expired.id
                    """,
                    int(ttl_h),
                    int(minimum_confirmations),
                )
                ids = [int(row["request_id"]) for row in rows]
                if ids:
                    await conn.execute(
                        """
                        UPDATE public.uid_verification_confirmations
                        SET status='expired', decided_at=now()
                        WHERE request_id=ANY($1::bigint[]) AND status='pending'
                        """,
                        ids,
                    )
                    await conn.execute(
                        """
                        INSERT INTO public.uid_verification_events(
                            request_id, actor_id, actor_username, event_type, details
                        )
                        SELECT unnest($1::bigint[]), NULL, NULL, 'request_expired',
                               '{"reason":"confirmation_timeout"}'::jsonb
                        """,
                        ids,
                    )
        return [dict(row) for row in rows]

    @staticmethod
    async def _set_request_status(
        conn: asyncpg.Connection,
        *,
        request_id: int,
        status: str,
        admin_id: int | None,
        comment: str,
    ) -> bool:
        row = await conn.fetchrow(
            """
            UPDATE public.uid_verification_requests
            SET status=$2,
                decided_at=CASE WHEN $2 IN ('approved','rejected','conflict','expired') THEN now() ELSE decided_at END,
                decided_by=COALESCE($3, decided_by),
                admin_comment=CASE WHEN $4 <> '' THEN $4 ELSE admin_comment END
            WHERE id=$1
            RETURNING id
            """,
            int(request_id),
            status,
            int(admin_id) if admin_id else None,
            comment,
        )
        return bool(row)

    @staticmethod
    async def _add_event(
        conn: asyncpg.Connection,
        *,
        request_id: int,
        actor_id: int | None,
        actor_username: str | None,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO public.uid_verification_events(
                request_id, actor_id, actor_username, event_type, details
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            int(request_id),
            int(actor_id) if actor_id else None,
            actor_username,
            event_type,
            json.dumps(details, ensure_ascii=False),
        )
