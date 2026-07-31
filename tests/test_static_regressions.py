from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_focused_profile_command_precedes_legacy_user_router() -> None:
    profile = (ROOT / "bot/handlers/profile.py").read_text(encoding="utf-8")
    bootstrap = (ROOT / "bot/bootstrap/routers.py").read_text(encoding="utf-8")

    assert profile.count('@router.message(Command("profile"), F.chat.type == "private")') == 1
    assert bootstrap.count("dispatcher.include_router(profile_router)") == 1
    assert bootstrap.index("dispatcher.include_router(profile_router)") < bootstrap.index(
        "dispatcher.include_router(users_router)"
    )


def test_debug_middleware_is_not_registered_unconditionally() -> None:
    source = (ROOT / "bot/bootstrap/routers.py").read_text(encoding="utf-8")
    assert source.count("dispatcher.message.outer_middleware(DebugAllMessages())") == 1
    assert "if debug_messages:" in source


def test_autobid_uses_domain_currency_policy() -> None:
    source = (ROOT / "bot/services/auction_autobids.py").read_text(encoding="utf-8")
    assert "step=auction.currency.autobid_step" in source
