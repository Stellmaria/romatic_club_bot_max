from __future__ import annotations

from pathlib import Path

import pytest

from bot.use_cases.auction_publication import (
    PublishAuctionCommand,
    PublishAuctionUseCase,
)
from bot.use_cases.common import ApplicationInvalidState


@pytest.mark.asyncio
async def test_positive_message_id_keeps_normal_publication() -> None:
    calls: list[tuple[str, object]] = []

    async def claim(auction_id: int) -> dict[str, object]:
        return {"auction_id": auction_id}

    async def build(row: dict[str, object]) -> str:
        return str(row["auction_id"])

    async def send(_row: dict[str, object], _payload: object) -> int:
        return 77

    async def mark_published(auction_id: int, message_id: int) -> bool:
        calls.append(("published", (auction_id, message_id)))
        return True

    async def mark_failed(auction_id: int, error: str) -> None:
        calls.append(("failed", (auction_id, error)))

    async def mark_deferred(auction_id: int) -> bool:
        calls.append(("deferred", auction_id))
        return True

    result = await PublishAuctionUseCase(
        claim=claim,
        build_payload=build,
        send=send,
        mark_published=mark_published,
        mark_failed=mark_failed,
        mark_deferred=mark_deferred,
    ).execute(PublishAuctionCommand(auction_id=42))

    assert result.message_id == 77
    assert calls == [("published", (42, 77))]


@pytest.mark.asyncio
async def test_zero_is_deferred_without_retry_or_database_zero() -> None:
    calls: list[tuple[str, object]] = []

    async def claim(auction_id: int) -> dict[str, object]:
        return {"auction_id": auction_id}

    async def build(_row: dict[str, object]) -> None:
        return None

    async def send(_row: dict[str, object], _payload: object) -> int:
        return 0

    async def mark_published(auction_id: int, message_id: int) -> bool:
        calls.append(("published", (auction_id, message_id)))
        return True

    async def mark_failed(auction_id: int, error: str) -> None:
        calls.append(("failed", (auction_id, error)))

    async def mark_deferred(auction_id: int) -> bool:
        calls.append(("deferred", auction_id))
        return True

    result = await PublishAuctionUseCase(
        claim=claim,
        build_payload=build,
        send=send,
        mark_published=mark_published,
        mark_failed=mark_failed,
        mark_deferred=mark_deferred,
    ).execute(PublishAuctionCommand(auction_id=99))

    assert result.message_id == 0
    assert calls == [("deferred", 99)]


@pytest.mark.asyncio
async def test_real_telegram_failure_keeps_failure_path() -> None:
    failures: list[str] = []

    async def claim(auction_id: int) -> dict[str, object]:
        return {"auction_id": auction_id}

    async def build(_row: dict[str, object]) -> None:
        return None

    async def send(_row: dict[str, object], _payload: object) -> int:
        raise RuntimeError("telegram unavailable")

    async def mark_published(_auction_id: int, _message_id: int) -> bool:
        raise AssertionError("must not commit")

    async def mark_failed(_auction_id: int, error: str) -> None:
        failures.append(error)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        await PublishAuctionUseCase(
            claim=claim,
            build_payload=build,
            send=send,
            mark_published=mark_published,
            mark_failed=mark_failed,
        ).execute(PublishAuctionCommand(auction_id=7))

    assert failures and "telegram unavailable" in failures[0]


@pytest.mark.asyncio
async def test_negative_message_id_is_rejected() -> None:
    async def claim(auction_id: int) -> dict[str, object]:
        return {"auction_id": auction_id}

    async def build(_row: dict[str, object]) -> None:
        return None

    async def send(_row: dict[str, object], _payload: object) -> int:
        return -1

    async def mark_published(_auction_id: int, _message_id: int) -> bool:
        return True

    async def mark_failed(_auction_id: int, _error: str) -> None:
        return None

    with pytest.raises(ApplicationInvalidState, match="negative"):
        await PublishAuctionUseCase(
            claim=claim,
            build_payload=build,
            send=send,
            mark_published=mark_published,
            mark_failed=mark_failed,
        ).execute(PublishAuctionCommand(auction_id=7))


def test_repository_and_migration_keep_deferred_separate() -> None:
    repository = Path("bot/repositories/auction_workflows.py").read_text(encoding="utf-8")
    migration = Path("db/migrations/019_deferred_auction_publication.sql").read_text(
        encoding="utf-8"
    )

    assert "status = 'publication_deferred'" in repository
    assert "WHERE status = 'publishing'" in repository
    assert "FOR UPDATE" in repository
    assert "chk_auctions_message_id_positive" in migration
    assert "NOT VALID" in migration
    assert "UPDATE public.auctions" not in migration
    assert "refresh_auction_publication" in migration


def test_reschedule_never_creates_scheduled_with_message_id() -> None:
    repository = Path("bot/repositories/auction_workflows.py").read_text(encoding="utf-8")

    assert "_publication_refresh_queued" in repository
    assert "message_id = NULL" in repository
    assert "status = 'active'" in repository
    assert "refresh_auction_publication" in repository
