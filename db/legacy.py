"""Temporary compatibility access point for legacy handler persistence calls.

New handlers must depend on repositories and use cases. This module exists to
centralize the remaining legacy calls while incremental migrations are made.
Legacy coroutine calls are wrapped in the strict persistence boundary so a
technical PostgreSQL failure cannot be returned as empty business data.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable

from db import db as _legacy_database
from db import legacy_impl as _legacy_impl
from db.core import logger
from db.errors import persistence_boundary

_wrapped_coroutines: dict[str, Callable[..., Any]] = {}


def _wrap_legacy_coroutine(name: str, func: Callable[..., Any]) -> Callable[..., Any]:
    cached = _wrapped_coroutines.get(name)
    if cached is not None:
        return cached

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async with persistence_boundary(f"db.legacy.{name}"):
            return await func(*args, **kwargs)

    _wrapped_coroutines[name] = wrapper
    return wrapper


def __getattr__(name: str):
    try:
        return getattr(_legacy_database, name)
    except AttributeError:
        value = getattr(_legacy_impl, name)
        if inspect.iscoroutinefunction(value):
            return _wrap_legacy_coroutine(name, value)
        return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy_database)) | set(dir(_legacy_impl)))
