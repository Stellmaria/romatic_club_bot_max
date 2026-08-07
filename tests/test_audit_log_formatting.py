from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bot.presentation.admin import format_admin_action_log
from bot.presentation.audit import (
    format_action_footer,
    format_audit_timestamp,
    format_bid_log,
    format_exchange_new_request_log,
)


def test_audit_timestamp_converts_explicit_instant_to_moscow() -> None:
    value = datetime(2026, 8, 7, 21, 1, 2, tzinfo=UTC)

    assert format_audit_timestamp(value) == "🕒 08.08.2026 00:01:02 (МСК)"  # noqa: RUF001


def test_action_footer_normalizes_legacy_transport_suffix() -> None:
    assert format_action_footer("exchange_reject через бота") == (
        "Действие: <code>exchange_reject</code> через бота."
    )


def test_exchange_submission_uses_human_labels_and_canonical_footer() -> None:
    text = format_exchange_new_request_log(
        batch_id=42,
        created_at_msk="08.08.2026 00:01:02",
        sender_username="alice",
        sender_id=1001,
        deck_id=22,
        deck_name="22 колода",
        mode="deck_split",
        items_count=3,
        price=120,
        currency="алмазы",
        has_proof=True,
        comment="готово",
    )

    assert "🧩 Режим: <b>Разбор колоды</b>" in text
    assert "deck_split" not in text
    assert "💰 Цена: <b>120 💎 алмазы</b>" in text
    assert "Действие: <code>exchange_add_request</code> через бота." in text


def test_bid_log_matches_audit_field_order() -> None:
    text = format_bid_log(
        auction_id=77,
        bidder_id=1002,
        bidder_username="bob",
        amount=250,
        currency="чашки",
        message_id=501,
    )

    lines = text.splitlines()
    assert lines[0] == "💬 <b>Новая ставка</b>"
    assert lines[1].startswith("🕒 ") and lines[1].endswith(" (МСК)")  # noqa: RUF001
    assert "🙍‍♂️ Участник:" in lines[2]
    assert "🎴 Лот №<code>77</code>" in lines[3]
    assert "💰 Ставка: <b>250 🍵 чай</b>" in lines[4]
    assert lines[-1] == "Действие: <code>place_bid</code> через бота."


def test_legacy_admin_formatter_is_wrapped_in_canonical_frame() -> None:
    text = format_admin_action_log(
        action="broadcast",
        admin={"id": 1, "username": "admin"},
        recipients=2,
        message_text="test",
    )

    lines = text.splitlines()
    assert lines[1].startswith("🕒 ") and lines[1].endswith(" (МСК)")  # noqa: RUF001
    assert lines[-1] == "Действие: <code>broadcast</code> через бота."


def test_bid_handler_does_not_bypass_admin_log_transport() -> None:
    source = Path("bot/handlers/auction/bidding.py").read_text(encoding="utf-8")

    assert "send_admin_log(" in source
    assert "send_message(\n                legacy_config.LOG_CHAT_ID" not in source


def test_winner_common_routes_audits_through_shared_transport() -> None:
    source = Path("bot/handlers/auction/winner_components/common.py").read_text(encoding="utf-8")

    assert "await send_admin_log(bot, payload)" in source
    assert "for chat_id in iter_admin_log_chats():" not in source
