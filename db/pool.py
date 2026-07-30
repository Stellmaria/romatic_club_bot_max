"""PostgreSQL connection-pool lifecycle.

This module is the infrastructure boundary used by services and repositories.
The legacy :mod:`db.db` facade still mirrors the pool reference while old query
functions are migrated into repositories.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from bot.core.settings import (
    DATABASE_POOL_MAX_SIZE,
    DATABASE_POOL_MIN_SIZE,
    DATABASE_URL,
)

logger = logging.getLogger("auction_bot.database")

_pool: asyncpg.Pool | None = None
_initialization_lock: asyncio.Lock | None = None


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database connection is requested without a DSN."""


def _get_initialization_lock() -> asyncio.Lock:
    global _initialization_lock
    if _initialization_lock is None:
        _initialization_lock = asyncio.Lock()
    return _initialization_lock


def current_pool() -> asyncpg.Pool | None:
    """Return the initialized pool without creating network connections."""

    return _pool


async def get_db_pool() -> asyncpg.Pool:
    """Return the process pool, creating it exactly once when necessary."""

    global _pool
    if _pool is not None:
        return _pool

    if not DATABASE_URL:
        raise DatabaseConfigurationError("DATABASE_URL is not configured")

    async with _get_initialization_lock():
        if _pool is None:
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=DATABASE_POOL_MIN_SIZE,
                max_size=DATABASE_POOL_MAX_SIZE,
            )
            logger.info("Database pool initialized")
    return _pool


async def close_db_pool() -> None:
    """Close and forget the process pool; safe to call more than once."""

    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()
        logger.info("Database pool closed")


def install_pool_for_testing(pool: asyncpg.Pool | None) -> None:
    """Inject a disposable pool without opening a production connection."""

    global _pool
    _pool = pool
