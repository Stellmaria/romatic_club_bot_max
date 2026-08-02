from __future__ import annotations

import json

import pytest
from aiogram import Dispatcher, Router

from bot.bootstrap.router_registry import (
    FeatureRegistration,
    PrepareHook,
    RouteCollisionError,
    RoutePriority,
    RouterDependencyError,
    RouterRegistry,
)
from bot.bootstrap.routers import get_router_registry, route_inventory_json


def _feature(
    name: str,
    priority: RoutePriority,
    *,
    router: Router | None = None,
    parent: str | None = None,
    dependencies: tuple[str, ...] = (),
    commands: tuple[str, ...] = (),
    callbacks: tuple[str, ...] = (),
    hooks: tuple[PrepareHook, ...] = (),
) -> FeatureRegistration:
    return FeatureRegistration(
        name=name,
        priority=priority,
        router=router,
        parent=parent,
        dependencies=dependencies,
        commands=commands,
        callback_namespaces=callbacks,
        prepare_hooks=hooks,
    )


def test_exact_command_collision_with_broad_fsm_is_rejected() -> None:
    with pytest.raises(RouteCollisionError, match="command /start"):
        RouterRegistry(
            (
                _feature(
                    "exact.start",
                    RoutePriority.EXACT_COMMANDS,
                    router=Router(name="exact-start"),
                    commands=("start",),
                ),
                _feature(
                    "broad.session",
                    RoutePriority.BROAD_FSM,
                    router=Router(name="broad-session"),
                    commands=("/START",),
                ),
            )
        )


def test_router_name_collision_is_rejected() -> None:
    with pytest.raises(RouteCollisionError, match="router name 'shared'"):
        RouterRegistry(
            (
                _feature(
                    "first",
                    RoutePriority.CALLBACKS,
                    router=Router(name="shared"),
                ),
                _feature(
                    "second",
                    RoutePriority.CALLBACKS,
                    router=Router(name="shared"),
                ),
            )
        )


def test_callback_namespace_collision_is_rejected() -> None:
    with pytest.raises(RouteCollisionError, match="callback namespace"):
        RouterRegistry(
            (
                _feature(
                    "first",
                    RoutePriority.CALLBACKS,
                    router=Router(name="first"),
                    callbacks=("auction",),
                ),
                _feature(
                    "second",
                    RoutePriority.CALLBACKS,
                    router=Router(name="second"),
                    callbacks=("AUCTION",),
                ),
            )
        )


def test_exact_routes_are_composed_before_broad_fsm_and_fallback() -> None:
    registry = RouterRegistry(
        (
            _feature(
                "fallback",
                RoutePriority.FALLBACK,
                router=Router(name="fallback"),
            ),
            _feature(
                "broad",
                RoutePriority.BROAD_FSM,
                router=Router(name="broad"),
            ),
            _feature(
                "exact",
                RoutePriority.EXACT_COMMANDS,
                router=Router(name="exact"),
                commands=("start",),
            ),
        )
    )

    assert [feature.name for feature in registry.ordered_features] == [
        "exact",
        "broad",
        "fallback",
    ]


def test_dependencies_are_topologically_ordered_inside_priority_group() -> None:
    registry = RouterRegistry(
        (
            _feature(
                "base",
                RoutePriority.SETUP_FSM,
                router=Router(name="base"),
                dependencies=("narrow",),
            ),
            _feature(
                "narrow",
                RoutePriority.SETUP_FSM,
                router=Router(name="narrow"),
            ),
        )
    )

    assert [feature.name for feature in registry.ordered_features] == ["narrow", "base"]


def test_cycles_are_rejected_before_dispatcher_mutation() -> None:
    with pytest.raises(RouterDependencyError, match="cyclic"):
        RouterRegistry(
            (
                _feature(
                    "a",
                    RoutePriority.CALLBACKS,
                    router=Router(name="a"),
                    dependencies=("b",),
                ),
                _feature(
                    "b",
                    RoutePriority.CALLBACKS,
                    router=Router(name="b"),
                    dependencies=("a",),
                ),
            )
        )


def test_repeated_install_on_same_dispatcher_is_idempotent() -> None:
    calls: list[str] = []
    router = Router(name="only")
    registry = RouterRegistry(
        (
            _feature(
                "only",
                RoutePriority.EXACT_COMMANDS,
                router=router,
                hooks=(PrepareHook("only.prepare", lambda: calls.append("prepared")),),
            ),
        )
    )
    dispatcher = Dispatcher()

    registry.install(dispatcher)
    registry.install(dispatcher)

    assert calls == ["prepared"]
    assert dispatcher.sub_routers == [router]


def test_nested_router_attachment_is_registry_managed() -> None:
    parent = Router(name="parent")
    child = Router(name="child")
    registry = RouterRegistry(
        (
            _feature(
                "parent",
                RoutePriority.CALLBACKS,
                router=parent,
            ),
            _feature(
                "child",
                RoutePriority.CALLBACKS,
                router=child,
                parent="parent",
            ),
        )
    )
    dispatcher = Dispatcher()

    registry.install(dispatcher)

    assert dispatcher.sub_routers == [parent]
    assert parent.sub_routers == [child]
    assert child.parent_router is parent


def test_project_inventory_is_stable_unique_and_machine_readable() -> None:
    registry = get_router_registry()
    payload = json.loads(route_inventory_json())

    names = [entry["name"] for entry in payload]
    router_names = [entry["router_name"] for entry in payload if entry["router_name"]]
    commands = [command for entry in payload for command in entry["commands"]]
    namespaces = [
        namespace for entry in payload for namespace in entry["callback_namespaces"]
    ]

    assert names == [entry.name for entry in registry.inventory()]
    assert len(names) == len(set(names))
    assert len(router_names) == len(set(router_names))
    assert len(commands) == len(set(commands))
    assert len(namespaces) == len(set(namespaces))
    assert names.index("schedule.setup.fields") < names.index("schedule.setup.base")
    assert names.index("users.menu") < names.index("auctions.bidding")
    assert payload[0]["priority"] == "system_owner"
