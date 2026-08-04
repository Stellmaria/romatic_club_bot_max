# ruff: noqa: SIM102
#!/usr/bin/env python3
"""Repair the six publication rows identified by issue #99.

The command never discovers or guesses Telegram identifiers. Operators must
supply a reviewed JSON plan. Every row is locked, printed before and after, and
updated in one transaction. Any mismatch aborts the whole repair.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

KNOWN_AUCTION_IDS = frozenset({9210, 9217, 9221, 9243, 3797, 7523})


class RepairPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RepairAction:
    auction_id: int
    action: str
    channel_message_id: int | None = None
    discussion_message_id: int | None = None
    post_verified_absent: bool = False


def _positive_optional(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RepairPlanError(f"{field} must be an integer") from exc
    if parsed <= 0:
        raise RepairPlanError(f"{field} must be positive")
    return parsed


def parse_plan(raw: Mapping[str, Any]) -> tuple[RepairAction, ...]:
    repairs = raw.get("repairs")
    if not isinstance(repairs, list):
        raise RepairPlanError("plan.repairs must be an array")
    actions: list[RepairAction] = []
    seen: set[int] = set()
    for item in repairs:
        if not isinstance(item, Mapping):
            raise RepairPlanError("each repair must be an object")
        try:
            auction_id = int(item.get("auction_id"))
        except (TypeError, ValueError) as exc:
            raise RepairPlanError("auction_id must be an integer") from exc
        if auction_id not in KNOWN_AUCTION_IDS:
            raise RepairPlanError(f"auction {auction_id} is not an issue #99 target")
        if auction_id in seen:
            raise RepairPlanError(f"auction {auction_id} appears more than once")
        seen.add(auction_id)
        action = str(item.get("action") or "").strip()
        if action not in {"confirm", "normalize_published", "requeue"}:
            raise RepairPlanError(f"unsupported action for {auction_id}: {action!r}")
        channel_message_id = _positive_optional(
            item.get("channel_message_id"), field="channel_message_id"
        )
        discussion_message_id = _positive_optional(
            item.get("discussion_message_id"), field="discussion_message_id"
        )
        post_verified_absent = item.get("post_verified_absent") is True
        if action in {"confirm", "normalize_published"} and channel_message_id is None:
            raise RepairPlanError(
                f"{action} for {auction_id} requires a verified channel_message_id"
            )
        if action == "requeue" and not post_verified_absent:
            raise RepairPlanError(f"requeue for {auction_id} requires post_verified_absent=true")
        actions.append(
            RepairAction(
                auction_id=auction_id,
                action=action,
                channel_message_id=channel_message_id,
                discussion_message_id=discussion_message_id,
                post_verified_absent=post_verified_absent,
            )
        )
    missing = KNOWN_AUCTION_IDS - seen
    if missing:
        raise RepairPlanError(f"plan is missing issue #99 rows: {sorted(missing)}")
    return tuple(sorted(actions, key=lambda action: action.auction_id))


def load_plan(path: Path) -> tuple[RepairAction, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RepairPlanError("plan root must be an object")
    return parse_plan(payload)


def _snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "auction_id",
        "status",
        "message_id",
        "discussion_message_id",
        "publication_started_at",
        "publication_finished_at",
        "publication_error",
        "publication_next_attempt_at",
    )
    return {field: row.get(field) for field in fields}


async def _apply_action(
    connection: asyncpg.Connection,
    action: RepairAction,
) -> dict[str, Any]:
    before_row = await connection.fetchrow(
        "SELECT * FROM public.auctions WHERE auction_id = $1 FOR UPDATE",
        action.auction_id,
    )
    if before_row is None:
        raise RepairPlanError(f"auction {action.auction_id} does not exist")
    before = dict(before_row)

    if action.action == "requeue":
        if before.get("message_id") not in {None, 0}:
            raise RepairPlanError(
                f"auction {action.auction_id} has a positive message_id; requeue is unsafe"
            )
        await connection.execute(
            """
            UPDATE public.auctions
            SET status = 'scheduled',
                message_id = NULL,
                discussion_message_id = NULL,
                publication_started_at = NULL,
                publication_finished_at = NULL,
                publication_attempts = 0,
                publication_error = NULL,
                publication_next_attempt_at = NULL
            WHERE auction_id = $1
            """,
            action.auction_id,
        )
    else:
        assert action.channel_message_id is not None
        existing = before.get("message_id")
        if existing is not None and int(existing) > 0:
            if int(existing) != action.channel_message_id:
                raise RepairPlanError(
                    f"auction {action.auction_id} has conflicting message_id {existing}"
                )
        existing_discussion = before.get("discussion_message_id")
        if (
            existing_discussion is not None
            and action.discussion_message_id is not None
            and int(existing_discussion) != action.discussion_message_id
        ):
            raise RepairPlanError(
                f"auction {action.auction_id} has conflicting discussion_message_id"
            )
        await connection.execute(
            """
            UPDATE public.auctions
            SET status = CASE
                    WHEN status = 'finished' THEN 'finished'
                    ELSE 'active'
                END,
                message_id = $2,
                discussion_message_id = COALESCE($3, discussion_message_id),
                publication_finished_at = COALESCE(publication_finished_at, NOW()),
                publication_error = NULL,
                publication_next_attempt_at = NULL
            WHERE auction_id = $1
            """,
            action.auction_id,
            action.channel_message_id,
            action.discussion_message_id,
        )

    after_row = await connection.fetchrow(
        "SELECT * FROM public.auctions WHERE auction_id = $1",
        action.auction_id,
    )
    assert after_row is not None
    return {"before": _snapshot(before), "after": _snapshot(dict(after_row))}


async def repair(
    connection: asyncpg.Connection,
    actions: tuple[RepairAction, ...],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    transaction = connection.transaction()
    await transaction.start()
    try:
        reports = [await _apply_action(connection, action) for action in actions]
        invalid_message_ids = int(
            await connection.fetchval("SELECT count(*) FROM public.auctions WHERE message_id <= 0")
            or 0
        )
        incompatible = int(await connection.fetchval("""
                SELECT count(*)
                FROM public.auctions
                WHERE status IN (
                    'scheduled', 'publishing', 'publication_deferred'
                )
                  AND message_id IS NOT NULL
                """) or 0)
        if invalid_message_ids or incompatible:
            raise RepairPlanError(
                "post-condition failed: "
                f"message_id<=0={invalid_message_ids}, incompatible={incompatible}"
            )
        if dry_run:
            await transaction.rollback()
        else:
            await connection.execute("""
                ALTER TABLE public.auctions
                    VALIDATE CONSTRAINT chk_auctions_message_id_positive
                """)
            await connection.execute("""
                ALTER TABLE public.auctions
                    VALIDATE CONSTRAINT chk_auctions_unpublished_state_has_no_message
                """)
            await transaction.commit()
        return reports
    except BaseException:
        await transaction.rollback()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL; defaults to DATABASE_URL",
    )
    parser.add_argument("--apply", action="store_true")
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    actions = load_plan(args.plan)
    connection = await asyncpg.connect(args.database_url)
    try:
        reports = await repair(connection, actions, dry_run=not args.apply)
    finally:
        await connection.close()
    print(json.dumps(reports, ensure_ascii=False, indent=2, default=str))
    print("DRY RUN: transaction rolled back" if not args.apply else "REPAIR COMMITTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
