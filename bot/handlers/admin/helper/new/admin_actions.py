"""Compatibility exports for decomposed administrative actions.

New production code imports the owning ``action_support`` module directly.
This module remains solely for third-party compatibility.
"""

from bot.handlers.admin.action_support import exchange, forms, moderation, roles, scheduled_edits, transport
from bot.handlers.admin.action_support.exchange import *  # noqa: F403
from bot.handlers.admin.action_support.forms import *  # noqa: F403
from bot.handlers.admin.action_support.moderation import *  # noqa: F403
from bot.handlers.admin.action_support.roles import *  # noqa: F403
from bot.handlers.admin.action_support.scheduled_edits import *  # noqa: F403
from bot.handlers.admin.action_support.transport import *  # noqa: F403
from bot.handlers.admin.helper.new.formatting import (
    format_admin_action_log,
    format_pending_lot,
    get_lot_owners_with_levels,
)
from bot.presentation.admin import format_owner_html, format_owners_block
from bot.security import admin_secret_matches
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.telegram.media import safe_send_media

__all__ = (
    *exchange.__all__,
    *transport.__all__,
    *moderation.__all__,
    *roles.__all__,
    *forms.__all__,
    *scheduled_edits.__all__,
    "admin_secret_matches",
    "admin_tag",
    "build_thanks_kb",
    "format_admin_action_log",
    "format_owner_html",
    "format_owners_block",
    "format_pending_lot",
    "get_lot_owners_text",
    "get_lot_owners_with_levels",
    "safe_send_media",
    "send_admin_log",
)
