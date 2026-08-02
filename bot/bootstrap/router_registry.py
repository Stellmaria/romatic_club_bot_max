"""Declarative and validated aiogram router composition."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from enum import IntEnum, StrEnum
from typing import Any

from aiogram import Dispatcher, Router


class RoutePriority(IntEnum):
    """Stable dispatch groups from narrow system routes to broad fallbacks."""

    SYSTEM_OWNER = 10
    SETUP_FSM = 20
    EXACT_COMMANDS = 30
    CALLBACKS = 40
    BROAD_FSM = 50
    FALLBACK = 60


class MiddlewareScope(StrEnum):
    UPDATE = "update"
    MESSAGE = "message"
    CALLBACK_QUERY = "callback_query"


class RouterRegistryError(RuntimeError):
    """Base class for invalid or unsafe router composition."""


class RouteCollisionError(RouterRegistryError):
    """Raised when two features claim the same public route identifier."""


class RouterDependencyError(RouterRegistryError):
    """Raised when feature dependencies are missing or cyclic."""


MiddlewareFactory = Callable[[], object]
PrepareCallback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class MiddlewareRegistration:
    name: str
    scope: MiddlewareScope
    factory: MiddlewareFactory
    debug_only: bool = False


@dataclass(frozen=True, slots=True)
class PrepareHook:
    """Idempotence key and callback for legacy handler registration."""

    name: str
    callback: PrepareCallback


@dataclass(frozen=True, slots=True)
class FeatureRegistration:
    """One feature's complete registration contract."""

    name: str
    priority: RoutePriority
    router: Router | None = None
    parent: str | None = None
    dependencies: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    callback_namespaces: tuple[str, ...] = ()
    middlewares: tuple[MiddlewareRegistration, ...] = ()
    prepare_hooks: tuple[PrepareHook, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class RouteInventoryEntry:
    order: int
    name: str
    priority: str
    router_name: str | None
    parent: str | None
    dependencies: tuple[str, ...]
    commands: tuple[str, ...]
    callback_namespaces: tuple[str, ...]
    middlewares: tuple[str, ...]
    description: str


_PREPARED_HOOKS: set[str] = set()
_INSTALLATION_KEY = "romatic_club.router_registry.signature"


def _normalize_command(command: str) -> str:
    normalized = command.strip().removeprefix("/").split("@", 1)[0].casefold()
    if not normalized or any(character.isspace() for character in normalized):
        raise RouterRegistryError(f"invalid command declaration: {command!r}")
    return normalized


def _normalize_namespace(namespace: str) -> str:
    normalized = namespace.strip().casefold()
    if not normalized:
        raise RouterRegistryError("callback namespace must not be empty")
    if any(character.isspace() for character in normalized):
        raise RouterRegistryError(f"invalid callback namespace: {namespace!r}")
    return normalized


class RouterRegistry:
    """Validate, order and install one immutable feature registry."""

    def __init__(self, features: Iterable[FeatureRegistration]) -> None:
        self._features = tuple(features)
        if not self._features:
            raise RouterRegistryError("router registry must contain at least one feature")
        self._by_name = self._validate_features(self._features)
        self._ordered = self._order_features(self._features, self._by_name)
        self._signature = self._build_signature(self._ordered)

    @property
    def signature(self) -> str:
        return self._signature

    @property
    def ordered_features(self) -> tuple[FeatureRegistration, ...]:
        return self._ordered

    def install(self, dispatcher: Dispatcher, *, debug_messages: bool = False) -> None:
        """Install the registry once on a dispatcher.

        Repeating the same composition is a no-op. Installing a different registry
        on an already composed dispatcher is rejected before any mutation.
        """

        workflow_data = dispatcher.workflow_data
        installed_signature = workflow_data.get(_INSTALLATION_KEY)
        if installed_signature == self._signature:
            return
        if installed_signature is not None:
            raise RouterRegistryError(
                "dispatcher already has a different router registry installed"
            )

        self._run_prepare_hooks()
        self._attach_nested_routers()
        self._install_middlewares(dispatcher, debug_messages=debug_messages)

        for feature in self._ordered:
            if feature.router is None or feature.parent is not None:
                continue
            if feature.router.parent_router is not None:
                raise RouterRegistryError(
                    f"router {feature.name!r} is already attached to another parent"
                )
            dispatcher.include_router(feature.router)

        workflow_data[_INSTALLATION_KEY] = self._signature

    def inventory(self) -> tuple[RouteInventoryEntry, ...]:
        entries: list[RouteInventoryEntry] = []
        for order, feature in enumerate(self._ordered, start=1):
            entries.append(
                RouteInventoryEntry(
                    order=order,
                    name=feature.name,
                    priority=feature.priority.name.lower(),
                    router_name=(feature.router.name if feature.router is not None else None),
                    parent=feature.parent,
                    dependencies=feature.dependencies,
                    commands=tuple(_normalize_command(item) for item in feature.commands),
                    callback_namespaces=tuple(
                        _normalize_namespace(item) for item in feature.callback_namespaces
                    ),
                    middlewares=tuple(item.name for item in feature.middlewares),
                    description=feature.description,
                )
            )
        return tuple(entries)

    def inventory_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            [asdict(entry) for entry in self.inventory()],
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )

    def _run_prepare_hooks(self) -> None:
        for feature in self._ordered:
            for hook in feature.prepare_hooks:
                if hook.name in _PREPARED_HOOKS:
                    continue
                hook.callback()
                _PREPARED_HOOKS.add(hook.name)

    def _attach_nested_routers(self) -> None:
        for feature in self._ordered:
            if feature.router is None or feature.parent is None:
                continue
            parent = self._by_name[feature.parent]
            if parent.router is None:
                raise RouterDependencyError(
                    f"feature {feature.name!r} has routerless parent {feature.parent!r}"
                )
            if feature.router.parent_router is parent.router:
                continue
            if feature.router.parent_router is not None:
                raise RouterRegistryError(
                    f"nested router {feature.name!r} is already attached elsewhere"
                )
            parent.router.include_router(feature.router)

    def _install_middlewares(
        self,
        dispatcher: Dispatcher,
        *,
        debug_messages: bool,
    ) -> None:
        installed_names: set[str] = set()
        for feature in self._ordered:
            for middleware in feature.middlewares:
                if middleware.debug_only and not debug_messages:
                    continue
                if middleware.name in installed_names:
                    raise RouteCollisionError(
                        f"middleware {middleware.name!r} is registered more than once"
                    )
                observer = getattr(dispatcher, middleware.scope.value)
                observer.outer_middleware(middleware.factory())
                installed_names.add(middleware.name)

    @staticmethod
    def _validate_features(
        features: Sequence[FeatureRegistration],
    ) -> dict[str, FeatureRegistration]:
        by_name: dict[str, FeatureRegistration] = {}
        router_owners: dict[str, str] = {}
        command_owners: dict[str, str] = {}
        namespace_owners: dict[str, str] = {}
        middleware_owners: dict[str, str] = {}
        hook_owners: dict[str, str] = {}

        for feature in features:
            name = feature.name.strip()
            if not name or name != feature.name:
                raise RouterRegistryError(f"invalid feature name: {feature.name!r}")
            if name in by_name:
                raise RouteCollisionError(f"duplicate feature name: {name!r}")
            by_name[name] = feature

            if feature.router is not None:
                router_name = feature.router.name.strip()
                if not router_name:
                    raise RouterRegistryError(
                        f"feature {name!r} has a router without a stable name"
                    )
                owner = router_owners.setdefault(router_name, name)
                if owner != name:
                    raise RouteCollisionError(
                        f"router name {router_name!r} belongs to {owner!r} and {name!r}"
                    )

            for command in feature.commands:
                normalized = _normalize_command(command)
                owner = command_owners.setdefault(normalized, name)
                if owner != name:
                    raise RouteCollisionError(
                        f"command /{normalized} is claimed by {owner!r} and {name!r}"
                    )

            for namespace in feature.callback_namespaces:
                normalized = _normalize_namespace(namespace)
                owner = namespace_owners.setdefault(normalized, name)
                if owner != name:
                    raise RouteCollisionError(
                        f"callback namespace {normalized!r} is claimed by "
                        f"{owner!r} and {name!r}"
                    )

            for middleware in feature.middlewares:
                owner = middleware_owners.setdefault(middleware.name, name)
                if owner != name:
                    raise RouteCollisionError(
                        f"middleware {middleware.name!r} belongs to {owner!r} and {name!r}"
                    )

            for hook in feature.prepare_hooks:
                owner = hook_owners.setdefault(hook.name, name)
                if owner != name:
                    raise RouteCollisionError(
                        f"prepare hook {hook.name!r} belongs to {owner!r} and {name!r}"
                    )

        for feature in features:
            dependencies = set(feature.dependencies)
            if feature.parent is not None:
                dependencies.add(feature.parent)
            for dependency in dependencies:
                if dependency not in by_name:
                    raise RouterDependencyError(
                        f"feature {feature.name!r} depends on unknown feature {dependency!r}"
                    )
                if dependency == feature.name:
                    raise RouterDependencyError(
                        f"feature {feature.name!r} cannot depend on itself"
                    )
                dependency_feature = by_name[dependency]
                if dependency_feature.priority > feature.priority:
                    raise RouterDependencyError(
                        f"feature {feature.name!r} depends on later priority feature "
                        f"{dependency!r}"
                    )

        return by_name

    @staticmethod
    def _order_features(
        features: Sequence[FeatureRegistration],
        by_name: dict[str, FeatureRegistration],
    ) -> tuple[FeatureRegistration, ...]:
        declaration_order = {feature.name: index for index, feature in enumerate(features)}
        ordered: list[FeatureRegistration] = []

        for priority in RoutePriority:
            group = [feature for feature in features if feature.priority is priority]
            if not group:
                continue
            names = {feature.name for feature in group}
            incoming: dict[str, set[str]] = {}
            children: dict[str, set[str]] = defaultdict(set)
            for feature in group:
                dependencies = set(feature.dependencies)
                if feature.parent is not None:
                    dependencies.add(feature.parent)
                local_dependencies = {item for item in dependencies if item in names}
                incoming[feature.name] = local_dependencies
                for dependency in local_dependencies:
                    children[dependency].add(feature.name)

            ready = sorted(
                (name for name, dependencies in incoming.items() if not dependencies),
                key=declaration_order.__getitem__,
            )
            emitted: list[str] = []
            while ready:
                name = ready.pop(0)
                emitted.append(name)
                for child in sorted(children[name], key=declaration_order.__getitem__):
                    incoming[child].discard(name)
                    if not incoming[child] and child not in ready and child not in emitted:
                        ready.append(child)
                        ready.sort(key=declaration_order.__getitem__)

            if len(emitted) != len(group):
                cyclic = sorted(name for name, dependencies in incoming.items() if dependencies)
                raise RouterDependencyError(
                    "cyclic router dependencies: " + ", ".join(cyclic)
                )
            ordered.extend(by_name[name] for name in emitted)

        return tuple(ordered)

    @staticmethod
    def _build_signature(features: Sequence[FeatureRegistration]) -> str:
        payload: list[dict[str, Any]] = []
        for feature in features:
            payload.append(
                {
                    "name": feature.name,
                    "router_name": feature.router.name if feature.router is not None else None,
                    "priority": int(feature.priority),
                    "parent": feature.parent,
                    "dependencies": feature.dependencies,
                    "commands": tuple(_normalize_command(item) for item in feature.commands),
                    "callback_namespaces": tuple(
                        _normalize_namespace(item) for item in feature.callback_namespaces
                    ),
                    "middlewares": tuple(
                        (item.name, item.scope.value, item.debug_only)
                        for item in feature.middlewares
                    ),
                    "prepare_hooks": tuple(item.name for item in feature.prepare_hooks),
                }
            )
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FeatureRegistration",
    "MiddlewareRegistration",
    "MiddlewareScope",
    "PrepareHook",
    "RouteCollisionError",
    "RouteInventoryEntry",
    "RoutePriority",
    "RouterDependencyError",
    "RouterRegistry",
    "RouterRegistryError",
]
