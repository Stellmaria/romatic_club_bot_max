from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_users_menu_buttons_have_handlers() -> None:
    sections = _source("bot/handlers/admin/admin_panel_sections.py")
    lists = _source("bot/handlers/admin/admin_user_lists.py")

    for label in (
        "👤 Список админов",
        "👥 Список пользователей",
        "🤝 Список доверенных",
    ):
        assert label in sections
        assert f'F.text == "{label}"' in lists


def test_user_lists_are_paginated_and_closeable() -> None:
    lists = _source("bot/handlers/admin/admin_user_lists.py")

    assert "_PAGE_SIZE = 20" in lists
    assert 'F.data.startswith(f"{_LIST_CALLBACK}|")' in lists
    assert "page - 1" in lists
    assert "page + 1" in lists
    assert 'kind == "close"' in lists
    assert "message.edit_text(" in lists


def test_admin_list_combines_database_admins_and_configured_owners() -> None:
    lists = _source("bot/handlers/admin/admin_user_lists.py")

    assert "await list_admins()" in lists
    assert "await get_all_users()" in lists
    assert "for raw_owner_id in legacy_config.ADMINS_OWNERS:" in lists
    assert 'current["is_owner"] = True' in lists


def test_admin_panel_registers_user_list_router() -> None:
    facade = _source("bot/handlers/admin/admin_panel.py")

    assert "admin_user_lists," in facade
    assert "admin_user_lists.router," in facade
    assert "*admin_user_lists.__all__" in facade
