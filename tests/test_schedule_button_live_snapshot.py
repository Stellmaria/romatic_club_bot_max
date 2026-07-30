from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_schedule_button_query_uses_live_statuses_only() -> None:
    for relative in (
        "db/db.py",
        "db/auctions.py",
        "db/repositories/auctions.py",
    ):
        source = _source(relative)
        start = source.index("async def get_auctions_by_date_with_owners")
        end = source.index("\n\n@require_db_pool", start + 10)
        block = source[start:end]
        assert "'approved', 'scheduled', 'publishing', 'active'" in block
        assert "'finished'" not in block
        assert "LEFT JOIN LATERAL" in block
        assert "LIMIT 1" in block


def test_active_schedule_button_renders_fresh_moscow_snapshot() -> None:
    source = _source("bot/handlers/admin/moderation.py")
    start = source.index("async def preview_schedule_day")
    end = source.index("\ndef split_message_by_blocks", start)
    block = source[start:end]

    assert "await get_auctions_by_date_with_owners(selected_date)" in block
    assert "to_moscow_wall(utc_now())" in block
    assert "Актуальное расписание" in block
    assert "Обновлено:" in block
    assert "to_moscow_wall(lot['start_time'])" in block
    assert "to_moscow_wall(lot['end_time'])" in block


def test_schedule_slot_conflicts_include_all_live_states() -> None:
    source = _source("db/db.py")
    assert "status IN ('approved', 'scheduled', 'publishing', 'active')" in source
