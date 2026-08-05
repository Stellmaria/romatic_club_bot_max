"""Validate, plan, and explicitly execute approved temporary privacy cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402

from bot.core.settings import DatabaseSettings
from bot.core.time import SystemClock
from bot.repositories.privacy_cleanup import (
    PrivacyCleanupConflict,
    PrivacyCleanupLockUnavailable,
    PrivacyCleanupRepository,
)
from bot.services.privacy_cleanup import (
    DEFAULT_INVENTORY_PATH,
    InventoryError,
    PrivacyCleanupConfirmationError,
    PrivacyCleanupPlan,
    PrivacyCleanupService,
    inventory_sha256,
    load_cleanup_policy,
    load_inventory,
    validate_inventory,
)
from db.pool import DatabaseRuntime


def _run_validate(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory = load_inventory(inventory_path)
    validate_inventory(inventory)
    policy = load_cleanup_policy(inventory_path)
    result = {
        "schema_version": 1,
        "status": "valid",
        "policy_sha256": inventory_sha256(inventory_path),
        "datasets": len(inventory["datasets"]),
        "tables": sum(len(dataset["tables"]) for dataset in inventory["datasets"]),
        "cleanup_rules": sum(
            len(dataset.get("cleanup_rules", [])) for dataset in inventory["datasets"]
        ),
        "destructive_mode": "approved-temporary-only",
        "enabled_rule": policy.rule_id,
        "retention_days": policy.retention_days,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


async def _with_service(args: argparse.Namespace) -> tuple[DatabaseRuntime, PrivacyCleanupService]:
    runtime = DatabaseRuntime(DatabaseSettings.from_env())
    pool = await runtime.start()
    service = PrivacyCleanupService(
        PrivacyCleanupRepository(pool),
        clock=SystemClock(),
        inventory_path=Path(args.inventory),
    )
    return runtime, service


def _render(value: Mapping[str, object], *, pretty: bool) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
        )
    )


async def _run_plan(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    if args.offline:
        inventory = load_inventory(inventory_path)
        validate_inventory(inventory)
        policy = load_cleanup_policy(inventory_path)
        _render(
            {
                "schema_version": 1,
                "mode": "offline-policy-validation",
                "policy_sha256": policy.policy_sha256,
                "rule_id": policy.rule_id,
                "retention_days": policy.retention_days,
                "database_queried": False,
                "mutation_performed": False,
            },
            pretty=args.pretty,
        )
        return 0

    runtime, service = await _with_service(args)
    try:
        plan = await service.build_plan(batch_limit=args.batch_limit)
    finally:
        await runtime.close()
    _render(plan.to_dict(), pretty=args.pretty)
    return 0


async def _run_apply(args: argparse.Namespace) -> int:
    runtime, service = await _with_service(args)
    try:
        plan: PrivacyCleanupPlan = await service.build_plan(batch_limit=args.batch_limit)
        result = await service.apply_plan(
            plan,
            confirmation=args.confirm,
        )
    finally:
        await runtime.close()
    _render(
        {
            **result.plan.to_dict(),
            "status": result.status,
            "deleted_rows": result.deleted_rows,
            "audit_id": result.audit_id,
            "mutation_performed": result.deleted_rows > 0,
        },
        pretty=args.pretty,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and execute only the explicitly approved temporary-data "
            "privacy cleanup rule."
        )
    )
    parser.add_argument(
        "--inventory",
        default=str(DEFAULT_INVENTORY_PATH),
        help="path to the machine-readable privacy inventory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="validate the cleanup policy contract")

    plan = subparsers.add_parser("plan", help="produce a cleanup plan")
    plan.add_argument(
        "--offline",
        action="store_true",
        help="validate policy without querying PostgreSQL",
    )
    plan.add_argument("--batch-limit", type=int, default=1_000)
    plan.add_argument("--pretty", action="store_true")

    apply = subparsers.add_parser(
        "apply",
        help="apply the current plan with its exact confirmation token",
    )
    apply.add_argument("--batch-limit", type=int, default=1_000)
    apply.add_argument("--confirm", required=True)
    apply.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "plan":
            return asyncio.run(_run_plan(args))
        if args.command == "apply":
            return asyncio.run(_run_apply(args))
        raise InventoryError(f"unsupported command: {args.command}")
    except (
        InventoryError,
        PrivacyCleanupConfirmationError,
        PrivacyCleanupConflict,
        PrivacyCleanupLockUnavailable,
        asyncpg.PostgresError,
        OSError,
        ValueError,
    ) as error:
        print(f"privacy cleanup error: {error}", file=sys.stderr)
        return 2


__all__ = [
    "InventoryError",
    "build_parser",
    "inventory_sha256",
    "load_inventory",
    "main",
    "validate_inventory",
]


if __name__ == "__main__":
    raise SystemExit(main())
