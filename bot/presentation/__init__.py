"""Pure presentation helpers shared by handlers and services."""

from __future__ import annotations

import html
from typing import Any

from bot.core.time import moscow_now

from . import admin as _admin

_ORIGINAL_ADMIN_ACTION_LOG = _admin.format_admin_action_log


def _canonical_admin_action_log(action: str, *args: Any, **kwargs: Any) -> str:
    """Keep legacy admin formatting while enforcing canonical audit framing."""

    text = _ORIGINAL_ADMIN_ACTION_LOG(action, *args, **kwargs)
    lines = text.splitlines()
    if len(lines) >= 2:
        lines[1] = f"🕒 {moscow_now().strftime(_admin.DT_FMT)} (МСК)"  # noqa: RUF001
    if lines:
        lines[-1] = f"Действие: <code>{html.escape(action)}</code> через бота."
    return "\n".join(lines)


_admin.format_admin_action_log = _canonical_admin_action_log

__all__ = []
