from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_schedule_button_query_uses_live_statuses_only() -> None:
    source = _source("db/repositories/auctions.py")
    start = source.index("async def get_auctions_by_date_with_owners")
    end = source.index("\n@require_db_pool", start + 10)
    block = source[start:end]

    assert "'approved', 'scheduled', 'publishing', 'active'" in block
    assert "'finished'" not in block
    assert "LEFT JOIN LATERAL" in block
    assert "LIMIT 1" in block


def test_active_schedule_button_renders_fresh_moscow_snapshot() -> None:
    source = _source("bot/handlers/admin/moderation_schedule.py")
    start = source.index("async def preview_schedule_day")
    end = source.index("\n@router.message", start)
    block = source[start:end]

    assert "await get_auctions_by_date_with_owners(selected_date)" in block
    assert "to_moscow_wall(utc_now())" in block
    assert "Актуальное расписание" in block
    assert "Обновлено:" in block
    assert "to_moscow(lot['start_time'])" in block
    assert "to_moscow(lot['end_time'])" in block


def test_schedule_slot_conflicts_include_all_live_states() -> None:
    source = _source("bot/repositories/auction_workflows.py")
    start = source.index("async def _has_prohibited_slot_overlap")
    end = source.index("\n    async def create_pending", start)
    block = source[start:end]

    for status in ("approved", "scheduled", "publishing", "active"):
        assert f"'{status}'" in block
    assert "'finished'" not in block
