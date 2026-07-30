from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _top_level_function_counts(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
    return counts


def _top_level_bound_name_counts(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                counts[name] = counts.get(name, 0) + 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                counts[name] = counts.get(name, 0) + 1
    return counts


def test_uid_handler_callbacks_are_not_duplicated() -> None:
    source = (ROOT / "bot/handlers/uid_verification.py").read_text(encoding="utf-8")
    assert source.count('@router.callback_query(F.data == "uidv|start")') == 1
    assert source.count('@router.callback_query(F.data.startswith("uidv_fix|"))') == 1
    assert source.count('@router.callback_query(F.data == "uidv_new")') == 1


def test_uid_public_database_api_has_single_definition() -> None:
    counts = _top_level_bound_name_counts(ROOT / "db/db.py")
    for name in (
        "approve_uid_verification_request",
        "reject_uid_verification_request",
        "mark_uid_verification_request_status",
        "get_user_id_by_uid_any",
        "set_uid_verification_confirmation_status",
        "set_uid_verification_request_revision",
    ):
        assert counts.get(name) == 1, name


def test_database_module_has_no_duplicate_top_level_functions() -> None:
    counts = _top_level_function_counts(ROOT / "db/db.py")
    duplicates = {name: count for name, count in counts.items() if count > 1}
    assert duplicates == {}


def test_new_uid_writes_use_digest_and_encrypted_value() -> None:
    source = (ROOT / "bot/repositories/uid_verification.py").read_text(encoding="utf-8")
    assert "uid_hash(normalized_uid)" in source
    assert "uid_encrypt(normalized_uid)" in source
    assert "VALUES (\n                    $1, $2, $2, $3" in source
    assert "SELECT user_id, uid, status" not in source
    assert 'DELETE FROM public.user_uids WHERE user_id' not in source
    assert "pg_advisory_xact_lock" in source


def test_uid_crypto_round_trip() -> None:
    os.environ.setdefault("UID_HASH_KEY", "test-only-hmac-key")
    os.environ.setdefault("UID_ENC_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")

    from bot.uid_crypto import uid_decrypt, uid_encrypt, uid_hash

    uid = "0123456789abcdef01234567"
    token = uid_encrypt(uid)
    assert token != uid
    assert uid_decrypt(token) == uid
    assert uid_hash(uid) == uid_hash(uid.upper())


def test_auction_finalization_is_claim_based() -> None:
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    repository_source = (ROOT / "bot/repositories/auctions.py").read_text(encoding="utf-8")
    assert "auction_winner_loop" not in main_source
    assert "auction_finalization_loop" in main_source
    assert "FOR UPDATE SKIP LOCKED" in repository_source
    assert "status='finalizing'" in repository_source
    assert "status='finalization_failed'" in repository_source
    assert "fail_stale_claims" in repository_source
    assert "manual review required" in repository_source
    migration_source = (ROOT / "migrations/002_auction_finalization.sql").read_text(encoding="utf-8")
    assert "'publishing'::text" in migration_source
    assert "'finalizing'::text" in migration_source
    assert "'finalization_failed'::text" in migration_source
    assert "unsupported auction statuses" in migration_source


def test_database_logging_is_configured_only_by_entrypoint() -> None:
    database_source = (ROOT / "db/db.py").read_text(encoding="utf-8")
    assert "logger.addHandler" not in database_source
    assert "logging.basicConfig" not in database_source


def test_schema_migrations_are_versioned() -> None:
    assert (ROOT / "db/migrations.py").is_file()
    assert (ROOT / "migrations/001_uid_encryption_and_reminders.sql").is_file()
    assert (ROOT / "migrations/002_auction_finalization.sql").is_file()
    source = (ROOT / "db/db.py").read_text(encoding="utf-8")
    assert "await apply_migrations(pool)" in source


def test_subscription_keyboard_callbacks_match_handlers() -> None:
    source = (ROOT / "bot/handlers/admin/helper/new/card_economy.py").read_text(encoding="utf-8")
    assert 'callback_data=f"{CONF_CB_PREFIX}{sub_id}"' in source
    assert 'callback_data=f"{UNSUB_CB_PREFIX}{sub_id}"' in source
    assert 'callback_data="sc:ok_all"' in source
    assert '@router.callback_query(F.data == "sc:ok_all")' in source
    assert 'callback_data="sc:close"' in source
    assert "subs:confirm:" not in source
    assert "subs:confirm_all" not in source
    assert "subs:close" not in source


def test_repository_intervals_use_typed_make_interval() -> None:
    auctions_source = (ROOT / "bot/repositories/auctions.py").read_text(encoding="utf-8")
    uid_source = (ROOT / "bot/repositories/uid_verification.py").read_text(encoding="utf-8")

    assert "make_interval(mins => $1::int)" in auctions_source
    assert "make_interval(hours => $1::int)" in uid_source
    assert "make_interval(hours => ($1::int + 24))" in uid_source

    # asyncpg infers placeholder codecs from the SQL expression. Concatenating
    # `$1::text` with an interval suffix makes it expect a Python str, while
    # these repository APIs intentionally pass integers.
    assert "$1::text || ' minutes'" not in auctions_source
    assert "$1::text || ' hours'" not in uid_source
    assert "($1 + 24)::text" not in uid_source
