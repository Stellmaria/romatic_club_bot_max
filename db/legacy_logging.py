"""Compatibility logger for modules that historically imported it from db."""

from __future__ import annotations

import logging


logger = logging.getLogger("auction_bot")


__all__ = ["logger"]
