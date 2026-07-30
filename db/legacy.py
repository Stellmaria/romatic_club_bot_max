"""Temporary compatibility access point for legacy handler persistence calls.

New handlers must depend on repositories and use cases.  This module exists to
centralize the remaining legacy calls while incremental migrations are made.
"""

from __future__ import annotations

from db import db as _legacy_database


def __getattr__(name: str):
    return getattr(_legacy_database, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy_database)))
