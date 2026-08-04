# ruff: noqa: RUF001
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.core.settings import UserbotSettings
from bot.core.time import ensure_utc
from bot.domain.auctions.publication_repair import PublicationRepairAction
from bot.services.auction_publication_repair import Issue99PublicationRepairService

logger = logging.getLogger("auction_userbot.publication_recovery")

_DISCUSSION_ROOTS = {
    9210: 1148772,
    9217: 1149339,
    9221: 1149326,
}
_KNOWN_CHANNEL_POSTS = {
    3797: 5948,
    7523: 10139,
}
_SEARCH_FALLBACKS = {
    9243: datetime(2026, 8, 3, 16, 30, tzinfo=UTC),
}
_LOT_ID_PATTERN = re.compile(r"(?i)\bлот\s*№\s*(\d{1,10})\b")


@dataclass(frozen=True, slots=True)
class RecoveryDiscovery:
    actions: tuple[PublicationRepairAction, ...]
    unresolved: tuple[dict[str, Any], ...]


def _message_text(message: Any) -> str:
    return str(getattr(message, "message", None) or "").strip()


def _extract_lot_id(text: str) -> int | None:
    match = _LOT_ID_PATTERN.search(text)
    return int(match.group(1)) if match else None


def _normalized_channel_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    rendered = str(number)
    if rendered.startswith("-100") and rendered[4:].isdigit():
        return int(rendered[4:])
    return abs(number)


def _verified_lot_message(message: Any, *, auction_id: int) -> bool:
    return bool(message) and _extract_lot_id(_message_text(message)) == int(auction_id)


async def _from_discussion_root(
    telegram_client: Any,
    settings: UserbotSettings,
    *,
    auction_id: int,
    discussion_message_id: int,
) -> PublicationRepairAction:
    message = await telegram_client.get_messages(
        int(settings.discussion_chat_id),
        ids=int(discussion_message_id),
    )
    if not _verified_lot_message(message, auction_id=auction_id):
        raise ValueError("discussion root does not contain the expected auction number")
    forwarded = getattr(message, "fwd_from", None)
    channel_message_id = getattr(forwarded, "channel_post", None) if forwarded else None
    source_channel_id = getattr(getattr(forwarded, "from_id", None), "channel_id", None)
    if not channel_message_id or int(channel_message_id) <= 0:
        raise ValueError("discussion root has no positive forwarded channel post ID")
    if _normalized_channel_id(source_channel_id) != _normalized_channel_id(
        settings.auction_channel_id
    ):
        raise ValueError("discussion root was forwarded from another channel")
    return PublicationRepairAction(
        auction_id=int(auction_id),
        action="confirm",
        channel_message_id=int(channel_message_id),
        discussion_message_id=int(discussion_message_id),
    )


async def _from_known_channel_post(
    telegram_client: Any,
    settings: UserbotSettings,
    *,
    auction_id: int,
    channel_message_id: int,
) -> PublicationRepairAction:
    message = await telegram_client.get_messages(
        int(settings.auction_channel_id),
        ids=int(channel_message_id),
    )
    if not _verified_lot_message(message, auction_id=auction_id):
        raise ValueError("known channel post does not contain the expected auction number")
    return PublicationRepairAction(
        auction_id=int(auction_id),
        action="normalize_published",
        channel_message_id=int(channel_message_id),
    )


async def _search_unique_channel_post(
    telegram_client: Any,
    settings: UserbotSettings,
    row: dict[str, Any],
) -> PublicationRepairAction:
    auction_id = int(row["auction_id"])
    reference = (
        row.get("publication_started_at") or row.get("start_time") or _SEARCH_FALLBACKS[auction_id]
    )
    reference_time = ensure_utc(reference)
    window_start = reference_time - timedelta(hours=6)
    window_end = reference_time + timedelta(hours=6)
    matches: list[Any] = []
    async for message in telegram_client.iter_messages(
        int(settings.auction_channel_id),
        search=str(auction_id),
        offset_date=window_end,
        limit=300,
    ):
        message_date = getattr(message, "date", None)
        if message_date is None:
            continue
        timestamp = ensure_utc(message_date)
        if timestamp < window_start:
            break
        if timestamp > window_end:
            continue
        if _verified_lot_message(message, auction_id=auction_id):
            matches.append(message)
    if len(matches) != 1:
        raise ValueError(
            f"channel search returned {len(matches)} exact posts in the verification window"
        )
    channel_message_id = int(matches[0].id)
    if channel_message_id <= 0:
        raise ValueError("channel search returned a non-positive message ID")
    return PublicationRepairAction(
        auction_id=auction_id,
        action="confirm",
        channel_message_id=channel_message_id,
    )


async def discover_issue99_repair_actions(
    telegram_client: Any,
    settings: UserbotSettings,
    rows: list[dict[str, Any]],
) -> RecoveryDiscovery:
    by_id = {int(row["auction_id"]): row for row in rows}
    actions: list[PublicationRepairAction] = []
    unresolved: list[dict[str, Any]] = []

    async def attempt(auction_id: int, operation: Any) -> None:
        try:
            action = await operation
        except Exception as exc:  # noqa: BLE001
            unresolved.append(
                {
                    "auction_id": int(auction_id),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            actions.append(action)

    for auction_id, discussion_message_id in _DISCUSSION_ROOTS.items():
        if auction_id not in by_id:
            unresolved.append({"auction_id": auction_id, "error": "database row is missing"})
            continue
        await attempt(
            auction_id,
            _from_discussion_root(
                telegram_client,
                settings,
                auction_id=auction_id,
                discussion_message_id=discussion_message_id,
            ),
        )

    for auction_id, channel_message_id in _KNOWN_CHANNEL_POSTS.items():
        if auction_id not in by_id:
            unresolved.append({"auction_id": auction_id, "error": "database row is missing"})
            continue
        await attempt(
            auction_id,
            _from_known_channel_post(
                telegram_client,
                settings,
                auction_id=auction_id,
                channel_message_id=channel_message_id,
            ),
        )

    for auction_id in _SEARCH_FALLBACKS:
        row = by_id.get(auction_id)
        if row is None:
            unresolved.append({"auction_id": auction_id, "error": "database row is missing"})
            continue
        await attempt(
            auction_id,
            _search_unique_channel_post(telegram_client, settings, row),
        )

    return RecoveryDiscovery(
        actions=tuple(sorted(actions, key=lambda item: item.auction_id)),
        unresolved=tuple(sorted(unresolved, key=lambda item: int(item["auction_id"]))),
    )


async def run_issue99_publication_recovery(
    telegram_client: Any,
    settings: UserbotSettings,
) -> dict[str, Any]:
    """Discover and apply only publication repairs proved by Telegram metadata."""

    service = await Issue99PublicationRepairService.create()
    initial = await service.status()
    if initial["completed"]:
        logger.info("issue99_publication_recovery_already_complete")
        return initial
    if initial["missing"]:
        logger.info(
            "issue99_publication_recovery_not_applicable",
            extra={"missing_auction_ids": initial["missing"]},
        )
        return initial

    discovery = await discover_issue99_repair_actions(
        telegram_client,
        settings,
        initial["rows"],
    )
    if discovery.unresolved:
        logger.warning(
            "issue99_publication_recovery_unresolved",
            extra={"unresolved": list(discovery.unresolved)},
        )
    if not discovery.actions:
        return {
            **initial,
            "discovery": asdict(discovery),
        }

    dry_run = await service.repair(discovery.actions, dry_run=True)
    logger.info(
        "issue99_publication_recovery_dry_run_passed",
        extra={
            "auction_ids": [action.auction_id for action in discovery.actions],
            "protected_snapshot": dry_run.protected_snapshot,
        },
    )
    applied = await service.repair(discovery.actions, dry_run=False)
    final = await service.status()
    logger.info(
        "issue99_publication_recovery_applied",
        extra={
            "reports": list(applied.reports),
            "protected_snapshot": applied.protected_snapshot,
            "unresolved": list(discovery.unresolved),
            "completed": final["completed"],
        },
    )
    return {
        **final,
        "discovery": asdict(discovery),
        "reports": list(applied.reports),
        "protected_snapshot": applied.protected_snapshot,
    }


__all__ = [
    "RecoveryDiscovery",
    "discover_issue99_repair_actions",
    "run_issue99_publication_recovery",
]
