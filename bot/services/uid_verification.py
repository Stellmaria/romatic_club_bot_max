from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.repositories.uid_verification import UIDApprovalResult, UIDVerificationRepository
from db.core import get_db_pool


@dataclass(slots=True)
class UIDVerificationService:
    repository: UIDVerificationRepository

    @classmethod
    async def create(cls) -> "UIDVerificationService":
        return cls(UIDVerificationRepository(await get_db_pool()))

    async def get_verified_uid(self, user_id: int) -> str | None:
        return await self.repository.get_verified_uid_for_user(user_id)

    async def get_latest_request(self, user_id: int) -> dict[str, Any] | None:
        return await self.repository.get_latest_request(user_id)

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        return await self.repository.get_request(request_id)

    async def create_request(self, **kwargs: Any) -> int:
        return await self.repository.create_request(**kwargs)

    async def approve_request(self, **kwargs: Any) -> UIDApprovalResult:
        return await self.repository.approve_request(**kwargs)

    async def reject_request(self, **kwargs: Any) -> UIDApprovalResult:
        return await self.repository.reject_request(**kwargs)

    async def claim_due_reminders(self, *, stage_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        return await self.repository.claim_due_reminders(
            stage_h=stage_h,
            minimum_confirmations=minimum_confirmations,
        )

    async def expire_due_requests(self, *, ttl_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        return await self.repository.expire_due_requests(
            ttl_h=ttl_h,
            minimum_confirmations=minimum_confirmations,
        )
