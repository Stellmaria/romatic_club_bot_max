from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

import asyncpg

from db.pool import get_db_pool as _runtime_get_db_pool

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
# Historical repository modules use this spelling.  Keep both names pointed at
# the same stable proxy instead of creating a second pool reference.
pool_proxy = db_pool


async def fetchall(query: str, *args: Any) -> list[dict[str, Any]]:
    pool = db_pool.require()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(row) for row in rows]


async def get_db_pool() -> asyncpg.Pool:
    pool = db_pool.pool
    if pool is not None:
        return pool
    pool = await _runtime_get_db_pool()
    db_pool.bind(pool)
    # Legacy compatibility functions are still imported by older handlers.
    # They must share the managed pool instead of retaining an uninitialized
    # module-level reference from the pre-refactor implementation.
    try:
        from db import legacy_impl

        legacy_impl.db_pool = pool
    except ImportError:
        pass
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


@require_db_pool
async def _pg_column_exists(table: str, column: str) -> bool:
    async with db_pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
                """,
                table,
                column,
            )
        )


@require_db_pool
async def _pg_table_exists(table: str) -> bool:
    async with db_pool.acquire() as conn:
        return bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))


@require_db_pool
async def _has_column(conn: asyncpg.Connection, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
            )
            """,
            table,
            column,
        )
    )


from db.lifecycle import close_db, init_db


__all__ = [
    "db_pool", "pool_proxy", "fetchall", "get_db_pool", "init_db", "close_db",
    "fetch", "fetchrow", "fetchval", "execute", "require_db_pool",
]
