"""Package-scoped compatibility access to legacy configuration constants."""

from __future__ import annotations

import importlib

_module = importlib.import_module("config")


def __getattr__(name: str):
    return getattr(_module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_module)))
