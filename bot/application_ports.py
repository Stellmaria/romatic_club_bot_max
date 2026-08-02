"""Framework-neutral ports used by application services and use cases."""

from __future__ import annotations

from collections.abc import AsyncContextManager, Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from bot.application_models import (
    AuctionRecord,
    ExchangeItemRecord,
    ExchangeRecord,
    OutboxRecord,
    UidVerificationRecord,
)

T = TypeVar("T")


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class TransactionManager(Protocol):
    def transaction(self) -> AsyncContextManager[object]: ...


@runtime_checkable
class AuctionRepositoryPort(Protocol):
    async def get(self, auction_id: int) -> AuctionRecord | None: ...
    async def schedule(
        self, auction_id: int, *, start_time: datetime, end_time: datetime
    ) -> AuctionRecord: ...
    async def cancel_by_owner(self, auction_id: int, *, owner_id: int) -> AuctionRecord: ...
    async def cancel_by_moderator(self, auction_id: int) -> AuctionRecord: ...


@runtime_checkable
class ExchangeRepositoryPort(Protocol):
    async def get(self, batch_id: int) -> ExchangeRecord | None: ...
    async def items(self, batch_id: int) -> Sequence[ExchangeItemRecord]: ...
    async def approve(
        self, batch_id: int, *, moderator_id: int, moderator_username: str | None
    ) -> ExchangeRecord: ...
    async def reject(
        self,
        batch_id: int,
        *,
        moderator_id: int,
        moderator_username: str | None,
        comment: str,
    ) -> ExchangeRecord: ...


@runtime_checkable
class UidVerificationRepositoryPort(Protocol):
    async def get(self, request_id: int) -> UidVerificationRecord | None: ...
    async def approve(self, request_id: int, *, admin_id: int) -> UidVerificationRecord: ...
    async def reject(
        self, request_id: int, *, admin_id: int, comment: str
    ) -> UidVerificationRecord: ...


@runtime_checkable
class OutboxRepositoryPort(Protocol):
    async def claim(self, *, limit: int) -> Sequence[OutboxRecord]: ...
    async def mark_delivered(self, event_id: int) -> None: ...
    async def mark_failed(self, event_id: int, *, error: str) -> None: ...


@runtime_checkable
class TelegramDeliveryPort(Protocol):
    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> int: ...


@runtime_checkable
class AuditPort(Protocol):
    async def write(
        self,
        *,
        actor_id: int,
        action: str,
        aggregate_id: int | str | None,
        details: Mapping[str, object],
    ) -> None: ...


@runtime_checkable
class FileStoragePort(Protocol):
    async def put(self, key: str, content: bytes) -> str: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...


@runtime_checkable
class SessionStoragePort(Protocol):
    async def load(self, key: str) -> Mapping[str, object] | None: ...
    async def save(self, key: str, value: Mapping[str, object]) -> None: ...
    async def clear(self, key: str) -> None: ...


class LocalFileStorage:
    """Small concrete adapter suitable for process-owned files and tests."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise ValueError("storage key escapes configured root")
        return candidate

    async def put(self, key: str, content: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


AsyncFactory = Callable[[], Awaitable[T]]


__all__ = [
    "AuditPort",
    "AuctionRepositoryPort",
    "Clock",
    "ExchangeRepositoryPort",
    "FileStoragePort",
    "LocalFileStorage",
    "OutboxRepositoryPort",
    "SessionStoragePort",
    "TelegramDeliveryPort",
    "TransactionManager",
    "UidVerificationRepositoryPort",
]
