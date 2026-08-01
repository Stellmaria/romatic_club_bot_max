from __future__ import annotations

from datetime import date, datetime

import pytest

from bot.use_cases.luxury_schedule import (
    LuxuryScheduleAccessDenied,
    LuxuryScheduleUseCase,
)


def make_use_case(*, allowed: bool = True, lots: list[dict] | None = None) -> LuxuryScheduleUseCase:
    async def is_luxury_user(_user_id: int) -> bool:
        return allowed

    async def get_all_decks() -> list[dict]:
        return [{"deck_id": 7, "deck_name": "Тестовая"}]

    async def get_last_nonempty_deck_id() -> int:
        return 12

    async def get_lots(_selected: date) -> list[dict]:
        return list(lots or [])

    async def get_cards_meta(_card_ids: list[int]) -> dict[int, dict]:
        return {
            100: {
                "hero_name": "Героиня",
                "deck_id": 7,
                "rarity": "gold",
                "gifts_cups": 15,
                "gifts_diamonds": 3,
            }
        }

    async def get_max_obtain_for_rarity(_rarity: str) -> dict:
        return {"cups": 20, "diamonds": 4}

    async def get_obtain_variants_for_rarity(_rarity: str) -> list[dict]:
        return []

    async def get_deck_treasure_sum(_deck_id: int) -> int:
        return 99

    async def get_deck_obtain_totals(_deck_id: int) -> dict:
        return {"cups": 30, "diamonds": 6}

    return LuxuryScheduleUseCase(
        is_luxury_user=is_luxury_user,
        get_all_decks=get_all_decks,
        get_last_nonempty_deck_id=get_last_nonempty_deck_id,
        get_lots=get_lots,
        get_cards_meta=get_cards_meta,
        get_max_obtain_for_rarity=get_max_obtain_for_rarity,
        get_obtain_variants_for_rarity=get_obtain_variants_for_rarity,
        get_deck_treasure_sum=get_deck_treasure_sum,
        get_deck_obtain_totals=get_deck_obtain_totals,
    )


@pytest.mark.asyncio
async def test_denies_non_luxury_user_before_loading_schedule() -> None:
    use_case = make_use_case(allowed=False)

    with pytest.raises(LuxuryScheduleAccessDenied):
        await use_case.execute(user_id=1, selected_date=date(2026, 8, 2))


@pytest.mark.asyncio
async def test_returns_explicit_empty_schedule_view() -> None:
    view = await make_use_case().execute(user_id=1, selected_date=date(2026, 8, 2))

    assert view.has_lots is False
    assert view.messages == ("На 02.08.2026 лотов нет.",)


@pytest.mark.asyncio
async def test_renders_enriched_card_without_telegram_objects() -> None:
    lot = {
        "card_id": 100,
        "card_name": "Карта <редкая>",
        "start_time": datetime(2026, 8, 2, 10, 0),
        "end_time": datetime(2026, 8, 2, 10, 30),
        "start_price": 50,
        "currency": "diamonds",
    }

    view = await make_use_case(lots=[lot]).execute(
        user_id=1,
        selected_date=date(2026, 8, 2),
    )

    rendered = "\n".join(view.messages)
    assert view.has_lots is True
    assert "Аукционы на 02.08.2026" in rendered
    assert "10:00–10:30" in rendered
    assert "Карта &lt;редкая&gt;" in rendered
    assert "(Героиня)" in rendered
    assert "💎3" in rendered
    assert "☕15" in rendered
    assert "Колода 7 — Тестовая" in rendered
    assert "50 💎" in rendered
