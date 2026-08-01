from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import ReplyKeyboardRemove

from bot.handlers.admin import admin_menu
from bot.handlers.admin.moderation_schedule import split_message_by_blocks
from bot.handlers.admin.presentation.exchange_queue import (
    EX1_APPROVE,
    EX1_DELETE,
    EX1_DEL_NO,
    EX1_DEL_YES,
    EX1_REJECT,
    build_exchange_one_delete_confirmation,
    build_exchange_one_keyboard,
)


def _reply_texts(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.keyboard]


def _callback_data(markup) -> list[list[str | None]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_admin_root_menu_preserves_schedule_exchange_and_owner_section() -> None:
    regular = _reply_texts(
        admin_menu.build_admin_main_keyboard(include_system=False)
    )
    owner = _reply_texts(
        admin_menu.build_admin_main_keyboard(include_system=True)
    )

    assert regular == [
        ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
        ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
        ["📅 Расписание", "🛒 Биржа"],
    ]
    assert owner == [*regular, ["🖥 Система"]]


@pytest.mark.asyncio
async def test_admin_menu_delivery_removes_stale_keyboard_before_root_menu(
    monkeypatch,
) -> None:
    sentinel_keyboard = object()
    monkeypatch.setattr(
        admin_menu,
        "build_admin_main_keyboard",
        lambda *, user_id: sentinel_keyboard,
    )

    calls: list[tuple[str, object | None]] = []

    class FakeMessage:
        from_user = SimpleNamespace(id=42)

        async def answer(self, text: str, *, reply_markup=None) -> None:
            calls.append((text, reply_markup))

    await admin_menu.send_admin_main_menu(
        FakeMessage(),
        prefix="✅ Готово",
    )

    assert len(calls) == 2
    assert isinstance(calls[0][1], ReplyKeyboardRemove)
    assert calls[1][0].startswith("✅ Готово\n\n")
    assert calls[1][1] is sentinel_keyboard


def test_exchange_queue_keyboard_keeps_public_callback_contract() -> None:
    with_proof = _callback_data(
        build_exchange_one_keyboard(73, has_proof=True)
    )
    without_proof = _callback_data(
        build_exchange_one_keyboard(73, has_proof=False)
    )
    confirmation = _callback_data(
        build_exchange_one_delete_confirmation(73)
    )

    assert with_proof == [
        ["exchange_proof|73", "exchange_items|73"],
        [f"{EX1_APPROVE}|73", f"{EX1_REJECT}|73"],
        [f"{EX1_DELETE}|73"],
    ]
    assert without_proof[0] == ["exchange_items|73"]
    assert confirmation == [[f"{EX1_DEL_YES}|73", f"{EX1_DEL_NO}|73"]]


def test_schedule_text_split_preserves_block_boundaries() -> None:
    blocks = ["first\n", "second\n", "third\n"]
    assert split_message_by_blocks(blocks, chunk_size=13) == [
        "first\nsecond\n",
        "third\n",
    ]
