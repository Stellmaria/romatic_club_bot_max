from __future__ import annotations

from db.migrator import _load_migrations


def test_runtime_migration_catalog_contains_schedule_master() -> None:
    migrations = _load_migrations()
    filenames = [migration.filename for migration in migrations]
    versions = [migration.version for migration in migrations]

    assert len(versions) == len(set(versions))
    assert filenames[-1] == "011_schedule_setup_master.sql"

    schedule_migration = migrations[-1]
    assert "schedule_emoji_assets" in schedule_migration.sql
    assert "schedule_deck_emojis" in schedule_migration.sql
    assert "schedule_card_emojis" in schedule_migration.sql
    assert "schedule_setup_sessions" in schedule_migration.sql
    assert "schedule_preview_target" in schedule_migration.sql
    assert "schedule_publication_reviews" in schedule_migration.sql
