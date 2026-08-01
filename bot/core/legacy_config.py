"""Temporary compatibility adapter for legacy configuration consumers.

The adapter is deliberately inert at import time. Composition roots configure
it with an already validated process model. New code must receive typed settings
explicitly instead of importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from bot.core.settings import BotProcessSettings, Settings, UserbotProcessSettings


class LegacyConfigNotConfigured(RuntimeError):
    pass


DEPRECATION_INVENTORY: Final[dict[str, str]] = {
    "BOT_TOKEN": "BotSettings.bot_token",
    "ADMINS": "BotSettings.admins / UserbotSettings.admins",
    "ADMINS_OWNERS": "BotSettings.admin_owners / UserbotSettings.admin_owners",
    "AUCTION_CHANNEL_ID": "*.auction_channel_id",
    "AUCTION_CHANNEL_USERNAME": "*.auction_channel_username",
    "DISCUSSION_CHAT_ID": "*.discussion_chat_id",
    "ADMIN_LOG_CHATS": "*.admin_log_chats",
    "LOG_CHAT_ID": "*.log_chat_id",
    "LUXURY_CHAT_ID": "*.luxury_chat_id",
    "LUXURY_CHAT_ID_LVL2": "*.luxury_chat_id_lvl2",
    "DATABASE_URL": "DatabaseSettings.url",
    "DATABASE_POOL_MIN_SIZE": "DatabaseSettings.pool_min_size",
    "DATABASE_POOL_MAX_SIZE": "DatabaseSettings.pool_max_size",
    "DB_AUTO_MIGRATE": "DatabaseSettings.auto_migrate",
    "UID_HASH_KEY": "*.uid_hash_key",
    "UID_ENC_KEY": "*.uid_enc_key",
    "TG_API_ID": "UserbotSettings.backfill_api_id",
    "TG_API_HASH": "UserbotSettings.backfill_api_hash",
    "TG_SESSION": "UserbotSettings.backfill_session",
    "USERBOT_API_ID": "UserbotSettings.api_id",
    "USERBOT_API_HASH": "UserbotSettings.api_hash",
    "USERBOT_SESSION": "UserbotSettings.session",
    "BACKFILL_LIMIT_POSTS": "UserbotSettings.backfill_limit_posts",
    "SCHEDULE_ANNOUNCEMENTS_ENABLED": "UserbotSettings.schedule_announcements_enabled",
    "SCHEDULE_ANNOUNCEMENTS_HOUR": "UserbotSettings.schedule_announcements_hour",
    "SCHEDULE_ANNOUNCEMENTS_MINUTE": "UserbotSettings.schedule_announcements_minute",
    "SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI": "UserbotSettings.schedule_announcements_require_custom_emoji",
    "SCHEDULE_ANNOUNCEMENT_STATE_FILE": "UserbotSettings.schedule_announcement_state_file",
    "WINNER_NOTIFY_DEADLINE_MINUTES": "*.winner_notify_deadline_minutes",
    "BID_VALIDATION_MODE": "*.bid_validation_mode",
    "USERBOT_BID_MODERATION": "*.userbot_bid_moderation",
    "LEGACY_BRIDGE_SECRET": "BotSettings.legacy_bridge_secret",
    "LEGACY_BRIDGE_MAX_SKEW_SECONDS": "BotSettings.legacy_bridge_max_skew_seconds",
    "LEGACY_BRIDGE_NONCE_CACHE_SIZE": "BotSettings.legacy_bridge_nonce_cache_size",
    "RUNTIME_DIR": "*ProcessSettings.runtime_dir",
    "ADMIN_SECRET": "retired; always empty",
    "AUTOBID_SET_PASSWORD": "retired; always empty",
}



@dataclass(slots=True)
class LegacyConfigAdapter:
    _bot_process: BotProcessSettings | None = None
    _userbot_process: UserbotProcessSettings | None = None
    _aggregate: Settings | None = None

    def configure(self, config: BotProcessSettings | UserbotProcessSettings | Settings) -> None:
        self._bot_process = config if isinstance(config, BotProcessSettings) else None
        self._userbot_process = config if isinstance(config, UserbotProcessSettings) else None
        self._aggregate = config if isinstance(config, Settings) else None

    def reset(self) -> None:
        self._bot_process = None
        self._userbot_process = None
        self._aggregate = None

    def _bot(self):
        if self._bot_process is not None:
            return self._bot_process.bot
        if self._aggregate is not None:
            return self._aggregate.bot
        if self._userbot_process is not None:
            return self._userbot_process.userbot
        raise LegacyConfigNotConfigured("legacy configuration adapter is not configured")

    def _userbot(self):
        if self._userbot_process is not None:
            return self._userbot_process.userbot
        if self._aggregate is not None:
            return self._aggregate.userbot
        raise LegacyConfigNotConfigured("userbot compatibility configuration is not configured")

    def _database(self):
        if self._bot_process is not None:
            return self._bot_process.database
        if self._userbot_process is not None:
            return self._userbot_process.database
        if self._aggregate is not None:
            return self._aggregate.database
        raise LegacyConfigNotConfigured("database compatibility configuration is not configured")

    def _runtime_dir(self):
        process = self._bot_process or self._userbot_process or self._aggregate
        if process is None:
            raise LegacyConfigNotConfigured("runtime compatibility configuration is not configured")
        return process.runtime_dir

    def __getattr__(self, name: str) -> Any:
        if name in {"ADMIN_SECRET", "AUTOBID_SET_PASSWORD"}:
            return ""
        if name in {"LOG_CHAT_ID2", "LOG_CHAT_IDS"}:
            return None

        bot = {
            "BOT_TOKEN": "bot_token",
            "ADMINS": "admins",
            "ADMINS_OWNERS": "admin_owners",
            "AUCTION_CHANNEL_ID": "auction_channel_id",
            "AUCTION_CHANNEL_USERNAME": "auction_channel_username",
            "DISCUSSION_CHAT_ID": "discussion_chat_id",
            "ADMIN_LOG_CHATS": "admin_log_chats",
            "LOG_CHAT_ID": "log_chat_id",
            "LUXURY_CHAT_ID": "luxury_chat_id",
            "LUXURY_CHAT_ID_LVL2": "luxury_chat_id_lvl2",
            "UID_HASH_KEY": "uid_hash_key",
            "UID_ENC_KEY": "uid_enc_key",
            "WINNER_NOTIFY_DEADLINE_MINUTES": "winner_notify_deadline_minutes",
            "BID_VALIDATION_MODE": "bid_validation_mode",
            "USERBOT_BID_MODERATION": "userbot_bid_moderation",
            "LEGACY_BRIDGE_SECRET": "legacy_bridge_secret",
            "LEGACY_BRIDGE_MAX_SKEW_SECONDS": "legacy_bridge_max_skew_seconds",
            "LEGACY_BRIDGE_NONCE_CACHE_SIZE": "legacy_bridge_nonce_cache_size",
        }
        if name in bot:
            value = getattr(self._bot(), bot[name])
            return value.value if hasattr(value, "value") else value

        database = {
            "DATABASE_URL": "url",
            "DATABASE_POOL_MIN_SIZE": "pool_min_size",
            "DATABASE_POOL_MAX_SIZE": "pool_max_size",
            "DB_AUTO_MIGRATE": "auto_migrate",
        }
        if name in database:
            return getattr(self._database(), database[name])

        userbot = {
            "TG_API_ID": "backfill_api_id",
            "TG_API_HASH": "backfill_api_hash",
            "TG_SESSION": "backfill_session",
            "USERBOT_API_ID": "api_id",
            "USERBOT_API_HASH": "api_hash",
            "USERBOT_SESSION": "session",
            "BACKFILL_LIMIT_POSTS": "backfill_limit_posts",
            "SCHEDULE_ANNOUNCEMENTS_ENABLED": "schedule_announcements_enabled",
            "SCHEDULE_ANNOUNCEMENTS_HOUR": "schedule_announcements_hour",
            "SCHEDULE_ANNOUNCEMENTS_MINUTE": "schedule_announcements_minute",
            "SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI": "schedule_announcements_require_custom_emoji",
            "SCHEDULE_ANNOUNCEMENT_STATE_FILE": "schedule_announcement_state_file",
        }
        if name in userbot:
            return getattr(self._userbot(), userbot[name])
        if name == "RUNTIME_DIR":
            return self._runtime_dir()
        raise AttributeError(name)


legacy_config = LegacyConfigAdapter()


def configure_legacy_config(config: BotProcessSettings | UserbotProcessSettings | Settings) -> None:
    legacy_config.configure(config)


def reset_legacy_config_for_testing() -> None:
    legacy_config.reset()


__all__ = (
    "DEPRECATION_INVENTORY",
    "LegacyConfigAdapter",
    "LegacyConfigNotConfigured",
    "configure_legacy_config",
    "legacy_config",
    "reset_legacy_config_for_testing",
)
