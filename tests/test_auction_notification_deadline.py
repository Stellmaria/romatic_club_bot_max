from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from bot.services.auction_notifications import canonicalize_notification_deadlines


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_notification_loop_receives_displayed_close_instant() -> None:
    stored_end_time = datetime(2026, 8, 2, 18, 30, 59)

    rows = canonicalize_notification_deadlines(
        [{"auction_id": 7, "end_time": stored_end_time}]
    )

    assert rows[0]["end_time"] == datetime(2026, 8, 2, 18, 30)
    assert stored_end_time - rows[0]["end_time"] == timedelta(seconds=59)


def test_notification_worker_uses_deadline_adapter() -> None:
    source = (ROOT / "bot" / "bootstrap" / "workers.py").read_text(encoding="utf-8")

    assert "from bot.services.auction_notifications import auction_notifications_loop" in source
    assert "from bot.auction_notify import (\n    auction_notifications_loop," not in source
