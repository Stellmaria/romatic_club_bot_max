from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from bot.core.settings import UserbotSettings
from userbot.handlers import schedule_admin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender_id", "expected"),
    [
        (101, True),
        (202, True),
        (303, False),
    ],
)
async def test_schedule_admin_access_combines_owners_and_admins(
    sender_id: int,
    expected: bool,
) -> None:
    config = cast(
        UserbotSettings,
        SimpleNamespace(admin_owners=(101,), admins=(202,)),
    )
    event = SimpleNamespace(out=False, sender_id=sender_id)

    assert await schedule_admin._is_authorized(event, config) is expected


@pytest.mark.asyncio
async def test_outgoing_schedule_admin_command_is_authorized() -> None:
    config = cast(
        UserbotSettings,
        SimpleNamespace(admin_owners=(), admins=()),
    )

    assert await schedule_admin._is_authorized(
        SimpleNamespace(out=True, sender_id=None),
        config,
    )


@pytest.mark.asyncio
async def test_schedule_command_is_allowed_in_configured_admin_thread(monkeypatch) -> None:
    async def fake_target():
        return {"chat_id": -100123, "thread_id": 77}

    monkeypatch.setattr(schedule_admin, "get_schedule_review_target", fake_target)
    event = SimpleNamespace(
        is_private=False,
        chat_id=-100123,
        message=SimpleNamespace(
            reply_to=SimpleNamespace(
                reply_to_top_id=77,
                forum_topic=True,
                reply_to_msg_id=77,
            )
        ),
    )

    assert await schedule_admin._is_allowed_command_chat(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_id", "thread_id"),
    [(-100999, 77), (-100123, 88)],
)
async def test_schedule_command_is_rejected_outside_configured_admin_thread(
    monkeypatch,
    chat_id: int,
    thread_id: int,
) -> None:
    async def fake_target():
        return {"chat_id": -100123, "thread_id": 77}

    monkeypatch.setattr(schedule_admin, "get_schedule_review_target", fake_target)
    event = SimpleNamespace(
        is_private=False,
        chat_id=chat_id,
        message=SimpleNamespace(
            reply_to=SimpleNamespace(
                reply_to_top_id=thread_id,
                forum_topic=True,
                reply_to_msg_id=thread_id,
            )
        ),
    )

    assert not await schedule_admin._is_allowed_command_chat(event)
