#!/usr/bin/env python3
"""Verify and repair publication rows identified by issue #99."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import asyncpg

from bot.domain.auctions.publication_repair import (
    ISSUE99_AUCTION_IDS,
    PublicationRepairAction,
    PublicationRepairError,
    PublicationRepairResult,
)
from bot.repositories.auction_publication_repair import (
    AuctionPublicationRepairRepository,
    SingleConnectionPool,
)
from bot.services.auction_publication_repair import parse_issue99_plan

KNOWN_AUCTION_IDS = ISSUE99_AUCTION_IDS
RepairAction = PublicationRepairAction
RepairPlanError = PublicationRepairError


def parse_plan(raw: Mapping[str, Any]) -> tuple[PublicationRepairAction, ...]:
    return parse_issue99_plan(raw, require_complete=True)


def load_plan(path: Path) -> tuple[PublicationRepairAction, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise PublicationRepairError("plan root must be an object")
    return parse_plan(payload)


async def repair(
    connection: asyncpg.Connection,
    actions: Sequence[PublicationRepairAction],
    *,
    dry_run: bool,
) -> PublicationRepairResult:
    repository = AuctionPublicationRepairRepository(SingleConnectionPool(connection))
    return await repository.repair(actions, dry_run=dry_run)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL; defaults to DATABASE_URL",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the plan and roll the transaction back (default).",
    )
    mode.add_argument("--apply", action="store_true", help="Commit the reviewed plan.")
    mode.add_argument(
        "--validate-constraints",
        action="store_true",
        help="Validate publication constraints without applying a plan.",
    )
    return parser


def _result_payload(result: PublicationRepairResult) -> dict[str, Any]:
    return {
        "dry_run": result.dry_run,
        "constraints_validated": result.constraints_validated,
        "protected_snapshot": result.protected_snapshot,
        "reports": list(result.reports),
    }


async def _main() -> int:
    args = _parser().parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    if not args.validate_constraints and args.plan is None:
        raise SystemExit("--plan is required unless --validate-constraints is used")

    connection = await asyncpg.connect(args.database_url)
    repository = AuctionPublicationRepairRepository(SingleConnectionPool(connection))
    try:
        if args.validate_constraints:
            status = await repository.validate_constraints()
            print(json.dumps({"constraints": status}, ensure_ascii=False, indent=2))
            print("CONSTRAINTS VALIDATED")
            return 0

        assert args.plan is not None
        actions = load_plan(args.plan)
        result = await repository.repair(actions, dry_run=not args.apply)
        print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2, default=str))
        print("REPAIR COMMITTED" if args.apply else "DRY RUN: transaction rolled back")
        return 0
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
