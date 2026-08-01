from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

import asyncpg

from bot.core.errors import PersistenceError
from db.errors import (
    persistence_boundary,
    record_database_failure,
    translate_database_error,
)
from db.pool import get_db_pool as _runtime_get_db_pool

logger = logging.getLogger("auction_bot")


_DATABASE_EXCEPTIONS = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    TimeoutError,
)


class _TransactionProxy:
    def __init__(self, transaction: Any, operation: str) -> None:
        self._transaction = transaction
        self._operation = operation

    async def __aenter__(self) -> Any:
        try:
            return await self._transaction.__aenter__()
        except _DATABASE_EXCEPTIONS as exc:
            error = translate_database_error(exc, f"{self._operation}.begin")
            record_database_failure(error)
            raise error from exc

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        try:
            return await self._transaction.__aexit__(exc_type, exc, traceback)
        except _DATABASE_EXCEPTIONS as transaction_exc:
            error = translate_database_error(transaction_exc, f"{self._operation}.finish")
            record_database_failure(error)
            raise error from transaction_exc


class _ConnectionProxy:
    """Instrument asyncpg calls while preserving domain-specific constraints."""

    def __init__(self, connection: Any) -> None:
        self.raw_connection = connection

    async def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            method = getattr(self.raw_connection, method_name)
            return await method(*args, **kwargs)
        except asyncpg.IntegrityConstraintViolationError as exc:
            # Repositories intentionally map several constraints to domain
            # errors. Record the technical failure for legacy boundaries, but
            # keep the original exception available to those mappings.
            record_database_failure(
                translate_database_error(exc, f"database.{method_name}")
            )
            raise
        except _DATABASE_EXCEPTIONS as exc:
            error = translate_database_error(exc, f"database.{method_name}")
            record_database_failure(error)
            raise error from exc

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> str:
        return await self._call("execute", query, *args, **kwargs)

    async def executemany(self, command: str, args: Any, **kwargs: Any) -> Any:
        return await self._call("executemany", command, args, **kwargs)

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._call("fetch", query, *args, **kwargs)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._call("fetchrow", query, *args, **kwargs)

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._call("fetchval", query, *args, **kwargs)

    async def copy_records_to_table(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("copy_records_to_table", *args, **kwargs)

    def transaction(self, *args: Any, **kwargs: Any) -> _TransactionProxy:
        return _TransactionProxy(
            self.raw_connection.transaction(*args, **kwargs),
            "database.transaction",
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw_connection, name)


class _AcquireProxy:
    def __init__(self, acquire_context: Any) -> None:
        self._acquire_context = acquire_context
        self._connection: _ConnectionProxy | None = None

    async def __aenter__(self) -> _ConnectionProxy:
        try:
            raw_connection = await self._acquire_context.__aenter__()
        except _DATABASE_EXCEPTIONS as exc:
            error = translate_database_error(exc, "database.acquire")
            record_database_failure(error)
            raise error from exc
        self._connection = _ConnectionProxy(raw_connection)
        return self._connection

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        try:
            return await self._acquire_context.__aexit__(exc_type, exc, traceback)
        except _DATABASE_EXCEPTIONS as release_exc:
            error = translate_database_error(release_exc, "database.release")
            record_database_failure(error)
            raise error from release_exc

    def __await__(self):
        async def acquire_one() -> _ConnectionProxy:
            try:
                raw_connection = await self._acquire_context
            except _DATABASE_EXCEPTIONS as exc:
                error = translate_database_error(exc, "database.acquire")
                record_database_failure(error)
                raise error from exc
            self._connection = _ConnectionProxy(raw_connection)
            return self._connection

        return acquire_one().__await__()


class PoolProxy:
    """Stable, instrumented reference to the current asyncpg pool."""

    def __init__(self) -> None:
        self._pool: Optional[Any] = None

    @property
    def pool(self) -> Optional[Any]:
        return self._pool

    def bind(self, pool: Any) -> None:
        self._pool = pool

    def clear(self) -> None:
        self._pool = None

    def require(self) -> Any:
        if self._pool is None:
            raise RuntimeError("db_pool is not initialized")
        return self._pool

    def acquire(self, *args: Any, **kwargs: Any) -> _AcquireProxy:
        return _AcquireProxy(self.require().acquire(*args, **kwargs))

    async def release(self, connection: Any, *args: Any, **kwargs: Any) -> Any:
        raw_connection = getattr(connection, "raw_connection", connection)
        try:
            return await self.require().release(raw_connection, *args, **kwargs)
        except _DATABASE_EXCEPTIONS as exc:
            error = translate_database_error(exc, "database.release")
            record_database_failure(error)
            raise error from exc

    def __bool__(self) -> bool:
        return self._pool is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.require(), name)


db_pool = PoolProxy()
# Historical repository modules use this spelling. Keep both names pointed at
# the same stable proxy instead of creating a second pool reference.
pool_proxy = db_pool


async def fetchall(query: str, *args: Any) -> list[dict[str, Any]]:
    async with persistence_boundary("db.fetchall"):
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


async def get_db_pool() -> PoolProxy:
    pool = db_pool.pool
    if pool is None:
        pool = await _runtime_get_db_pool()
        db_pool.bind(pool)
        logger.info("Database pool initialized")

    # Legacy compatibility functions must use the instrumented proxy. Their
    # broad catches can no longer turn a recorded DB failure into false data.
    try:
        from db import legacy_impl

        legacy_impl.db_pool = db_pool
    except ImportError:
        pass
    return db_pool


def require_db_pool(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not db_pool:
            logger.error("Database pool not initialized")
            raise RuntimeError("Database pool not initialized")
        async with persistence_boundary(f"{func.__module__}.{func.__name__}"):
            return await func(*args, **kwargs)

    return wrapper


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_db_pool()
    async with persistence_boundary("db.fetch"):
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> Optional[asyncpg.Record]:
    pool = await get_db_pool()
    async with persistence_boundary("db.fetchrow"):
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    pool = await get_db_pool()
    async with persistence_boundary("db.fetchval"):
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_db_pool()
    async with persistence_boundary("db.execute"):
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
    "PersistenceError",
    "db_pool",
    "pool_proxy",
    "fetchall",
    "get_db_pool",
    "init_db",
    "close_db",
    "fetch",
    "fetchrow",
    "fetchval",
    "execute",
    "require_db_pool",
]
