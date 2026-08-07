from __future__ import annotations

import html
from typing import Any

from bot.core.time import moscow_now

from . import formatting as _formatting

_ORIGINAL_ADMIN_ACTION_LOG = _formatting.format_admin_action_log


def _canonical_admin_action_log(action: str, *args: Any, **kwargs: Any) -> str:
    """Compatibility wrapper for the retired duplicate admin formatter."""

    text = _ORIGINAL_ADMIN_ACTION_LOG(action, *args, **kwargs)
    lines = text.splitlines()
    if len(lines) >= 2:
        lines[1] = f"🕒 {moscow_now().strftime(_formatting.DT_FMT)} (МСК)"
    if lines:
        lines[-1] = f"Действие: <code>{html.escape(action)}</code> через бота."
    return "\n".join(lines)


_formatting.format_admin_action_log = _canonical_admin_action_log

__all__ = []
