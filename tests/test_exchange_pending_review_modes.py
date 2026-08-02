from bot.handlers.admin.presentation.exchange_pending_view import (
    pending_exchange_mode_kb,
    pending_exchange_navigation_kb,
)


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_pending_exchange_mode_keyboard_offers_both_modes() -> None:
    callbacks = _callbacks(pending_exchange_mode_kb())

    assert "expend_mode|one" in callbacks
    assert "expend_mode|all" in callbacks
    assert "admreq_back" in callbacks


def test_pending_exchange_navigation_pages_one_request_at_a_time() -> None:
    first = _callbacks(pending_exchange_navigation_kb(page=0, total=3))
    middle = _callbacks(pending_exchange_navigation_kb(page=1, total=3))
    last = _callbacks(pending_exchange_navigation_kb(page=2, total=3))

    assert "expend_page|-1" not in first
    assert "expend_page|1" in first
    assert "expend_page|0" in middle
    assert "expend_page|2" in middle
    assert "expend_page|3" not in last
    assert "expend_mode|all" in middle
    assert "admreq|pending|exchange" in middle
