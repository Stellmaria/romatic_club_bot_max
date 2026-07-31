from __future__ import annotations

import asyncio
from pathlib import Path

from bot.repositories.card_subscriptions import CardSubscriptionsRepository
from db.migrations import migration_files
from db.subscriptions import _deck_id_from_lot_title, preset_keys_for_auction


ROOT = Path(__file__).resolve().parents[1]


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class _Connection:
    def __init__(self):
        self.query = ""

    async def fetch(self, query, *arguments):
        self.query = query
        return [{"key": "deck_all_26", "title": "Вся колода 26 — Test"}]


def test_deck_presets_are_backfilled_and_kept_in_sync_by_migration() -> None:
    migration = (
        ROOT / "database/migrations/010_deck_notification_presets.sql"
    ).read_text(encoding="utf-8")

    assert "FROM public.decks" in migration
    assert "deck_all_" in migration
    assert "CREATE TRIGGER trg_sync_deck_notification_preset" in migration
    assert "AFTER INSERT OR UPDATE OF name ON public.decks" in migration
    assert "010_deck_notification_presets.sql" in {
        path.name for path in migration_files()
    }


def test_preset_catalog_hides_deck_presets_not_present_in_auction_catalog() -> None:
    async def scenario() -> None:
        connection = _Connection()
        repository = CardSubscriptionsRepository(_Pool(connection))  # type: ignore[arg-type]

        rows = await repository.list_presets()

        assert rows[0]["key"] == "deck_all_26"
        assert "public.decks" in connection.query
        assert "deck_all_" in connection.query

    asyncio.run(scenario())


def test_specific_card_matches_current_and_legacy_preset_keys() -> None:
    keys = preset_keys_for_auction(
        lot_title="Люцифер",
        card_id=146,
        rarity="золотая",
        deck_id=26,
        deck_name="Inferno",
    )

    assert "any_card" in keys
    assert "any_gold" in keys
    assert "rarity:gold" in keys
    assert "deck_all_26" in keys
    assert "deck:26" in keys
    assert "deck:inferno" in keys


def test_any_rarity_title_matches_any_card_and_rarity_presets() -> None:
    keys = preset_keys_for_auction(lot_title="Любая золотая")
    assert "any_card" in keys
    assert "any_gold" in keys
    assert "rarity:gold" in keys


def test_whole_deck_title_resolves_deck_and_any_deck_presets() -> None:
    assert _deck_id_from_lot_title("Вся колода №26") == 26
    keys = preset_keys_for_auction(lot_title="Вся колода №26")
    assert "any_deck" in keys
    assert "deck_all_26" in keys
    assert "deck:26" in keys
    assert "any_card" not in keys


def test_service_presets_are_derived_from_actual_auction_titles() -> None:
    assert "friends_plus" in preset_keys_for_auction(lot_title="Друзья+")
    assert "progress_slots" in preset_keys_for_auction(lot_title="Слоты прогресса")
    assert "spins_50" in preset_keys_for_auction(lot_title="Кручения (50 шт.)")
    assert "subscription_gold_3" in preset_keys_for_auction(
        lot_title="Золотой пропуск (3 месяца)"
    )
    assert "subscription_premium_12" in preset_keys_for_auction(
        lot_title="Премиум пропуск (12 месяцев)"
    )


def test_unified_matching_query_and_notifier_compatibility_are_present() -> None:
    matching = (ROOT / "db/subscriptions.py").read_text(encoding="utf-8")
    notifier = (ROOT / "bot/auction_notify.py").read_text(encoding="utf-8")

    assert "async def subscribers_for_auction_presets" in matching
    assert "keys = preset_keys_for_auction(" in matching
    # The legacy notifier composes the same dimensions through compatibility
    # exports while newer callers can use the unified query directly.
    assert "subscribers_for_lot_title" in notifier
    assert "subscribers_for_rarity" in notifier
    assert "subscribers_for_deck" in notifier
