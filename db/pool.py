"""PostgreSQL connection-pool lifecycle."""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from bot.core.settings import DatabaseSettings

logger = logging.getLogger("auction_bot.database")

_pool: asyncpg.Pool | None = None
_database_settings: DatabaseSettings | None = None
_initialization_lock: asyncio.Lock | None = None


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database connection is requested before configuration."""


def configure_database(settings: DatabaseSettings) -> None:
    """Bind validated settings without opening a network connection."""

    global _database_settings
    if _pool is not None and settings != _database_settings:
        raise RuntimeError("cannot replace database settings while the pool is open")
    _database_settings = settings


def reset_database_configuration_for_testing() -> None:
    global _database_settings
    if _pool is not None:
        raise RuntimeError("cannot reset database settings while the pool is open")
    _database_settings = None


def _get_initialization_lock() -> asyncio.Lock:
    global _initialization_lock
    if _initialization_lock is None:
        _initialization_lock = asyncio.Lock()
    return _initialization_lock


def current_pool() -> asyncpg.Pool | None:
    return _pool


async def get_db_pool(settings: DatabaseSettings | None = None) -> asyncpg.Pool:
    """Return the process pool, creating it exactly once when necessary."""

    global _pool
    if settings is not None:
        configure_database(settings)
    if _pool is not None:
        return _pool

    config = _database_settings
    if config is None:
        raise DatabaseConfigurationError("database settings are not configured")

    async with _get_initialization_lock():
        if _pool is None:
            _pool = await asyncpg.create_pool(
                config.url,
                min_size=config.pool_min_size,
                max_size=config.pool_max_size,
            )
            logger.info("Database pool initialized")
    return _pool


async def close_db_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()
        logger.info("Database pool closed")


def install_pool_for_testing(pool: asyncpg.Pool | None) -> None:
    global _pool
    _pool = pool


__all__ = (
    "DatabaseConfigurationError",
    "close_db_pool",
    "configure_database",
    "current_pool",
    "get_db_pool",
    "install_pool_for_testing",
    "reset_database_configuration_for_testing",
)
