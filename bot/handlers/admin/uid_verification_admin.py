"""Compatibility facade for focused UID administration routers."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.telegram.states import ModActionFSM, UIDVerificationRevisionFSM

from . import master_ban
from . import telegram_user_bans
from . import uid_admin_bans
from . import uid_admin_presentation
from . import uid_admin_resolvers
from . import uid_admin_shared
from . import uid_verification_review
from . import uid_verification_revision
from . import uid_whois


router = Router(name=__name__)
router.include_routers(
    uid_admin_bans.router,
    uid_verification_review.router,
    uid_whois.router,
    uid_verification_review.late_router,
    uid_verification_revision.router,
    telegram_user_bans.router,
    master_ban.router,
)

# Preserve the historical import surface while implementation ownership stays
# in focused modules. Public imported helpers are intentionally re-exported.
_OWNERS = (
    uid_admin_shared,
    uid_admin_resolvers,
    uid_admin_presentation,
    uid_admin_bans,
    uid_verification_review,
    uid_whois,
    uid_verification_revision,
    telegram_user_bans,
    master_ban,
)
for _owner in _OWNERS:
    for _name in dir(_owner):
        if _name.startswith("_") or _name == "router":
            continue
        globals().setdefault(_name, getattr(_owner, _name))

__all__ = tuple(
    sorted(
        name
        for name in globals()
        if not name.startswith("_") and name not in {"annotations"}
    )
)
