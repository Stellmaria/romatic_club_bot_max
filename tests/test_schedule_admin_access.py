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
