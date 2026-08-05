"""Policy validation and orchestration for approved temporary cleanup."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from bot.application_ports import Clock
from bot.repositories.privacy_cleanup import (
    PrivacyCleanupExecution,
    PrivacyCleanupRepository,
)

DEFAULT_INVENTORY_PATH = Path("docs/privacy/data_inventory.json")
_APPROVED_RETENTION_CLASS = "temporary_7d"
_APPROVED_DATASET_ID = "schedule_operator_state"
_APPROVED_RULE_ID = "expired_schedule_setup_sessions"
_APPROVED_PLANNER_KEY = "schedule_setup_sessions"


class InventoryError(ValueError):
    """Raised when cleanup policy is incomplete, inconsistent, or unsafe."""


class PrivacyCleanupConfirmationError(ValueError):
    """Raised when an operator confirmation does not match the current plan."""


@dataclass(frozen=True, slots=True)
class PrivacyCleanupPolicy:
    policy_sha256: str
    retention_days: int
    dataset_id: str
    rule_id: str
    planner_key: str


@dataclass(frozen=True, slots=True)
class PrivacyCleanupPlan:
    schema_version: int
    run_id: str
    generated_at: datetime
    cutoff: datetime
    policy_sha256: str
    plan_sha256: str
    dataset_id: str
    rule_id: str
    retention_days: int
    eligible_rows: int
    delete_limit: int

    @property
    def confirmation_token(self) -> str:
        return f"APPLY:{self.plan_sha256}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "generated_at": self.generated_at.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "policy_sha256": self.policy_sha256,
            "plan_sha256": self.plan_sha256,
            "dataset_id": self.dataset_id,
            "rule_id": self.rule_id,
            "retention_days": self.retention_days,
            "eligible_rows": self.eligible_rows,
            "delete_limit": self.delete_limit,
            "planned_deletions": min(self.eligible_rows, self.delete_limit),
            "confirmation_token": self.confirmation_token,
            "contains_personal_values": False,
        }


@dataclass(frozen=True, slots=True)
class PrivacyCleanupResult:
    status: str
    plan: PrivacyCleanupPlan
    deleted_rows: int
    audit_id: int | None


def load_inventory(path: Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise InventoryError(f"privacy inventory not found: {path}") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InventoryError(f"privacy inventory is invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise InventoryError("privacy inventory root must be an object")
    return payload


def inventory_sha256(path: Path = DEFAULT_INVENTORY_PATH) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise InventoryError(f"privacy inventory not found: {path}") from error
    return hashlib.sha256(raw).hexdigest()


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InventoryError(f"{field} must be a non-empty string")
    return value


def _validate_retention_classes(value: object) -> Mapping[str, Mapping[str, object]]:
    if not isinstance(value, dict) or not value:
        raise InventoryError("retention_classes must be a non-empty object")

    result: dict[str, Mapping[str, object]] = {}
    for raw_name, raw_policy in value.items():
        name = _string(raw_name, field="retention class name")
        if not isinstance(raw_policy, dict):
            raise InventoryError(f"retention class {name!r} must be an object")
        days = raw_policy.get("days")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days <= 0):
            raise InventoryError(f"retention class {name!r} days must be positive")
        enabled = raw_policy.get("destructive_enabled")
        if not isinstance(enabled, bool):
            raise InventoryError(f"retention class {name!r} must declare destructive_enabled")
        if enabled and name != _APPROVED_RETENTION_CLASS:
            raise InventoryError(
                f"retention class {name!r} is not allowlisted for destructive cleanup"
            )
        if enabled and (raw_policy.get("status") != "approved" or not isinstance(days, int)):
            raise InventoryError(f"retention class {name!r} requires approved finite retention")
        result[name] = raw_policy
    return result


def validate_inventory(inventory: Mapping[str, Any]) -> None:
    if inventory.get("schema_version") != 1:
        raise InventoryError("privacy inventory schema_version must be 1")
    retention_classes = _validate_retention_classes(inventory.get("retention_classes"))
    datasets = inventory.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise InventoryError("datasets must be a non-empty array")

    seen_ids: set[str] = set()
    seen_tables: set[str] = set()
    enabled_rules: list[tuple[str, Mapping[str, object]]] = []
    for raw_dataset in datasets:
        if not isinstance(raw_dataset, dict):
            raise InventoryError("each dataset must be an object")
        dataset_id = _string(raw_dataset.get("id"), field="dataset id")
        if dataset_id in seen_ids:
            raise InventoryError(f"duplicate dataset id: {dataset_id}")
        seen_ids.add(dataset_id)

        tables = raw_dataset.get("tables")
        if (
            not isinstance(tables, list)
            or not tables
            or not all(isinstance(table, str) and table for table in tables)
        ):
            raise InventoryError(f"dataset {dataset_id!r} tables are invalid")
        overlap = seen_tables.intersection(tables)
        if overlap:
            raise InventoryError(f"tables assigned to multiple datasets: {sorted(overlap)}")
        seen_tables.update(tables)

        retention_class = raw_dataset.get("retention_class")
        if retention_class not in retention_classes:
            raise InventoryError(f"dataset {dataset_id!r} references unknown retention class")
        if raw_dataset.get("backup_presence") is not True:
            raise InventoryError(f"dataset {dataset_id!r} must acknowledge backup presence")

        rules = raw_dataset.get("cleanup_rules", [])
        if not isinstance(rules, list):
            raise InventoryError(f"dataset {dataset_id!r} cleanup_rules must be an array")
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                raise InventoryError(f"dataset {dataset_id!r} cleanup rule must be an object")
            enabled = raw_rule.get("destructive_enabled")
            if not isinstance(enabled, bool):
                raise InventoryError(
                    f"cleanup rule in {dataset_id!r} must declare destructive_enabled"
                )
            if enabled:
                enabled_rules.append((dataset_id, raw_rule))

    temporary_policy = retention_classes.get(_APPROVED_RETENTION_CLASS)
    if temporary_policy is None:
        raise InventoryError("temporary_7d retention class is missing")
    if temporary_policy.get("destructive_enabled") is not True:
        raise InventoryError("temporary_7d destructive cleanup is not enabled")
    if temporary_policy.get("status") != "approved":
        raise InventoryError("temporary_7d must remain explicitly approved")

    if len(enabled_rules) != 1:
        raise InventoryError("exactly one cleanup rule must be destructively enabled")
    dataset_id, rule = enabled_rules[0]
    if dataset_id != _APPROVED_DATASET_ID:
        raise InventoryError("enabled cleanup rule belongs to an unexpected dataset")
    if rule.get("id") != _APPROVED_RULE_ID:
        raise InventoryError("enabled cleanup rule id is not allowlisted")
    if rule.get("planner_key") != _APPROVED_PLANNER_KEY:
        raise InventoryError("enabled cleanup planner key is not allowlisted")
    if rule.get("status") != "approved":
        raise InventoryError("enabled cleanup rule must remain explicitly approved")

    schedule_dataset = next(
        (
            dataset
            for dataset in datasets
            if isinstance(dataset, dict) and dataset.get("id") == _APPROVED_DATASET_ID
        ),
        None,
    )
    if schedule_dataset is None:
        raise InventoryError("schedule_operator_state dataset is missing")
    tables = schedule_dataset.get("tables")
    if _APPROVED_PLANNER_KEY not in tables:
        raise InventoryError("approved cleanup table is absent from its dataset")
    if "schedule_setup_deck_scopes" in tables:
        raise InventoryError("inventory references nonexistent table schedule_setup_deck_scopes")


def load_cleanup_policy(
    path: Path = DEFAULT_INVENTORY_PATH,
) -> PrivacyCleanupPolicy:
    inventory = load_inventory(path)
    validate_inventory(inventory)
    policy = inventory["retention_classes"][_APPROVED_RETENTION_CLASS]
    return PrivacyCleanupPolicy(
        policy_sha256=inventory_sha256(path),
        retention_days=int(policy["days"]),
        dataset_id=_APPROVED_DATASET_ID,
        rule_id=_APPROVED_RULE_ID,
        planner_key=_APPROVED_PLANNER_KEY,
    )


def _plan_sha256(
    *,
    policy: PrivacyCleanupPolicy,
    cutoff: datetime,
    eligible_rows: int,
    delete_limit: int,
) -> str:
    canonical = json.dumps(
        {
            "cutoff": cutoff.isoformat(),
            "dataset_id": policy.dataset_id,
            "delete_limit": delete_limit,
            "eligible_rows": eligible_rows,
            "planner_key": policy.planner_key,
            "policy_sha256": policy.policy_sha256,
            "retention_days": policy.retention_days,
            "rule_id": policy.rule_id,
            "schema_version": 1,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PrivacyCleanupService:
    """Plan and execute only the explicitly approved temporary cleanup rule."""

    def __init__(
        self,
        repository: PrivacyCleanupRepository,
        *,
        clock: Clock,
        inventory_path: Path = DEFAULT_INVENTORY_PATH,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._inventory_path = inventory_path

    async def build_plan(self, *, batch_limit: int = 1_000) -> PrivacyCleanupPlan:
        if batch_limit <= 0 or batch_limit > 10_000:
            raise ValueError("batch_limit must be between 1 and 10000")
        policy = load_cleanup_policy(self._inventory_path)
        generated_at = self._clock.now().astimezone(UTC)
        day_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = day_start - timedelta(days=policy.retention_days)
        eligible_rows = await self._repository.count_expired_schedule_sessions(cutoff=cutoff)
        plan_sha256 = _plan_sha256(
            policy=policy,
            cutoff=cutoff,
            eligible_rows=eligible_rows,
            delete_limit=batch_limit,
        )
        return PrivacyCleanupPlan(
            schema_version=1,
            run_id=f"privacy-cleanup-{uuid.uuid4().hex}",
            generated_at=generated_at,
            cutoff=cutoff,
            policy_sha256=policy.policy_sha256,
            plan_sha256=plan_sha256,
            dataset_id=policy.dataset_id,
            rule_id=policy.rule_id,
            retention_days=policy.retention_days,
            eligible_rows=eligible_rows,
            delete_limit=batch_limit,
        )

    async def apply_plan(
        self,
        plan: PrivacyCleanupPlan,
        *,
        confirmation: str | None = None,
        automated: bool = False,
    ) -> PrivacyCleanupResult:
        current_policy = load_cleanup_policy(self._inventory_path)
        if current_policy.policy_sha256 != plan.policy_sha256:
            raise PrivacyCleanupConfirmationError("privacy inventory changed after plan generation")
        if not automated and confirmation != plan.confirmation_token:
            raise PrivacyCleanupConfirmationError(
                "confirmation must exactly match the current plan token"
            )
        if plan.eligible_rows == 0:
            return PrivacyCleanupResult(
                status="noop",
                plan=plan,
                deleted_rows=0,
                audit_id=None,
            )

        execution: PrivacyCleanupExecution = await self._repository.apply_expired_schedule_sessions(
            cutoff=plan.cutoff,
            expected_eligible_rows=plan.eligible_rows,
            delete_limit=plan.delete_limit,
            policy_sha256=plan.policy_sha256,
            plan_sha256=plan.plan_sha256,
            run_id=plan.run_id,
        )
        return PrivacyCleanupResult(
            status="applied",
            plan=plan,
            deleted_rows=execution.deleted_rows,
            audit_id=execution.audit_id,
        )

    async def run_approved_cleanup(
        self,
        *,
        batch_limit: int = 1_000,
    ) -> PrivacyCleanupResult:
        plan = await self.build_plan(batch_limit=batch_limit)
        return await self.apply_plan(plan, automated=True)


__all__ = [
    "DEFAULT_INVENTORY_PATH",
    "InventoryError",
    "PrivacyCleanupConfirmationError",
    "PrivacyCleanupPlan",
    "PrivacyCleanupPolicy",
    "PrivacyCleanupResult",
    "PrivacyCleanupService",
    "inventory_sha256",
    "load_cleanup_policy",
    "load_inventory",
    "validate_inventory",
]
