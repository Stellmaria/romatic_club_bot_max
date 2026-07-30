from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_review_queue_includes_all_pre_schedule_statuses() -> None:
    source = (ROOT / "db/legacy_impl.py").read_text(encoding="utf-8")
    assert 'review_statuses = ("draft", "moderation", "pending", "approved")' in source
    assert 'a.status = ANY($1::text[])' in source
    assert 'a.status,' in source
    assert 'LEFT JOIN LATERAL' in source


def test_active_admin_panel_has_workflow_imports_and_immediate_callback_answer() -> None:
    source = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("bot.services.auction_workflows", "AuctionModerationService") in imported
    assert ("bot.domain.auctions", "AuctionSlotConflict") in imported
    assert ("bot.domain.auctions", "InvalidAuctionTransition") in imported

    handler = source[source.index("async def save_edited_time"):]
    answer = handler.index('safe_callback_answer(call, "⏳ Переношу лот…")')
    db_call = handler.index("moderation_service.reschedule(")
    assert answer < db_call


def test_whois_uses_binding_status_not_decryption_success() -> None:
    users = (ROOT / "bot/handlers/users.py").read_text(encoding="utf-8")
    admin = (ROOT / "bot/handlers/admin/uid_verification_admin.py").read_text(encoding="utf-8")
    repo = (ROOT / "bot/repositories/uid_verification.py").read_text(encoding="utf-8")

    assert 'str(uid_record.get("status") or "").lower() == "verified"' in users
    assert 'UID-верификация: <b>✅ подтверждена</b>' in admin
    assert 'SELECT uid, uid_enc' in repo
    assert 'legacy_uid' in repo


def test_uid_binding_repair_migration_is_safe_and_indexed() -> None:
    sql = (ROOT / "db/migrations/008_repair_uid_bindings_and_review_queue.sql").read_text(encoding="utf-8")
    assert "WHERE r.status = 'approved'" in sql
    assert "other.user_id <> l.user_id" in sql
    assert "ON CONFLICT (user_id) DO UPDATE" in sql
    assert "status      = 'verified'" in sql
    assert "ix_auctions_moderation_queue_created" in sql
