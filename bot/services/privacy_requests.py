from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from bot.application_ports import Clock
from bot.repositories.privacy_requests import (
    PrivacyRequestBlocked,
    PrivacyRequestConflict,
    PrivacyRequestPlan,
    PrivacyRequestRecord,
    PrivacyRequestRepository,
)
from bot.uid_crypto import identity_digest

DEFAULT_INVENTORY_PATH = Path("docs/privacy/data_inventory.json")


class PrivacyRequestAuthorizationError(PermissionError):
    """Raised when a self-service privacy operation targets another subject."""


class PrivacyRequestService:
    """Coordinate authenticated request lifecycle without owning SQL boundaries."""

    def __init__(
        self,
        repository: PrivacyRequestRepository,
        *,
        clock: Clock,
        inventory_path: Path = DEFAULT_INVENTORY_PATH,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._inventory_path = inventory_path

    def _policy_sha256(self) -> str:
        return hashlib.sha256(self._inventory_path.read_bytes()).hexdigest()

    @staticmethod
    def _subject_digest(user_id: int) -> str:
        return identity_digest("privacy-request-subject", str(int(user_id)))

    async def _deny(self, *, action: str, actor_user_id: int, subject_user_id: int) -> None:
        await self._repository.append_denied_audit(
            action_type=f"privacy.request.{action}.denied",
            details={
                "schema_version": 1,
                "actor_digest": identity_digest("privacy-request-actor", str(int(actor_user_id))),
                "subject_digest": self._subject_digest(subject_user_id),
                "outcome": "denied",
                "reason": "self-service-subject-mismatch",
                "contains_personal_values": False,
            },
        )

    async def request_self(
        self,
        *,
        actor_user_id: int,
        subject_user_id: int,
    ) -> PrivacyRequestRecord:
        if int(actor_user_id) != int(subject_user_id):
            await self._deny(
                action="create",
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
            )
            raise PrivacyRequestAuthorizationError("privacy requests are self-service only")
        return await self._repository.create_request(
            request_id=uuid4(),
            subject_user_id=int(subject_user_id),
            subject_digest=self._subject_digest(subject_user_id),
            policy_sha256=self._policy_sha256(),
            requested_at=self._clock.now(),
        )

    async def status_self(
        self,
        *,
        actor_user_id: int,
        subject_user_id: int,
    ) -> PrivacyRequestRecord | None:
        if int(actor_user_id) != int(subject_user_id):
            await self._deny(
                action="status",
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
            )
            raise PrivacyRequestAuthorizationError("privacy status is self-service only")
        return await self._repository.latest_for_subject(self._subject_digest(subject_user_id))

    async def cancel_self(
        self,
        *,
        actor_user_id: int,
        subject_user_id: int,
    ) -> PrivacyRequestRecord:
        record = await self.status_self(
            actor_user_id=actor_user_id,
            subject_user_id=subject_user_id,
        )
        if record is None:
            raise LookupError("no privacy request exists")
        return await self._repository.cancel_request(
            request_id=record.request_id,
            subject_digest=record.subject_digest,
            cancelled_at=self._clock.now(),
        )

    async def plan_operator(self, request_id: UUID) -> PrivacyRequestPlan:
        return await self._repository.plan_request(request_id)

    async def approve_operator(
        self,
        *,
        request_id: UUID,
        expected_plan_sha256: str,
        operator_identity: str,
    ) -> PrivacyRequestPlan:
        return await self._repository.approve_request(
            request_id=request_id,
            expected_plan_sha256=expected_plan_sha256,
            operator_digest=identity_digest("privacy-operator", operator_identity),
            approved_at=self._clock.now(),
        )

    async def execute_operator(
        self,
        *,
        request_id: UUID,
        expected_plan_sha256: str,
        operator_identity: str,
        confirmation: str,
    ) -> PrivacyRequestRecord:
        expected_confirmation = f"APPLY:{request_id}:{expected_plan_sha256[:12]}"
        if confirmation != expected_confirmation:
            raise ValueError("explicit privacy execution confirmation does not match")
        return await self._repository.execute_request(
            request_id=request_id,
            expected_plan_sha256=expected_plan_sha256,
            operator_digest=identity_digest("privacy-operator", operator_identity),
            completed_at=self._clock.now(),
        )

    @staticmethod
    def plan_payload(plan: PrivacyRequestPlan) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "request_id": str(plan.request_id),
                "status": plan.status,
                "policy_sha256": plan.policy_sha256,
                "plan_sha256": plan.plan_sha256,
                "executable": plan.executable,
                "blocking_holds": list(plan.blocking_holds),
                "retained_holds": list(plan.retained_holds),
                "action_counts": plan.action_counts,
                "contains_personal_values": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = [
    "PrivacyRequestAuthorizationError",
    "PrivacyRequestBlocked",
    "PrivacyRequestConflict",
    "PrivacyRequestService",
]
