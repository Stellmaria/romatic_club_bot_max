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


def test_user_lists_use_keyset_navigation_and_remain_closeable() -> None:
    lists = _source("bot/handlers/admin/admin_user_lists.py")

    assert "_PAGE_SIZE = 20" in lists
    assert 'F.data.startswith(f"{_LIST_CALLBACK}|")' in lists
    assert "PageCursor" in lists
    assert "next_cursor" in lists
    assert 'text="⏮ В начало"' in lists
    assert 'text="Далее ➡️"' in lists
    assert 'request.kind == "close"' in lists
    assert "message.edit_text(" in lists
    assert "page - 1" not in lists
    assert "page + 1" not in lists


def test_admin_list_is_one_bounded_sql_page_with_configured_owners() -> None:
    lists = _source("bot/handlers/admin/admin_user_lists.py")
    queries = _source("db/user_list_queries.py")

    assert "list_admins_page(" in lists
    assert "_owner_ids()" in lists
    assert "legacy_config.ADMINS_OWNERS" in lists
    assert "WITH owner_ids AS" in queries
    assert "LEFT JOIN public.users u USING (user_id)" in queries
    assert "LIMIT $4" in queries
    assert "await list_admins()" not in lists
    assert "await get_all_users()" not in lists


def test_admin_panel_registers_user_list_router() -> None:
    facade = _source("bot/handlers/admin/admin_panel.py")

    assert "admin_user_lists," in facade
    assert "admin_user_lists.router," in facade
    assert "*admin_user_lists.__all__" in facade
