"""Application-facing marketplace operations.

The service constructs a repository from the process pool.  Telegram modules
depend on these named operations instead of SQL helpers or the legacy ``db``
facade; tests can instantiate :class:`MarketService` with a fake repository.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from bot.repositories.market import MarketRepository
from db.pool import get_db_pool


class MarketService:
    """Thin use-case facade around :class:`MarketRepository`."""

    def __init__(self, repository: MarketRepository):
        self.repository = repository

    @classmethod
    async def create(cls) -> "MarketService":
        return cls(MarketRepository(await get_db_pool()))


async def _repository() -> MarketRepository:
    return (await MarketService.create()).repository


async def market_create_listing(**values: Any) -> int:
    return await (await _repository()).create_listing(**values)


async def market_add_listing_item(
    listing_id: int,
    card_id: int,
    quantity: int = 1,
    proof_file_id: str | None = None,
) -> None:
    await (await _repository()).add_listing_item(
        listing_id,
        card_id,
        quantity,
        proof_file_id,
    )


async def market_add_items(
    listing_id: int,
    items: Iterable[int | dict[str, Any]],
) -> int:
    return await (await _repository()).add_items(listing_id, items)


async def market_add_rate_tiers(
    listing_id: int,
    tiers: Iterable[dict[str, Any]],
) -> int:
    return await (await _repository()).add_rate_tiers(listing_id, tiers)


async def market_get_rate_tiers(listing_id: int) -> list[dict[str, Any]]:
    return await (await _repository()).get_rate_tiers(listing_id)


async def market_get_listing(listing_id: int) -> dict[str, Any] | None:
    return await (await _repository()).get_listing(listing_id)


async def market_set_status(listing_id: int, status: str) -> None:
    await (await _repository()).set_status(listing_id, status)


async def market_get_status(listing_id: int) -> str | None:
    return await (await _repository()).get_status(listing_id)


async def market_bump(listing_id: int) -> None:
    await (await _repository()).bump(listing_id)


async def market_toggle_actual(listing_id: int) -> str:
    return await (await _repository()).toggle_actual(listing_id)


async def market_toggle_named_status(listing_id: int, status: str) -> None:
    await (await _repository()).toggle_named_status(listing_id, status)


async def market_set_cover(
    listing_id: int,
    file_id: str | None,
    *,
    touch_updated_at: bool = False,
) -> None:
    await (await _repository()).set_cover(
        listing_id,
        file_id,
        touch_updated_at=touch_updated_at,
    )


async def market_set_description(
    listing_id: int,
    description: str | None,
) -> None:
    await (await _repository()).set_description(listing_id, description)


async def market_set_item_proof(
    listing_id: int,
    card_id: int,
    file_id: str,
) -> None:
    await (await _repository()).set_item_proof(listing_id, card_id, file_id)


async def market_set_item_quantity(
    listing_id: int,
    card_id: int,
    quantity: int,
) -> None:
    await (await _repository()).set_item_quantity(
        listing_id,
        card_id,
        quantity,
    )


async def market_set_item_qty(listing_id: int, quantity: int) -> None:
    await (await _repository()).set_listing_item_quantity(listing_id, quantity)


async def market_dec_item_qty(listing_id: int, amount: int) -> int:
    return await (await _repository()).decrement_item_quantity(listing_id, amount)


async def market_decrement_all_items_and_total(listing_id: int) -> int:
    return await (await _repository()).decrement_all_items_and_total(listing_id)


async def market_quantity_total(listing_id: int) -> int:
    return await (await _repository()).quantity_total(listing_id)


async def market_delete_all_prices(listing_id: int) -> None:
    await (await _repository()).delete_all_prices(listing_id)


async def market_replace_price(
    listing_id: int,
    *,
    pay_type: str,
    cash_code: str | None,
    price: float | None,
) -> None:
    await (await _repository()).replace_price(
        listing_id,
        pay_type=pay_type,
        cash_code=cash_code,
        price=price,
    )


async def market_hard_delete_listing(listing_id: int) -> None:
    await (await _repository()).hard_delete_listing(listing_id)


async def market_seller_listing_ids(
    seller_id: int,
    statuses: list[str],
) -> list[int]:
    return await (await _repository()).seller_listing_ids(seller_id, statuses)


async def market_seller_listing_summaries(
    seller_id: int,
    statuses: list[str],
) -> list[dict[str, Any]]:
    return await (await _repository()).seller_listing_summaries(
        seller_id,
        statuses,
    )


async def market_seller_listings(seller_id: int) -> list[dict[str, Any]]:
    return await (await _repository()).seller_listings(seller_id)


async def market_listing_items(listing_id: int) -> list[dict[str, Any]]:
    return await (await _repository()).listing_items(listing_id)


async def market_listing_display_tiers(
    listing_id: int,
) -> list[dict[str, Any]]:
    return await (await _repository()).listing_display_tiers(listing_id)


async def market_listing_navigation_view(
    listing_id: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    return await (await _repository()).listing_navigation_view(listing_id)


async def market_listing_reload_view(
    listing_id: int,
) -> dict[str, Any] | None:
    return await (await _repository()).listing_reload_view(listing_id)


async def market_search(
    *,
    deck_id: int | None = None,
    rarity: str | None = None,
    q: str | None = None,
    currency: str | None = None,
    cash_code: str | None = None,
    offer_kind: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return await (await _repository()).search(
        deck_id=deck_id,
        rarity=rarity,
        query=q,
        currency=currency,
        cash_code=cash_code,
        offer_kind=offer_kind,
        price_min=price_min,
        price_max=price_max,
        limit=limit,
        offset=offset,
    )


async def market_get_cover_file_id(listing_id: int) -> str | None:
    return await (await _repository()).get_cover_file_id(listing_id)


async def market_has_any_proof(listing_id: int) -> bool:
    return await (await _repository()).has_any_proof(listing_id)


async def market_price_map(listing_id: int) -> dict[str, int | float]:
    return await (await _repository()).price_map(listing_id)


async def fetch_card(card_id: int) -> dict[str, Any]:
    return await (await _repository()).fetch_card(card_id)


async def get_all_decks() -> list[dict[str, Any]]:
    return await (await _repository()).all_decks()


async def get_cards_by_deck(deck_id: int) -> list[dict[str, Any]]:
    return await (await _repository()).cards_by_deck(deck_id)


async def get_cards_ids_by_deck(deck_id: int) -> list[int]:
    return await (await _repository()).card_ids_by_deck(deck_id)


async def market_persist_proofs(
    listing_id: int,
    *,
    proof_file_id: str | None,
    proof_by_card: dict[str, str],
) -> None:
    await (await _repository()).persist_proofs(
        listing_id,
        proof_file_id=proof_file_id,
        proof_by_card=proof_by_card,
    )


async def market_get_listing_core(listing_id: int) -> dict[str, Any] | None:
    return await (await _repository()).listing_core(listing_id)
