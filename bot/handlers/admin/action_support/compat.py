"""Temporary import bridge for administrative action consumers.

It exposes the decomposed owners without depending on the retired legacy
facade. Consumers can migrate to narrower modules incrementally.
"""

from bot.handlers.admin.action_support import (
    exchange,
    forms,
    moderation,
    roles,
    scheduled_edits,
    transport,
)
for _module in (exchange, forms, moderation, roles, scheduled_edits, transport):
    for _name in _module.__all__:
        globals()[_name] = getattr(_module, _name)

del _module, _name

from bot.handlers.admin.helper.new.formatting import get_lot_owners_with_levels
from bot.presentation.admin import format_owner_html, format_owners_block
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.telegram.media import safe_send_media
