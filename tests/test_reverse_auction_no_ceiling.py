from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reverse_submission_skips_start_price_range() -> None:
    source = (ROOT / "bot/handlers/auction/submission.py").read_text(encoding="utf-8")
    assert "auction_kind == AuctionKind.REVERSE.value" in source
    assert "start_price=0" in source
    assert "waiting_for_comment" in source


def test_reverse_caption_has_no_fixed_ceiling_label() -> None:
    source = (ROOT / "bot/handlers/admin/helper/admin_constants.py").read_text(encoding="utf-8")
    assert 'if kind_key == "reverse"' in source
    assert "Ставки идут на понижение" in source


def test_reverse_bid_constraint_uses_lowest_existing_bid() -> None:
    sql = (ROOT / "db/migrations/006_reverse_auction_without_ceiling.sql").read_text(encoding="utf-8")
    assert "kind = 'reverse'" in sql
    assert "SELECT min(b.amount)" in sql
    assert "p_amount <= best_b - step" in sql


def test_legacy_winner_loop_uses_lowest_bid_for_reverse() -> None:
    source = (ROOT / "bot/handlers/auction_comments.py").read_text(encoding="utf-8")
    assert "lowest_wins = kind is AuctionKind.REVERSE" in source
    assert "amt < max_amt if lowest_wins else amt > max_amt" in source
    assert "threshold = 0 if lowest_wins" in source
