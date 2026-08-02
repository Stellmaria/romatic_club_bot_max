from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.domain.auctions import (
    AuctionNotFound,
    AuctionSlotConflict,
    InvalidAuctionTransition,
)
from bot.use_cases.common import (
    ApplicationConflict,
    ApplicationInvalidState,
    ApplicationNotFound,
    ApplicationTimeout,
    ApplicationValidationError,
)

Row = dict[str, Any]
GetLot = Callable[[int], Awaitable[Row | None]]
MutateSchedule = Callable[..., Awaitable[Row]]
GetOwners = Callable[[int], Awaitable[list[Row]]]
GetUser = Callable[[int], Awaitable[Row | None]]
IsLuxury = Callable[[int], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class OwnerSnapshot:
    user_id: int
    username: str | None
    full_name: str | None
    is_luxury: bool
    is_trusted: bool

    @property
    def display(self) -> str:
        if self.username:
            return f"{'👑 ' if self.is_luxury else ''}@{self.username}"
        return f"id:{self.user_id}"


@dataclass(frozen=True, slots=True)
class ScheduleAuctionCommand:
    auction_id: int
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True, slots=True)
class ScheduledAuction:
    lot: Row
    owners: tuple[OwnerSnapshot, ...]
    start_time: datetime
    end_time: datetime

    @property
    def owners_text(self) -> str:
        return ", ".join(owner.display for owner in self.owners) or "-"


@dataclass(frozen=True, slots=True)
class RescheduledAuction(ScheduledAuction):
    old_start_time: datetime
    old_end_time: datetime

    @property
    def owner_flags(self) -> str:
        flags: set[str] = set()
        for owner in self.owners:
            if owner.is_luxury:
                flags.add("Лакшери")
            if owner.is_trusted:
                flags.add("Доверенный")
        return ", ".join(sorted(flags)) if flags else "Обычный"


class _AuctionScheduleUseCase:
    def __init__(
        self,
        *,
        get_lot: GetLot,
        mutate: MutateSchedule,
        get_owners: GetOwners,
        get_user: GetUser,
        is_luxury: IsLuxury,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._get_lot = get_lot
        self._mutate = mutate
        self._get_owners = get_owners
        self._get_user = get_user
        self._is_luxury = is_luxury
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _validate(command: ScheduleAuctionCommand) -> None:
        if int(command.auction_id) <= 0:
            raise ApplicationValidationError("auction_id must be positive")
        if command.end_time <= command.start_time:
            raise ApplicationValidationError("end_time must be after start_time")

    async def _owner_snapshot(self, raw: Row) -> OwnerSnapshot:
        user_id = int(raw.get("user_id") or 0)
        user_result, luxury_result = await asyncio.gather(
            self._get_user(user_id),
            self._is_luxury(user_id),
            return_exceptions=True,
        )
        user = None if isinstance(user_result, BaseException) else user_result
        is_luxury = False if isinstance(luxury_result, BaseException) else bool(luxury_result)
        row = dict(user or raw)
        return OwnerSnapshot(
            user_id=user_id,
            username=(str(row.get("username") or "").strip() or None),
            full_name=(str(row.get("full_name") or "").strip() or None),
            is_luxury=is_luxury,
            is_trusted=bool(row.get("is_trusted")),
        )

    async def _owners(self, auction_id: int) -> tuple[OwnerSnapshot, ...]:
        try:
            raw = await self._get_owners(int(auction_id))
        except Exception:
            return ()
        snapshots = await asyncio.gather(
            *(self._owner_snapshot(dict(item)) for item in raw),
            return_exceptions=True,
        )
        return tuple(item for item in snapshots if isinstance(item, OwnerSnapshot))

    async def _commit(self, command: ScheduleAuctionCommand) -> Row:
        try:
            return dict(
                await asyncio.wait_for(
                    self._mutate(
                        int(command.auction_id),
                        start_time=command.start_time,
                        end_time=command.end_time,
                    ),
                    timeout=self._timeout_seconds,
                )
            )
        except asyncio.TimeoutError as exc:
            raise ApplicationTimeout("auction schedule mutation timed out") from exc
        except AuctionSlotConflict as exc:
            raise ApplicationConflict("auction slot is occupied") from exc
        except AuctionNotFound as exc:
            raise ApplicationNotFound("auction not found") from exc
        except InvalidAuctionTransition as exc:
            raise ApplicationInvalidState(
                "auction cannot enter scheduled state",
                details={"current": exc.current, "target": exc.target},
            ) from exc


class ScheduleAuctionUseCase(_AuctionScheduleUseCase):
    async def execute(self, command: ScheduleAuctionCommand) -> ScheduledAuction:
        self._validate(command)
        lot = await self._commit(command)
        owners = await self._owners(command.auction_id)
        return ScheduledAuction(
            lot=lot,
            owners=owners,
            start_time=lot.get("start_time") or command.start_time,
            end_time=lot.get("end_time") or command.end_time,
        )


class RescheduleAuctionUseCase(_AuctionScheduleUseCase):
    async def execute(self, command: ScheduleAuctionCommand) -> RescheduledAuction:
        self._validate(command)
        before = await self._get_lot(int(command.auction_id))
        if not before:
            raise ApplicationNotFound("auction not found")
        old_start = before.get("start_time")
        old_end = before.get("end_time")
        if not isinstance(old_start, datetime) or not isinstance(old_end, datetime):
            raise ApplicationInvalidState("auction has no persisted schedule")

        lot = await self._commit(command)
        actual_start = lot.get("start_time") or command.start_time
        actual_end = lot.get("end_time") or command.end_time
        if not isinstance(actual_start, datetime) or not isinstance(actual_end, datetime):
            raise ApplicationInvalidState("repository returned an invalid schedule")
        if (
            actual_start.replace(second=0, microsecond=0)
            != command.start_time.replace(second=0, microsecond=0)
            or actual_end.replace(microsecond=0) != command.end_time.replace(microsecond=0)
        ):
            raise ApplicationInvalidState(
                "persisted schedule differs from requested schedule",
                details={
                    "expected_start": command.start_time.isoformat(),
                    "expected_end": command.end_time.isoformat(),
                    "actual_start": actual_start.isoformat(),
                    "actual_end": actual_end.isoformat(),
                },
            )
        owners = await self._owners(command.auction_id)
        return RescheduledAuction(
            lot=lot,
            owners=owners,
            start_time=actual_start,
            end_time=actual_end,
            old_start_time=old_start,
            old_end_time=old_end,
        )
