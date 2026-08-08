from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_mini_app_navigation_and_theme_controls_are_interactive() -> None:
    html = _source("webapp/index.html")
    javascript = _source("webapp/static/main.js")

    for view in ("auction", "my-lots", "submit", "subscriptions", "profile"):
        assert f'data-view="{view}"' in html
        assert f'id="{view}-view"' in html

    assert 'id="theme-toggle"' in html
    assert 'id="profile-theme-toggle"' in html
    assert "localStorage" in javascript
    assert 'telegram?.onEvent?.("themeChanged"' in javascript
    assert 'document.documentElement.dataset.theme' in javascript
    assert 'class="nav-item" type="button" disabled' not in html


def test_mini_app_is_compact_and_respects_safe_areas() -> None:
    css = _source("webapp/static/styles.css")

    assert "width: min(100%, 480px);" in css
    assert "100dvh" in css
    assert "env(safe-area-inset-bottom)" in css
    assert 'html[data-theme="light"]' in css


def test_mini_app_does_not_render_seller_identity() -> None:
    javascript = _source("webapp/static/main.js")

    assert "seller.display_name" not in javascript
    assert "Продавец: скрыт" in javascript


def test_mini_app_api_requests_have_a_timeout() -> None:
    api = _source("webapp/static/api.js")

    assert "AbortController" in api
    assert "request_timeout" in api
    assert "signal: controller.signal" in api
