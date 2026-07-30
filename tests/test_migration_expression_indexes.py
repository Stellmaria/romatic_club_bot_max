from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_initial_schema_handles_timestamp_and_timestamptz_date_indexes() -> None:
    sql = (ROOT / "db/migrations/002_initial_schema.sql").read_text(
        encoding="utf-8"
    )

    assert "a.atttypid = 'timestamp with time zone'::regtype" in sql
    assert "start_time AT TIME ZONE 'Europe/Moscow'" in sql
    assert "post_date_msk AT TIME ZONE 'Europe/Moscow'" in sql
    assert "to_regclass('public.idx_auctions_start_time_date')" in sql
    assert "to_regclass('public.idx_apb_post_day')" in sql


def test_database_module_does_not_install_its_own_log_handler() -> None:
    source = (ROOT / "db/db.py").read_text(encoding="utf-8")

    assert "logger.addHandler" not in source
    assert "logging.StreamHandler" not in source
