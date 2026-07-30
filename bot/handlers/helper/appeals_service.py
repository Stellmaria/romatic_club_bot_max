"""Compatibility imports for the user-appeal persistence service.

This module remains at its historical handler path so existing imports keep
working; persistence belongs to ``bot.services.appeals``.
"""

from bot.services.appeals import (
    create_appeal,
    get_appeal_by_id,
    get_first_pending,
    get_next_pending,
    set_reply,
    set_status,
)

__all__ = [
    "create_appeal",
    "get_appeal_by_id",
    "get_first_pending",
    "get_next_pending",
    "set_status",
    "set_reply",
]
