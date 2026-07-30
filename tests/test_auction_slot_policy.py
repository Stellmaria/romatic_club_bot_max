from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_conflict_matches_moderation_slot_policy() -> None:
    repository = (ROOT / "bot/repositories/auction_workflows.py").read_text(
        encoding="utf-8"
    )

    assert "_has_prohibited_slot_overlap" in repository
    assert "public.auction_owners AS current_owner" in repository
    assert "public.auction_owners AS existing_owner" in repository
    assert "existing_owner.user_id = current_owner.user_id" in repository
    assert "lower(btrim(existing.card_name))" in repository
    assert "lower(btrim(coalesce(existing.hero_name" in repository
    assert repository.count("await self._has_prohibited_slot_overlap(") == 2

    # A slot is not globally exclusive. The old query rejected every overlap,
    # including a different card or a different owner.
    assert "FROM public.auctions\n                        WHERE auction_id <> $1" not in repository


def test_standard_auction_end_uses_second_59() -> None:
    core_time = (ROOT / "bot/core/time.py").read_text(encoding="utf-8")
    moderation = (ROOT / "bot/handlers/admin/moderation_lots.py").read_text(
        encoding="utf-8"
    )
    schedule = (ROOT / "bot/handlers/admin/admin_panel_schedule.py").read_text(
        encoding="utf-8"
    )
    lifecycle = (ROOT / "bot/handlers/auction/admin_lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert "def auction_end_at_59(" in core_time
    assert ".replace(second=59, microsecond=0)" in core_time
    assert "end_time = auction_end_at_59(selected_time)" in moderation
    assert "end_time = auction_end_at_59(start_time)" in moderation
    assert "end_time = auction_end_at_59(start_time)" in schedule
    assert "new_end_time = auction_end_at_59(to_moscow(utc_now()))" in lifecycle


def test_slot_policy_migration_removes_legacy_padding() -> None:
    migration = (
        ROOT / "database/migrations/008_auction_slot_policy.sql"
    ).read_text(encoding="utf-8")
    runner = (ROOT / "db/migrations.py").read_text(encoding="utf-8")

    assert "DROP TRIGGER IF EXISTS trg_auctions_fix_end_time" in migration
    assert "start_time + INTERVAL '30 minutes'" in migration
    assert "start_time > now()" in migration
    assert "status IN ('scheduled', 'publication_failed')" in migration
    assert '"008_auction_slot_policy.sql"' in runner

    restore = (
        ROOT / "database/migrations/009_auction_end_second_59.sql"
    ).read_text(encoding="utf-8")
    assert "date_trunc('minute', start_time + INTERVAL '30 minutes')" in restore
    assert "+ INTERVAL '59 seconds'" in restore
    assert "start_time > now()" in restore
    assert '"009_auction_end_second_59.sql"' in runner


def test_card_media_sync_does_not_depend_on_legacy_31_minute_rows() -> None:
    cards = (ROOT / "db/cards.py").read_text(encoding="utf-8")

    assert "interval '31 minutes'" not in cards.lower()
    assert "_update_auctions_strict(\"\")" in cards
    assert "_update_auctions_fallback(\"\")" in cards
