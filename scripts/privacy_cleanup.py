from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

DEFAULT_INVENTORY_PATH = Path("docs/privacy/data_inventory.json")

_REQUIRED_DATASET_FIELDS = {
    "id",
    "tables",
    "data_fields",
    "purpose",
    "sensitivity",
    "access_roles",
    "retention_class",
    "backup_presence",
    "deletion_action",
    "exceptions",
}
_ALLOWED_SENSITIVITY = {"moderate", "high", "restricted"}
_ALLOWED_RETENTION_STATUS = {
    "approved",
    "proposed",
    "owner-legal-decision-required",
}
_COUNT_QUERIES = {
    "schedule_setup_sessions": """
        SELECT count(*)::bigint
        FROM public.schedule_setup_sessions
        WHERE updated_at < now() - make_interval(days => $1)
    """,
    "schedule_setup_deck_scopes": """
        SELECT count(*)::bigint
        FROM public.schedule_setup_deck_scopes
        WHERE updated_at < now() - make_interval(days => $1)
    """,
}

type Counter = Callable[[str, int], Awaitable[int]]


class InventoryError(ValueError):
    """Raised when the privacy inventory violates the fail-closed contract."""


def load_inventory(path: Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InventoryError(f"privacy inventory not found: {path}") from error
    except json.JSONDecodeError as error:
        raise InventoryError(f"privacy inventory is invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise InventoryError("privacy inventory root must be an object")
    return payload


def validate_inventory(inventory: Mapping[str, Any]) -> None:
    if inventory.get("schema_version") != 1:
        raise InventoryError("privacy inventory schema_version must be 1")

    retention_classes = inventory.get("retention_classes")
    if not isinstance(retention_classes, dict) or not retention_classes:
        raise InventoryError("retention_classes must be a non-empty object")

    for class_name, raw_policy in retention_classes.items():
        if not isinstance(class_name, str) or not class_name:
            raise InventoryError("retention class names must be non-empty strings")
        if not isinstance(raw_policy, dict):
            raise InventoryError(f"retention class {class_name!r} must be an object")
        if raw_policy.get("status") not in _ALLOWED_RETENTION_STATUS:
            raise InventoryError(f"retention class {class_name!r} has invalid status")
        days = raw_policy.get("days")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days <= 0):
            raise InventoryError(f"retention class {class_name!r} days must be positive")
        if raw_policy.get("destructive_enabled") is not False:
            raise InventoryError(
                f"retention class {class_name!r} must keep destructive_enabled=false"
            )

    datasets = inventory.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise InventoryError("datasets must be a non-empty array")

    seen_dataset_ids: set[str] = set()
    seen_tables: set[str] = set()
    seen_rule_ids: set[str] = set()

    for raw_dataset in datasets:
        if not isinstance(raw_dataset, dict):
            raise InventoryError("each dataset must be an object")
        missing = sorted(_REQUIRED_DATASET_FIELDS - raw_dataset.keys())
        if missing:
            raise InventoryError(
                f"dataset {raw_dataset.get('id', '<unknown>')!r} misses fields: {missing}"
            )

        dataset_id = raw_dataset["id"]
        if not isinstance(dataset_id, str) or not dataset_id:
            raise InventoryError("dataset id must be a non-empty string")
        if dataset_id in seen_dataset_ids:
            raise InventoryError(f"duplicate dataset id: {dataset_id}")
        seen_dataset_ids.add(dataset_id)

        tables = raw_dataset["tables"]
        if (
            not isinstance(tables, list)
            or not tables
            or not all(isinstance(table, str) and table for table in tables)
        ):
            raise InventoryError(f"dataset {dataset_id!r} tables must be non-empty strings")
        overlap = seen_tables.intersection(tables)
        if overlap:
            raise InventoryError(f"tables assigned to multiple datasets: {sorted(overlap)}")
        seen_tables.update(tables)

        for field_name in ("data_fields", "access_roles", "exceptions"):
            values = raw_dataset[field_name]
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise InventoryError(f"dataset {dataset_id!r} {field_name} must contain strings")
        for field_name in ("purpose", "deletion_action"):
            value = raw_dataset[field_name]
            if not isinstance(value, str) or not value:
                raise InventoryError(
                    f"dataset {dataset_id!r} {field_name} must be a non-empty string"
                )

        if raw_dataset["sensitivity"] not in _ALLOWED_SENSITIVITY:
            raise InventoryError(f"dataset {dataset_id!r} has invalid sensitivity")
        retention_class = raw_dataset["retention_class"]
        if retention_class not in retention_classes:
            raise InventoryError(
                f"dataset {dataset_id!r} references unknown retention class {retention_class!r}"
            )
        if raw_dataset["backup_presence"] is not True:
            raise InventoryError(
                f"dataset {dataset_id!r} must explicitly acknowledge backup presence"
            )

        for rule in raw_dataset.get("cleanup_rules", []):
            if not isinstance(rule, dict):
                raise InventoryError(f"dataset {dataset_id!r} cleanup rule must be an object")
            rule_id = rule.get("id")
            planner_key = rule.get("planner_key")
            if not isinstance(rule_id, str) or not rule_id:
                raise InventoryError(f"dataset {dataset_id!r} cleanup rule id is invalid")
            if rule_id in seen_rule_ids:
                raise InventoryError(f"duplicate cleanup rule id: {rule_id}")
            seen_rule_ids.add(rule_id)
            if planner_key not in _COUNT_QUERIES:
                raise InventoryError(f"cleanup rule {rule_id!r} uses unknown planner key")
            if rule.get("status") != "approved":
                raise InventoryError(f"cleanup rule {rule_id!r} must be explicitly approved")
            if rule.get("destructive_enabled") is not False:
                raise InventoryError(
                    f"cleanup rule {rule_id!r} must keep destructive_enabled=false"
                )
            policy = retention_classes[retention_class]
            if policy.get("status") != "approved" or not isinstance(policy.get("days"), int):
                raise InventoryError(
                    f"cleanup rule {rule_id!r} requires an approved finite retention class"
                )


def inventory_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _database_counter(database_url: str) -> tuple[asyncpg.Connection, Counter]:
    connection = await asyncpg.connect(database_url, command_timeout=30)
    await connection.execute("SET statement_timeout = '30s'")
    await connection.execute("SET lock_timeout = '2s'")
    await connection.execute("SET default_transaction_read_only = on")

    async def count(planner_key: str, retention_days: int) -> int:
        query = _COUNT_QUERIES[planner_key]
        value = await connection.fetchval(query, retention_days)
        return int(value or 0)

    return connection, count


async def build_plan(
    inventory: Mapping[str, Any],
    *,
    policy_sha256: str,
    counter: Counter | None,
) -> dict[str, Any]:
    validate_inventory(inventory)
    retention_classes = inventory["retention_classes"]
    items: list[dict[str, Any]] = []
    blocked_rules = 0
    total_candidates = 0

    for dataset in inventory["datasets"]:
        policy = retention_classes[dataset["retention_class"]]
        rules = dataset.get("cleanup_rules", [])
        for rule in rules:
            retention_days = int(policy["days"])
            blocked_rules += 1
            if counter is None:
                eligible_rows: int | None = None
                blocked_reason = "database-not-queried"
            else:
                eligible_rows = await counter(rule["planner_key"], retention_days)
                blocked_reason = "destructive-mode-not-implemented"
                total_candidates += eligible_rows

            items.append(
                {
                    "dataset_id": dataset["id"],
                    "rule_id": rule["id"],
                    "retention_class": dataset["retention_class"],
                    "retention_days": retention_days,
                    "eligible_rows": eligible_rows,
                    "status": "dry-run-only",
                    "blocked_reason": blocked_reason,
                    "mutation_performed": False,
                }
            )

    return {
        "schema_version": 1,
        "run_id": f"privacy-plan-{uuid.uuid4().hex}",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "dry-run",
        "policy_sha256": policy_sha256,
        "database_queried": counter is not None,
        "mutation_performed": False,
        "items": items,
        "metrics": {
            "privacy_cleanup_candidates_total": total_candidates,
            "privacy_cleanup_rules_total": len(items),
            "privacy_cleanup_blocked_rules_total": blocked_rules,
        },
        "safety": {
            "apply_command_available": False,
            "destructive_policy_flags_enabled": False,
            "contains_personal_values": False,
        },
    }


async def _run_plan(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory = load_inventory(inventory_path)
    validate_inventory(inventory)

    connection: asyncpg.Connection | None = None
    counter: Counter | None = None
    if not args.offline:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise InventoryError("DATABASE_URL is required unless --offline is explicitly selected")
        connection, counter = await _database_counter(database_url)

    try:
        plan = await build_plan(
            inventory,
            policy_sha256=inventory_sha256(inventory_path),
            counter=counter,
        )
    finally:
        if connection is not None:
            await connection.close()

    indent = 2 if args.pretty else None
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=indent))
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory = load_inventory(inventory_path)
    validate_inventory(inventory)
    result = {
        "schema_version": 1,
        "status": "valid",
        "policy_sha256": inventory_sha256(inventory_path),
        "datasets": len(inventory["datasets"]),
        "tables": sum(len(dataset["tables"]) for dataset in inventory["datasets"]),
        "cleanup_rules": sum(
            len(dataset.get("cleanup_rules", [])) for dataset in inventory["datasets"]
        ),
        "destructive_mode": "unavailable",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the personal-data inventory and produce fail-closed cleanup plans."
    )
    parser.add_argument(
        "--inventory",
        default=str(DEFAULT_INVENTORY_PATH),
        help="path to the machine-readable privacy inventory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="validate the inventory contract")

    plan = subparsers.add_parser("plan", help="produce a read-only cleanup plan")
    plan.add_argument(
        "--offline",
        action="store_true",
        help="validate and render rules without querying PostgreSQL",
    )
    plan.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "plan":
            return asyncio.run(_run_plan(args))
        raise InventoryError(f"unsupported command: {args.command}")
    except (InventoryError, OSError, asyncpg.PostgresError) as error:
        print(f"privacy lifecycle error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
