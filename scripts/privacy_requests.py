from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from uuid import UUID

import asyncpg

from bot.core.time import SystemClock
from bot.repositories.privacy_requests import PrivacyRequestRepository
from bot.services.privacy_requests import PrivacyRequestService
from bot.uid_crypto import configure_uid_crypto


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and execute reviewed privacy requests.")
    parser.add_argument("--operator", required=True, help="stable operator identifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("request_id", type=UUID)

    approve = subparsers.add_parser("approve")
    approve.add_argument("request_id", type=UUID)
    approve.add_argument("--plan-sha256", required=True)

    apply = subparsers.add_parser("apply")
    apply.add_argument("request_id", type=UUID)
    apply.add_argument("--plan-sha256", required=True)
    apply.add_argument("--confirm", required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    hash_key = os.getenv("UID_HASH_KEY", "").strip()
    encryption_key = os.getenv("UID_ENC_KEY", "").strip()
    if not database_url or not hash_key or not encryption_key:
        raise RuntimeError("DATABASE_URL, UID_HASH_KEY and UID_ENC_KEY are required")
    configure_uid_crypto(hash_key, encryption_key)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, command_timeout=30)
    try:
        service = PrivacyRequestService(
            PrivacyRequestRepository(pool),
            clock=SystemClock(),
        )
        if args.command == "plan":
            plan = await service.plan_operator(args.request_id)
            print(service.plan_payload(plan))
            return 0
        if args.command == "approve":
            plan = await service.approve_operator(
                request_id=args.request_id,
                expected_plan_sha256=args.plan_sha256,
                operator_identity=args.operator,
            )
            print(service.plan_payload(plan))
            return 0
        if args.command == "apply":
            record = await service.execute_operator(
                request_id=args.request_id,
                expected_plan_sha256=args.plan_sha256,
                operator_identity=args.operator,
                confirmation=args.confirm,
            )
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "request_id": str(record.request_id),
                        "status": record.status,
                        "retained_holds": list(record.retained_holds),
                        "outcome_counts": record.outcome_counts,
                        "contains_personal_values": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        raise RuntimeError(f"unsupported command: {args.command}")
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (RuntimeError, ValueError, LookupError, asyncpg.PostgresError) as error:
        print(f"privacy request error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
