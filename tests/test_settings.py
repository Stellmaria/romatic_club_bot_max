from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from config import PROJECT_ROOT, Settings


def test_settings_support_documented_mtproto_aliases() -> None:
    env = {
        "BOT_TOKEN": "token",
        "DATABASE_URL": "postgresql://localhost/test",
        "TELETHON_API_ID": "123",
        "TELETHON_API_HASH": "hash",
        "RUNTIME_DIR": "runtime-test",
    }
    with patch.dict(os.environ, env, clear=True):
        value = Settings.from_env()

    assert value.tg_api_id == 123
    assert value.userbot_api_id == 123
    assert value.tg_api_hash == "hash"
    assert value.userbot_api_hash == "hash"
    assert value.runtime_dir == PROJECT_ROOT / "runtime-test"


def test_settings_validate_fatal_bot_requirements_without_exposing_values() -> None:
    with patch.dict(os.environ, {}, clear=True):
        value = Settings.from_env()

    assert value.bot_configuration_errors() == (
        "BOT_TOKEN is not configured",
        "DATABASE_URL is not configured",
        "AUCTION_CHANNEL_ID is not configured",
        "DISCUSSION_CHAT_ID is not configured",
        "UID_HASH_KEY is not configured",
        "UID_ENC_KEY is not configured",
    )


def test_database_pool_bounds_are_normalized() -> None:
    with patch.dict(
        os.environ,
        {"DATABASE_POOL_MIN_SIZE": "8", "DATABASE_POOL_MAX_SIZE": "2"},
        clear=True,
    ):
        value = Settings.from_env()

    assert value.database_pool_min_size == 8
    assert value.database_pool_max_size == 8
    assert isinstance(value.runtime_dir, Path)
