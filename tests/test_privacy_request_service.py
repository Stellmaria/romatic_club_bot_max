from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from bot.repositories.privacy_requests import PrivacyRequestPlan, PrivacyRequestRecord
from bot.services.privacy_requests import (
    PrivacyRequestAuthorizationError,
    PrivacyRequestService,
)
from bot.uid_crypto import configure_uid_crypto


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


class FakeRepository:
    def __init__(self) -> None:
        self.denied: list[tuple[str, dict[str, Any]]] = []
        self.created: dict[str, Any] | None = None
        self.executed: dict[str, Any] | None = None
        self.record = PrivacyRequestRecord(
            request_id=uuid4(),
            subject_digest="digest",
            status="pending_review",
            policy_sha256="policy",
            approved_plan_sha256=None,
            blocking_holds=(),
            retained_holds=(),
            outcome_counts={},
            requested_at=datetime(2026, 8, 5, tzinfo=UTC),
            updated_at=datetime(2026, 8, 5, tzinfo=UTC),
            completed_at=None,
        )

    async def append_denied_audit(self, *, action_type: str, details: dict[str, Any]) -> None:
        self.denied.append((action_type, details))

    async def create_request(self, **kwargs: Any) -> PrivacyRequestRecord:
        self.created = kwargs
        return self.record

    async def latest_for_subject(self, _subject_digest: str) -> PrivacyRequestRecord | None:
        return self.record

    async def cancel_request(self, **kwargs: Any) -> PrivacyRequestRecord:
        del kwargs
        return self.record

    async def plan_request(self, request_id: UUID) -> PrivacyRequestPlan:
        return PrivacyRequestPlan(
            request_id=request_id,
            status="approved",
            policy_sha256="policy",
            plan_sha256="a" * 64,
            blocking_holds=(),
            retained_holds=("business-history-retained",),
            action_counts={"profile_rows": 1},
        )

    async def approve_request(self, **kwargs: Any) -> PrivacyRequestPlan:
        return await self.plan_request(kwargs["request_id"])

    async def execute_request(self, **kwargs: Any) -> PrivacyRequestRecord:
        self.executed = kwargs
        return self.record


@pytest.fixture(autouse=True)
def _configure_crypto() -> None:
    configure_uid_crypto(
        "privacy-request-test-hmac-key",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )


@pytest.mark.asyncio
async def test_request_self_rejects_cross_subject_and_audits(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}", encoding="utf-8")
    repository = FakeRepository()
    service = PrivacyRequestService(  # type: ignore[arg-type]
        repository,
        clock=FixedClock(datetime(2026, 8, 5, tzinfo=UTC)),
        inventory_path=inventory,
    )

    with pytest.raises(PrivacyRequestAuthorizationError):
        await service.request_self(actor_user_id=1, subject_user_id=2)

    assert repository.denied[0][0] == "privacy.request.create.denied"
    assert repository.denied[0][1]["contains_personal_values"] is False


@pytest.mark.asyncio
async def test_execute_operator_requires_exact_confirmation(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}", encoding="utf-8")
    repository = FakeRepository()
    service = PrivacyRequestService(  # type: ignore[arg-type]
        repository,
        clock=FixedClock(datetime(2026, 8, 5, tzinfo=UTC)),
        inventory_path=inventory,
    )
    request_id = uuid4()
    plan_sha = "b" * 64

    with pytest.raises(ValueError):
        await service.execute_operator(
            request_id=request_id,
            expected_plan_sha256=plan_sha,
            operator_identity="operator",
            confirmation="wrong",
        )

    confirmation = f"APPLY:{request_id}:{plan_sha[:12]}"
    await service.execute_operator(
        request_id=request_id,
        expected_plan_sha256=plan_sha,
        operator_identity="operator",
        confirmation=confirmation,
    )
    assert repository.executed is not None
    assert repository.executed["expected_plan_sha256"] == plan_sha
