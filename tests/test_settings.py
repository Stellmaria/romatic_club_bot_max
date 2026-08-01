from __future__ import annotations

from pathlib import Path

import pytest

from bot.core.settings import (
    BidValidationMode,
    BotProcessSettings,
    BotSettings,
    ConfigurationError,
    DatabaseSettings,
    UserbotProcessSettings,
    UserbotSettings,
)

FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def _shared_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://localhost/test",
        "AUCTION_CHANNEL_ID": "-100123",
        "DISCUSSION_CHAT_ID": "-100456",
        "UID_HASH_KEY": "test-only-hmac-key",
        "UID_ENC_KEY": FERNET_KEY,
    }


def _bot_env() -> dict[str, str]:
    return {**_shared_env(), "BOT_TOKEN": "123:token"}


def _userbot_env() -> dict[str, str]:
    return {
        **_shared_env(),
        "USERBOT_API_ID": "12345",
        "USERBOT_API_HASH": "hash",
    }


def test_bot_process_does_not_require_userbot_credentials(tmp_path: Path) -> None:
    value = BotProcessSettings.from_env(_bot_env(), project_root=tmp_path)
    assert value.bot.bot_token == "123:token"
    assert value.database.url.endswith("/test")


def test_userbot_process_does_not_require_bot_or_supervisor_values(tmp_path: Path) -> None:
    value = UserbotProcessSettings.from_env(_userbot_env(), project_root=tmp_path)
    assert value.userbot.api_id == 12345
    assert value.userbot.session == str(tmp_path / "var" / "userbot_session")


def test_documented_mtproto_aliases_are_strictly_supported(tmp_path: Path) -> None:
    env = {
        **_shared_env(),
        "TELETHON_API_ID": "123",
        "TELETHON_API_HASH": "hash",
        "RUNTIME_DIR": "runtime-test",
    }
    value = UserbotSettings.from_env(env, project_root=tmp_path)
    assert value.api_id == 123
    assert value.backfill_api_id == 123
    assert value.api_hash == "hash"
    assert value.runtime_dir == tmp_path / "runtime-test"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DATABASE_POOL_MIN_SIZE", "many", "must be an integer"),
        ("DB_AUTO_MIGRATE", "sometimes", "must be a boolean"),
        ("BID_VALIDATION_MODE", "oracle", "must be one of"),
        ("ADMINS", "1,not-an-id", "item 2 must be an integer"),
        ("SCHEDULE_ANNOUNCEMENTS_HOUR", "24", "must be at most 23"),
    ],
)
def test_malformed_values_are_configuration_errors(
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    env = _userbot_env() if name == "SCHEDULE_ANNOUNCEMENTS_HOUR" else _bot_env()
    env[name] = value
    loader = UserbotProcessSettings if name == "SCHEDULE_ANNOUNCEMENTS_HOUR" else BotProcessSettings
    with pytest.raises(ConfigurationError) as captured:
        loader.from_env(env, project_root=tmp_path)
    assert name in str(captured.value)
    assert message in str(captured.value)


def test_database_pool_bounds_are_rejected_not_normalized(tmp_path: Path) -> None:
    env = _shared_env() | {
        "DATABASE_POOL_MIN_SIZE": "8",
        "DATABASE_POOL_MAX_SIZE": "2",
    }
    with pytest.raises(ConfigurationError, match="DATABASE_POOL_MAX_SIZE"):
        DatabaseSettings.from_env(env, project_root=tmp_path)


def test_configuration_error_never_contains_secret_values(tmp_path: Path) -> None:
    secret = "not-a-valid-fernet-secret-value"
    env = _bot_env() | {"UID_ENC_KEY": secret}
    with pytest.raises(ConfigurationError) as captured:
        BotProcessSettings.from_env(env, project_root=tmp_path)
    rendered = str(captured.value)
    assert "UID_ENC_KEY" in rendered
    assert secret not in rendered


def test_multiple_independent_models_can_exist_in_one_process(tmp_path: Path) -> None:
    first = BotSettings.from_env(_bot_env() | {"BOT_TOKEN": "first"}, project_root=tmp_path)
    second = BotSettings.from_env(_bot_env() | {"BOT_TOKEN": "second"}, project_root=tmp_path)
    assert first.bot_token == "first"
    assert second.bot_token == "second"
    assert first is not second


def test_valid_enum_is_typed(tmp_path: Path) -> None:
    value = BotSettings.from_env(
        _bot_env() | {"BID_VALIDATION_MODE": "db"},
        project_root=tmp_path,
    )
    assert value.bid_validation_mode is BidValidationMode.DATABASE
