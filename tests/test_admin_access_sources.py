import asyncio

from bot.security import admin_access


def test_env_admin_and_owner_bypass_database(monkeypatch) -> None:
    checked: list[int] = []

    async def fake_database_check(user_id: int) -> bool:
        checked.append(user_id)
        return False

    monkeypatch.setattr(admin_access, "ADMINS", [101])
    monkeypatch.setattr(admin_access, "ADMINS_OWNERS", [202])
    monkeypatch.setattr(admin_access, "_is_database_admin", fake_database_check)

    assert asyncio.run(admin_access.is_admin_user(101)) is True
    assert asyncio.run(admin_access.is_admin_user(202)) is True
    assert checked == []


def test_database_admin_is_authorized(monkeypatch) -> None:
    async def fake_database_check(user_id: int) -> bool:
        return user_id == 303

    monkeypatch.setattr(admin_access, "ADMINS", [])
    monkeypatch.setattr(admin_access, "ADMINS_OWNERS", [])
    monkeypatch.setattr(admin_access, "_is_database_admin", fake_database_check)

    assert asyncio.run(admin_access.is_admin_user(303)) is True
    assert asyncio.run(admin_access.is_admin_user(404)) is False
    assert asyncio.run(admin_access.is_admin_user(None)) is False
