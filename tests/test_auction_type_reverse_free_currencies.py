from __future__ import annotations

from pathlib import Path

import pytest

from bot.domain.auctions import AuctionKind, Currency, currency_choices_label
from bot.domain.auctions.exceptions import BidTooHigh
from bot.domain.auctions.rules import validate_bid_for_kind

ROOT = Path(__file__).resolve().parents[1]


def test_reverse_bid_validation_accepts_lower_and_rejects_higher() -> None:
    validate_bid_for_kind(
        amount=90,
        currency=Currency.DIAMONDS,
        start_price=0,
        current_best=100,
        auction_kind=AuctionKind.REVERSE,
    )
    with pytest.raises(BidTooHigh):
        validate_bid_for_kind(
            amount=100,
            currency=Currency.DIAMONDS,
            start_price=0,
            current_best=100,
            auction_kind=AuctionKind.REVERSE,
        )


def test_reverse_and_free_currency_label_supports_tea_and_diamonds() -> None:
    assert currency_choices_label(["чашки", "алмазы"]) == "🍵 чай или/и 💎 алмазы"


def test_free_custom_combo_label_overrides_currency_list() -> None:
    assert currency_choices_label(
        ["чашки", "алмазы"],
        custom_terms="2 чая + карта из КР",
    ) == "🧩 2 чая + карта из КР"


def test_runtime_userbot_uses_minimum_as_best_reverse_bid() -> None:
    source = (ROOT / "find_discussion_id.py").read_text(encoding="utf-8")
    assert "THEN MIN(b.amount)" in source
    assert 'lowest_wins = kind_key == "reverse"' in source
    assert "В обратном аукционе выигрывает меньшая ставка" in source
    assert 'kind_key in {"standard", "fast", "black"}' in source


def test_runtime_winner_helpers_rank_reverse_ascending() -> None:
    comments = (ROOT / "bot/handlers/auction_comments.py").read_text(encoding="utf-8")
    database = (ROOT / "db/db.py").read_text(encoding="utf-8")
    assert "THEN b.amount END ASC" in comments
    assert "get_best_bid_for_auction" in comments
    assert "THEN b.amount END ASC" in database


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
    assert "ADD COLUMN IF NOT EXISTS accepted_currencies text[]" in sql9
    assert "ARRAY[currency]::text[]" in sql9
    assert "cardinality(accepted_currencies) BETWEEN 1 AND 2" in sql9
    assert "ADD COLUMN IF NOT EXISTS custom_offer_terms text" in sql10
    assert "NOT IN ('free', 'reverse')" in sql10
    assert "UPDATE OF currency, accepted_currencies, custom_offer_terms, auction_kind" in sql10


def test_initial_schema_history_remains_immutable() -> None:
    import hashlib

    migration = ROOT / "db/migrations/002_initial_schema.sql"
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (
        "6e11ca64b0ef60fabfa7aa92aafd0d4988643db3667f8ebe45b85a5a4da90975"
    )
    initial_sql = migration.read_text(encoding="utf-8")
    assert "accepted_currencies" not in initial_sql
