from __future__ import annotations

import random
import string
from datetime import date
from enum import Enum

import pytest
from aiogram import types

from bot.middlewares import telegram_boundary as middleware_module
from bot.middlewares.telegram_boundary import TelegramBoundaryMiddleware
from bot.telegram.boundary import (
    CallbackField,
    CallbackSchema,
    TelegramBoundaryError,
    render_html,
    trusted_html,
    validate_callback_payload,
    validate_date,
    validate_enum,
    validate_int,
    validate_text,
)


class Mode(str, Enum):
    CREATE = "create"
    DELETE = "delete"


def _callback(*, callback_id: str, data: str, user_id: int = 42) -> types.CallbackQuery:
    return types.CallbackQuery(
        id=callback_id,
        from_user=types.User(id=user_id, is_bot=False, first_name="Test"),
        chat_instance="test-chat",
        data=data,
    )


def test_versioned_callback_codec_round_trips_random_values() -> None:
    schema = CallbackSchema(
        namespace="auction",
        action="bid",
        fields=(
            CallbackField("auction_id", kind="int", minimum=1, maximum=10**9),
            CallbackField(
                "mode",
                kind="enum",
                values=frozenset({"normal", "auto"}),
            ),
        ),
    )
    rng = random.Random(260801)

    for _ in range(1000):
        auction_id = rng.randint(1, 10**9)
        mode = rng.choice(("normal", "auto"))
        payload = schema.pack(auction_id=auction_id, mode=mode)

        assert len(payload.encode("utf-8")) <= 64
        assert schema.unpack(payload) == {
            "auction_id": auction_id,
            "mode": mode,
        }


def test_callback_codec_rejects_fuzzed_malformed_payloads() -> None:
    schema = CallbackSchema(
        namespace="market",
        action="open",
        fields=(CallbackField("listing_id", kind="int", minimum=1),),
    )
    rng = random.Random(641337)
    alphabet = string.printable + "Лот💣"

    malformed = {"", "v1|market|open", "v1|market|open|0", "v2|market|open|1"}
    malformed.update(
        "".join(rng.choice(alphabet) for _ in range(rng.randint(65, 120)))
        for _ in range(100)
    )

    for payload in malformed:
        with pytest.raises(TelegramBoundaryError):
            schema.unpack(payload)


def test_renderer_escapes_user_text_and_keeps_explicit_trusted_fragments() -> None:
    rendered = render_html(
        trusted_html("<b>Пользователь:</b> "),
        '<script>alert("x")</script> & test',
    )

    assert rendered.startswith("<b>Пользователь:</b> ")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp; test" in rendered


def test_renderer_rejects_oversized_messages() -> None:
    with pytest.raises(TelegramBoundaryError, match="слишком длинное"):
        render_html("x" * 4097)


def test_shared_validators_reject_invalid_values_before_use_case() -> None:
    assert validate_int("12", minimum=1, maximum=20) == 12
    assert validate_enum("create", Mode) is Mode.CREATE
    assert validate_date("2026-08-01", minimum=date(2026, 1, 1)) == date(2026, 8, 1)
    assert validate_text("  карточка  ", maximum=20) == "карточка"

    with pytest.raises(TelegramBoundaryError):
        validate_int("1.2")
    with pytest.raises(TelegramBoundaryError):
        validate_enum("drop-table", Mode)
    with pytest.raises(TelegramBoundaryError):
        validate_date("01.08.2026")
    with pytest.raises(TelegramBoundaryError):
        validate_text("a\x00b")


def test_callback_payload_limit_is_measured_in_utf8_bytes() -> None:
    assert validate_callback_payload("я" * 32) == "я" * 32
    with pytest.raises(TelegramBoundaryError):
        validate_callback_payload("я" * 33)


@pytest.mark.asyncio
async def test_duplicate_delivery_is_not_dispatched_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[tuple[str | None, bool]] = []

    async def answer(callback, text=None, *, show_alert=False, **kwargs):
        answers.append((text, show_alert))
        return True

    monkeypatch.setattr(middleware_module, "safe_callback_answer", answer)
    middleware = TelegramBoundaryMiddleware(clock=lambda: 10.0)
    callback = _callback(callback_id="same", data="v1|auction|open|1")
    calls = 0

    async def handler(event, data):
        nonlocal calls
        calls += 1
        return "ok"

    assert await middleware(handler, callback, {}) == "ok"
    assert await middleware(handler, callback, {}) is None
    assert calls == 1
    assert answers == [("Запрос уже обработан.", False)]


@pytest.mark.asyncio
async def test_repeated_button_and_rate_limit_do_not_reach_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[str | None] = []
    now = [20.0]

    async def answer(callback, text=None, *, show_alert=False, **kwargs):
        answers.append(text)
        return True

    monkeypatch.setattr(middleware_module, "safe_callback_answer", answer)
    middleware = TelegramBoundaryMiddleware(
        rate_limit=2,
        rate_window_seconds=5,
        duplicate_window_seconds=1,
        clock=lambda: now[0],
    )
    calls = 0

    async def handler(event, data):
        nonlocal calls
        calls += 1

    await middleware(handler, _callback(callback_id="1", data="nav|open"), {})
    now[0] += 0.2
    await middleware(handler, _callback(callback_id="2", data="nav|open"), {})
    now[0] += 1.1
    await middleware(handler, _callback(callback_id="3", data="nav|first"), {})
    await middleware(handler, _callback(callback_id="4", data="nav|second"), {})
    await middleware(handler, _callback(callback_id="5", data="nav|third"), {})

    assert calls == 2
    assert "Кнопка уже нажата." in answers
    assert "Слишком много действий. Подождите немного." in answers


@pytest.mark.asyncio
async def test_malformed_and_boundary_errors_are_controlled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: list[str | None] = []

    async def answer(callback, text=None, *, show_alert=False, **kwargs):
        answers.append(text)
        return True

    monkeypatch.setattr(middleware_module, "safe_callback_answer", answer)
    middleware = TelegramBoundaryMiddleware(clock=lambda: 30.0)
    calls = 0

    async def handler(event, data):
        nonlocal calls
        calls += 1
        raise TelegramBoundaryError("Неверный идентификатор.")

    oversized = _callback(callback_id="bad", data="x" * 65)
    assert await middleware(handler, oversized, {}) is None
    assert calls == 0

    valid = _callback(callback_id="valid", data="v1|x|open")
    assert await middleware(handler, valid, {}) is None
    assert calls == 1
    assert answers == [
        "Данные кнопки превышают лимит Telegram.",
        "Неверный идентификатор.",
    ]
