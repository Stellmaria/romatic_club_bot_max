from __future__ import annotations

from types import SimpleNamespace

import pytest

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
    monkeypatch,
    sender_id: int,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        schedule_admin,
        "legacy_config",
        SimpleNamespace(ADMINS_OWNERS=(101,), ADMINS=(202,)),
    )
    event = SimpleNamespace(out=False, sender_id=sender_id)

    assert await schedule_admin._is_authorized(event) is expected


@pytest.mark.asyncio
async def test_outgoing_schedule_admin_command_is_authorized(monkeypatch) -> None:
    monkeypatch.setattr(
        schedule_admin,
        "legacy_config",
        SimpleNamespace(ADMINS_OWNERS=(), ADMINS=()),
    )

    assert await schedule_admin._is_authorized(SimpleNamespace(out=True, sender_id=None))
