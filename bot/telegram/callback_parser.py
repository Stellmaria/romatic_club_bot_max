"""Compatibility tokenization for callback families awaiting typed schemas.

Production handlers import these functions instead of calling ``str.split`` on
Telegram payloads.  Every legacy callback therefore passes the same size and
control-character validation before a handler interprets any field.
"""

from __future__ import annotations

from bot.telegram.boundary import validate_callback_payload


def split_callback_data(
    payload: object,
    separator: str | None = None,
    maxsplit: int = -1,
) -> list[str]:
    raw = validate_callback_payload(payload)
    return raw.split(separator, maxsplit)


def rsplit_callback_data(
    payload: object,
    separator: str | None = None,
    maxsplit: int = -1,
) -> list[str]:
    raw = validate_callback_payload(payload)
    return raw.rsplit(separator, maxsplit)


__all__ = ["rsplit_callback_data", "split_callback_data"]
