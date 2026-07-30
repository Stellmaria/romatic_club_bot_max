"""Telegram content policy: ordinary messages are open, Luxury opts in."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Bot

AdminCheck = Callable[[int], Awaitable[bool]]


def patch_bot_protect_content(bot: Bot, *, is_admin: AdminCheck) -> None:
    """Keep Telegram's open default and preserve explicit Luxury protection.

    Telegram already treats omitted ``protect_content`` as ``False``.  The old
    project-wide wrapper forced protection onto every private reply.  It is now
    intentionally a compatibility no-op so existing bootstrap code does not
    need special cases.  Luxury handlers still pass ``protect_content=True``
    explicitly.
    """

    del is_admin
    setattr(bot, "_protect_content_patched", True)
