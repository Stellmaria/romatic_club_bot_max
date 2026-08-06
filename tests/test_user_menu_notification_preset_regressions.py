from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from bot.handlers import user_menu
from bot.keyboards.keyboards import (
    USER_MENU_ADD_LOT,
    USER_MENU_EXCHANGE,
    USER_MENU_HOME,
    USER_MENU_LAYOUT,
)
from db import subscriptions


def _menu_labels() -> list[str]:
    return [label for row in USER_MENU_LAYOUT for label in row]


def test_user_menu_keeps_add_lot_but_hides_exchange_and_self_menu() -> None:
    labels = _menu_labels()
    assert USER_MENU_ADD_LOT in labels
    assert USER_MENU_EXCHANGE not in labels
    assert USER_MENU_HOME not in labels


@pytest.mark.asyncio
async def test_notification_screen_explains_every_toggle(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_settings(user_id: int) -> dict[str, bool]:
        assert user_id == 42
        return {}

    async def fake_is_subscribed(user_id: int) -> bool:
        assert user_id == 42
        return False

    async def fake_edit_or_answer(message, *, text, reply_markup) -> None:
        captured["message"] = message
        captured["text"] = text
        captured["reply_markup"] = reply_markup

    monkeypatch.setattr(user_menu, "get_settings", fake_get_settings)
    monkeypatch.setattr(user_menu, "is_subscribed", fake_is_subscribed)
    monkeypatch.setattr(user_menu, "_edit_or_answer", fake_edit_or_answer)

    marker = object()
    await user_menu.show_notifications_menu(marker, user_id=42)

    text = str(captured["text"])
    for expected in (
        "главный переключатель",
        "О начале аукциона",
        "За минуту до конца",
        "О завершении",
        "Анонс дня в 00:00",
        "✅ — включено, ❌ — выключено",
    ):
        assert expected in text
    assert "выключены" in text


@pytest.mark.asyncio
async def test_user_exchange_browser_never_reveals_offers() -> None:
    answers: list[str] = []

    class FakeMessage:
        async def answer(self, text: str, **kwargs) -> None:
            answers.append(text)

    await user_menu.show_exchange_browser(FakeMessage())
    assert answers == ["Просмотр предложений биржи доступен только администраторам."]


def test_notification_preferences_respect_global_delivery_switch() -> None:
    source = inspect.getsource(subscriptions.get_users_with_pref)
    assert "COALESCE(u.is_subscribed, TRUE) = TRUE" in source
    assert "COALESCE(u.pm_opened, FALSE) = TRUE" in source
    assert "uu.user_id IS NULL" in source


def test_support_cancel_has_user_menu_escape_for_staff() -> None:
    source = Path("bot/handlers/admin/admin_navigation.py").read_text(
        encoding="utf-8"
    )
    assert "current_state in _USER_APPEAL_STATES" in source
    assert "Обращение отменено." in source
    assert "build_user_main_keyboard()" in source


def test_deck_preset_migration_backfills_and_tracks_new_decks() -> None:
    migration = Path("db/migrations/022_deck_subscription_presets.sql").read_text(
        encoding="utf-8"
    )
    assert "'deck_all_' || d.id::text" in migration
    assert "ON CONFLICT (key) DO UPDATE" in migration
    assert "AFTER INSERT OR UPDATE OF name ON public.decks" in migration
    assert "sync_deck_subscription_preset" in migration
