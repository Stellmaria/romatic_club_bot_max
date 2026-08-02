from __future__ import annotations

import ast
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]


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


def test_day_parser_uses_relative_moscow_calendar_words() -> None:
    parser = _load_function(
        ROOT / "bot/handlers/users.py",
        "_parse_day_arg",
        {
            "date": date,
            "datetime": datetime,
            "timedelta": timedelta,
            "re": re,
            "_moscow_today": lambda: date(2026, 7, 15),
        },
    )
    today = date(2026, 7, 15)
    assert parser(None, today=today) == today
    assert parser("сегодня", today=today) == today
    assert parser("завтра", today=today) == date(2026, 7, 16)
    assert parser("послезавтра", today=today) == date(2026, 7, 17)
    assert parser("16.07", today=today) == date(2026, 7, 16)
    assert parser("2026-07-16", today=today) == date(2026, 7, 16)
    assert parser("не-дата", today=today) is None


def test_day_formatter_accepts_aware_and_legacy_naive_datetimes() -> None:
    moscow = ZoneInfo("Europe/Moscow")

    def ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=moscow)
        return value.astimezone(timezone.utc)

    async def short_card_line(lot: dict, finished: bool = False) -> str:
        return f"{lot['auction_id']}:{finished}"

    formatter = _load_function(
        ROOT / "bot/handlers/helper/helpers_users.py",
        "format_today_lots_fancy",
        {
            "date": date,
            "timedelta": timedelta,
            "ensure_utc": ensure_utc,
            "utc_now": lambda: datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc),
            "short_card_line": short_card_line,
        },
    )
    lots = [
        {"auction_id": 1, "start_time": datetime(2026, 7, 15, 19, 0, tzinfo=moscow), "end_time": datetime(2026, 7, 15, 19, 30, tzinfo=moscow)},
        {"auction_id": 2, "start_time": datetime(2026, 7, 15, 22, 0), "end_time": datetime(2026, 7, 15, 22, 30)},
        {"auction_id": 3, "start_time": datetime(2026, 7, 15, 23, 0, tzinfo=moscow), "end_time": datetime(2026, 7, 15, 23, 30, tzinfo=moscow)},
        {"auction_id": 4, "start_time": datetime(2026, 7, 15, 18, 0), "end_time": datetime(2026, 7, 15, 18, 30)},
    ]
    rendered = asyncio.run(formatter(date(2026, 7, 15), lots))
    assert "4:True" in rendered
    assert "1:True" in rendered
    assert rendered.index("4:True") < rendered.index("1:True")
    assert "2:False" in rendered
    assert "3:False" in rendered
    assert rendered.index("2:False") < rendered.index("3:False")


def test_day_query_is_pinned_to_moscow_calendar_date() -> None:
    source = (ROOT / "db/auctions.py").read_text(encoding="utf-8")
    start = source.index("async def get_auctions_by_date(")
    block = source[start : source.index("async def", start + 10)]
    assert "(a.start_time AT TIME ZONE 'Europe/Moscow')::date" in block
    assert "pg_typeof(a.start_time)::text = 'timestamp with time zone'" in block
    assert "WHERE DATE(a.start_time) = $1" not in block
    assert "'finished'" in block


def test_priority_command_routers_precede_stateful_auction_router() -> None:
    names = [feature.name for feature in get_router_registry().ordered_features]
    schedule = names.index("auctions.schedule")
    users = names.index("users.core")
    auctions = names.index("auctions.core")
    assert schedule < users < auctions
