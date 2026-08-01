"""PostgreSQL exception translation and swallowed-error detection."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

import asyncpg

from bot.core.errors import (
    PersistenceConflictError,
    PersistenceError,
    PersistenceIntegrityError,
    PersistenceOperationError,
    PersistenceUnavailableError,
)

_failure: ContextVar[PersistenceError | None] = ContextVar(
    "database_persistence_failure",
    default=None,
)
_boundary_depth: ContextVar[int] = ContextVar("database_boundary_depth", default=0)


def translate_database_error(exc: BaseException, operation: str) -> PersistenceError:
    """Translate a technical database exception without leaking its payload."""

    if isinstance(exc, PersistenceError):
        return exc
    if isinstance(
        exc,
        (
            OSError,
            TimeoutError,
            asyncpg.InterfaceError,
            asyncpg.PostgresConnectionError,
        ),
    ):
        return PersistenceUnavailableError(operation)
    if isinstance(exc, asyncpg.IntegrityConstraintViolationError):
        return PersistenceIntegrityError(operation)
    if isinstance(exc, (asyncpg.SerializationError, asyncpg.DeadlockDetectedError)):
        return PersistenceConflictError(operation)
    return PersistenceOperationError(operation)


def record_database_failure(error: PersistenceError) -> None:
    """Remember the first failure inside the current persistence boundary."""

    if _failure.get() is None:
        _failure.set(error)


def current_database_failure() -> PersistenceError | None:
    return _failure.get()


@asynccontextmanager
async def persistence_boundary(operation: str) -> AsyncIterator[None]:
    """Raise a recorded DB error even when legacy code returned a fallback.

    A number of historical functions catch ``Exception`` and return ``None`` or
    an empty collection.  Instrumented connections record the real failure;
    this boundary converts that false success back into a typed application
    error.  Nested boundaries propagate the marker to their parent.
    """

    parent_depth = _boundary_depth.get()
    depth_token = _boundary_depth.set(parent_depth + 1)
    failure_token = _failure.set(None)
    local_failure: PersistenceError | None = None
    try:
        yield
        local_failure = _failure.get()
        if local_failure is not None:
            raise local_failure
    except PersistenceError as exc:
        local_failure = _failure.get() or exc
        record_database_failure(local_failure)
        raise
    except (
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
        OSError,
        TimeoutError,
    ) as exc:
        local_failure = translate_database_error(exc, operation)
        record_database_failure(local_failure)
        raise local_failure from exc
    finally:
        local_failure = _failure.get() or local_failure
        _failure.reset(failure_token)
        _boundary_depth.reset(depth_token)
        if parent_depth > 0 and local_failure is not None and _failure.get() is None:
            _failure.set(local_failure)


__all__ = [
    "current_database_failure",
    "persistence_boundary",
    "record_database_failure",
    "translate_database_error",
]
