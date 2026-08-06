from pathlib import Path


def test_preorder_auction_kind_opens_future_empty_deck_cart() -> None:
    source = Path("bot/handlers/auctions.py").read_text(encoding="utf-8")
    assert "start_preorder_auction_kind" in source
    assert "StateFilter(UserAddLotFSM.waiting_for_auction_kind)" in source
    assert "await preorder._show_future_decks(message, state)" in source
