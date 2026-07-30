from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from bot.uid_crypto import uid_encrypt, uid_hash, uid_last4


@dataclass(slots=True, frozen=True)
class MasterBanResult:
    owner_user_id: int | None


@dataclass(slots=True, frozen=True)
class MasterUnbanResult:
    uid_removed: bool
    user_removed: bool


class UIDIdentityAdminRepository:
    """Persistence boundary for identity lookup and moderation actions.

    Plain UID values are accepted only at the method boundary. Queries and
    returned moderation payloads use the digest, encrypted value, or last four
    characters, so callers cannot accidentally render a full UID.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lstrip("@").lower()
        if not normalized:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM public.users
                WHERE lower(username)=lower($1)
                LIMIT 1
                """,
                normalized,
            )
        return dict(row) if row else None

    async def get_user_id_by_username(self, username: str) -> int | None:
        normalized = (username or "").strip().lstrip("@").lower()
        if not normalized:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id
                FROM public.users
                WHERE lower(username)=$1
                LIMIT 1
                """,
                normalized,
            )
        return int(row["user_id"]) if row and row.get("user_id") else None

    async def get_username_by_user_id(self, user_id: int) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT username
                FROM public.users
                WHERE user_id=$1
                LIMIT 1
                """,
                int(user_id),
            )
        if not row:
            return None
        return (row.get("username") or "").strip() or None

    async def get_user_basic_info_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lstrip("@").lower()
        if not normalized:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT u.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury,
                       u.created_at AS registered_at,
                       u.pm_opened,
                       u.first_pm_at,
                       u.last_pm_at,
                       EXISTS(
                           SELECT 1 FROM public.admins a WHERE a.user_id=u.user_id
                       ) AS is_admin
                FROM public.users u
                WHERE lower(u.username)=$1
                LIMIT 1
                """,
                normalized,
            )
        return dict(row) if row else None

    async def upsert_uid_ban(
        self,
        uid: str,
        *,
        banned_by: int | None,
        reason: str = "",
        banned_until: datetime | None = None,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await self._upsert_uid_ban(
                conn,
                uid=uid,
                banned_by=banned_by,
                reason=reason,
                banned_until=banned_until,
            )
        return dict(row) if row else {}

    async def remove_uid_ban(self, uid: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                DELETE FROM public.uid_bans
                WHERE uid_hash=$1
                RETURNING uid_hash
                """,
                uid_hash(uid),
            )
        return bool(row)

    async def list_uid_bans(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        only_active: bool = True,
    ) -> list[dict[str, Any]]:
        active_clause = "AND (banned_until IS NULL OR banned_until > now())" if only_active else ""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
                FROM public.uid_bans
                WHERE TRUE {active_clause}
                ORDER BY banned_at DESC
                LIMIT $1 OFFSET $2
                """,
                int(limit),
                int(offset),
            )
        return [dict(row) for row in rows]

    async def get_uid_profile_binding(self, uid: str) -> dict[str, Any] | None:
        digest = uid_hash(uid)
        async with self._pool.acquire() as conn:
            verified = await conn.fetchrow(
                """
                SELECT uu.user_id,
                       u.username,
                       u.full_name,
                       uu.status,
                       uu.verified_at,
                       uu.verified_by,
                       uu.uid_last4
                FROM public.user_uids uu
                LEFT JOIN public.users u ON u.user_id=uu.user_id
                WHERE uu.uid_hash=$1
                LIMIT 1
                """,
                digest,
            )
            request = await conn.fetchrow(
                """
                SELECT r.id,
                       r.user_id,
                       u.username,
                       u.full_name,
                       r.status,
                       r.created_at,
                       r.decided_at,
                       r.uid_last4
                FROM public.uid_verification_requests r
                LEFT JOIN public.users u ON u.user_id=r.user_id
                WHERE r.uid_hash=$1
                ORDER BY r.id DESC
                LIMIT 1
                """,
                digest,
            )
            ban = await conn.fetchrow(
                """
                SELECT uid_hash, uid_last4, reason, banned_at, banned_until, banned_by
                FROM public.uid_bans
                WHERE uid_hash=$1
                  AND (banned_until IS NULL OR banned_until > now())
                LIMIT 1
                """,
                digest,
            )
        if not verified and not request and not ban:
            return None
        return {
            "verified": dict(verified) if verified else None,
            "request": dict(request) if request else None,
            "ban": dict(ban) if ban else None,
            "is_banned": bool(ban),
        }

    async def get_user_id_by_uid_any(self, uid: str) -> int | None:
        digest = uid_hash(uid)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id
                FROM public.user_uids
                WHERE uid_hash=$1
                LIMIT 1
                """,
                digest,
            )
            if not row:
                row = await conn.fetchrow(
                    """
                    SELECT user_id
                    FROM public.uid_verification_requests
                    WHERE uid_hash=$1
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    digest,
                )
        return int(row["user_id"]) if row and row.get("user_id") else None

    async def ban_user(
        self,
        *,
        user_id: int,
        banned_until: datetime,
        reason: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.user_bans(user_id, banned_until, reason)
                VALUES ($1, $2, $3)
                """,
                int(user_id),
                banned_until,
                (reason or "").strip(),
            )

    async def unban_user(self, user_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.user_bans WHERE user_id=$1",
                int(user_id),
            )

    async def list_active_user_bans(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ub.user_id, u.username, ub.banned_until, ub.reason
                FROM public.user_bans ub
                LEFT JOIN public.users u ON u.user_id=ub.user_id
                WHERE ub.banned_until > now()
                ORDER BY ub.banned_until DESC
                LIMIT $1
                """,
                int(limit),
            )
        return [dict(row) for row in rows]

    async def apply_master_ban(
        self,
        *,
        uid: str,
        user_id: int,
        banned_by: int,
        reason: str,
        uid_banned_until: datetime | None,
        user_banned_until: datetime,
    ) -> MasterBanResult:
        digest = uid_hash(uid)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                owner = await conn.fetchrow(
                    """
                    SELECT user_id
                    FROM public.user_uids
                    WHERE uid_hash=$1
                    LIMIT 1
                    FOR UPDATE
                    """,
                    digest,
                )
                await self._upsert_uid_ban(
                    conn,
                    uid=uid,
                    banned_by=banned_by,
                    reason=reason,
                    banned_until=uid_banned_until,
                )
                await conn.execute(
                    "DELETE FROM public.user_bans WHERE user_id=$1",
                    int(user_id),
                )
                await conn.execute(
                    """
                    INSERT INTO public.user_bans(user_id, banned_until, reason)
                    VALUES ($1, $2, $3)
                    """,
                    int(user_id),
                    user_banned_until,
                    (reason or "").strip(),
                )
        owner_id = int(owner["user_id"]) if owner and owner.get("user_id") else None
        return MasterBanResult(owner_user_id=owner_id)

    async def apply_master_unban(self, *, uid: str | None, user_id: int | None) -> MasterUnbanResult:
        user_requested = bool(user_id and int(user_id) > 0)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                uid_row = None
                if uid:
                    uid_row = await conn.fetchrow(
                        """
                        WITH removed AS (
                            DELETE FROM public.uid_bans
                            WHERE uid_hash=$1
                            RETURNING 1
                        )
                        SELECT count(*)::int AS removed_count FROM removed
                        """,
                        uid_hash(uid),
                    )
                if user_requested:
                    await conn.execute(
                        "DELETE FROM public.user_bans WHERE user_id=$1",
                        int(user_id),
                    )
        return MasterUnbanResult(
            uid_removed=bool(uid_row and int(uid_row.get("removed_count") or 0) > 0),
            # The legacy flow reports a valid user target as handled even when
            # there was no active row to delete. Preserve that UI contract.
            user_removed=user_requested,
        )

    async def get_whois_admin_payload(self, *, user_id: int) -> dict[str, Any] | None:
        uid = int(user_id)
        async with self._pool.acquire() as conn:
            try:
                user = await conn.fetchrow(
                    """
                    SELECT u.user_id,
                           u.username,
                           u.full_name,
                           u.is_luxury,
                           u.warnings_count,
                           u.created_at,
                           u.is_trusted,
                           u.pm_opened,
                           u.first_pm_at,
                           u.last_pm_at,
                           u.uid_verif_confirmed_count,
                           u.uid_verif_rejected_count,
                           u.uid_verif_last_confirmed_at,
                           u.uid_verif_last_rejected_at,
                           (a.user_id IS NOT NULL) AS is_admin
                    FROM public.users u
                    LEFT JOIN public.admins a ON a.user_id=u.user_id
                    WHERE u.user_id=$1
                    """,
                    uid,
                )
            except Exception:
                user = await conn.fetchrow(
                    """
                    SELECT u.user_id,
                           u.username,
                           u.full_name,
                           u.is_luxury,
                           u.warnings_count,
                           u.created_at,
                           u.is_trusted,
                           u.pm_opened,
                           u.first_pm_at,
                           u.last_pm_at,
                           (a.user_id IS NOT NULL) AS is_admin
                    FROM public.users u
                    LEFT JOIN public.admins a ON a.user_id=u.user_id
                    WHERE u.user_id=$1
                    """,
                    uid,
                )
            if not user:
                return None

            try:
                lots_posted = int(
                    await conn.fetchval(
                        "SELECT count(*) FROM public.auction_owners WHERE user_id=$1",
                        uid,
                    )
                    or 0
                )
            except Exception:
                lots_posted = 0

            try:
                uid_row = await conn.fetchrow(
                    """
                    SELECT uu.user_id,
                           uu.status,
                           uu.verified_at,
                           uu.verified_by,
                           uu.updated_at,
                           uu.uid_last4,
                           (ub.uid_hash IS NOT NULL) AS is_banned,
                           ub.banned_at,
                           ub.banned_by,
                           ub.banned_until,
                           ub.reason AS ban_reason
                    FROM public.user_uids uu
                    LEFT JOIN public.uid_bans ub
                      ON ub.uid_hash=uu.uid_hash
                     AND (ub.banned_until IS NULL OR ub.banned_until > now())
                    WHERE uu.user_id=$1
                    LIMIT 1
                    """,
                    uid,
                )
                uid_record = dict(uid_row) if uid_row else None
            except Exception:
                uid_record = None

            uid_verification = None
            try:
                request = await conn.fetchrow(
                    """
                    SELECT r.*, u.username, u.full_name
                    FROM public.uid_verification_requests r
                    LEFT JOIN public.users u ON u.user_id=r.user_id
                    WHERE r.user_id=$1
                    ORDER BY r.id DESC
                    LIMIT 1
                    """,
                    uid,
                )
                if request:
                    confirmations = await conn.fetch(
                        """
                        SELECT *
                        FROM public.uid_verification_confirmations
                        WHERE request_id=$1
                        ORDER BY id
                        """,
                        int(request["id"]),
                    )
                    uid_verification = dict(request)
                    uid_verification["confirmations"] = [dict(row) for row in confirmations]
            except Exception:
                uid_verification = None

            try:
                unreachable_row = await conn.fetchrow(
                    """
                    SELECT user_id, reason, last_seen
                    FROM public.unreachable_users
                    WHERE user_id=$1
                    LIMIT 1
                    """,
                    uid,
                )
                unreachable = dict(unreachable_row) if unreachable_row else None
            except Exception:
                unreachable = None

            try:
                ban_row = await conn.fetchrow(
                    """
                    SELECT user_id, banned_until, reason, issued_at
                    FROM public.user_bans
                    WHERE user_id=$1 AND banned_until > now()
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """,
                    uid,
                )
                user_ban = dict(ban_row) if ban_row else None
            except Exception:
                user_ban = None

        uid_in_blacklist = bool((uid_record or {}).get("is_banned"))
        user_in_blacklist = bool(user_ban)
        return {
            "user": dict(user),
            "lots_posted": lots_posted,
            "uid_record": uid_record,
            "uid_verif": uid_verification,
            "unreachable": unreachable,
            "user_ban": user_ban,
            "uid_in_blacklist": uid_in_blacklist,
            "user_in_blacklist": user_in_blacklist,
            "in_blacklist": bool(uid_in_blacklist or user_in_blacklist),
        }

    @staticmethod
    async def _upsert_uid_ban(
        conn: asyncpg.Connection,
        *,
        uid: str,
        banned_by: int | None,
        reason: str,
        banned_until: datetime | None,
    ) -> asyncpg.Record | None:
        digest = uid_hash(uid)
        return await conn.fetchrow(
            """
            INSERT INTO public.uid_bans(
                uid, uid_hash, uid_enc, uid_last4,
                reason, banned_by, banned_until, banned_at
            )
            VALUES ($1, $1, $2, $3, NULLIF($4, ''), $5, $6, now())
            ON CONFLICT (uid)
            DO UPDATE SET uid_hash=EXCLUDED.uid_hash,
                          uid_enc=EXCLUDED.uid_enc,
                          uid_last4=EXCLUDED.uid_last4,
                          reason=EXCLUDED.reason,
                          banned_by=EXCLUDED.banned_by,
                          banned_until=EXCLUDED.banned_until,
                          banned_at=now()
            RETURNING uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
            """,
            digest,
            uid_encrypt(uid),
            uid_last4(uid),
            (reason or "").strip(),
            int(banned_by) if banned_by is not None else None,
            banned_until,
        )
