from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from db.profile_sync import sync_user_profile

logger = logging.getLogger("auction_bot.user_sync")

ProfileSync = Callable[[int, str, str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class _Profile:
    username: str
    full_name: str


class UserSyncMiddleware(BaseMiddleware):
    """Debounce Telegram profile persistence outside the update critical path."""

    def __init__(
        self,
        *,
        profile_sync: ProfileSync = sync_user_profile,
        cache_size: int = 4_096,
        debounce_seconds: float = 0.050,
        timeout_seconds: float = 0.750,
        max_concurrency: int = 8,
        max_pending: int = 4_096,
    ) -> None:
        self._profile_sync = profile_sync
        self._cache_size = max(1, int(cache_size))
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._timeout_seconds = max(0.050, float(timeout_seconds))
        self._max_pending = max(1, int(max_pending))
        self._write_slots = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._known_profiles: OrderedDict[int, _Profile] = OrderedDict()
        self._pending: dict[int, asyncio.Task[None]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user") or getattr(
            event,
            "from_user",
            None,
        )
        if user and not user.is_bot:
            self.schedule(user)
        return await handler(event, data)

    def schedule(self, user: User) -> bool:
        """Schedule one changed profile and return whether work was created."""

        user_id = int(user.id)
        profile = _Profile(
            username=(user.username or "").strip().lstrip("@"),
            full_name=" ".join(
                filter(None, [user.first_name, user.last_name])
            ).strip(),
        )
        if self._known_profiles.get(user_id) == profile:
            self._known_profiles.move_to_end(user_id)
            return False

        if user_id not in self._pending and len(self._pending) >= self._max_pending:
            self._known_profiles.pop(user_id, None)
            logger.warning(
                "User profile sync backlog full pending=%d user_id=%s",
                len(self._pending),
                user_id,
            )
            return False

        self._known_profiles[user_id] = profile
        self._known_profiles.move_to_end(user_id)
        previous = self._pending.get(user_id)
        task = asyncio.create_task(
            self._sync_after(previous, user_id, profile),
            name=f"user-profile-sync:{user_id}",
        )
        self._pending[user_id] = task
        task.add_done_callback(
            lambda completed, uid=user_id: self._task_finished(uid, completed)
        )
        self._trim_cache()
        return True

    async def _sync_after(
        self,
        previous: asyncio.Task[None] | None,
        user_id: int,
        profile: _Profile,
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        if self._debounce_seconds:
            await asyncio.sleep(self._debounce_seconds)
        if self._known_profiles.get(user_id) != profile:
            return

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._write_slots:
                    await self._profile_sync(
                        user_id,
                        profile.username,
                        profile.full_name,
                    )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._invalidate(user_id, profile)
            logger.warning(
                "User profile sync timed out user_id=%s timeout_seconds=%.3f",
                user_id,
                self._timeout_seconds,
            )
        except Exception:
            self._invalidate(user_id, profile)
            logger.exception("User profile sync failed user_id=%s", user_id)

    def _invalidate(self, user_id: int, profile: _Profile) -> None:
        if self._known_profiles.get(user_id) == profile:
            self._known_profiles.pop(user_id, None)

    def _task_finished(self, user_id: int, task: asyncio.Task[None]) -> None:
        if self._pending.get(user_id) is task:
            self._pending.pop(user_id, None)
        if not task.cancelled():
            task.exception()
        self._trim_cache()

    def _trim_cache(self) -> None:
        attempts = 0
        while len(self._known_profiles) > self._cache_size:
            attempts += 1
            oldest_user_id = next(iter(self._known_profiles))
            pending = self._pending.get(oldest_user_id)
            if pending is not None and not pending.done():
                self._known_profiles.move_to_end(oldest_user_id)
                if attempts >= len(self._known_profiles):
                    break
                continue
            self._known_profiles.pop(oldest_user_id, None)

    async def drain(self) -> None:
        """Wait for currently scheduled writes; intended for shutdown and tests."""

        while self._pending:
            await asyncio.gather(*tuple(self._pending.values()), return_exceptions=True)

    async def close(self) -> None:
        tasks = tuple(self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()


__all__ = ["UserSyncMiddleware"]
