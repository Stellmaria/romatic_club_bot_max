from __future__ import annotations

from db.migrator import _load_migrations


def test_runtime_migration_catalog_contains_schedule_and_bid_contracts() -> None:
    migrations = _load_migrations()
    filenames = [migration.filename for migration in migrations]
    versions = [migration.version for migration in migrations]

    assert len(versions) == len(set(versions))
    assert filenames[-1] == "012_bid_currency_and_deadline_contract.sql"

    schedule_migration = next(
        migration
        for migration in migrations
        if migration.filename == "011_schedule_setup_master.sql"
    )
    assert "schedule_emoji_assets" in schedule_migration.sql
    assert "schedule_deck_emojis" in schedule_migration.sql
    assert "schedule_card_emojis" in schedule_migration.sql
    assert "schedule_setup_sessions" in schedule_migration.sql
    assert "schedule_preview_target" in schedule_migration.sql
    assert "schedule_publication_reviews" in schedule_migration.sql

    bid_contract = migrations[-1]
    assert "ADD COLUMN IF NOT EXISTS currency TEXT" in bid_contract.sql
    assert "CREATE TRIGGER trg_fill_bid_currency" in bid_contract.sql
