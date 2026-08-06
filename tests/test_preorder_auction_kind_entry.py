from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_preorder_auction_kind_opens_future_empty_deck_cart() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")

    expected_snippets = (
        'F.data == f"auk_kind:{AuctionKind.PREORDER.value}"',
        "StateFilter(UserAddLotFSM.waiting_for_auction_kind)",
        "await state.set_state(UserAddLotFSM.waiting_for_own_variant)",
        "await preorder._show_future_decks(message, state)",
    )
    for snippet in expected_snippets:
        assert snippet in source

    handler_start = source.index("async def start_preorder_auction_kind")
    router_composition = source.index("router.include_routers")
    handler_source = source[handler_start:router_composition]

    assert "get_all_decks" not in handler_source
    assert source.index("@router.callback_query") < router_composition
