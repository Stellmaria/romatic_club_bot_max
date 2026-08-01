"""Unified administrator and owner access checks for Telegram handlers.

Administrators are configured from two supported sources:

* ``ADMINS`` and ``ADMINS_OWNERS`` in the process environment;
* rows in the PostgreSQL ``admins`` table.

Owner-only operations deliberately use only ``ADMINS_OWNERS``. Permanent
passwords sent through Telegram are not an authorization factor.
"""

from __future__ import annotations

from bot.core.legacy_config import legacy_config
from db.admin import is_admin as _is_database_admin


def configured_owner_ids() -> frozenset[int]:
    """Return every owner ID configured through the environment."""

    return frozenset(int(value) for value in legacy_config.ADMINS_OWNERS)


def configured_admin_ids() -> frozenset[int]:
    """Return every administrator ID configured through the environment."""

    return frozenset(int(value) for value in (*legacy_config.ADMINS, *legacy_config.ADMINS_OWNERS))


def is_owner_user(user_id: int | None) -> bool:
    """Authorize an owner exclusively by Telegram user ID."""

    if user_id is None:
        return False
    return int(user_id) in configured_owner_ids()


async def is_admin_user(user_id: int | None) -> bool:
    """Authorize an administrator from either environment or PostgreSQL."""

    if user_id is None:
        return False

    normalized = int(user_id)
    if normalized in configured_admin_ids():
        return True

    return bool(await _is_database_admin(normalized))


__all__ = [
    "configured_admin_ids",
    "configured_owner_ids",
    "is_admin_user",
    "is_owner_user",
]
