from __future__ import annotations

import inspect

import pytest

from db import auction_id_stats, auction_mutations


@pytest.mark.asyncio
async def test_update_lot_field_rejects_unknown_column(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(query: str, *args: object) -> str:
        calls.append((query, args))
        return "UPDATE 1"

    monkeypatch.setattr(auction_mutations, "execute", fake_execute)

    with pytest.raises(ValueError, match="unsupported auction field"):
        await auction_mutations.update_lot_field(17, "status = 'finished' --", "ignored")

    assert calls == []


@pytest.mark.asyncio
async def test_update_lot_field_preserves_start_time_notification_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(query: str, *args: object) -> str:
        calls.append((query, args))
        return "UPDATE 1"

    monkeypatch.setattr(auction_mutations, "execute", fake_execute)

    await auction_mutations.update_lot_field(18, "start_time", "2026-08-01 20:00")

    assert len(calls) == 1
    query, args = calls[0]
    assert "notified_card_subs = FALSE" in query
    assert args == ("2026-08-01 20:00", 18)


def test_auction_id_reservation_is_transactional_and_race_bounded() -> None:
    source = inspect.getsource(auction_id_stats.reserve_first_missing_auction_id_for_stats)

    assert "connection.transaction()" in source
    assert "pg_advisory_xact_lock" in source
    assert "ON CONFLICT (auction_id) DO NOTHING" in source
    assert "RETURNING auction_id" in source
    assert "DELETE FROM public.auctions" not in source


def test_auction_id_gap_queries_are_bounded() -> None:
    source = inspect.getsource(auction_id_stats)

    assert "_MAX_RESULT_LIMIT = 200" in source
    assert "generate_series" in source
    assert "LIMIT $1" in source
