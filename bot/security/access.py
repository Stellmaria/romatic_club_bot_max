"""Centralized legacy administrator authorization checks.

Telegram-ID RBAC is preferred. The shared secret remains only for backwards
compatibility and is deliberately disabled when no non-blank value is set.
"""

from __future__ import annotations

import hmac
from collections.abc import Collection

from bot.core.settings import ADMIN_SECRET, ADMINS_OWNERS


def admin_secret_matches(
    candidate: str | None,
    *,
    configured_secret: str | None = None,
) -> bool:
    expected = ADMIN_SECRET if configured_secret is None else configured_secret
    expected = (expected or "").strip()
    supplied = (candidate or "").strip()
    return bool(expected) and bool(supplied) and hmac.compare_digest(supplied, expected)


def is_owner_or_valid_secret(
    user_id: int | None,
    candidate: str | None,
    *,
    owner_ids: Collection[int] | None = None,
    configured_secret: str | None = None,
) -> bool:
    owners = ADMINS_OWNERS if owner_ids is None else owner_ids
    return (user_id is not None and user_id in owners) or admin_secret_matches(
        candidate,
        configured_secret=configured_secret,
    )
