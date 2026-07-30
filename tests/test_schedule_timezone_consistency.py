from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.core.time import schedule_slot_key, to_moscow_wall

ROOT = Path(__file__).resolve().parents[1]
MOSCOW = ZoneInfo("Europe/Moscow")


def _load_function(path: Path, name: str, namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def test_aware_database_time_is_rendered_on_moscow_schedule_grid() -> None:
    stored_utc = datetime(2026, 7, 16, 19, 30, tzinfo=timezone.utc)
    assert to_moscow_wall(stored_utc) == datetime(2026, 7, 16, 22, 30)
    assert schedule_slot_key(stored_utc) == datetime(2026, 7, 16, 22, 30)


def test_legacy_naive_moscow_time_stays_on_same_schedule_slot() -> None:
    legacy = datetime(2026, 7, 16, 22, 30)
    assert to_moscow_wall(legacy) == legacy
    assert schedule_slot_key(legacy) == legacy


def test_free_slot_calculation_blocks_aware_utc_row_at_its_moscow_time() -> None:
    owners = {
        100: [{"user_id": 7, "username": "owner"}],
        200: [{"user_id": 7, "username": "owner"}],
    }

    async def get_lot_owners(auction_id: int):
        return owners[auction_id]

    def all_slots(selected_date: date):
        return [
            datetime.combine(selected_date, datetime.min.time()).replace(hour=22),
            datetime.combine(selected_date, datetime.min.time()).replace(hour=22, minute=30),
        ]

    find_free_slots = _load_function(
        ROOT / "bot/handlers/admin/helper/user_helpers.py",
        "find_free_slots",
        {
            "get_lot_owners": get_lot_owners,
            "all_30min_slots_for_date": all_slots,
            "schedule_slot_key": schedule_slot_key,
        },
    )

    auctions = [
        {
            "auction_id": 200,
            "card_name": "Любая золотая",
            "start_time": datetime(2026, 7, 16, 19, 30, tzinfo=timezone.utc),
            "end_time": datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc),
        }
    ]
    result = asyncio.run(
        find_free_slots(
            auctions,
            {"card_name": "Любая золотая"},
            100,
            date(2026, 7, 16),
        )
    )

    assert datetime(2026, 7, 16, 22, 0) in result
    assert datetime(2026, 7, 16, 22, 30) not in result


def test_edit_schedule_lines_use_same_moscow_time_as_day_announcement() -> None:
    async def get_lot_owners(_auction_id: int):
        return [{"user_id": 7, "username": "owner"}]

    formatter = _load_function(
        ROOT / "bot/handlers/admin/helper/user_helpers.py",
        "build_grouped_schedule_lines_with_prefixes",
        {
            "defaultdict": __import__("collections").defaultdict,
            "get_lot_owners": get_lot_owners,
            "to_moscow_wall": to_moscow_wall,
        },
    )

    lines = asyncio.run(
        formatter(
            [
                {
                    "auction_id": 8834,
                    "card_name": "Любая золотая",
                    "start_time": datetime(2026, 7, 16, 19, 30, tzinfo=timezone.utc),
                    "end_time": datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc),
                }
            ],
            {"card_name": "Другая карта"},
            [99],
        )
    )
    assert any("22:30–23:00" in line for line in lines)
    assert all("19:30–20:00" not in line for line in lines)


def test_live_admin_schedule_paths_do_not_render_raw_database_clock() -> None:
    moderation = (ROOT / "bot/handlers/admin/moderation.py").read_text(encoding="utf-8")
    helpers = (ROOT / "bot/handlers/admin/helper/user_helpers.py").read_text(encoding="utf-8")
    admin_panel = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")

    assert "schedule_slot_key(a['start_time']) == selected_grid_time" in moderation
    assert "start_msk = to_moscow_wall(lot['start_time'])" in moderation
    assert "busy_starts.add(schedule_slot_key(start))" in helpers
    assert "start_msk = to_moscow_wall(lot[\"start_time\"])" in admin_panel
