from __future__ import annotations

import asyncio

from bot.domain.auctions import Currency
from bot.services.exchanges import ExchangeService


class FakeExchangeRepository:
    def __init__(self) -> None:
        self.created = None
        self.moderation = None

    async def create(self, draft):
        self.created = draft
        return {"batch_id": 17, "status": "pending"}

    async def moderate(self, batch_id: int, **kwargs):
        self.moderation = (batch_id, kwargs)
        return {"batch_id": batch_id, "status": kwargs["target_status"]}


def test_exchange_submission_normalizes_domain_input() -> None:
    async def scenario() -> None:
        repository = FakeExchangeRepository()
        service = ExchangeService(repository)  # type: ignore[arg-type]

        result = await service.submit(
            user_id=42,
            deck_id=3,
            mode=" CARD ",
            currency="diamonds",
            price=100,
            card_ids=[9, 9],
            comment="  comment  ",
            proof_photo_id=" proof ",
        )

        assert result == {"batch_id": 17, "status": "pending"}
        assert repository.created.user_id == 42
        assert repository.created.mode == "card"
        assert repository.created.currency is Currency.DIAMONDS
        assert repository.created.card_ids == (9, 9)
        assert repository.created.comment == "comment"
        assert repository.created.proof_photo_id == "proof"

    asyncio.run(scenario())


def test_exchange_service_rejects_invalid_drafts_before_repository_call() -> None:
    async def scenario() -> None:
        repository = FakeExchangeRepository()
        service = ExchangeService(repository)  # type: ignore[arg-type]

        for kwargs in (
            {"mode": "unknown", "price": 1, "card_ids": [1]},
            {"mode": "card", "price": -1, "card_ids": [1]},
            {"mode": "card", "price": 1, "card_ids": []},
        ):
            try:
                await service.submit(
                    user_id=1,
                    deck_id=1,
                    currency="diamonds",
                    **kwargs,
                )
            except ValueError:
                pass
            else:  # pragma: no cover
                raise AssertionError(f"invalid draft accepted: {kwargs}")

        assert repository.created is None

    asyncio.run(scenario())


def test_exchange_moderation_delegates_atomic_transition() -> None:
    async def scenario() -> None:
        repository = FakeExchangeRepository()
        service = ExchangeService(repository)  # type: ignore[arg-type]

        result = await service.approve(
            17,
            moderator_id=5,
            moderator_username="admin",
            comment="ok",
        )

        assert result["status"] == "approved"
        assert repository.moderation == (
            17,
            {
                "target_status": "approved",
                "moderator_id": 5,
                "moderator_username": "admin",
                "moderator_comment": "ok",
            },
        )

    asyncio.run(scenario())
