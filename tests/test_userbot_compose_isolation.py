from pathlib import Path


def test_compose_separates_userbot_secrets_and_session_volume() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    assert "${BOT_ENV_FILE:-.env.bot}" in compose
    assert "${USERBOT_ENV_FILE:-.env.userbot}" in compose
    assert "/run/romatic-userbot-session" in compose
    assert "userbot.healthcheck" in compose
    assert "stdin_open: false" in compose
