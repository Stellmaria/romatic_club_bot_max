from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "bot" / "auction_notify.py").read_text(encoding="utf-8")


def test_auction_lifecycle_notifications_require_matching_subscription() -> None:
    source = _source()

    assert "user_ids = list(users_start & recipients)" in source
    assert "user_ids = list(users_1min & recipients)" in source
    assert "(users_end & recipients)" in source

    assert "users_start | recipients" not in source
    assert "users_1min | recipients" not in source
    assert "users_end | recipients" not in source


def test_auction_lifecycle_notifications_respect_global_opt_out() -> None:
    source = _source()

    assert "globally_enabled_users = set(await list_broadcast_targets())" in source
    assert (
        "return set(await get_users_with_pref(name)) & globally_enabled_users"
        in source
    )
    assert "if owner_id and owner_id in users_end:" in source
