"""Temporary import bridge for administrative action consumers.

It exposes the decomposed owners without depending on the retired legacy
facade. Consumers can migrate to narrower modules incrementally.
"""

from bot.handlers.admin.action_support.exchange import *  # noqa: F403
from bot.handlers.admin.action_support.forms import *  # noqa: F403
from bot.handlers.admin.action_support.moderation import *  # noqa: F403
from bot.handlers.admin.action_support.roles import *  # noqa: F403
from bot.handlers.admin.action_support.scheduled_edits import *  # noqa: F403
from bot.handlers.admin.action_support.transport import *  # noqa: F403
from bot.handlers.admin.helper.new.formatting import get_lot_owners_with_levels
from bot.presentation.admin import format_owner_html, format_owners_block
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.telegram.media import safe_send_media
