from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest

_EXPIRED_CALLBACK_MARKERS = (
    "query is too old",
    "response timeout expired",
    "query id is invalid",
)


def is_expired_callback_error(error: BaseException) -> bool:
    """Return True only for Telegram's harmless expired callback-query error."""
    message = str(error).lower()
    return any(marker in message for marker in _EXPIRED_CALLBACK_MARKERS)


async def safe_callback_answer(
    callback: Any,
    text: str | None = None,
    *,
    show_alert: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Answer a callback query without crashing when Telegram has already expired it.

    Returns True when Telegram accepted the answer and False when the query was
    already too old/invalid. Other Telegram errors are deliberately re-raised.
    """
    try:
        await callback.answer(
            text=text or None,
            show_alert=show_alert,
            **kwargs,
        )
        return True
    except TelegramBadRequest as error:
        if is_expired_callback_error(error):
            return False
        raise
