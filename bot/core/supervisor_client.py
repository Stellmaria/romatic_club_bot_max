from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import aiohttp


class SupervisorUnavailable(RuntimeError):
    """Raised when the host-side Supervisor cannot be reached."""


@dataclass(frozen=True, slots=True)
class SupervisorClient:
    base_url: str
    token: str
    timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "SupervisorClient | None":
        enabled = os.getenv("SUPERVISOR_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        base_url = os.getenv("SUPERVISOR_BASE_URL", "").strip().rstrip("/")
        token = os.getenv("SUPERVISOR_TOKEN", "").strip()
        if not base_url or len(token) < 24:
            return None
        try:
            timeout = float(os.getenv("SUPERVISOR_CLIENT_TIMEOUT_SECONDS", "20"))
        except ValueError:
            timeout = 20.0
        return cls(base_url=base_url, token=token, timeout_seconds=max(2.0, timeout))

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        detail = str(data.get("error") or data.get("message") or response.status)
                        raise SupervisorUnavailable(detail)
                    if not isinstance(data, dict):
                        raise SupervisorUnavailable("Некорректный ответ Supervisor")
                    return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise SupervisorUnavailable(str(exc)) from exc

    async def status(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/status")

    async def logs(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/logs")

    async def restart(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/restart")

    async def restart_userbot(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/restart-userbot")

    async def update(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/update")

    async def rollback(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/rollback")


supervisor_client = SupervisorClient.from_env()


__all__ = (
    "SupervisorClient",
    "SupervisorUnavailable",
    "supervisor_client",
)
