"""Compatibility facade for :mod:`bot.features.exchange.contracts`."""

from __future__ import annotations

from bot.features.exchange import contracts as _contracts


for _name in dir(_contracts):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_contracts, _name)

__all__ = tuple(name for name in dir(_contracts) if not name.startswith("__"))

del _name
