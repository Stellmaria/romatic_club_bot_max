from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bot.use_cases.common import (
    ApplicationConflict,
    ApplicationInvalidState,
    ApplicationNotFound,
    ApplicationValidationError,
)

Row = dict[str, Any]
GetRequest = Callable[[int], Awaitable[Row | None]]
Decide = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ModerateUidCommand:
    request_id: int
    admin_id: int
    admin_username: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModeratedUidRequest:
    request: Row


class _ModerateUidUseCase:
    def __init__(
        self,
        *,
        get_request: GetRequest,
        decide: Decide,
        target: str,
        required_confirmations: int = 0,
    ) -> None:
        self._get_request = get_request
        self._decide = decide
        self._target = target
        self._required_confirmations = max(0, int(required_confirmations))

    @staticmethod
    def _confirmation_count(request: Row) -> int:
        confirmations = request.get("confirmations") or []
        return sum(
            1
            for item in confirmations
            if str(dict(item).get("status") or "").strip().lower() == "confirmed"
        )

    async def execute(self, command: ModerateUidCommand) -> ModeratedUidRequest:
        if int(command.request_id) <= 0 or int(command.admin_id) <= 0:
            raise ApplicationValidationError("request_id and admin_id must be positive")
        reason = (command.reason or "").strip() or None
        if self._target == "rejected" and not reason:
            raise ApplicationValidationError("rejection reason is required")

        request = await self._get_request(int(command.request_id))
        if not request:
            raise ApplicationNotFound("UID verification request not found")
        request = dict(request)
        if self._target == "approved":
            confirmed = self._confirmation_count(request)
            if confirmed < self._required_confirmations:
                raise ApplicationConflict(
                    "not enough confirmations",
                    details={
                        "confirmed": confirmed,
                        "required": self._required_confirmations,
                    },
                )

        kwargs: dict[str, Any] = {
            "request_id": int(command.request_id),
            "admin_id": int(command.admin_id),
        }
        if self._target == "approved":
            kwargs["admin_username"] = command.admin_username
        else:
            kwargs["admin_comment"] = reason

        result = await self._decide(**kwargs)
        ok = bool(getattr(result, "ok", result[0] if isinstance(result, tuple) else result))
        code = str(
            getattr(
                result,
                "code",
                result[1] if isinstance(result, tuple) and len(result) > 1 else "",
            )
            or ""
        )
        if not ok:
            if code == "not_found":
                raise ApplicationNotFound("UID verification request not found")
            if code.startswith("conflict"):
                raise ApplicationConflict(code)
            raise ApplicationInvalidState(
                "UID verification request cannot be processed",
                details={"reason": code or "already_processed"},
            )

        try:
            after = await self._get_request(int(command.request_id))
        except Exception:
            after = None
        fallback = dict(request)
        fallback["status"] = self._target
        return ModeratedUidRequest(request=dict(after or fallback))


class ApproveUidVerificationUseCase(_ModerateUidUseCase):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(target="approved", **kwargs)


class RejectUidVerificationUseCase(_ModerateUidUseCase):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(target="rejected", **kwargs)
