from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.core.time import ensure_utc, utc_now
from bot.domain.auctions import auction_bidding_closes_at
from bot.domain.auctions.publication_repair import (
    ISSUE99_AUCTION_IDS,
    PublicationRepairAction,
    PublicationRepairError,
    PublicationRepairResult,
)
from bot.repositories.auction_publication_repair import (
    AuctionPublicationRepairRepository,
)
from db.core import get_db_pool

_PUBLISHED_STATES = frozenset(
    {
        "active",
        "finalizing",
        "finalization_failed",
        "finished",
    }
)


def _positive_optional(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PublicationRepairError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PublicationRepairError(f"{field} must be an integer") from exc
    if parsed <= 0:
        raise PublicationRepairError(f"{field} must be positive")
    return parsed


def parse_issue99_plan(
    raw: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> tuple[PublicationRepairAction, ...]:
    repairs = raw.get("repairs")
    if not isinstance(repairs, list):
        raise PublicationRepairError("plan.repairs must be an array")
    actions: list[PublicationRepairAction] = []
    seen: set[int] = set()
    for item in repairs:
        if not isinstance(item, Mapping):
            raise PublicationRepairError("each repair must be an object")
        raw_auction_id = item.get("auction_id")
        if isinstance(raw_auction_id, bool) or not isinstance(raw_auction_id, (str, int)):
            raise PublicationRepairError("auction_id must be an integer")
        try:
            auction_id = int(raw_auction_id)
        except ValueError as exc:
            raise PublicationRepairError("auction_id must be an integer") from exc
        if auction_id not in ISSUE99_AUCTION_IDS:
            raise PublicationRepairError(f"auction {auction_id} is not an issue #99 target")
        if auction_id in seen:
            raise PublicationRepairError(f"auction {auction_id} appears more than once")
        seen.add(auction_id)
        action = str(item.get("action") or "").strip()
        if action not in {
            "confirm",
            "normalize_published",
            "replace_published",
            "requeue",
        }:
            raise PublicationRepairError(f"unsupported action for {auction_id}: {action!r}")
        channel_message_id = _positive_optional(
            item.get("channel_message_id"),
            field="channel_message_id",
        )
        discussion_message_id = _positive_optional(
            item.get("discussion_message_id"),
            field="discussion_message_id",
        )
        expected_previous_channel_message_id = _positive_optional(
            item.get("expected_previous_channel_message_id"),
            field="expected_previous_channel_message_id",
        )
        post_verified_absent = item.get("post_verified_absent") is True
        if action in {"confirm", "normalize_published", "replace_published"} and (
            channel_message_id is None
        ):
            raise PublicationRepairError(
                f"{action} for {auction_id} requires a verified channel_message_id"
            )
        if (
            action == "replace_published"
            and expected_previous_channel_message_id is None
        ):
            raise PublicationRepairError(
                f"replace_published for {auction_id} requires "
                "expected_previous_channel_message_id"
            )
        if action != "replace_published" and expected_previous_channel_message_id is not None:
            raise PublicationRepairError(
                "expected_previous_channel_message_id is only valid for replace_published"
            )
        if action == "requeue" and not post_verified_absent:
            raise PublicationRepairError(
                f"requeue for {auction_id} requires post_verified_absent=true"
            )
        actions.append(
            PublicationRepairAction(
                auction_id=auction_id,
                action=action,
                channel_message_id=channel_message_id,
                discussion_message_id=discussion_message_id,
                post_verified_absent=post_verified_absent,
                expected_previous_channel_message_id=(
                    expected_previous_channel_message_id
                ),
            )
        )
    if require_complete:
        missing = ISSUE99_AUCTION_IDS - seen
        if missing:
            raise PublicationRepairError(f"plan is missing issue #99 rows: {sorted(missing)}")
    return tuple(sorted(actions, key=lambda item: item.auction_id))


class Issue99PublicationRepairService:
    def __init__(self, repository: AuctionPublicationRepairRepository) -> None:
        self._repository = repository

    @classmethod
    async def create(cls) -> Issue99PublicationRepairService:
        return cls(AuctionPublicationRepairRepository(await get_db_pool()))

    async def list_targets(self) -> list[dict[str, Any]]:
        return await self._repository.list_targets(sorted(ISSUE99_AUCTION_IDS))

    async def status(self) -> dict[str, Any]:
        rows = await self.list_targets()
        by_id = {int(row["auction_id"]): row for row in rows}
        missing = sorted(ISSUE99_AUCTION_IDS - by_id.keys())
        unresolved: list[int] = []
        for auction_id in sorted(ISSUE99_AUCTION_IDS & by_id.keys()):
            row = by_id[auction_id]
            status = str(row.get("status") or "")
            message_id = row.get("message_id")
            positive = message_id is not None and int(message_id) > 0
            end_time = row.get("end_time")
            expired = bool(
                end_time is not None
                and auction_bidding_closes_at(ensure_utc(end_time)) <= utc_now()
            )
            if auction_id == 9210:
                resolved = positive and status == "finished"
            elif expired:
                resolved = positive and status in {
                    "finalizing",
                    "finalization_failed",
                    "finished",
                }
            else:
                resolved = positive and status in _PUBLISHED_STATES
            if not resolved:
                unresolved.append(auction_id)
        constraints = await self._repository.constraint_status()
        constraints_validated = all(
            constraints.get(name) is True
            for name in (
                "chk_auctions_message_id_positive",
                "chk_auctions_unpublished_state_has_no_message",
            )
        )
        return {
            "completed": not missing and not unresolved and constraints_validated,
            "missing": missing,
            "unresolved": unresolved,
            "constraints": constraints,
            "rows": rows,
        }

    async def repair(
        self,
        actions: Sequence[PublicationRepairAction],
        *,
        dry_run: bool,
    ) -> PublicationRepairResult:
        return await self._repository.repair(actions, dry_run=dry_run)

    async def validate_constraints(self) -> dict[str, bool]:
        return await self._repository.validate_constraints()


__all__ = [
    "Issue99PublicationRepairService",
    "parse_issue99_plan",
]
