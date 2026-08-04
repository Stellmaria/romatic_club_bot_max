from __future__ import annotations

import hashlib
import re
from tempfile import TemporaryDirectory
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database"
ARCHIVED_MIGRATIONS = DATABASE / "migrations"
RUNTIME_MIGRATIONS = ROOT / "db" / "migrations"


def test_runtime_migrations_are_importable_for_wheel_deployments() -> None:
    import db
    from db.migrator import MIGRATIONS_DIR, _load_migrations

    package_dir = Path(db.__file__).resolve().parent
    assert MIGRATIONS_DIR == package_dir / "migrations"
    names = {migration.filename for migration in _load_migrations()}
    assert {
        "001_extensions_and_types.sql",
        "011_schedule_setup_master.sql",
    } <= names


def test_migration_discovery_fails_closed_for_missing_resources() -> None:
    from db.migrator import _load_migrations

    with TemporaryDirectory() as directory:
        try:
            _load_migrations(Path(directory))
        except RuntimeError as error:
            assert "No SQL migrations found" in str(error)
        else:  # pragma: no cover
            raise AssertionError("an empty migration package was accepted")


IMMUTABLE_MIGRATION_HASHES = {
    "001_uid_encryption_and_reminders.sql": (
        "8ad7b4ffa56b44ecf7ba76a8a726988e70c87ceca2b01a9a8bb1d8fe42f5bb42"
    ),
    "002_auction_finalization.sql": (
        "4699fcd079a162d46a0e1778a8d9e9a302a7137c3656d501d17f6ab1f06b4f43"
    ),
    "003_auction_bid_integrity.sql": (
        "2966a66a04a77ac3b54c71b69f3e2a34810929c2c3196ff074307a956649d6f2"
    ),
    "004_auction_workflows.sql": (
        "00c5e1b84ea42f8eddf2466332531c98c3b1ec4f3c8a02ad2362039d51e45765"
    ),
    "005_transactional_outbox_and_utc.sql": (
        "c04c5c9d37253638ed33115a3448deaded14b82c9be883651c67a954a6a6665c"
    ),
    "006_outbox_delivery_control.sql": (
        "a315cff67fbe593969bcca7e680e39a28554672b64cda464beb2fd3edc77ddcc"
    ),
}


def _sql(name: str) -> str:
    return (DATABASE / name).read_text(encoding="utf-8")


def _strip_line_comments(sql: str) -> str:
    return re.sub(r"(?m)--[^\n]*$", "", sql)


def _create_table_blocks(sql: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"^create\s+table(?:\s+if\s+not\s+exists)?\s+"
        r"(?:public\.)?([a-z_]\w*)\s*\(",
        re.IGNORECASE | re.MULTILINE,
    )
    blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(sql):
        depth = 1
        index = match.end()
        start = index
        in_quote = False
        while index < len(sql) and depth:
            char = sql[index]
            if char == "'":
                if in_quote and index + 1 < len(sql) and sql[index + 1] == "'":
                    index += 2
                    continue
                in_quote = not in_quote
            elif not in_quote:
                depth += int(char == "(") - int(char == ")")
            index += 1
        assert depth == 0, f"unclosed CREATE TABLE for {match.group(1)}"
        blocks.append((match.group(1).lower(), sql[start : index - 1]))
    return blocks


def _split_top_level_csv(body: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if in_quote and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif not in_quote:
            depth += int(char == "(") - int(char == ")")
            if char == "," and depth == 0:
                parts.append(body[start:index])
                start = index + 1
        index += 1
    parts.append(body[start:])
    return parts


def _table_columns(sql: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    non_columns = {
        "check",
        "constraint",
        "exclude",
        "foreign",
        "like",
        "primary",
        "unique",
    }
    for table, body in _create_table_blocks(sql):
        assert table not in result, f"duplicate CREATE TABLE for {table}"
        columns: set[str] = set()
        for item in _split_top_level_csv(body):
            normalized = " ".join(item.split())
            if not normalized:
                continue
            first = normalized.split()[0].strip('"').lower()
            if first not in non_columns:
                columns.add(first)
        result[table] = columns
    return result


def _named_constraints(sql: str) -> set[str]:
    return {
        name.lower()
        for name in re.findall(r"(?i)\bconstraint\s+([a-z_]\w*)", sql)
    }


def _index_names(sql: str) -> set[str]:
    return {
        name.lower()
        for name in re.findall(
            r"(?im)^create\s+(?:unique\s+)?index"
            r"(?:\s+if\s+not\s+exists)?\s+([a-z_]\w*)",
            sql,
        )
    }


def test_schema_snapshots_are_ddl_only() -> None:
    for name in ("pgadmin_schema.sql", "reference_schema.sql"):
        sql = _strip_line_comments(_sql(name))
        assert not re.search(r"(?im)^\s*(?:insert|copy)\b", sql), name


def test_pgadmin_snapshot_is_the_newer_table_inventory() -> None:
    current = _table_columns(_sql("pgadmin_schema.sql"))
    earlier = _table_columns(_sql("reference_schema.sql"))

    assert len(current) == 49
    assert len(earlier) == 45
    assert set(current) - set(earlier) == {
        "bid_duplicate_archive",
        "schema_migrations",
        "telegram_outbox",
        "uid_verification_request_reminders",
    }
    assert not set(earlier) - set(current)


def test_bootstrap_matches_every_current_table_and_column() -> None:
    current = _table_columns(_sql("pgadmin_schema.sql"))
    bootstrap = _table_columns(_sql("bootstrap.sql"))
    assert bootstrap == current


def test_bootstrap_contains_enum_and_extension_dependencies() -> None:
    bootstrap = _sql("bootstrap.sql").lower()
    assert "create extension if not exists pg_trgm" in bootstrap
    expected = {
        "obtain_type": ("diamonds", "tea"),
        "deck_type": ("resource", "roulette"),
        "market_currency": ("cups", "diamonds", "treasures", "cash"),
        "listing_status": ("active", "hidden", "sold", "archived", "deleted"),
        "offer_kind": (
            "cards",
            "cups",
            "diamonds",
            "treasures",
            "whole_deck",
            "service",
        ),
    }
    for enum_name, labels in expected.items():
        assert f"create type public.{enum_name} as enum" in bootstrap
        for label in labels:
            assert f"''{label}''" in bootstrap


def test_bootstrap_restores_supplementary_snapshot_objects() -> None:
    bootstrap = _sql("bootstrap.sql").lower()
    required_functions = {
        "auctions_fix_end_time",
        "is_valid_bid",
        "list_missing_ids",
        "norm_hero",
        "norm_username",
        "prevent_currency_change_if_bids",
        "prevent_time_change_if_bids",
        "set_updated_at",
        "touch_market_listing",
        "touch_updated_at",
        "trg_set_updated_at",
        "uid_verif_sync_cols",
    }
    for function in required_functions:
        assert f"function public.{function}" in bootstrap
    assert "view public.v_user_uid_status" in bootstrap


def test_bootstrap_preserves_named_constraints_and_current_indexes() -> None:
    bootstrap = _sql("bootstrap.sql")
    current_constraints = _named_constraints(_sql("pgadmin_schema.sql"))
    historical_constraints = _named_constraints(_sql("reference_schema.sql"))
    deliberately_removed = {
        "auctions_end_eq_start_plus_31",
        "auctions_end_eq_start_plus_31_window",
    }
    assert current_constraints <= _named_constraints(bootstrap)
    assert historical_constraints - deliberately_removed <= _named_constraints(bootstrap)

    historical_indexes = _index_names(_sql("reference_schema.sql"))
    assert historical_indexes - {"ux_exchange_items_batch_card"} <= _index_names(bootstrap)
    for index in (
        "ix_auctions_due_finalization",
        "ix_auctions_publication_queue",
        "ix_bids_auction_winner_order",
        "ix_telegram_outbox_failed_review",
        "ux_bids_discussion_message_id",
        "ux_exchange_batches_posted_message",
    ):
        assert index in _index_names(bootstrap)


def test_current_sql_contract_columns_are_materialized() -> None:
    columns = _table_columns(_sql("bootstrap.sql"))
    required = {
        "auction_manual_results": {"moderator_comment"},
        "auctions": {
            "finalization_attempts",
            "finalization_error",
            "publication_attempts",
            "publication_next_attempt_at",
        },
        "exchange_batches": {
            "publication_error",
            "publication_finished_at",
            "publication_started_at",
        },
        "telegram_outbox": {
            "delivery_state",
            "review_note",
            "reviewed_at",
            "reviewed_by",
            "topic",
        },
        "uid_bans": {"uid_enc", "uid_hash", "uid_last4"},
        "uid_verification_requests": {"uid_enc", "uid_hash", "uid_last4"},
        "user_uids": {"uid_enc", "uid_hash", "uid_last4"},
    }
    for table, expected_columns in required.items():
        assert expected_columns <= columns[table]


def test_historical_migrations_remain_byte_for_byte_immutable() -> None:
    for name, expected in IMMUTABLE_MIGRATION_HASHES.items():
        digest = hashlib.sha256((ARCHIVED_MIGRATIONS / name).read_bytes()).hexdigest()
        assert digest == expected, name


def test_alignment_migration_is_additive_and_repeat_safe() -> None:
    migration = (ARCHIVED_MIGRATIONS / "007_schema_alignment.sql").read_text(encoding="utf-8")
    executable = _strip_line_comments(migration).lower()

    assert "add column if not exists moderator_comment" in executable
    assert "to_regprocedure(" in executable
    assert "not exists (" in executable
    assert "create or replace function" not in executable
    assert not re.search(r"(?im)^\s*(?:drop|update|delete|insert|copy)\b", executable)
    assert not re.search(r"(?i)alter\s+type\b", executable)

    for trigger in (
        "trg_no_currency_flip",
        "trg_user_appeals_touch",
        "trg_market_listings_touch",
        "trg_uid_verif_sync_cols",
    ):
        assert f"create trigger {trigger}" in executable
    assert "create trigger trg_auctions_fix_end_time" not in executable
    assert "create trigger trg_prevent_time_change" not in executable


def test_bootstrap_is_single_transaction_without_legacy_alternative() -> None:
    bootstrap = _sql("bootstrap.sql")
    executable = _strip_line_comments(bootstrap)
    assert "-- OR" not in bootstrap
    assert len(_create_table_blocks(bootstrap)) == 49
    assert len(re.findall(r"(?im)^\s*begin\s*;", executable)) == 1
    assert len(re.findall(r"(?im)^\s*commit\s*;", executable)) == 1
    assert "create trigger trg_auctions_fix_end_time" not in executable.lower()
    assert "create trigger trg_prevent_time_change" not in executable.lower()


def test_sql_dollar_quote_tags_are_balanced() -> None:
    paths = [
        DATABASE / "bootstrap.sql",
        *sorted(ARCHIVED_MIGRATIONS.glob("*.sql")),
        *sorted(RUNTIME_MIGRATIONS.glob("*.sql")),
    ]
    for path in paths:
        sql = path.read_text(encoding="utf-8")
        tags = re.findall(r"\$[a-z_]*\$", sql, flags=re.IGNORECASE)
        for tag in set(tags):
            assert tags.count(tag) % 2 == 0, f"unbalanced {tag} in {path.name}"
