from __future__ import annotations

from db.migrator import _load_migrations


def test_runtime_migration_catalog_contains_schedule_and_bid_contracts() -> None:
    migrations = _load_migrations()
    filenames = [migration.filename for migration in migrations]
    versions = [migration.version for migration in migrations]

    assert len(versions) == len(set(versions))
    assert filenames[-1] == "020_privacy_export_audit_immutability.sql"

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

    bid_contract = next(
        migration
        for migration in migrations
        if migration.filename == "012_bid_currency_and_deadline_contract.sql"
    )
    assert "ADD COLUMN IF NOT EXISTS currency TEXT" in bid_contract.sql
    assert "CREATE TRIGGER trg_fill_bid_currency" in bid_contract.sql

    temporary_emoji_contract = next(
        migration
        for migration in migrations
        if migration.filename == "013_schedule_temporary_emoji_marks.sql"
    )
    assert "schedule_temporary_emoji_marks" in temporary_emoji_contract.sql
    assert "PRIMARY KEY (scope, entity_key)" in temporary_emoji_contract.sql

    utc_outbox_contract = next(
        migration
        for migration in migrations
        if migration.filename == "014_transactional_outbox_and_utc.sql"
    )
    assert "ALTER COLUMN start_time TYPE timestamptz" in utc_outbox_contract.sql
    assert "CREATE TABLE IF NOT EXISTS public.telegram_outbox" in utc_outbox_contract.sql

    delivery_contract = next(
        migration
        for migration in migrations
        if migration.filename == "015_outbox_delivery_control.sql"
    )
    assert "delivery_state" in delivery_contract.sql
    assert "copy_message" in delivery_contract.sql

    processing_contract = next(
        migration
        for migration in migrations
        if migration.filename == "016_auction_processing_leases.sql"
    )
    assert "publication_started_at" in processing_contract.sql
    assert "finalization_started_at" in processing_contract.sql
    assert "ix_auctions_publication_due" in processing_contract.sql

    performance_contract = next(
        migration
        for migration in migrations
        if migration.filename == "017_query_performance_indexes.sql"
    )
    assert "ix_users_username_ci" in performance_contract.sql
    assert "ix_users_trusted_username_ci" in performance_contract.sql
    assert "ix_exchange_batches_status_created" in performance_contract.sql
    assert "ix_telegram_outbox_pending" in performance_contract.sql
