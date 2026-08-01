from __future__ import annotations

from pathlib import Path

import pytest

from bot.domain.auctions import BidTooHigh, Currency, validate_reverse_offer

ROOT = Path(__file__).resolve().parents[1]


def test_reverse_first_bid_respects_starting_ceiling() -> None:
    assert validate_reverse_offer(
        amount=100,
        currency=Currency.DIAMONDS,
        start_price=10,
        base_currency=Currency.CUPS,
        current_best_units=None,
    ) == 100
    with pytest.raises(BidTooHigh) as exc:
        validate_reverse_offer(
            amount=110,
            currency=Currency.DIAMONDS,
            start_price=10,
            base_currency=Currency.CUPS,
            current_best_units=None,
        )
    assert exc.value.maximum == 100


def test_reverse_mixed_currency_bid_must_improve_in_common_units() -> None:
    assert validate_reverse_offer(
        amount=8,
        currency=Currency.CUPS,
        start_price=20,
        base_currency=Currency.CUPS,
        current_best_units=100,
    ) == 8
    assert validate_reverse_offer(
        amount=90,
        currency=Currency.DIAMONDS,
        start_price=20,
        base_currency=Currency.CUPS,
        current_best_units=100,
    ) == 90


def test_submission_routes_reverse_to_starting_ceiling() -> None:
    source = (ROOT / "bot/handlers/auction/submission.py").read_text(encoding="utf-8")
    assert "if is_reverse:" in source
    assert "Стартовый потолок обратного аукциона" in source
    assert "if is_reverse or is_free" not in source


def test_finalizer_waits_until_next_minute() -> None:
    source = (ROOT / "bot/repositories/auctions.py").read_text(encoding="utf-8")
    assert "date_trunc('minute', end_time)" in source
    assert "+ INTERVAL '1 minute' <= $1" in source


def test_bid_currency_migration_is_packaged() -> None:
    migration = ROOT / "db/migrations/012_bid_currency_and_deadline_contract.sql"
    source = migration.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS currency TEXT" in source
    assert "CREATE TRIGGER trg_fill_bid_currency" in source
