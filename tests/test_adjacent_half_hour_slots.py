from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repository_uses_grid_start_instead_of_deadline_overlap() -> None:
    source = (ROOT / "bot/repositories/auction_workflows.py").read_text(
        encoding="utf-8"
    )

    assert "date_trunc('minute', existing.start_time)" in source
    assert "date_trunc('minute', $2::timestamptz)" in source
    assert "existing_owner.user_id = current_owner.user_id" in source
    assert "existing.card_id = current_lot.card_id" in source

    # 18:00–18:30:59 must not block the next grid position at 18:30.
    assert "AND start_time < $3" not in source
    assert "AND end_time > $2" not in source


def test_legacy_scheduler_uses_the_same_start_slot_policy() -> None:
    source = (ROOT / "db/db.py").read_text(encoding="utf-8")

    assert "date_trunc('minute', a.start_time)" in source
    assert "current_owner.user_id = existing_owner.user_id" in source
    assert "card_id, card_name, hero_name" in source
