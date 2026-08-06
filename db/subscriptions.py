"""Compatibility facade for subscription queries with delivery eligibility enforced."""

from __future__ import annotations

from typing import Any, List

from db import subscriptions_impl as _impl


@_impl.require_db_pool
async def get_users_with_pref(pref: str) -> List[int]:
    """Return users who enabled a notification and remain globally reachable."""

    column = _impl.ALLOWED_PREFS.get(pref)
    if not column:
        return []
    query = f"""
        SELECT s.user_id
        FROM settings s
        JOIN users u ON u.user_id = s.user_id
        LEFT JOIN unreachable_users uu ON uu.user_id = s.user_id
        WHERE COALESCE(s.{column}, TRUE) = TRUE
          AND uu.user_id IS NULL
          AND COALESCE(u.is_subscribed, TRUE) = TRUE
          AND COALESCE(u.pm_opened, FALSE) = TRUE
    """
    async with _impl.db_pool.acquire() as connection:
        rows = await connection.fetch(query)
    return [int(row["user_id"]) for row in rows]


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))


_impl_exports = getattr(_impl, "__all__", None)
if _impl_exports is None:
    _impl_exports = tuple(name for name in dir(_impl) if not name.startswith("_"))
__all__ = list(dict.fromkeys([*_impl_exports, "get_users_with_pref"]))
