from __future__ import annotations

from db.user_list_queries import PageCursor, UserListPage


def test_cursor_callbacks_fit_telegram_limit_and_round_trip() -> None:
    from bot.handlers.admin import admin_user_lists as module

    cursors = {
        "users": PageCursor((str(9_223_372_036_854_775_807),)),
        "admins": PageCursor(("1", str(9_223_372_036_854_775_807))),
        "trusted": PageCursor(("a" * 32, str(9_223_372_036_854_775_807))),
    }

    for kind, cursor in cursors.items():
        payload = module._callback_data(kind, cursor)
        assert len(payload.encode("utf-8")) <= 64
        request = module._parse_request(payload)
        assert request is not None
        assert request.kind == kind
        assert request.cursor == cursor


def test_legacy_numbered_callback_resets_to_keyset_start() -> None:
    from bot.handlers.admin import admin_user_lists as module

    request = module._parse_request("admin_user_list|users|7")

    assert request is not None
    assert request.kind == "users"
    assert request.cursor is None
    assert request.legacy_reset is True


def test_rendered_page_exposes_cursor_navigation_without_total_materialization() -> None:
    from bot.handlers.admin import admin_user_lists as module

    page = UserListPage(
        rows=({"user_id": 1, "username": "alice", "is_luxury": False},),
        next_cursor=PageCursor(("1",)),
    )

    text, keyboard = module._render_page("users", page, None)

    assert "@alice" in text
    assert "SQL keyset pagination" in text
    callback_values = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert module._callback_data("users", PageCursor(("1",))) in callback_values
