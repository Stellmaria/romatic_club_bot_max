from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_legacy_main_registers_expired_callback_guard_and_drops_backlog() -> None:
    source = _source("main.py")

    assert "from bot.middlewares.expired_callback import ExpiredCallbackMiddleware" in source
    assert "dp.update.outer_middleware(ExpiredCallbackMiddleware())" in source
    assert 'os.getenv("DROP_PENDING_UPDATES", "1")' in source
    assert "await bot.delete_webhook(drop_pending_updates=drop_pending_updates)" in source
    assert source.index("await bot.delete_webhook") < source.index("# Фоновые задачи")


def test_edit_lot_menu_uses_safe_callback_answer() -> None:
    source = _source("bot/handlers/admin/admin_panel.py")
    start = source.index("async def edit_lot_menu")
    end = source.index("@router.callback_query", start)
    handler = source[start:end]

    assert "await safe_callback_answer(call)" in handler
    assert "await call.answer()" not in handler


def test_admin_guard_uses_safe_callback_answer() -> None:
    source = _source("bot/handlers/admin/helper/new/wrapper.py")
    assert "from bot.telegram.callbacks import safe_callback_answer" in source
    assert "await safe_callback_answer(message_or_call, NO_ACCESS_MSG, show_alert=True)" in source


def test_expired_callback_filter_remains_narrow() -> None:
    callback_utils = _source("bot/telegram/callbacks.py")
    middleware = _source("bot/middlewares/expired_callback.py")

    assert '"query is too old"' in callback_utils
    assert '"response timeout expired"' in callback_utils
    assert '"query id is invalid"' in callback_utils
    assert "if is_expired_callback_error(error):" in callback_utils
    assert "if not is_expired_callback_error(error):" in middleware
    assert "raise" in middleware
