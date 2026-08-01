"""Application-owned PostgreSQL connection-pool lifecycle.

The runtime object is the only owner of an asyncpg pool.  This module keeps a
few deprecated function wrappers so old maintenance scripts continue to import,
but those wrappers delegate to the runtime installed by the composition root
and never hold a second pool reference.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from bot.core.settings import DatabaseSettings

logger = logging.getLogger("auction_bot.database")

PoolFactory = Callable[..., Awaitable[Any]]


class DatabaseConfigurationError(RuntimeError):
    """Raised when database work is requested without an installed runtime."""


class DatabaseRuntime:
    """Own exactly one PostgreSQL pool for one application lifecycle.

    The object is intentionally independent from module globals.  Bot and
    userbot construct their own runtime, start it during application startup
    and close the same object during shutdown.  Tests may create any number of
    independent runtimes in one interpreter.
    """

    def __init__(
        self,
        settings: DatabaseSettings | None,
        *,
        pool_factory: PoolFactory = asyncpg.create_pool,
    ) -> None:
        self._settings = settings
        self._pool_factory = pool_factory
        self._pool: Any | None = None
        self._initialization_lock: asyncio.Lock | None = None

    @classmethod
    def for_testing(cls, pool: Any) -> "DatabaseRuntime":
        runtime = cls(None)
        runtime._pool = pool
        return runtime

    @property
    def settings(self) -> DatabaseSettings | None:
        return self._settings

    @property
    def pool(self) -> Any | None:
        return self._pool

    @property
    def started(self) -> bool:
        return self._pool is not None

    def configure(self, settings: DatabaseSettings) -> None:
        """Bind validated settings before the pool is started."""

        if self.started and settings != self._settings:
            raise RuntimeError("cannot replace database settings while the pool is open")
        self._settings = settings

    def _lock(self) -> asyncio.Lock:
        if self._initialization_lock is None:
            self._initialization_lock = asyncio.Lock()
        return self._initialization_lock

    async def start(self) -> Any:
        """Create and return this runtime's pool exactly once."""

        if self._pool is not None:
            return self._pool
        settings = self._settings
        if settings is None:
            raise DatabaseConfigurationError("database settings are not configured")

        async with self._lock():
            if self._pool is None:
                self._pool = await self._pool_factory(
                    settings.url,
                    min_size=settings.pool_min_size,
                    max_size=settings.pool_max_size,
                )
                logger.info("Database pool initialized")
        return self._pool

    def require_pool(self) -> Any:
        pool = self._pool
        if pool is None:
            raise DatabaseConfigurationError("database runtime is not started")
        return pool

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        return self.require_pool().acquire(*args, **kwargs)

    async def release(self, connection: Any, *args: Any, **kwargs: Any) -> Any:
        return await self.require_pool().release(connection, *args, **kwargs)

    async def close(self) -> None:
        """Close this runtime and discard all loop-bound lifecycle state."""

        pool, self._pool = self._pool, None
        self._initialization_lock = None
        if pool is not None:
            await pool.close()
            logger.info("Database pool closed")


# Deprecated import-compatible wrappers.  They deliberately keep no state.
def configure_database(settings: DatabaseSettings) -> None:
    from db.core import configure_database as configure

    configure(settings)


def reset_database_configuration_for_testing() -> None:
    from db.core import reset_database_configuration_for_testing as reset

    reset()


def current_pool() -> Any | None:
    from db.core import current_database_runtime

    runtime = current_database_runtime()
    return None if runtime is None else runtime.pool


async def get_db_pool(settings: DatabaseSettings | None = None) -> Any:
    from db.core import get_db_pool as get_pool

    return await get_pool(settings)


async def close_db_pool() -> None:
    from db.lifecycle import close_db

    await close_db()


def install_pool_for_testing(pool: Any | None) -> None:
    from db.core import db_pool

    db_pool.clear()
    if pool is not None:
        db_pool.bind(pool)


__all__ = (
    "DatabaseConfigurationError",
    "DatabaseRuntime",
    "close_db_pool",
    "configure_database",
    "current_pool",
    "get_db_pool",
    "install_pool_for_testing",
    "reset_database_configuration_for_testing",
)
