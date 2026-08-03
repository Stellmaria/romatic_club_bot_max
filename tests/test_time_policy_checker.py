from __future__ import annotations

import ast

from scripts.check_time_policy import TimePolicyVisitor, new_violations


def _visit(source: str) -> TimePolicyVisitor:
    visitor = TimePolicyVisitor("bot/example.py")
    visitor.visit(ast.parse(source))
    return visitor


def test_checker_detects_aliased_direct_datetime_calls() -> None:
    visitor = _visit(
        "from datetime import datetime as dt, date as day\n"
        "def build():\n"
        "    return dt.now(), day.today()\n"
    )

    assert visitor.violations == {
        "bot/example.py::build::datetime.now": 1,
        "bot/example.py::build::date.today": 1,
    }


def test_checker_detects_legacy_timezone_imports() -> None:
    visitor = _visit("import pytz\nfrom dateutil import tz\n")

    assert visitor.legacy_timezone_imports == {
        "bot/example.py::pytz": 1,
        "bot/example.py::dateutil.tz": 1,
    }


def test_baseline_allows_removal_but_rejects_new_violation() -> None:
    baseline = {
        "direct_datetime_calls": {"bot/legacy.py::worker::datetime.now": 1},
        "legacy_timezone_imports": {},
    }
    reduced = {
        "direct_datetime_calls": {},
        "legacy_timezone_imports": {},
    }
    increased = {
        "direct_datetime_calls": {
            "bot/legacy.py::worker::datetime.now": 1,
            "bot/new.py::worker::datetime.now": 1,
        },
        "legacy_timezone_imports": {},
    }

    assert new_violations(reduced, baseline) == []
    assert new_violations(increased, baseline) == [
        "direct_datetime_calls: bot/new.py::worker::datetime.now (+1)"
    ]
