"""Infrastructure adapters for role mutations."""
from __future__ import annotations

from db.admin import add_admin, is_admin, log_admin_action, remove_admin
from db.users import set_trusted_status

__all__ = [
    "add_admin",
    "is_admin",
    "log_admin_action",
    "remove_admin",
    "set_trusted_status",
]
