"""Persistence recovery for legacy UID verification requests."""

from __future__ import annotations

import json
import re
from typing import Literal

from bot.uid_crypto import norm_uid, uid_decrypt, uid_encrypt, uid_hash, uid_last4
from db.core import get_db_pool

UIDRecoveryState = Literal[
    "ready",
    "needs_uid",
    "not_found",
    "forbidden",
    "wrong_status",
    "invalid",
]

_UID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
_CODE_RE = re.compile(r"^MX-[0-9]{5}$", re.IGNORECASE)


def _valid_uid(value: str | None) -> str | None:
    normalized = norm_uid(value or "")
    return normalized if _UID_RE.fullmatch(normalized) else None


async def _lock_revision_request(
    conn: object,
    request_id: int,
    *,
    user_id: int,
) -> UIDRecoveryState:
    row = await conn.fetchrow(  # type: ignore[attr-defined]
        """
        SELECT user_id, status
        FROM public.uid_verification_requests
        WHERE id=$1
        FOR UPDATE
        """,
        int(request_id),
    )
    if not row:
        return "not_found"
    if int(row["user_id"] or 0) != int(user_id):
        return "forbidden"
    if str(row["status"] or "").strip().lower() != "revision":
        return "wrong_status"
    return "ready"


async def ensure_request_uid(
    request_id: int,
    *,
    expected_user_id: int | None,
    allowed_statuses: set[str],
) -> UIDRecoveryState:
    """Repair a legacy plaintext UID row and verify that approval can decrypt it."""

    pool = await get_db_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT user_id, uid, uid_hash, uid_enc, uid_last4, status
            FROM public.uid_verification_requests
            WHERE id=$1
            FOR UPDATE
            """,
            int(request_id),
        )
        if not row:
            return "not_found"

        user_id = int(row["user_id"] or 0)
        if expected_user_id is not None and user_id != int(expected_user_id):
            return "forbidden"

        status = str(row["status"] or "").strip().lower()
        if status not in allowed_statuses:
            return "wrong_status"

        encrypted = str(row["uid_enc"] or "").strip()
        plain: str | None = None
        if encrypted:
            try:
                plain = _valid_uid(uid_decrypt(encrypted))
            except Exception:  # noqa: BLE001
                plain = None

        if plain is None:
            plain = _valid_uid(str(row["uid"] or ""))
            if plain is None:
                return "needs_uid"
            encrypted = uid_encrypt(plain)

        digest = uid_hash(plain)
        last4 = uid_last4(plain)
        stored_uid = str(row["uid"] or "").strip()
        stored_hash = str(row["uid_hash"] or "").strip()
        stored_last4 = str(row["uid_last4"] or "").strip()

        if (
            stored_uid != digest
            or stored_hash != digest
            or not str(row["uid_enc"] or "").strip()
            or stored_last4 != last4
        ):
            await conn.execute(
                """
                UPDATE public.uid_verification_requests
                SET uid=$2,
                    uid_hash=$2,
                    uid_enc=$3,
                    uid_last4=$4
                WHERE id=$1
                """,
                int(request_id),
                digest,
                encrypted,
                last4,
            )
        return "ready"


async def replace_revision_uid(
    request_id: int,
    *,
    user_id: int,
    uid: str,
) -> UIDRecoveryState:
    """Persist a replacement UID only for the owner of a revision request."""

    normalized = _valid_uid(uid)
    if normalized is None:
        return "invalid"

    digest = uid_hash(normalized)
    encrypted = uid_encrypt(normalized)
    last4 = uid_last4(normalized)

    pool = await get_db_pool()
    async with pool.acquire() as conn, conn.transaction():
        state = await _lock_revision_request(
            conn,
            request_id,
            user_id=user_id,
        )
        if state != "ready":
            return state

        await conn.execute(
            """
            UPDATE public.uid_verification_requests
            SET uid=$2,
                uid_hash=$2,
                uid_enc=$3,
                uid_last4=$4
            WHERE id=$1
            """,
            int(request_id),
            digest,
            encrypted,
            last4,
        )
    return "ready"


async def prepare_revision_profile(
    request_id: int,
    *,
    user_id: int,
    verification_code: str,
) -> UIDRecoveryState:
    """Store a fresh challenge code before accepting a replacement profile proof."""

    code = str(verification_code or "").strip().upper()
    if not _CODE_RE.fullmatch(code):
        return "invalid"

    pool = await get_db_pool()
    async with pool.acquire() as conn, conn.transaction():
        state = await _lock_revision_request(
            conn,
            request_id,
            user_id=user_id,
        )
        if state != "ready":
            return state

        await conn.execute(
            """
            UPDATE public.uid_verification_requests
            SET verification_code=$2
            WHERE id=$1
            """,
            int(request_id),
            code,
        )
    return "ready"


async def replace_revision_profile(
    request_id: int,
    *,
    user_id: int,
    packed_file_id: str,
) -> UIDRecoveryState:
    """Replace the profile proof only for the owner of an active revision."""

    packed = str(packed_file_id or "").strip()
    if not packed:
        return "invalid"

    pool = await get_db_pool()
    async with pool.acquire() as conn, conn.transaction():
        state = await _lock_revision_request(
            conn,
            request_id,
            user_id=user_id,
        )
        if state != "ready":
            return state

        await conn.execute(
            """
            UPDATE public.uid_verification_requests
            SET profile_proof_file_id=$2
            WHERE id=$1
            """,
            int(request_id),
            packed,
        )
    return "ready"


async def save_revision_other_response(
    request_id: int,
    *,
    user_id: int,
    response: str,
) -> UIDRecoveryState:
    """Persist the user's free-form revision response in the immutable event log."""

    text = str(response or "").strip()
    if not text:
        return "invalid"

    payload = json.dumps(
        {"flag": "other", "response": text},
        ensure_ascii=False,
    )

    pool = await get_db_pool()
    async with pool.acquire() as conn, conn.transaction():
        state = await _lock_revision_request(
            conn,
            request_id,
            user_id=user_id,
        )
        if state != "ready":
            return state

        await conn.execute(
            """
            INSERT INTO public.uid_verification_events(
                request_id,
                actor_id,
                actor_username,
                event_type,
                details
            )
            VALUES ($1, $2, NULL, 'revision_response', $3::jsonb)
            """,
            int(request_id),
            int(user_id),
            payload,
        )
    return "ready"


__all__ = [
    "UIDRecoveryState",
    "ensure_request_uid",
    "prepare_revision_profile",
    "replace_revision_profile",
    "replace_revision_uid",
    "save_revision_other_response",
]
