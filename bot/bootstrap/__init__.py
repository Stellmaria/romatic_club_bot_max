"""Application composition helpers with lazy runtime imports."""

from __future__ import annotations

from typing import Any


def build_background_task_specs(*args: Any, **kwargs: Any):
    from .workers import build_background_task_specs as implementation

    return implementation(*args, **kwargs)


def get_router_registry():
    from .routers import get_router_registry as implementation

    return implementation()


def register_all_routers(*args: Any, **kwargs: Any) -> None:
    from .routers import register_all_routers as implementation

    implementation(*args, **kwargs)


def route_inventory_json(*args: Any, **kwargs: Any) -> str:
    from .routers import route_inventory_json as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "build_background_task_specs",
    "get_router_registry",
    "register_all_routers",
    "route_inventory_json",
]
