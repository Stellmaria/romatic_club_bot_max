from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _top_level_functions(relative: str) -> set[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_creation_menu_enables_all_supported_kinds_without_forged_access() -> None:
    handler = (ROOT / "bot/handlers/auction/submission.py").read_text(encoding="utf-8")
    kinds = (ROOT / "bot/handlers/auction/kinds.py").read_text(encoding="utf-8")
    assert "Пока в разработке" not in handler
    assert "selected_kind.minimum_luxury_level" in handler
    assert "AuctionCreationService.create()" in handler
    assert "kind.minimum_luxury_level" in kinds


def test_publication_is_extracted_and_claim_based() -> None:
    monolith_functions = _top_level_functions("bot/handlers/auctions.py")
    assert "publish_auction_lot" not in monolith_functions
    assert "auction_publisher_loop" not in monolith_functions

    publication = (ROOT / "bot/handlers/auction/publication.py").read_text(encoding="utf-8")
    repository = (ROOT / "bot/repositories/auction_workflows.py").read_text(encoding="utf-8")
    workers = (ROOT / "bot/bootstrap/workers.py").read_text(encoding="utf-8")
    assert "AuctionPublicationService" in publication
    assert "await service.mark_published" in publication
    assert "FOR UPDATE SKIP LOCKED" in repository
    assert "publication_attempts" in repository
    assert "auction_publisher_loop" in workers


def test_moderation_schedule_is_guarded_against_slot_races() -> None:
    repository = (ROOT / "bot/repositories/auction_workflows.py").read_text(encoding="utf-8")
    moderation = (ROOT / "bot/handlers/admin/moderation.py").read_text(encoding="utf-8")
    admin_panel = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in repository
    assert "AuctionSlotConflict" in repository
    assert "AuctionModerationService.create()" in moderation
    assert "AuctionModerationService.create()" in admin_panel
    assert "update_auction_time_status" not in moderation
    assert "update_auction_time_status" not in admin_panel


def test_exchange_writes_are_transactional_and_moderation_is_single_use() -> None:
    repository = (ROOT / "bot/repositories/exchanges.py").read_text(encoding="utf-8")
    exchange_dir = ROOT / "bot/handlers/auction/exchange"
    handler = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(exchange_dir.glob("*.py"))
    )
    assert "async with conn.transaction()" in repository
    assert "executemany" in repository
    assert "AND status = 'pending'" in repository
    assert "claim_for_post" in repository
    assert "ExchangeService.create()" in handler
    tree = ast.parse(handler)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for legacy_call in (
        "create_exchange_batch",
        "add_exchange_item_for_card",
        "set_exchange_batch_moderation",
        "set_exchange_batch_status",
        "set_exchange_batch_posted",
        "set_exchange_batch_deleted",
    ):
        assert legacy_call not in called_names


def test_phase4_legacy_database_write_api_is_removed() -> None:
    functions = _top_level_functions("db/db.py")
    for name in (
        "add_pending_auction",
        "add_pending_auction_by_card_id",
        "create_exchange_batch",
        "add_exchange_item_for_card",
        "set_exchange_batch_moderation",
        "set_exchange_batch_status",
        "set_exchange_batch_posted",
        "set_exchange_batch_deleted",
        "update_auction_time_status",
        "update_auction_status",
        "update_auction_currency",
        "update_auction_price",
        "delete_lot",
        "update_card_field",
    ):
        assert name not in functions


def test_shared_helpers_do_not_reintroduce_auction_admin_import_cycle() -> None:
    admin_actions = (
        ROOT / "bot/handlers/admin/helper/new/admin_actions.py"
    ).read_text(encoding="utf-8")
    assert "from bot.handlers.auctions import" not in admin_actions
    assert "from bot.services.admin_thanks import" in admin_actions
    assert "from bot.telegram.media import" in admin_actions


def test_phase4_migration_covers_statuses_indexes_and_copy_mode() -> None:
    migration = (ROOT / "migrations/004_auction_workflows.sql").read_text(encoding="utf-8")
    bootstrap = (ROOT / "init_db.sql").read_text(encoding="utf-8")
    assert "publication_failed" in migration
    assert "'publishing'" in migration
    assert "'publication_failed'" in migration
    assert "'published'" in migration
    assert "ix_auctions_publication_queue" in migration
    assert "ix_bids_auction_lowest_winner_order" in migration
    assert "ux_exchange_batches_posted_message" in migration
    assert "DROP INDEX IF EXISTS public.ux_exchange_items_batch_card" in migration
    assert "DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31" in migration
    assert "ADD CONSTRAINT chk_auctions_time_order" in migration
    assert "ix_auctions_publication_queue" in bootstrap
    assert "chk_exchange_batches_status" in bootstrap
    assert "auctions_end_eq_start_plus_31" not in bootstrap


def test_userbot_leaves_free_auction_comments_for_manual_review() -> None:
    source = (ROOT / "userbot/handlers/new_messages.py").read_text(encoding="utf-8")
    assert "if not auction_kind.is_automatic_bidding:" in source
    assert "_fetch_best_bid_units" in source
    assert "lowest_wins=False" in source
    engine = (ROOT / "userbot/autobid_engine.py").read_text(encoding="utf-8")
    assert "if not kind.supports_autobid:" in engine


def test_owner_edits_and_cancellation_are_owner_scoped() -> None:
    repository = (ROOT / "bot/repositories/auction_workflows.py").read_text(encoding="utf-8")
    users = (ROOT / "bot/handlers/users.py").read_text(encoding="utf-8")
    assert "ao.user_id" in repository
    assert "update_owner_fields" in repository
    assert "cancel_by_owner" in repository
    assert "AuctionOwnerService.create()" in users
    assert "owner_id=call.from_user.id" in users
    assert "await delete_lot(" not in users


def test_application_layers_do_not_import_handlers_or_override_symbols() -> None:
    python_files = sorted((ROOT / "bot").rglob("*.py"))
    for path in python_files:
        relative_parts = path.relative_to(ROOT).parts
        if relative_parts[:2] not in {
            ("bot", "services"),
            ("bot", "repositories"),
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert node.name not in names, f"duplicate top-level symbol: {path}:{node.name}"
                names.add(node.name)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bot.handlers"), (
                    f"application layer depends on handler: {path}:{node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("bot.handlers"), (
                        f"application layer depends on handler: {path}:{alias.name}"
                    )

    finalization = (
        ROOT / "bot/services/auction_finalization.py"
    ).read_text(encoding="utf-8")
    workers = (ROOT / "bot/bootstrap/workers.py").read_text(encoding="utf-8")
    assert "self._announcer" in finalization
    assert "auction_finalization_loop(bot, announce_winner)" in workers
