from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger("auction_bot")


class PoolProxy:
    """Stable reference to the current asyncpg pool.

    The old project imported ``db_pool`` directly in several modules. A normal
    module variable becomes stale after reassignment, so this proxy keeps those
    imports valid while the actual pool is created and closed.
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self._pool

    def bind(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def clear(self) -> None:
        self._pool = None

    def require(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("db_pool is not initialized")
        return self._pool

    def __bool__(self) -> bool:
        return self._pool is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.require(), name)


db_pool = PoolProxy()


async def fetchall(query: str, *args: Any) -> list[dict[str, Any]]:
    pool = db_pool.require()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(row) for row in rows]


async def get_db_pool() -> asyncpg.Pool:
    pool = db_pool.pool
    if pool is not None:
        return pool
    try:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    except Exception as exc:
        logger.error("Не удалось создать пул соединений с БД: %s", exc)
        raise
    db_pool.bind(pool)
    logger.info("Database pool initialized")
    return pool


def require_db_pool(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not db_pool:
            logger.error("Database pool not initialized!")
            raise RuntimeError("Database pool not initialized!")
        return await func(*args, **kwargs)

    return wrapper


async def close_db() -> None:
    pool = db_pool.pool
    if pool is None:
        return
    await pool.close()
    db_pool.clear()
    logger.info("Database pool closed")


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> Optional[asyncpg.Record]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)
