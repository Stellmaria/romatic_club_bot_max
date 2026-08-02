"""Framework-neutral role and trusted-status mutations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from bot.use_cases.common import ApplicationConflict, ApplicationPermissionDenied


class RoleKind(str, Enum):
    ADMIN = "admin"
    TRUSTED = "trusted"


@dataclass(frozen=True, slots=True)
class ChangeRoleCommand:
    target_id: int
    target_username: str | None
    actor_id: int
    actor_username: str | None
    role: RoleKind
    grant: bool
    owner_ids: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class RoleChange:
    command: ChangeRoleCommand
    action_type: str
    details: str


class ChangeRoleUseCase:
    def __init__(
        self,
        *,
        is_admin: Callable[[int], Awaitable[bool]],
        add_admin: Callable[[int, str | None, int], Awaitable[object]],
        remove_admin: Callable[[int], Awaitable[object]],
        set_trusted: Callable[[int, bool], Awaitable[object]],
        audit: Callable[..., Awaitable[object]],
    ) -> None:
        self._is_admin = is_admin
        self._add_admin = add_admin
        self._remove_admin = remove_admin
        self._set_trusted = set_trusted
        self._audit = audit

    async def execute(self, command: ChangeRoleCommand) -> RoleChange:
        username = (command.target_username or "").strip().lstrip("@") or None
        if command.role is RoleKind.ADMIN:
            if not command.grant and command.target_id in command.owner_ids:
                raise ApplicationPermissionDenied(
                    "Нельзя удалить владельца из администраторов.", code="owner_role_protected"
                )
            if not command.grant and command.target_id == command.actor_id:
                raise ApplicationPermissionDenied(
                    "Нельзя удалить собственную роль администратора.", code="self_role_protected"
                )
            already_admin = bool(await self._is_admin(command.target_id))
            if command.grant and already_admin:
                raise ApplicationConflict(
                    "Пользователь уже является администратором.", code="admin_already_granted"
                )
            if command.grant:
                await self._add_admin(command.target_id, username, command.actor_id)
                action = "add_admin"
                details = f"Добавлен админ {command.target_id} (@{username or 'no_username'})"
            else:
                await self._remove_admin(command.target_id)
                action = "remove_admin"
                details = f"Удалён админ {command.target_id} (@{username or 'no_username'})"
        else:
            await self._set_trusted(command.target_id, command.grant)
            action = "give_trusted" if command.grant else "remove_trusted"
            details = (
                ("Выдан" if command.grant else "Снят")
                + f" trusted @{username or command.target_id} (id {command.target_id})"
            )
        await self._audit(
            user_id=command.actor_id,
            action_type=action,
            auction_id=None,
            details=details,
        )
        return RoleChange(command=command, action_type=action, details=details)
