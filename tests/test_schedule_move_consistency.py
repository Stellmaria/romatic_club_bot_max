from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_admin_panel_answers_callback_before_slow_side_effects() -> None:
    source = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")

    answer = source.index('await safe_callback_answer(call, "⏳ Переношу лот…")')
    reschedule = source.index("moderation_service.reschedule(", answer)
    success = source.index('"✅ <b>Лот перенесён</b>\\n"', reschedule)
    refresh = source.index("refresh_schedule_card_origin(", success)
    log = source.index("send_admin_log(call.bot, log_text)", refresh)

    assert answer < reschedule < success < refresh < log
    assert "timeout=12" in source
    assert "except asyncio.TimeoutError" in source
    assert "from bot.services.auction_workflows import AuctionModerationService" in source
    assert "AuctionSlotConflict, InvalidAuctionTransition" in source


def test_schedule_card_origin_is_remembered_and_refreshed() -> None:
    legacy = (ROOT / "bot/handlers/admin/admin_panel.py").read_text(encoding="utf-8")
    split = (ROOT / "bot/handlers/admin/admin_panel_schedule.py").read_text(encoding="utf-8")
    view = (ROOT / "bot/handlers/admin/schedule_card_view.py").read_text(encoding="utf-8")

    assert "await remember_schedule_card_origin(" in legacy
    assert "refresh_schedule_card_origin(" in legacy
    assert "await remember_schedule_card_origin(" in split
    assert "refresh_schedule_card_origin(" in split
    assert "await bot.edit_message_caption(" in view
    assert "await bot.edit_message_text(" in view
    assert "message is not modified" in view


def test_schedule_cards_render_database_time_in_moscow() -> None:
    view = (ROOT / "bot/handlers/admin/schedule_card_view.py").read_text(encoding="utf-8")

    assert 'start_time = to_moscow(lot["start_time"])' in view
    assert 'end_time = to_moscow(lot["end_time"])' in view
    assert "build_schedule_lot_caption(lot, owners_text)" in view
