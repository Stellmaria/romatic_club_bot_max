from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram.types import CallbackQuery, Chat, Message, User

from bot.handlers.admin.action_support import transport
from bot.handlers.auction import autobid


def _user(user_id: int, *, is_bot: bool = False) -> User:
    return User(id=user_id, is_bot=is_bot, first_name="Test")


def _message(user_id: int, text: str = "/danger") -> Message:
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=_user(user_id),
        text=text,
    )


async def test_former_secret_suffix_does_not_authorize_non_owner(
    monkeypatch,
    caplog,
) -> None:
    answers: list[str] = []
    calls: list[int] = []

    async def fake_answer(self: Message, text: str, *args, **kwargs) -> None:
        del self, args, kwargs
        answers.append(text)

    monkeypatch.setattr(Message, "answer", fake_answer)
    monkeypatch.setattr(transport, "is_owner_user", lambda user_id: user_id == 42)
    caplog.set_level(logging.WARNING, logger="auction_bot.security.access")

    @transport.owner_or_secret_required
    async def protected(message: Message) -> None:
        calls.append(message.from_user.id)

    await protected(_message(7, "/danger former-secret"))

    assert calls == []
    assert answers == ["Действие доступно только владельцу."]
    assert "owner_access_denied" in caplog.text
    assert "user_id=7" in caplog.text


async def test_owner_id_authorizes_and_is_audited(monkeypatch, caplog) -> None:
    calls: list[int] = []
    monkeypatch.setattr(transport, "is_owner_user", lambda user_id: user_id == 42)
    caplog.set_level(logging.INFO, logger="auction_bot.security.access")

    @transport.owner_required
    async def protected(message: Message) -> None:
        calls.append(message.from_user.id)

    await protected(_message(42))

    assert calls == [42]
    assert "owner_access_granted" in caplog.text
    assert "request_id=100:10" in caplog.text


async def test_callback_authorizes_the_clicking_user_not_message_sender(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(transport, "is_owner_user", lambda user_id: user_id == 42)

    callback = CallbackQuery(
        id="callback-request-id",
        from_user=_user(42),
        chat_instance="test-chat-instance",
        message=Message(
            message_id=11,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            from_user=_user(999, is_bot=True),
            text="system message",
        ),
    )

    @transport.owner_required
    async def protected(call: CallbackQuery) -> None:
        calls.append(call.from_user.id)

    await protected(callback)

    assert calls == [42]


def test_autobid_command_no_longer_accepts_a_password_argument() -> None:
    usage = autobid._usage().casefold()

    assert "password" not in usage
    assert "парол" not in usage
    assert not hasattr(autobid, "_password_is_valid")
