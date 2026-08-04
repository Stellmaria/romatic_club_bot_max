"""Userbot-facing compatibility facade for Telethon session maintenance."""

from scripts.telethon_session_schema import (
    CURRENT_COLUMNS,
    KNOWN_BROKEN_VERSION,
    LEGACY_COLUMNS,
    TelethonSessionSchemaError,
    repair_session_schema,
    run,
)

__all__ = [
    "CURRENT_COLUMNS",
    "KNOWN_BROKEN_VERSION",
    "LEGACY_COLUMNS",
    "TelethonSessionSchemaError",
    "repair_session_schema",
    "run",
]
