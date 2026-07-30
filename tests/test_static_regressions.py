from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_single_profile_command_handler() -> None:
    source = (ROOT / "bot/handlers/users.py").read_text(encoding="utf-8")
    assert source.count('@router.message(Command("profile"), F.chat.type == "private")') == 1


def test_debug_middleware_is_not_registered_unconditionally() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert source.count("dp.message.outer_middleware(DebugAllMessages())") == 1
    assert 'if os.getenv("DEBUG_MW") == "1"' in source


def test_autobid_uses_domain_currency_policy() -> None:
    source = (ROOT / "bot/services/auction_autobids.py").read_text(encoding="utf-8")
    assert "step=auction.currency.autobid_step" in source
