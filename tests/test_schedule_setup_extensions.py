from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.bootstrap.routers import get_router_registry
from db import schedule_setup_extensions

ROOT = Path(__file__).resolve().parents[1]


def test_temporary_emoji_uses_existing_placeholder_and_marks_it(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []
    upserts: list[tuple[int, int, int]] = []

    async def fake_fetchrow(query: str, *args: object):
        calls.append((query, args))
        return {"custom_emoji_id": 777}

    async def fake_execute(query: str, *args: object):
        calls.append((query, args))
        return "OK"

    async def fake_upsert_card(card_id: int, emoji_id: int, *, updated_by: int) -> None:
        upserts.append((card_id, emoji_id, updated_by))

    async def unused(*args: object, **kwargs: object) -> None:
        raise AssertionError("wrong upsert helper called")

    monkeypatch.setattr(schedule_setup_extensions, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(schedule_setup_extensions, "execute", fake_execute)
    placeholder = asyncio.run(
        schedule_setup_extensions.create_temporary_emoji(
            "card", "123", fallback="🎴", updated_by=42,
            upsert_asset=unused, upsert_deck=unused, upsert_card=fake_upsert_card,
        )
    )
    assert placeholder == 777
    assert upserts == [(123, 777, 42)]
    sql = "\n".join(query for query, _ in calls)
    assert "INSERT INTO public.schedule_temporary_emoji_marks" in sql
    assert any(args == ("card", "123", 777, "🎴", 42) for _, args in calls)


def test_ids_cannot_be_edited(monkeypatch) -> None:
    async def fake_execute(query: str, *args: object):
        raise AssertionError("execute must not be called")

    monkeypatch.setattr(schedule_setup_extensions, "execute", fake_execute)
    with pytest.raises(ValueError):
        asyncio.run(schedule_setup_extensions.update_schedule_card_field(1, "card_id", 2))
    with pytest.raises(ValueError):
        asyncio.run(schedule_setup_extensions.update_schedule_deck_field(1, "id", 2))


def test_restart_only_resets_review_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_execute(query: str, *args: object):
        captured["query"] = query
        captured["args"] = args
        return "OK"

    monkeypatch.setattr(schedule_setup_extensions, "execute", fake_execute)
    asyncio.run(schedule_setup_extensions.restart_schedule_card_reviews())
    query = str(captured["query"])
    assert "verified = false" in query
    assert "DELETE" not in query.upper()
    assert "custom_emoji_id" not in query
    assert captured["args"] == ()


def test_restart_can_be_limited_to_selected_deck(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_execute(query: str, *args: object):
        captured["query"] = query
        captured["args"] = args
        return "OK"

    monkeypatch.setattr(schedule_setup_extensions, "execute", fake_execute)
    asyncio.run(schedule_setup_extensions.restart_schedule_card_reviews(23))
    query = str(captured["query"])
    assert "verified = false" in query
    assert "FROM public.cards" in query
    assert "WHERE deck_id = $1" in query
    assert captured["args"] == (23,)


def test_selected_deck_scope_is_persisted_and_cleared(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(query: str, *args: object):
        calls.append((query, args))
        return "OK"

    async def fake_fetchrow(query: str, *args: object):
        calls.append((query, args))
        return {"deck_id": 23}

    monkeypatch.setattr(schedule_setup_extensions, "execute", fake_execute)
    monkeypatch.setattr(schedule_setup_extensions, "fetchrow", fake_fetchrow)
    asyncio.run(schedule_setup_extensions.set_schedule_deck_scope(42, 23))
    assert asyncio.run(schedule_setup_extensions.get_schedule_deck_scope(42)) == 23
    asyncio.run(schedule_setup_extensions.clear_schedule_deck_scope(42))
    sql = "\n".join(query for query, _ in calls)
    assert "INSERT INTO public.schedule_setup_deck_scopes" in sql
    assert "DELETE FROM public.schedule_setup_deck_scopes" in sql


def test_extension_modules_and_migration_contract() -> None:
    handlers = ROOT / "bot" / "handlers" / "admin"
    ui = (handlers / "schedule_setup_ui.py").read_text(encoding="utf-8")
    fields = (handlers / "schedule_setup_fields.py").read_text(encoding="utf-8")
    restart = (handlers / "schedule_setup_restart.py").read_text(encoding="utf-8")
    temp = (handlers / "schedule_setup_temp.py").read_text(encoding="utf-8")
    persistence = (ROOT / "db" / "schedule_setup_extensions.py").read_text(encoding="utf-8")
    migration = (ROOT / "db" / "migrations" / "013_schedule_temporary_emoji_marks.sql").read_text(encoding="utf-8")
    names = [feature.name for feature in get_router_registry().ordered_features]

    assert 'Command("schedule_setup_restart")' in restart
    assert 'Command("schedule_audit")' in restart
    assert "schsetup:restart:all" in restart
    assert "set_schedule_deck_scope" in restart
    assert 'Command("schedule_temp")' in temp
    assert "schtmpreplace:" in temp
    assert "schcard:fields:" in fields
    assert "update_schedule_card_field" in fields
    assert "get_schedule_deck_scope" in ui
    assert "_show_next_scoped_card" in ui
    assert "base._show_next_step = show_next" in ui
    assert "_CARD_FIELDS" in persistence and "_DECK_FIELDS" in persistence
    base_position = names.index("schedule.setup.base")
    for name in (
        "schedule.setup.fields",
        "schedule.setup.restart",
        "schedule.setup.temporary",
    ):
        assert names.index(name) < base_position
    assert "CREATE TABLE IF NOT EXISTS public.schedule_temporary_emoji_marks" in migration
    assert "PRIMARY KEY (scope, entity_key)" in migration
    assert "CREATE TABLE IF NOT EXISTS public.schedule_setup_deck_scopes" in migration
    assert "user_id bigint PRIMARY KEY" in migration
