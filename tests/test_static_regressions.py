from pathlib import Path

from bot.bootstrap.router_registry import MiddlewareScope
from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]


def test_focused_profile_command_precedes_legacy_user_router() -> None:
    profile = (ROOT / "bot/handlers/profile.py").read_text(encoding="utf-8")
    names = [feature.name for feature in get_router_registry().ordered_features]

    assert profile.count('@router.message(Command("profile"), F.chat.type == "private")') == 1
    assert names.count("users.profile") == 1
    assert names.index("users.profile") < names.index("users.core")


def test_debug_middleware_is_not_registered_unconditionally() -> None:
    middleware_feature = next(
        feature
        for feature in get_router_registry().ordered_features
        if feature.name == "system.middleware"
    )
    debug = next(
        middleware
        for middleware in middleware_feature.middlewares
        if middleware.name == "debug-all-messages"
    )

    assert debug.scope is MiddlewareScope.MESSAGE
    assert debug.factory.__name__ == "DebugAllMessages"
    assert debug.debug_only is True


def test_autobid_uses_domain_currency_policy() -> None:
    source = (ROOT / "bot/services/auction_autobids.py").read_text(encoding="utf-8")
    assert "step=auction.currency.autobid_step" in source
