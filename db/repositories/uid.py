from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from datetime import date as _date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import asyncpg

from bot.repositories.uid_verification import UIDVerificationRepository
from bot.uid_crypto import (
    mask_uid,
    mask_uid_by_last4,
    norm_uid,
    uid_decrypt,
    uid_encrypt,
    uid_hash,
    uid_last4,
)
from db.core import (
    close_db,
    db_pool,
    execute,
    fetch,
    fetchall,
    fetchrow,
    fetchval,
    get_db_pool,
    logger,
    require_db_pool,
)
from db.repositories._compat import (
    get_user_by_username,
)

"""Uid persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    '_mask_uid',
    'upsert_uid_ban',
    'remove_uid_ban',
    'get_uid_ban',
    'list_uid_bans',
    'is_identity_blocked',
    '_normalize_username',
    '_norm_uid',
    'get_user_verified_uid',
    'get_uid_owner',
    'is_uid_banned',
    'is_user_uid_banned',
    '_is_user_uid_verified',
    '_users_uid_verification_counts',
    'get_uid_verification_request',
    'get_verified_uid_for_user',
    'create_uid_verification_request',
    'add_uid_verification_confirmation',
    'set_uid_verification_confirmation_message',
    '_set_uid_verification_confirmation_status_impl',
    'list_uid_verification_requests',
    'get_uid_verification_confirmation',
    'get_uid_profile_binding',
    'get_user_id_by_uid_any',
    'is_user_banned_now',
    'clear_uid_verification_request_revision',
    'set_uid_verification_request_profile_proof',
    'set_uid_verification_request_uid_proof',
    'set_uid_verification_request_reg_date_proof',
    'replace_uid_verification_request_extra_proofs',
    'set_uid_verification_request_deal_media',
    'set_uid_verification_request_deal_username',
    'add_uid_verification_event',
    'list_uid_verification_events',
    'set_uid_verification_confirmation_status',
    'mark_uid_verification_request_status',
    'approve_uid_verification_request',
    'reject_uid_verification_request',
    'set_uid_verification_request_revision',
    'update_uid_verification_confirmation_status',
]

def _mask_uid(uid: str) -> str:
    s = (uid or "").strip()
    if len(s) <= 8:
        return s
    return f"{s[:4]}…{s[-4:]}"

@require_db_pool
async def upsert_uid_ban(
        uid: str,
        *,
        banned_by: int | None,
        reason: str = "",
        banned_until: Optional[datetime] = None,
) -> dict:
    digest = uid_hash(uid)
    row = await fetchrow(
        """
        INSERT INTO public.uid_bans(
            uid, uid_hash, uid_enc, uid_last4,
            reason, banned_by, banned_until, banned_at
        )
        VALUES ($1, $1, $2, $3, NULLIF($4, ''), $5, $6, NOW())
        ON CONFLICT (uid)
        DO UPDATE SET uid_hash=EXCLUDED.uid_hash,
                      uid_enc=EXCLUDED.uid_enc,
                      uid_last4=EXCLUDED.uid_last4,
                      reason=EXCLUDED.reason,
                      banned_by=EXCLUDED.banned_by,
                      banned_until=EXCLUDED.banned_until,
                      banned_at=NOW()
        RETURNING uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
        """,
        digest,
        uid_encrypt(uid),
        uid_last4(uid),
        (reason or "").strip(),
        int(banned_by) if banned_by is not None else None,
        banned_until,
    )
    return dict(row) if row else {}

@require_db_pool
async def remove_uid_ban(uid: str) -> bool:
    row = await fetchrow(
        "DELETE FROM public.uid_bans WHERE uid_hash=$1 RETURNING uid_hash",
        uid_hash(uid),
    )
    return bool(row)

@require_db_pool
async def get_uid_ban(uid: str) -> Optional[dict]:
    row = await fetchrow(
        """
        SELECT uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
        FROM public.uid_bans
        WHERE uid_hash=$1
        """,
        uid_hash(uid),
    )
    return dict(row) if row else None

@require_db_pool
async def list_uid_bans(*, limit: int = 50, offset: int = 0, only_active: bool = True) -> list[dict]:
    where = "WHERE (banned_until IS NULL OR banned_until > NOW())" if only_active else ""
    rows = await fetch(
        f"""
        SELECT uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
        FROM public.uid_bans
        {where}
        ORDER BY banned_at DESC
        LIMIT $1 OFFSET $2
        """,
        int(limit),
        int(offset),
    )
    return [dict(r) for r in (rows or [])]

@require_db_pool
async def is_identity_blocked(user_id: int | None = None, username: str | None = None, uid: str | None = None) -> tuple[bool, str | None]:
    # 1. бан по user_id
    if user_id:
        if await is_user_banned_now(int(user_id)):
            return True, "user_id"

        # если у юзера уже есть verified uid — проверяем и его
        try:
            verified_uid = await get_user_verified_uid(int(user_id))
        except Exception:
            verified_uid = None

        if verified_uid and await is_uid_banned(str(verified_uid)):
            return True, "verified_uid"

    # 2. бан по username -> если username есть в базе, проверим user_id
    uname = (username or "").strip().lstrip("@")
    if uname:
        user = await get_user_by_username(uname)
        if user:
            uid_num = int(user["user_id"])
            if await is_user_banned_now(uid_num):
                return True, "username_user_id"

            try:
                verified_uid = await get_user_verified_uid(uid_num)
            except Exception:
                verified_uid = None

            if verified_uid and await is_uid_banned(str(verified_uid)):
                return True, "username_verified_uid"

    # 3. бан по UID, который пытаются ввести
    if uid:
        if await is_uid_banned(str(uid)):
            return True, "uid"

    return False, None

def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    u = str(username).strip().lstrip("@")
    return u or None

def _norm_uid(uid: str) -> str:
    return (uid or "").strip().replace(" ", "")

async def get_user_verified_uid(user_id: int) -> Optional[str]:
    repository = UIDVerificationRepository(await get_db_pool())
    return await repository.get_verified_uid_for_user(int(user_id))

async def get_uid_owner(uid: str) -> Optional[dict]:
    repository = UIDVerificationRepository(await get_db_pool())
    return await repository.get_uid_owner(uid)

@require_db_pool
async def is_uid_banned(uid: str) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.uid_bans
        WHERE uid_hash=$1
          AND (banned_until IS NULL OR banned_until > NOW())
        """,
        uid_hash(uid),
    )
    return bool(row)

@require_db_pool
async def is_user_uid_banned(user_id: int) -> bool:
    uid = await get_user_verified_uid(int(user_id))
    if not uid:
        return False
    return await is_uid_banned(uid)

async def _is_user_uid_verified(user_id: int | None) -> bool:
    if not user_id:
        return False

    row = await fetchrow(
        """
        SELECT 1
        FROM public.user_uids
        WHERE user_id = $1
          AND status = 'verified'
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)

async def _users_uid_verification_counts(user_ids: list[int] | None) -> tuple[int, int, bool]:
    ids = [int(x) for x in (user_ids or []) if x]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0, 0, False

    row = await fetchrow(
        """
        SELECT COUNT(*)::int                                                               AS total,
               COALESCE(SUM(CASE WHEN uu.status = 'verified' THEN 1 ELSE 0 END), 0)::int AS verified_cnt
        FROM unnest($1::bigint[]) AS u(user_id)
                 LEFT JOIN public.user_uids uu
                           ON uu.user_id = u.user_id
        """,
        ids,
    ) or {}

    total = int(row.get("total") or 0)
    verified_cnt = int(row.get("verified_cnt") or 0)
    return total, verified_cnt, (total > 0 and verified_cnt == total)

@require_db_pool
async def get_uid_verification_request(
        request_id: int | None = None,
        *,
        req_id: int | None = None,
) -> Optional[dict]:
    rid = int(request_id or req_id or 0)
    if rid <= 0:
        return None
    repository = UIDVerificationRepository(await get_db_pool())
    return await repository.get_request(rid)

async def get_verified_uid_for_user(user_id: int) -> Optional[str]:
    repository = UIDVerificationRepository(await get_db_pool())
    return await repository.get_verified_uid_for_user(int(user_id))

async def create_uid_verification_request(
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
    repository = UIDVerificationRepository(await get_db_pool())
    return await repository.create_request(
        user_id=user_id,
        uid=uid,
        verification_code=verification_code,
        profile_proof_file_id=profile_proof_file_id,
        deal_file_ids=deal_file_ids,
        counterparty_usernames=counterparty_usernames,
        status=status,
        uid_proof_file_id=uid_proof_file_id,
        reg_date_proof_file_id=reg_date_proof_file_id,
        extra_proof_file_ids=extra_proof_file_ids,
    )

@require_db_pool
async def add_uid_verification_confirmation(
        *,
        request_id: int,
        counterparty_user_id: int,
        counterparty_username: str,
) -> dict:
    uname = (counterparty_username or "").strip().lstrip("@").lower()
    row = await fetchrow(
        """
        WITH ins AS (
            INSERT INTO uid_verification_confirmations
                (request_id, counterparty_user_id, counterparty_username, status)
                VALUES ($1, $2, $3, 'pending')
                ON CONFLICT DO NOTHING
                RETURNING *)
        SELECT *
        FROM ins
        UNION ALL
        SELECT *
        FROM uid_verification_confirmations
        WHERE request_id = $1
          AND lower(counterparty_username) = lower($3)
        LIMIT 1
        """,
        int(request_id),
        int(counterparty_user_id),
        uname,
    )
    return dict(row)

async def set_uid_verification_confirmation_message(
        conf_id: int,
        chat_id: int,
        message_id: int,
) -> None:
    await execute(
        """
        UPDATE uid_verification_confirmations
        SET message_chat_id=$2,
            message_id=$3
        WHERE id = $1
        """,
        int(conf_id), int(chat_id), int(message_id),
    )

async def _set_uid_verification_confirmation_status_impl(
        *,
        conf_id: int | None = None,
        confirmation_id: int | None = None,
        status: str,
) -> bool:
    status = (status or "").strip().lower()
    _id = int(conf_id or confirmation_id or 0)

    allowed = ("confirmed", "rejected", "unreachable", "expired")
    if not _id or status not in allowed:
        return False

    row = await fetchrow(
        """
        UPDATE uid_verification_confirmations
        SET status=$2,
            decided_at=now()
        WHERE id = $1
          AND status = 'pending'
        RETURNING counterparty_user_id
        """,
        _id,
        status,
    )
    if not row:
        return False

    cp_id = row["counterparty_user_id"]

    # статистику пишем только по confirmed/rejected
    if cp_id and status in ("confirmed", "rejected"):
        if status == "confirmed":
            await execute(
                """
                UPDATE users
                SET uid_verif_confirmed_count   = COALESCE(uid_verif_confirmed_count, 0) + 1,
                    uid_verif_last_confirmed_at = now()
                WHERE user_id = $1
                """,
                int(cp_id),
            )
        else:
            await execute(
                """
                UPDATE users
                SET uid_verif_rejected_count   = COALESCE(uid_verif_rejected_count, 0) + 1,
                    uid_verif_last_rejected_at = now()
                WHERE user_id = $1
                """,
                int(cp_id),
            )

    return True

async def list_uid_verification_requests(status: str, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT r.*,
               u.username,
               u.full_name,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status = 'confirmed') AS confirmed_cnt,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status = 'rejected')  AS rejected_cnt,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id)    AS total_cnt
        FROM uid_verification_requests r
                 LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.status = $1
        ORDER BY r.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        status, int(limit), int(offset),
    )
    return [dict(x) for x in rows]

async def get_uid_verification_confirmation(*, confirmation_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT *
        FROM public.uid_verification_confirmations
        WHERE id = $1
        """,
        int(confirmation_id),
    )
    return dict(row) if row else None

@require_db_pool
async def get_uid_profile_binding(uid: str) -> dict | None:
    """
    Для админского /whois по UID:
    - есть ли verified-привязка в user_uids
    - есть ли заявка в uid_verification_requests
    - есть ли UID-ban
    """
    h = uid_hash(uid)

    verified = await fetchrow(
        """
        SELECT uu.user_id,
               u.username,
               u.full_name,
               uu.status,
               uu.verified_at,
               uu.verified_by,
               uu.uid_last4
        FROM public.user_uids uu
        LEFT JOIN public.users u ON u.user_id = uu.user_id
        WHERE uu.uid_hash = $1
        LIMIT 1
        """,
        h,
    )

    request = await fetchrow(
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
        LEFT JOIN public.users u ON u.user_id = r.user_id
        WHERE r.uid_hash = $1
        ORDER BY r.id DESC
        LIMIT 1
        """,
        h,
    )

    ban = await fetchrow(
        """
        SELECT uid_hash,
               uid_last4,
               reason,
               banned_at,
               banned_until,
               banned_by
        FROM public.uid_bans
        WHERE uid_hash = $1
          AND (banned_until IS NULL OR banned_until > NOW())
        LIMIT 1
        """,
        h,
    )

    if not verified and not request and not ban:
        return None

    return {
        "verified": dict(verified) if verified else None,
        "request": dict(request) if request else None,
        "ban": dict(ban) if ban else None,
        "is_banned": bool(ban),
    }

@require_db_pool
async def get_user_id_by_uid_any(uid: str) -> Optional[int]:
    """
    Ищем пользователя:
    1) среди verified UID в user_uids
    2) если не нашли — среди заявок uid_verification_requests по uid_hash
    """
    h = uid_hash(uid)

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.user_uids
        WHERE uid_hash = $1
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.uid_verification_requests
        WHERE uid_hash = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    return None

@require_db_pool
async def is_user_banned_now(user_id: int) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.user_bans
        WHERE user_id = $1
          AND banned_until > NOW()
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)

@require_db_pool
async def clear_uid_verification_request_revision(request_id: int) -> bool:
    if not request_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status               = 'pending',
            revision_reason      = NULL,
            revision_flags       = '{}'::text[],
            revision_by          = NULL,
            revision_by_username = NULL,
            revision_at          = NULL
        WHERE id = $1
          AND status = 'revision'
        RETURNING 1 AS ok
        """,
        int(request_id),
    )
    return bool(row)

@require_db_pool
async def set_uid_verification_request_profile_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET profile_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)

@require_db_pool
async def set_uid_verification_request_uid_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET uid_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)

@require_db_pool
async def set_uid_verification_request_reg_date_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET reg_date_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)

@require_db_pool
async def replace_uid_verification_request_extra_proofs(request_id: int, packed_file_ids: list[str]) -> bool:
    if not request_id:
        return False
    packed_file_ids = [str(x).strip() for x in (packed_file_ids or []) if str(x).strip()]
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET extra_proof_file_ids = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        packed_file_ids,
    )
    return bool(row)

@require_db_pool
async def set_uid_verification_request_deal_media(request_id: int, idx: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id or idx <= 0:
        return False

    row = await fetchrow(
        "SELECT deal_file_ids FROM public.uid_verification_requests WHERE id=$1",
        int(request_id),
    )
    if not row:
        return False

    arr = list(row.get("deal_file_ids") or [])
    if idx - 1 >= len(arr):
        return False

    arr[idx - 1] = str(packed_file_id)

    ok = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET deal_file_ids = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        arr,
    )
    return bool(ok)

@require_db_pool
async def set_uid_verification_request_deal_username(request_id: int, idx: int, username: str) -> bool:
    if not request_id or idx <= 0:
        return False

    username = (username or '').strip().lstrip('@').lower()
    if not username:
        return False

    row = await fetchrow(
        "SELECT counterparty_usernames FROM public.uid_verification_requests WHERE id=$1",
        int(request_id),
    )
    if not row:
        return False

    arr = list(row.get("counterparty_usernames") or [])
    if idx - 1 >= len(arr):
        return False

    arr[idx - 1] = username

    ok = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET counterparty_usernames = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        arr,
    )
    return bool(ok)

async def add_uid_verification_event(
        request_id: int,
        *,
        actor_id: Optional[int] = None,
        actor_username: Optional[str] = None,
        event_type: str,
        details_json: Optional[dict[str, Any]] = None,
) -> bool:
    try:
        payload = json.dumps(details_json or {}, ensure_ascii=False)
        await execute(
            """
            INSERT INTO public.uid_verification_events(request_id, actor_id, actor_username, event_type, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            int(request_id),
            int(actor_id) if actor_id is not None else None,
            str(actor_username) if actor_username else None,
            str(event_type),
            payload,
        )
        return True
    except Exception:
        return False

async def list_uid_verification_events(request_id: int, *, limit: int = 30) -> list[dict]:
    try:
        rows = await fetch(
            """
            SELECT actor_id, actor_username, event_type, details, created_at
            FROM public.uid_verification_events
            WHERE request_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            int(request_id),
            int(limit),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []

async def set_uid_verification_confirmation_status(  # type: ignore[override]
        confirmation_id: int | None = None,
        status: str | None = None,
        decided_by: int | None = None,
        decided_by_username: str | None = None,
        *,
        conf_id: int | None = None,
) -> bool:
    """Совместимая обёртка.

    В БД НЕТ decided_by / decided_by_username для confirmations, поэтому эти параметры
    просто принимаем и игнорируем, чтобы не падать TypeError-ами.
    """
    cid = int(confirmation_id or conf_id or 0)
    st = (status or "").strip().lower()
    if cid <= 0 or st not in {"confirmed", "rejected", "unreachable", "expired"}:
        return False
    return await _set_uid_verification_confirmation_status_impl(confirmation_id=cid, status=st)

async def mark_uid_verification_request_status(  # type: ignore[override]
        request_id: int,
        status: str,
        admin_id: int | None = None,
        admin_username: str | None = None,
        comment: str | None = None,
) -> bool:
    """Меняет статус заявки (requests) и пишет событие.

    В БД НЕТ decided_by_username, поэтому username идёт только в события.
    """
    rid = int(request_id or 0)
    st = (status or "").strip().lower()
    if rid <= 0 or not st:
        return False

    cmt = (comment or "").strip()

    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status=$2,
            decided_at=CASE WHEN $2 IN ('approved', 'rejected', 'conflict', 'expired') THEN now() ELSE decided_at END,
            decided_by=COALESCE($3, decided_by),
            admin_comment=CASE WHEN $4 <> '' THEN $4 ELSE admin_comment END
        WHERE id = $1
        RETURNING id
        """,
        rid,
        st,
        int(admin_id) if admin_id else None,
        cmt,
    )
    ok = bool(row)

    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=admin_id,
                actor_username=admin_username,
                event_type="request_status_changed",
                details_json={"status": st, "comment": cmt},
            )
        except Exception:
            pass

    return ok

async def approve_uid_verification_request(
        request_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
        *,
        req_id: int | None = None,
) -> tuple[bool, str]:
    repository = UIDVerificationRepository(await get_db_pool())
    result = await repository.approve_request(
        request_id=int(request_id or req_id or 0),
        admin_id=int(admin_id or 0),
        admin_username=admin_username,
    )
    return result.ok, result.code

async def reject_uid_verification_request(
        request_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
        reason: str | None = None,
        comment: str | None = None,
        admin_comment: str | None = None,
        *,
        req_id: int | None = None,
) -> tuple[bool, str]:
    repository = UIDVerificationRepository(await get_db_pool())
    result = await repository.reject_request(
        request_id=int(request_id or req_id or 0),
        admin_id=int(admin_id or 0),
        admin_username=admin_username,
        comment=(admin_comment or reason or comment or "").strip(),
    )
    return result.ok, result.code

async def set_uid_verification_request_revision(  # type: ignore[override]
        request_id: int | None = None,
        moderator_id: int | None = None,
        moderator_username: str | None = None,
        reason: str | None = None,
        flags: list[str] | None = None,
        *,
        req_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
) -> bool:
    """Перевод заявки в 'revision' (нужно исправить).

    В БД НЕТ revision_completed_at, поэтому используем:
      - revision_requested_at (когда запросили правки)
      - revision_returned_at (когда пользователь вернул исправления)
      - revision_at (когда админ подтвердил возврат/закрыл правки)
    """
    rid = int(request_id or req_id or 0)
    aid = int(moderator_id or admin_id or 0)
    auser = moderator_username or admin_username
    rsn = (reason or "").strip()
    flg = flags or []

    if rid <= 0 or aid <= 0:
        return False

    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status='revision',
            revision_flags=$2,
            revision_reason=$3,
            revision_requested_at=now(),
            revision_at=NULL,
            revision_returned_at=NULL,
            revision_by=$4,
            revision_by_username=$5
        WHERE id = $1
          AND status IN ('pending', 'revision')
        RETURNING id
        """,
        rid,
        flg,
        rsn if rsn else None,
        aid,
        auser,
    )
    ok = bool(row)

    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=aid,
                actor_username=auser,
                event_type="request_revision_required",
                details_json={"flags": flg, "reason": rsn},
            )
        except Exception:
            pass

    return ok

async def update_uid_verification_confirmation_status(
        confirmation_id: int,
        status: str,
        decided_by: int | None = None,
        decided_by_username: str | None = None,
) -> bool:
    """
    Back-compat alias: часть кода/импортов ждала update_*,
    а реальная функция у нас set_uid_verification_confirmation_status().
    """
    return await set_uid_verification_confirmation_status(
        confirmation_id=int(confirmation_id),
        status=str(status),
        decided_by=decided_by,
        decided_by_username=decided_by_username,
    )

