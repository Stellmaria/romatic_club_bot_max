from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ISSUE99_AUCTION_IDS = frozenset({3797, 7523, 9210, 9217, 9221, 9243})


class PublicationRepairError(ValueError):
    """Raised when a publication repair cannot be proved safe."""


@dataclass(frozen=True, slots=True)
class PublicationRepairAction:
    auction_id: int
    action: str
    channel_message_id: int | None = None
    discussion_message_id: int | None = None
    post_verified_absent: bool = False


@dataclass(frozen=True, slots=True)
class PublicationRepairResult:
    reports: tuple[dict[str, Any], ...]
    dry_run: bool
    constraints_validated: bool
    protected_snapshot: dict[str, Any]


__all__ = [
    "ISSUE99_AUCTION_IDS",
    "PublicationRepairAction",
    "PublicationRepairError",
    "PublicationRepairResult",
]
