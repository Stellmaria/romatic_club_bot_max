from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bot.repositories.privacy_cleanup import PrivacyCleanupExecution
from bot.services.privacy_cleanup import (
    InventoryError,
    PrivacyCleanupConfirmationError,
    PrivacyCleanupService,
    load_inventory,
    validate_inventory,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class FakeRepository:
    def __init__(self, count: int) -> None:
        self.count = count
        self.applied: list[dict[str, object]] = []

    async def count_expired_schedule_sessions(self, *, cutoff: datetime) -> int:
        return self.count

    async def apply_expired_schedule_sessions(self, **kwargs: object) -> PrivacyCleanupExecution:
        self.applied.append(dict(kwargs))
        return PrivacyCleanupExecution(
            deleted_rows=min(
                int(kwargs["expected_eligible_rows"]),
                int(kwargs["delete_limit"]),
            ),
            audit_id=17,
        )


def _inventory(*, enabled: bool = True, status: str = "approved") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "retention_classes": {
            "temporary_7d": {
                "days": 7,
                "status": status,
                "purpose": "temporary operator state",
                "destructive_enabled": enabled,
            },
            "business_hold": {
                "days": None,
                "status": "owner-legal-decision-required",
                "purpose": "business history",
                "destructive_enabled": False,
            },
        },
        "datasets": [
            {
                "id": "schedule_operator_state",
                "tables": ["schedule_setup_sessions"],
                "data_fields": ["user_id", "workflow state"],
                "purpose": "temporary workflow",
                "sensitivity": "moderate",
                "access_roles": ["application"],
                "retention_class": "temporary_7d",
                "backup_presence": True,
                "deletion_action": "delete expired inactive state",
                "exceptions": ["active workflow"],
                "cleanup_rules": [
                    {
                        "id": "expired_schedule_setup_sessions",
                        "planner_key": "schedule_setup_sessions",
                        "status": "approved",
                        "destructive_enabled": enabled,
                    }
                ],
            }
        ],
    }


def _write_inventory(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_plan_hash_is_stable_for_same_day_count_and_policy(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, _inventory())
    repository = FakeRepository(12)
    service = PrivacyCleanupService(
        repository,  # type: ignore[arg-type]
        clock=FixedClock(datetime(2026, 8, 5, 11, 30, tzinfo=UTC)),
        inventory_path=inventory_path,
    )

    first = await service.build_plan(batch_limit=5)
    second = await service.build_plan(batch_limit=5)

    assert first.plan_sha256 == second.plan_sha256
    assert first.cutoff == datetime(2026, 7, 29, tzinfo=UTC)
    assert first.eligible_rows == 12
    assert first.to_dict()["planned_deletions"] == 5
    assert first.confirmation_token == f"APPLY:{first.plan_sha256}"


@pytest.mark.asyncio
async def test_manual_apply_requires_exact_plan_confirmation(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, _inventory())
    repository = FakeRepository(3)
    service = PrivacyCleanupService(
        repository,  # type: ignore[arg-type]
        clock=FixedClock(datetime(2026, 8, 5, 1, tzinfo=UTC)),
        inventory_path=inventory_path,
    )
    plan = await service.build_plan(batch_limit=2)

    with pytest.raises(PrivacyCleanupConfirmationError):
        await service.apply_plan(plan, confirmation="APPLY:wrong")

    result = await service.apply_plan(
        plan,
        confirmation=plan.confirmation_token,
    )
    assert result.status == "applied"
    assert result.deleted_rows == 2
    assert result.audit_id == 17
    assert len(repository.applied) == 1


def test_inventory_fails_closed_for_unapproved_destructive_policy(
    tmp_path: Path,
) -> None:
    payload = _inventory(status="proposed")
    path = _write_inventory(tmp_path, payload)

    with pytest.raises(InventoryError, match="approved finite retention"):
        validate_inventory(load_inventory(path))


def test_inventory_rejects_nonexistent_deck_scope_table(tmp_path: Path) -> None:
    payload = _inventory()
    payload["datasets"][0]["tables"].append("schedule_setup_deck_scopes")
    path = _write_inventory(tmp_path, payload)

    with pytest.raises(InventoryError, match="nonexistent table"):
        validate_inventory(load_inventory(path))
