from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from bot.core.settings import SupervisorClientSettings


class SupervisorUnavailable(RuntimeError):
    """Raised when the host-side Supervisor cannot be reached."""


@dataclass(slots=True)
class SupervisorClient:
    base_url: str
    token: str
    timeout_seconds: float = 20.0
    default_actor: str = "telegram-bot"
    _session: aiohttp.ClientSession | None = field(default=None, init=False, repr=False)
    _session_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: SupervisorClientSettings,
    ) -> "SupervisorClient | None":
        """Create an optional client from a validated process configuration."""

        if not settings.enabled:
            return None
        return cls(
            base_url=settings.base_url,
            token=settings.token,
            timeout_seconds=settings.timeout_seconds,
            default_actor=settings.actor,
        )

    async def start(self) -> None:
        if self._session is not None and not self._session.closed:
            return
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        async with self._session_lock:
            session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        await self.start()
        session = self._session
        if session is None:
            raise SupervisorUnavailable("Supervisor HTTP session is unavailable")

        normalized_method = method.upper()
        operation_request_id = (request_id or uuid.uuid4().hex).strip()[:64]
        operation_actor = (actor or self.default_actor).strip()[:64] or self.default_actor
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Request-ID": operation_request_id,
            "X-Actor": operation_actor,
        }

        for attempt in range(2):
            try:
                async with session.request(
                    normalized_method,
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=headers,
                ) as response:
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise SupervisorUnavailable("Некорректный ответ Supervisor")
                    if response.status >= 400:
                        detail = str(
                            data.get("error")
                            or data.get("message")
                            or response.status
                        )
                        raise SupervisorUnavailable(detail)
                    return data
            except SupervisorUnavailable:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                if attempt >= 1:
                    raise SupervisorUnavailable(str(exc)) from exc
                await asyncio.sleep(0.2)

        raise SupervisorUnavailable("Supervisor request failed")

    async def status(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("GET", "/v1/status", actor=actor)

    async def logs(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("GET", "/v1/logs", actor=actor)

    async def restart(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("POST", "/v1/restart", actor=actor)

    async def restart_userbot(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("POST", "/v1/restart-userbot", actor=actor)

    async def update(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("POST", "/v1/update", actor=actor)

    async def rollback(self, *, actor: str | None = None) -> dict[str, Any]:
        return await self.request("POST", "/v1/rollback", actor=actor)


__all__ = ("SupervisorClient", "SupervisorUnavailable")
