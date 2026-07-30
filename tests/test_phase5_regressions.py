from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _functions(relative: str) -> set[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_auction_notifications_are_transactionally_enqueued() -> None:
    repository = (ROOT / "bot/repositories/outbox.py").read_text(encoding="utf-8")
    notifications = (ROOT / "bot/auction_notify.py").read_text(encoding="utf-8")
    database = (ROOT / "db/db.py").read_text(encoding="utf-8")

    assert "async with conn.transaction()" in repository
    assert "FOR UPDATE SKIP LOCKED" in repository
    assert "ON CONFLICT (dedupe_key) DO NOTHING" in repository
    assert "SET {flag} = TRUE" in repository
    assert 'event="start"' in notifications
    assert 'event="one_minute"' in notifications
    assert 'event="end"' in notifications
    assert "Аукцион завершён!" in notifications
    assert "update_lot_field" not in database
    assert "update_lot_field" not in notifications


def test_outbox_worker_avoids_blind_retry_after_unknown_delivery() -> None:
    worker = (ROOT / "bot/telegram/outbox.py").read_text(encoding="utf-8")
    workers = (ROOT / "bot/bootstrap/workers.py").read_text(encoding="utf-8")
    assert "except TelegramRetryAfter" in worker
    assert "delivery outcome unknown; manual review required" in worker
    assert "repository.mark_failed" in worker
    assert 'BackgroundTaskSpec("telegram-outbox"' in workers
    assert "telegram_outbox_loop(bot)" in workers


def test_phase5_migration_converts_legacy_moscow_times_and_creates_outbox() -> None:
    migration = (
        ROOT / "migrations/005_transactional_outbox_and_utc.sql"
    ).read_text(encoding="utf-8")
    bootstrap = (ROOT / "init_db.sql").read_text(encoding="utf-8")
    assert "start_time TYPE timestamptz" in migration
    assert "end_time TYPE timestamptz" in migration
    assert "placed_at TYPE timestamptz" in migration
    assert "AT TIME ZONE 'Europe/Moscow'" in migration
    assert "CREATE TABLE IF NOT EXISTS public.telegram_outbox" in migration
    assert "dedupe_key text NOT NULL UNIQUE" in migration
    assert "ix_telegram_outbox_pending" in migration
    assert "start_time timestamp with time zone NOT NULL" in bootstrap
    assert "placed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP" in bootstrap


def test_phase5_migration_preserves_start_time_trigger_during_type_change() -> None:
    migration = (
        ROOT / "migrations/005_transactional_outbox_and_utc.sql"
    ).read_text(encoding="utf-8")
    assert "pg_get_triggerdef" in migration
    assert "DROP TRIGGER trg_auctions_fix_end_time" in migration
    assert "EXECUTE fix_end_time_trigger_ddl" in migration
    assert "fix_end_time_trigger_enabled" in migration


def test_admin_lifecycle_router_is_extracted_and_registered() -> None:
    legacy_functions = _functions("bot/handlers/auction_comments.py")
    extracted_functions = _functions("bot/handlers/auction/admin_lifecycle.py")
    routers = (ROOT / "bot/bootstrap/routers.py").read_text(encoding="utf-8")

    moved = {
        "show_lot_owners",
        "activate_lot_cmd",
        "show_user_lots",
        "admin_delete_bid",
        "admin_start_auction",
        "admin_stop_auction",
    }
    assert not (legacy_functions & moved)
    assert moved <= extracted_functions
    assert "dispatcher.include_router(auction_admin_lifecycle_router)" in routers


def test_admin_bid_delete_is_a_transactional_application_operation() -> None:
    handler = (
        ROOT / "bot/handlers/auction/admin_lifecycle.py"
    ).read_text(encoding="utf-8")
    repository = (
        ROOT / "bot/repositories/auction_admin.py"
    ).read_text(encoding="utf-8")
    assert "AuctionAdminService.create()" in handler
    assert 'DELETE FROM public.bids WHERE bid_id = $1' not in handler
    assert "async with conn.transaction()" in repository
    assert 'DELETE FROM public.bids WHERE bid_id = $1' in repository
    assert "UPDATE public.users" in repository


def test_workflow_services_normalize_boundaries_to_utc() -> None:
    workflows = (
        ROOT / "bot/services/auction_workflows.py"
    ).read_text(encoding="utf-8")
    bids = (ROOT / "bot/services/auction_bids.py").read_text(encoding="utf-8")
    finalization = (
        ROOT / "bot/services/auction_finalization.py"
    ).read_text(encoding="utf-8")
    publication = (
        ROOT / "bot/handlers/auction/publication.py"
    ).read_text(encoding="utf-8")
    assert workflows.count("ensure_utc(") >= 5
    assert "utc_now()" in bids
    assert "utc_now()" in finalization
    assert "claim_due(now=utc_now()" in publication


def test_migration_runner_serializes_concurrent_replicas() -> None:
    runner = (ROOT / "db/migrations.py").read_text(encoding="utf-8")
    assert "pg_advisory_lock" in runner
    assert "pg_advisory_unlock" in runner
    assert "MIGRATION_LOCK_KEY" in runner
