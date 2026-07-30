from __future__ import annotations

from enum import Enum


class AuctionStatus(str, Enum):
    DRAFT = "draft"
    MODERATION = "moderation"
    PENDING = "pending"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    FINISHED = "finished"
    FINALIZATION_FAILED = "finalization_failed"
    PUBLICATION_FAILED = "publication_failed"
    REJECTED = "rejected"
    CLOSED = "closed"
    CANCELLED = "cancelled"

    @classmethod
    def from_raw(cls, value: object) -> "AuctionStatus | None":
        normalized = str(value or "").strip().lower()
        aliases = {
            "awaiting_moderation": cls.MODERATION,
            "ended": cls.FINISHED,
            "completed": cls.FINISHED,
            "canceled": cls.CANCELLED,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError:
            return None


class AuctionKind(str, Enum):
    """Supported auction workflows and their access/winner policies."""

    STANDARD = "standard"
    REVERSE = "reverse"
    FAST = "fast"
    FREE = "free"
    BLACK = "black"
    EXCHANGE = "exchange"

    @classmethod
    def from_raw(cls, value: object) -> "AuctionKind":
        normalized = str(value or cls.STANDARD.value).strip().lower()
        aliases = {
            "default": cls.STANDARD,
            "regular": cls.STANDARD,
            "normal": cls.STANDARD,
            "freeform": cls.FREE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unsupported auction kind: {normalized or 'empty'}") from exc

    @property
    def minimum_luxury_level(self) -> int:
        return {
            AuctionKind.STANDARD: 0,
            AuctionKind.REVERSE: 1,
            AuctionKind.FAST: 2,
            AuctionKind.FREE: 2,
            AuctionKind.BLACK: 2,
            AuctionKind.EXCHANGE: 0,
        }[self]

    @property
    def is_automatic_bidding(self) -> bool:
        return self not in {AuctionKind.FREE, AuctionKind.EXCHANGE}

    @property
    def lowest_bid_wins(self) -> bool:
        return self is AuctionKind.REVERSE

    @property
    def requires_luxury_bidder(self) -> bool:
        return self is AuctionKind.BLACK

    @property
    def supports_autobid(self) -> bool:
        return self in {AuctionKind.STANDARD, AuctionKind.FAST, AuctionKind.BLACK}


class ExchangeStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHING = "publishing"
    PUBLICATION_FAILED = "publication_failed"
    PUBLISHED = "published"
    DELETED = "deleted"

    @classmethod
    def from_raw(cls, value: object) -> "ExchangeStatus | None":
        normalized = str(value or "").strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            return None


class Currency(str, Enum):
    DIAMONDS = "алмазы"
    CUPS = "чашки"
    TREASURES = "сокровища"

    @classmethod
    def from_raw(cls, value: object) -> "Currency":
        normalized = str(value or "").strip().lower()
        aliases = {
            "💎": cls.DIAMONDS,
            "алмаз": cls.DIAMONDS,
            "алмазы": cls.DIAMONDS,
            "diamond": cls.DIAMONDS,
            "diamonds": cls.DIAMONDS,
            "🍵": cls.CUPS,
            "☕": cls.CUPS,
            "☕️": cls.CUPS,
            "чай": cls.CUPS,
            "чашка": cls.CUPS,
            "чашки": cls.CUPS,
            "cup": cls.CUPS,
            "cups": cls.CUPS,
            "tea": cls.CUPS,
            "🪙": cls.TREASURES,
            "сокровище": cls.TREASURES,
            "сокровища": cls.TREASURES,
            "сокры": cls.TREASURES,
            "сокр": cls.TREASURES,
            "treasure": cls.TREASURES,
            "treasures": cls.TREASURES,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            from bot.domain.auctions.exceptions import UnsupportedCurrency

            raise UnsupportedCurrency(normalized or "—") from exc

    @property
    def bid_step(self) -> int:
        return {
            Currency.DIAMONDS: 10,
            Currency.CUPS: 2,
            Currency.TREASURES: 10,
        }[self]

    @property
    def autobid_step(self) -> int:
        return {
            Currency.DIAMONDS: 90,
            Currency.CUPS: 2,
            Currency.TREASURES: 10,
        }[self]

    @property
    def emoji(self) -> str:
        return {
            Currency.DIAMONDS: "💎",
            Currency.CUPS: "☕️",
            Currency.TREASURES: "🪙",
        }[self]


def normalize_currency_choices(
    values: object,
    *,
    fallback: object | None = None,
) -> tuple[Currency, ...]:
    """Return a stable, duplicate-free list of accepted auction currencies.

    PostgreSQL returns ``text[]`` as a list, while old rows only have the
    scalar ``currency`` column.  This helper deliberately accepts both so the
    display and workflow layers use one compatibility rule.
    """
    raw_values: list[object] = []
    if values is None:
        raw_values = []
    elif isinstance(values, str):
        text = values.strip()
        if text:
            for separator in ("+", ",", ";", "|"):
                text = text.replace(separator, " ")
            raw_values = [part for part in text.split() if part]
    elif isinstance(values, (list, tuple, set, frozenset)):
        raw_values = list(values)
    else:
        raw_values = [values]

    result: list[Currency] = []
    for raw in raw_values:
        try:
            currency = Currency.from_raw(raw)
        except Exception:
            continue
        if currency not in result:
            result.append(currency)

    if not result and fallback is not None:
        try:
            result.append(Currency.from_raw(fallback))
        except Exception:
            pass
    return tuple(result)


def currency_choices_label(
    values: object,
    *,
    fallback: object | None = None,
    with_words: bool = True,
    custom_terms: object | None = None,
) -> str:
    custom_text = str(custom_terms or "").strip()
    if custom_text:
        return f"🧩 {custom_text}"
    choices = normalize_currency_choices(values, fallback=fallback)
    if not choices:
        return "—"
    labels = {
        Currency.DIAMONDS: "💎 алмазы",
        Currency.CUPS: "🍵 чай",
        Currency.TREASURES: "🪙 сокровища",
    }
    if with_words:
        return " или/и ".join(labels[currency] for currency in choices)
    return " / ".join(currency.emoji for currency in choices)
