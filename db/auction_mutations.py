"""Narrow auction mutations retained for legacy Telegram flows."""

from __future__ import annotations

from typing import Any

from db.core import execute


_ALLOWED_LOT_FIELDS = frozenset(
    {
        "comment",
        "currency",
        "discussion_message_id",
        "end_time",
        "start_price",
        "start_time",
    }
)


async def update_auction_status(auction_id: int, new_status: str) -> None:
    """Update one auction status without masking persistence failures."""

    await execute(
        "UPDATE public.auctions SET status = $1 WHERE auction_id = $2",
        new_status,
        int(auction_id),
    )


async def update_lot_field(lot_id: int, field: str, value: Any) -> None:
    """Update one allow-listed auction field used by legacy handlers.

    The historical implementation interpolated arbitrary field names into SQL.
    Keeping an explicit allow-list preserves the callers while closing that SQL
    boundary. Updating ``start_time`` also resets the card-subscriber notice,
    matching the previous behavior.
    """

    normalized = (field or "").strip()
    if normalized not in _ALLOWED_LOT_FIELDS:
        raise ValueError(f"unsupported auction field: {normalized!r}")

    if normalized == "start_time":
        await execute(
            """
            UPDATE public.auctions
            SET start_time = $1,
                notified_card_subs = FALSE
            WHERE auction_id = $2
            """,
            value,
            int(lot_id),
        )
        return

    await execute(
        f"UPDATE public.auctions SET {normalized} = $1 WHERE auction_id = $2",
        value,
        int(lot_id),
    )


__all__ = ["update_auction_status", "update_lot_field"]
