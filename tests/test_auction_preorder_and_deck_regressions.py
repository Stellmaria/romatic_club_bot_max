from pathlib import Path

from bot.domain.auctions import AuctionKind
from bot.handlers.auction.kinds import auction_kind_keyboard


ROOT = Path(__file__).resolve().parents[1]


def _callbacks(markup: object) -> dict[str, str]:
    rows = getattr(markup, "inline_keyboard")
    return {
        str(button.text): str(button.callback_data)
        for row in rows
        for button in row
        if button.callback_data
    }


def test_preorder_uses_standard_auction_policies() -> None:
    assert AuctionKind.from_raw("предзаказ") is AuctionKind.PREORDER
    assert AuctionKind.PREORDER.minimum_luxury_level == 1
    assert AuctionKind.PREORDER.is_automatic_bidding
    assert AuctionKind.PREORDER.supports_autobid
    assert not AuctionKind.PREORDER.lowest_bid_wins
    assert not AuctionKind.PREORDER.requires_luxury_bidder


def test_preorder_button_is_locked_without_luxury() -> None:
    callbacks = _callbacks(auction_kind_keyboard(0))
    assert callbacks["🔒 📦 Предзаказ (Л1)"] == "auk_kind_locked:preorder:1"


def test_preorder_button_is_available_to_luxury_levels_one_and_two() -> None:
    for level in (1, 2):
        callbacks = _callbacks(auction_kind_keyboard(level))
        assert callbacks["📦 Предзаказ"] == "auk_kind:preorder"


def test_whole_deck_button_is_added_even_when_card_list_is_empty() -> None:
    source = (ROOT / "bot/handlers/auction/submission.py").read_text(
        encoding="utf-8"
    )
    card_list = "for c in (cards or [])"
    whole_deck = 'callback_data=f"user_all_deck_{deck_id}"'
    assert card_list in source
    assert whole_deck in source
    assert source.index(card_list) < source.index(whole_deck)


def test_deck_name_migration_sets_canonical_names() -> None:
    sql = (ROOT / "migrations/009_fix_deck_names_28_29.sql").read_text(
        encoding="utf-8"
    )
    assert "WHEN 28 THEN '28 колода'" in sql
    assert "WHEN 29 THEN '29 колода'" in sql
    assert "WHERE id IN (28, 29)" in sql
    assert "IS DISTINCT FROM" in sql
