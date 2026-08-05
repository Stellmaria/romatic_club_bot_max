"""Service boundary for userbot schedule review handlers."""

from __future__ import annotations

from datetime import date
from typing import Any

from db.schedule_setup import (
    get_preview_target,
    get_publication_review,
    set_publication_review_status,
)


async def schedule_review_snapshot(
    target_date: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return await get_preview_target(), await get_publication_review(target_date)


async def get_schedule_review(target_date: date) -> dict[str, Any] | None:
    return await get_publication_review(target_date)


async def get_schedule_review_target() -> dict[str, Any] | None:
    return await get_preview_target()


async def decide_schedule_review(
    target_date: date,
    *,
    approved: bool,
    reviewed_by: int,
) -> None:
    await set_publication_review_status(
        target_date,
        status="approved" if approved else "rejected",
        reviewed_by=int(reviewed_by),
    )


__all__ = [
    "decide_schedule_review",
    "get_schedule_review",
    "get_schedule_review_target",
    "schedule_review_snapshot",
]
