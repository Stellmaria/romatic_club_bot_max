from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

import asyncpg

from bot.core.errors import PersistenceError
from bot.core.settings import DatabaseSettings
from db.errors import (
    persistence_boundary,
    record_database_failure,
    translate_database_error,
)
from db.pool import DatabaseConfigurationError, DatabaseRuntime

logger = logging.getLogger("auction_bot")


_DATABASE_EXCEPTIONS = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    TimeoutError,
)
_active_runtime: DatabaseRuntime | None = None


def current_database_runtime() -> DatabaseRuntime | None:
    """Return the runtime installed by the application composition root."""

    return _active_runtime


def install_database_runtime(runtime: DatabaseRuntime) -> None:
    """Expose one application-owned runtime to temporary compatibility APIs."""

    global _active_runtime
    current = _active_runtime
    if current is runtime:
        return
    if current is not None and current.started:
        raise RuntimeError("a different database runtime is already active")
    _active_runtime = runtime


def uninstall_database_runtime(runtime: DatabaseRuntime | None = None) -> None:
    """Remove a closed runtime without touching another process lifecycle."""

    global _active_runtime
    current = _active_runtime
    if current is None:
        return
    if runtime is not None and current is not runtime:
        raise RuntimeError("cannot uninstall a different database runtime")
    if current.started:
        raise RuntimeError("cannot uninstall database runtime while its pool is open")
    _active_runtime = None


def configure_database(settings: DatabaseSettings) -> DatabaseRuntime:
    """Compatibility helper that configures, but does not start, one runtime."""

    runtime = _active_runtime
    if runtime is None:
        runtime = DatabaseRuntime(settings)
        install_database_runtime(runtime)
    else:
        runtime.configure(settings)
    return runtime


def reset_database_configuration_for_testing() -> None:
    global _active_runtime
    if _active_runtime is not None and _active_runtime.started:
        raise RuntimeError("cannot reset database settings while the pool is open")
    _active_runtime = None


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


class DatabaseAccess:
    """Non-owning compatibility view of the installed DatabaseRuntime.

    It intentionally stores no pool.  The application lifecycle owns the
    runtime; this object only keeps legacy query functions operational while
    they are migrated to constructor-injected repositories.
    """

    @property
    def runtime(self) -> DatabaseRuntime:
        runtime = _active_runtime
        if runtime is None:
            raise DatabaseConfigurationError("database runtime is not installed")
        return runtime

    @property
    def pool(self) -> Any | None:
        runtime = _active_runtime
        return None if runtime is None else runtime.pool

    def bind(self, pool: Any) -> None:
        """Install a fake pool for compatibility tests only."""

        global _active_runtime
        _active_runtime = DatabaseRuntime.for_testing(pool)

    def clear(self) -> None:
        """Detach the compatibility runtime without closing a test double."""

        global _active_runtime
        _active_runtime = None

    def require(self) -> Any:
        return self.runtime.require_pool()

    def acquire(self, *args: Any, **kwargs: Any) -> _AcquireProxy:
        return _AcquireProxy(self.runtime.acquire(*args, **kwargs))

    async def release(self, connection: Any, *args: Any, **kwargs: Any) -> Any:
        raw_connection = getattr(connection, "raw_connection", connection)
        try:
            return await self.runtime.release(raw_connection, *args, **kwargs)
        except _DATABASE_EXCEPTIONS as exc:
            error = translate_database_error(exc, "database.release")
            record_database_failure(error)
            raise error from exc

    def __bool__(self) -> bool:
        return self.pool is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.require(), name)


db_pool = DatabaseAccess()
# Deprecated spelling retained only as a non-owning adapter.
pool_proxy = db_pool


async def fetchall(query: str, *args: Any) -> list[dict[str, Any]]:
    async with persistence_boundary("db.fetchall"):
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


async def get_db_pool(settings: DatabaseSettings | None = None) -> Any:
    """Start and return the pool owned by the installed runtime."""

    runtime = _active_runtime
    if runtime is None:
        if settings is None:
            raise DatabaseConfigurationError("database runtime is not installed")
        runtime = DatabaseRuntime(settings)
        install_database_runtime(runtime)
    elif settings is not None:
        runtime.configure(settings)
    return await runtime.start()


def require_db_pool(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not db_pool:
            logger.error("Database runtime is not started")
            raise RuntimeError("Database runtime is not started")
        async with persistence_boundary(f"{func.__module__}.{func.__name__}"):
            return await func(*args, **kwargs)

    return wrapper


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    await get_db_pool()
    async with persistence_boundary("db.fetch"):
        async with db_pool.acquire() as conn:
            return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> Optional[asyncpg.Record]:
    await get_db_pool()
    async with persistence_boundary("db.fetchrow"):
        async with db_pool.acquire() as conn:
            return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    await get_db_pool()
    async with persistence_boundary("db.fetchval"):
        async with db_pool.acquire() as conn:
            return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    await get_db_pool()
    async with persistence_boundary("db.execute"):
        async with db_pool.acquire() as conn:
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
    "DatabaseAccess",
    "configure_database",
    "current_database_runtime",
    "db_pool",
    "pool_proxy",
    "fetchall",
    "get_db_pool",
    "init_db",
    "close_db",
    "install_database_runtime",
    "uninstall_database_runtime",
    "fetch",
    "fetchrow",
    "fetchval",
    "execute",
    "require_db_pool",
    "reset_database_configuration_for_testing",
]
