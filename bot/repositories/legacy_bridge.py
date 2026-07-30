"""Database gateway used only by the synchronous legacy HTTP bridge.

The Flask bridge executes delivery on a short-lived event loop.  Its database
connection must therefore remain separate from the Telegram application's
event-loop-owned pool.
"""

from __future__ import annotations

import asyncpg


class LegacyBridgeWarningGateway:
    """Load warning counts over one short-lived, explicitly configured link."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    async def warning_count(self, user_id: int) -> int | None:
        connection = await asyncpg.connect(dsn=self._database_url)
        try:
            value = await connection.fetchval(
                "SELECT warnings_count FROM public.users WHERE user_id = $1",
                int(user_id),
            )
            return int(value) if value is not None else None
        finally:
            await connection.close()


__all__ = ["LegacyBridgeWarningGateway"]
