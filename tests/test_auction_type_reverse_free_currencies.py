from __future__ import annotations

from pathlib import Path

import pytest

from bot.domain.auctions import (
    AuctionKind,
    Currency,
    comparison_units,
    currency_choices_label,
    parse_bid_offer,
)
from bot.domain.auctions.exceptions import BidFormatError, BidTooHigh
from bot.domain.auctions.rules import validate_bid_for_kind

ROOT = Path(__file__).resolve().parents[1]


def test_reverse_bid_validation_accepts_lower_and_rejects_higher() -> None:
    validate_bid_for_kind(
        amount=90,
        currency=Currency.DIAMONDS,
        start_price=100,
        current_best=100,
        auction_kind=AuctionKind.REVERSE,
    )
    with pytest.raises(BidTooHigh):
        validate_bid_for_kind(
            amount=100,
            currency=Currency.DIAMONDS,
            start_price=100,
            current_best=100,
            auction_kind=AuctionKind.REVERSE,
        )


def test_reverse_and_free_currency_label_supports_tea_and_diamonds() -> None:
    assert currency_choices_label(["чашки", "алмазы"]) == "🍵 чай или/и 💎 алмазы"


def test_mixed_currency_bid_requires_explicit_marker() -> None:
    choices = (Currency.CUPS, Currency.DIAMONDS)
    with pytest.raises(BidFormatError):
        parse_bid_offer("12", accepted_currencies=choices, fallback=Currency.CUPS)
    tea = parse_bid_offer("12 чай", accepted_currencies=choices, fallback=Currency.CUPS)
    diamonds = parse_bid_offer(
        "120 алмазов",
        accepted_currencies=choices,
        fallback=Currency.CUPS,
    )
    assert comparison_units(tea.amount, tea.currency) == 120
    assert comparison_units(diamonds.amount, diamonds.currency) == 120


def test_free_custom_combo_label_overrides_currency_list() -> None:
    assert currency_choices_label(
        ["чашки", "алмазы"],
        custom_terms="2 чая + карта из КР",
    ) == "🧩 2 чая + карта из КР"


def test_runtime_userbot_uses_currency_aware_reverse_pipeline() -> None:
    handler = (ROOT / "userbot/handlers/new_messages.py").read_text(encoding="utf-8")
    repository = (ROOT / "userbot/repositories.py").read_text(encoding="utf-8")
    service = (ROOT / "bot/services/auction_bids.py").read_text(encoding="utf-8")
    assert "parse_bid_offer(" in handler
    assert "accepted_currencies" in handler
    assert "fetch_best_bid_units" in repository
    assert "validate_reverse_offer(" in service
    assert "currency=bid_currency.value" in service


def test_runtime_winner_helpers_rank_reverse_in_common_units() -> None:
    comments = (ROOT / "bot/handlers/auction_comments.py").read_text(encoding="utf-8")
    winner_repository = (ROOT / "bot/repositories/auction_winners.py").read_text(encoding="utf-8")
    announcement = (
        ROOT / "bot/handlers/auction/winner_components/announcement.py"
    ).read_text(encoding="utf-8")
    assert "WHEN 'чашки' THEN amount * 10" in winner_repository
    assert "WHEN 'чашки' THEN b.amount * 10" in winner_repository
    assert "get_best_bid_for_auction" in comments
    assert "comparison_units(amount" in announcement


def test_free_auction_currency_picker_has_both_single_and_combined_choices() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
    assert 'KeyboardButton(text="🍵 Чай")' in source
    assert 'KeyboardButton(text="💎 Алмазы")' in source
    assert 'KeyboardButton(text="🍵 + 💎 Чай или/и алмазы")' in source
    assert 'KeyboardButton(text="🧩 Комби (свои варианты)")' in source
    assert 'accepted_currencies = ["чашки", "алмазы"]' in source


def test_moderation_schedule_and_edit_card_show_auction_type() -> None:
    moderation = (ROOT / "bot/handlers/admin/moderation.py").read_text(encoding="utf-8")
    edit_card = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")
    assert 'f"⚙️ Тип: {kind_text}\\n"' in moderation
    assert 'f"⚙️ <b>Тип аука:</b> {kind_label}\\n"' in edit_card
    assert "Побеждает минимальная ставка" in moderation
    assert "Победитель:</b> минимальная ставка" in edit_card


def test_currency_storage_migrations_are_backward_compatible() -> None:
    sql9 = (ROOT / "db/migrations/009_auction_type_and_free_currencies.sql").read_text(encoding="utf-8")
    sql10 = (ROOT / "db/migrations/010_reverse_free_diamonds_custom_combo.sql").read_text(encoding="utf-8")
    sql12 = (ROOT / "db/migrations/012_bid_currency_and_deadline_contract.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS accepted_currencies text[]" in sql9
    assert "ARRAY[currency]::text[]" in sql9
    assert "cardinality(accepted_currencies) BETWEEN 1 AND 2" in sql9
    assert "ADD COLUMN IF NOT EXISTS custom_offer_terms text" in sql10
    assert "NOT IN ('free', 'reverse')" in sql10
    assert "UPDATE OF currency, accepted_currencies, custom_offer_terms, auction_kind" in sql10
    assert "ADD COLUMN IF NOT EXISTS currency TEXT" in sql12
    assert "CREATE TRIGGER trg_fill_bid_currency" in sql12


def test_initial_schema_history_remains_immutable() -> None:
    import hashlib

    migration = ROOT / "db/migrations/002_initial_schema.sql"
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (
        "6e11ca64b0ef60fabfa7aa92aafd0d4988643db3667f8ebe45b85a5a4da90975"
    )
    initial_sql = migration.read_text(encoding="utf-8")
    assert "accepted_currencies" not in initial_sql
