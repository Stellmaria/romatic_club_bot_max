from __future__ import annotations

from dataclasses import dataclass

from bot.domain.auctions.enums import AuctionKind, Currency


@dataclass(frozen=True, slots=True)
class AuctionDraft:
    owner_id: int
    start_price: int
    currency: Currency
    accepted_currencies: tuple[Currency, ...] = ()
    custom_offer_terms: str | None = None
    auction_kind: AuctionKind = AuctionKind.STANDARD
    card_id: int | None = None
    card_name: str | None = None
    hero_name: str | None = None
    image_id: str | None = None
    comment: str = ""
    proof_photo_id: str | None = None
    craft_uid_possible: bool | None = None


@dataclass(frozen=True, slots=True)
class ExchangeDraft:
    user_id: int
    deck_id: int
    mode: str
    currency: Currency
    price: int
    card_ids: tuple[int, ...]
    comment: str = ""
    proof_photo_id: str = "NO_PROOF"


@dataclass(frozen=True, slots=True)
class PublicationFailure:
    auction_id: int
    terminal: bool
    attempts: int
