"""Global Telegram message-size and control-character validation."""

from __future__ import annotations

import re
from typing import Protocol

from bot.telegram.boundary import (
    TELEGRAM_CAPTION_CHARS,
    TELEGRAM_MESSAGE_CHARS,
    TelegramBoundaryError,
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class MessageLike(Protocol):
    text: str | None
    caption: str | None


def validate_incoming_text(
    value: str | None,
    *,
    field: str,
    maximum: int,
) -> None:
    """Reject input that handlers cannot safely persist or echo."""

    if value is None:
        return
    if _CONTROL_RE.search(value):
        raise TelegramBoundaryError(
            f"{field} содержит недопустимые управляющие символы.",
            code="control_characters",
        )
    if len(value) > maximum:
        raise TelegramBoundaryError(
            f"{field} слишком длинный: максимум {maximum} символов.",
            code="telegram_input_too_long",
        )


def validate_incoming_message(message: MessageLike) -> None:
    validate_incoming_text(
        message.text,
        field="Сообщение",
        maximum=TELEGRAM_MESSAGE_CHARS,
    )
    validate_incoming_text(
        message.caption,
        field="Подпись",
        maximum=TELEGRAM_CAPTION_CHARS,
    )


__all__ = ["validate_incoming_message", "validate_incoming_text"]
