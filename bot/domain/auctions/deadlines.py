from __future__ import annotations

from datetime import datetime, timedelta

from bot.core.time import ensure_utc, utc_now

_WINNER_GRACE = timedelta(minutes=1)


def winner_deadline_reached(
    end_time: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the winner announcement grace period has elapsed.

    PostgreSQL ``timestamptz`` values arrive as timezone-aware datetimes.
    Legacy rows can still contain naive Moscow wall-clock values.  Both are
    normalized to UTC before comparison.
    """

    if end_time is None:
        return False

    current_utc = ensure_utc(now) if now is not None else utc_now()
    deadline_utc = ensure_utc(end_time) + _WINNER_GRACE
    return current_utc >= deadline_utc
