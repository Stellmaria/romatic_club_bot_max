"""Database queries used by schedule presentation flows."""

from __future__ import annotations

from db.core import fetchrow


async def get_last_nonempty_card_deck_id() -> int:
    """Return the latest deck represented by a card."""

    row = await fetchrow("SELECT COALESCE(MAX(deck_id), 0) AS mx FROM cards")
    try:
        return int(row["mx"]) if row and row["mx"] is not None else 0
    except (KeyError, TypeError, IndexError):
        return 0


__all__ = ["get_last_nonempty_card_deck_id"]
