"""Compatibility facade for :mod:`bot.features.auction_submission`."""

from __future__ import annotations

from bot.features import auction_submission as _implementation
from bot.features.auction_submission import auction_currency_kb, compute_start_price_limits


for _name in dir(_implementation):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_implementation, _name)

__all__ = tuple(
    name for name in dir(_implementation) if not name.startswith("__")
)

del _name
