"""Temporary compatibility access point for modular persistence functions.

The former fallback to ``db.legacy_impl`` was removed.  Every surviving symbol
now has one implementation in a thematic database module.  New code must use
repositories/use cases rather than this dynamic facade.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable

from db import db as _database
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
    value = getattr(_database, name)
    if inspect.iscoroutinefunction(value):
        return _wrap_legacy_coroutine(name, value)
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_database)))
