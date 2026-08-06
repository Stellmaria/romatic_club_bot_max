from __future__ import annotations

from typing import Any, cast

import pytest

from bot.domain.preorders import PREORDER_MODE_WHOLE_DECK
from bot.services.preorder_submissions import (
    PreorderAccessDenied,
    PreorderSubmissionService,
)


class FakePreorderRepository:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None

    async def create_pending(self, **kwargs: Any) -> dict[str, Any]:
        self.created = dict(kwargs)
        return {
            "auction_id": 734,
            "was_existing": False,
            "preorder_mode": kwargs["mode"],
            "preorder_items": kwargs["items"],
        }

    async def list_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{"auction_id": limit}]


@pytest.mark.asyncio
async def test_preorder_service_persists_whole_deck_with_fixed_price() -> None:
    repository = FakePreorderRepository()
    service = PreorderSubmissionService(cast(Any, repository))

    result = await service.submit(
        owner_id=55,
        luxury_level=1,
        is_admin=False,
        deck_id=29,
        deck_name="Будущая колода",
        mode=PREORDER_MODE_WHOLE_DECK,
        items={},
        request_key="preorder:55:0123456789abcdef",
        start_price=1_000,
        currency="алмазы",
        comment="",
        image_id="cover-file-id",
    )

    assert result.auction_id == 734
    assert result.was_existing is False
    assert repository.created is not None
    assert repository.created["mode"] == PREORDER_MODE_WHOLE_DECK
    assert repository.created["items"] == {}
    assert repository.created["start_price"] == 1_000
    assert repository.created["currency"] == "алмазы"


@pytest.mark.asyncio
async def test_preorder_service_rejects_price_outside_shared_range() -> None:
    repository = FakePreorderRepository()
    service = PreorderSubmissionService(cast(Any, repository))

    with pytest.raises(ValueError, match="between 1000 and 6000"):
        await service.submit(
            owner_id=55,
            luxury_level=1,
            is_admin=False,
            deck_id=29,
            deck_name="Будущая колода",
            mode=PREORDER_MODE_WHOLE_DECK,
            items={},
            request_key="preorder:55:0123456789abcdef",
            start_price=999,
            currency="алмазы",
            comment="",
            image_id=None,
        )

    assert repository.created is None


@pytest.mark.asyncio
async def test_preorder_service_allows_admin_without_luxury_level() -> None:
    repository = FakePreorderRepository()
    service = PreorderSubmissionService(cast(Any, repository))

    result = await service.submit(
        owner_id=55,
        luxury_level=0,
        is_admin=True,
        deck_id=29,
        deck_name="Будущая колода",
        mode=PREORDER_MODE_WHOLE_DECK,
        items={},
        request_key="preorder:55:0123456789abcdef",
        start_price=6_000,
        currency="чашки",
        comment="админ",
        image_id=None,
    )

    assert result.auction_id == 734


@pytest.mark.asyncio
async def test_preorder_service_rejects_regular_user_without_luxury() -> None:
    repository = FakePreorderRepository()
    service = PreorderSubmissionService(cast(Any, repository))

    with pytest.raises(PreorderAccessDenied):
        await service.submit(
            owner_id=55,
            luxury_level=0,
            is_admin=False,
            deck_id=29,
            deck_name="Будущая колода",
            mode=PREORDER_MODE_WHOLE_DECK,
            items={},
            request_key="preorder:55:0123456789abcdef",
            start_price=1_000,
            currency="алмазы",
            comment="",
            image_id=None,
        )

    assert repository.created is None
