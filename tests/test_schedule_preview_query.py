from __future__ import annotations

import asyncio
from datetime import date

from db import schedule_setup


def test_schedule_preview_query_uses_resolved_card_id(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch(query: str, *args: object) -> list[object]:
        captured["query"] = query
        captured["args"] = args
        return []

    monkeypatch.setattr(schedule_setup, "fetch", fake_fetch)

    result = asyncio.run(schedule_setup.get_schedule_lots_for_day(date(2026, 8, 3)))

    assert result == []
    assert captured["args"] == (date(2026, 8, 3),)

    query = str(captured["query"])
    assert "COALESCE(a.card_id, c.card_id) AS resolved_card_id" in query
    assert "LEFT JOIN public.schedule_card_emojis ce ON ce.card_id = m.resolved_card_id" in query
    assert "SELECT a.*,\n                   c.card_id," not in query
    assert "candidate.card_id = a.card_id" in query
