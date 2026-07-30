"""Runtime state shared by the Telethon handler modules.

The module deliberately owns only in-process state.  It does not create a
Telegram client, open a database pool, or register event handlers on import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telethon import TelegramClient


BOT_DELETED: dict[int, float] = {}
BOT_DELETED_TTL = 300.0

# key: (chat_id, message_id)
# value: root_id, amount, user_id, text and auction_id of an accepted bid
ACCEPTED_BIDS: dict[tuple[int, int], dict[str, Any]] = {}

CHAT_ADMINS_CACHE: dict[int, tuple[set[int], float]] = {}
CHAT_ADMINS_TTL = 300.0

_client: TelegramClient | None = None


def bind_client(telegram_client: TelegramClient) -> None:
    """Bind the already constructed client for handler helper operations."""

    global _client
    _client = telegram_client


def require_client() -> TelegramClient:
    """Return the bound client or fail before a partial handler operation."""

    if _client is None:
        raise RuntimeError("Userbot client is not initialized; call register_handlers() first")
    return _client


def bound_client() -> TelegramClient | None:
    """Expose the current binding for diagnostics and regression tests."""

    return _client


__all__ = [
    "ACCEPTED_BIDS",
    "BOT_DELETED",
    "BOT_DELETED_TTL",
    "CHAT_ADMINS_CACHE",
    "CHAT_ADMINS_TTL",
    "bind_client",
    "bound_client",
    "require_client",
]
