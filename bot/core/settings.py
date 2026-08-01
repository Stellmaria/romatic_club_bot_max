"""Application settings and legacy constant exports.

The project historically imported module-level constants from ``config``.
``Settings`` provides one typed source of truth while those constants remain as
a compatibility layer during the gradual architecture migration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from bot.core.environment import PROJECT_ROOT


def parse_int_list(env_val: str) -> list[int]:
    """Parse comma-separated integers, silently skipping malformed entries."""

    result: list[int] = []
    for item in (env_val or "").split(","):
        try:
            result.append(int(item.strip()))
        except (TypeError, ValueError):
            continue
    return result


def get_int_list(var: str) -> list[int]:
    return parse_int_list(os.getenv(var, ""))


def get_int(var: str, default: int = 0) -> int:
    try:
        return int(os.getenv(var, str(default)))
    except (TypeError, ValueError):
        return default


def get_str(var: str, default: str = "") -> str:
    return os.getenv(var, default).strip()


def get_bool(var: str, default: bool = False) -> bool:
    raw = os.getenv(var)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _first_str(*names: str, default: str = "") -> str:
    for name in names:
        value = get_str(name)
        if value:
            return value
    return default


def _first_int(*names: str, default: int = 0) -> int:
    for name in names:
        value = get_str(name)
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            continue
    return default


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admins: tuple[int, ...]
    admin_owners: tuple[int, ...]
    auction_channel_id: int
    auction_channel_username: str
    discussion_chat_id: int
    database_url: str
    database_pool_min_size: int
    database_pool_max_size: int
    uid_hash_key: str
    uid_enc_key: str
    # Deprecated compatibility fields. Telegram shared-secret authorization was
    # retired in issue #23; these values are always empty and will be removed
    # together with the remaining legacy settings facade.
    admin_secret: str
    legacy_bridge_secret: str
    legacy_bridge_max_skew_seconds: int
    legacy_bridge_nonce_cache_size: int
    admin_log_chats: tuple[int, ...]
    log_chat_id: int
    luxury_chat_id: int
    luxury_chat_id_lvl2: int
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    userbot_api_id: int
    userbot_api_hash: str
    userbot_session: str
    backfill_limit_posts: int
    autobid_set_password: str
    winner_notify_deadline_minutes: int
    log_level: str
    aiogram_debug: bool
    debug_middleware: bool
    drop_pending_updates: bool
    bid_validation_mode: str
    userbot_bid_moderation: bool
    runtime_dir: Path
    schedule_announcements_enabled: bool = True
    schedule_announcements_hour: int = 23
    schedule_announcements_minute: int = 0
    schedule_announcements_require_custom_emoji: bool = True
    schedule_announcement_state_file: Path = Path("var/schedule_announcements.json")

    @classmethod
    def from_env(cls) -> "Settings":
        min_pool = max(1, get_int("DATABASE_POOL_MIN_SIZE", 1))
        max_pool = max(min_pool, get_int("DATABASE_POOL_MAX_SIZE", 5))
        runtime_dir = Path(get_str("RUNTIME_DIR", str(PROJECT_ROOT / "var")))
        if not runtime_dir.is_absolute():
            runtime_dir = PROJECT_ROOT / runtime_dir

        schedule_state_file = Path(
            get_str(
                "SCHEDULE_ANNOUNCEMENT_STATE_FILE",
                str(runtime_dir / "schedule_announcements.json"),
            )
        )
        if not schedule_state_file.is_absolute():
            schedule_state_file = PROJECT_ROOT / schedule_state_file

        return cls(
            bot_token=get_str("BOT_TOKEN"),
            admins=tuple(get_int_list("ADMINS")),
            admin_owners=tuple(get_int_list("ADMINS_OWNERS")),
            auction_channel_id=get_int("AUCTION_CHANNEL_ID"),
            auction_channel_username=get_str("AUCTION_CHANNEL_USERNAME").lstrip("@"),
            discussion_chat_id=get_int("DISCUSSION_CHAT_ID"),
            database_url=get_str("DATABASE_URL"),
            database_pool_min_size=min_pool,
            database_pool_max_size=max_pool,
            uid_hash_key=get_str("UID_HASH_KEY"),
            uid_enc_key=get_str("UID_ENC_KEY"),
            admin_secret="",
            legacy_bridge_secret=get_str("LEGACY_BRIDGE_SECRET"),
            legacy_bridge_max_skew_seconds=max(
                1,
                get_int("LEGACY_BRIDGE_MAX_SKEW_SECONDS", 300),
            ),
            legacy_bridge_nonce_cache_size=max(
                1,
                get_int("LEGACY_BRIDGE_NONCE_CACHE_SIZE", 4096),
            ),
            admin_log_chats=tuple(get_int_list("ADMIN_LOG_CHATS")),
            log_chat_id=get_int("LOG_CHAT_ID"),
            luxury_chat_id=get_int("LUXURY_CHAT_ID"),
            luxury_chat_id_lvl2=get_int("LUXURY_CHAT_ID_LVL2"),
            tg_api_id=_first_int("TG_API_ID", "TELETHON_API_ID", "USERBOT_API_ID"),
            tg_api_hash=_first_str("TG_API_HASH", "TELETHON_API_HASH", "USERBOT_API_HASH"),
            tg_session=get_str("TG_SESSION", str(runtime_dir / "backfill")),
            userbot_api_id=_first_int("USERBOT_API_ID", "TELETHON_API_ID", "TG_API_ID"),
            userbot_api_hash=_first_str("USERBOT_API_HASH", "TELETHON_API_HASH", "TG_API_HASH"),
            userbot_session=get_str("USERBOT_SESSION", str(runtime_dir / "userbot_session")),
            backfill_limit_posts=max(1, get_int("BACKFILL_LIMIT_POSTS", 500)),
            autobid_set_password="",
            winner_notify_deadline_minutes=max(1, get_int("WINNER_NOTIFY_DEADLINE_MINUTES", 5)),
            log_level=get_str("LOG_LEVEL", "INFO").upper(),
            aiogram_debug=get_bool("AIOGRAM_DEBUG"),
            debug_middleware=get_bool("DEBUG_MW"),
            drop_pending_updates=get_bool("DROP_PENDING_UPDATES", True),
            bid_validation_mode=get_str("BID_VALIDATION_MODE", "userbot").lower(),
            userbot_bid_moderation=get_bool("USERBOT_BID_MODERATION", True),
            runtime_dir=runtime_dir,
            schedule_announcements_enabled=get_bool("SCHEDULE_ANNOUNCEMENTS_ENABLED", True),
            schedule_announcements_hour=min(
                23,
                max(0, get_int("SCHEDULE_ANNOUNCEMENTS_HOUR", 23)),
            ),
            schedule_announcements_minute=min(
                59,
                max(0, get_int("SCHEDULE_ANNOUNCEMENTS_MINUTE", 0)),
            ),
            schedule_announcements_require_custom_emoji=get_bool(
                "SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI",
                True,
            ),
            schedule_announcement_state_file=schedule_state_file,
        )

    def bot_configuration_errors(self) -> tuple[str, ...]:
        """Return fatal bot configuration problems without exposing secrets."""

        errors: list[str] = []
        if not self.bot_token or self.bot_token == "YOUR_TOKEN_HERE":
            errors.append("BOT_TOKEN is not configured")
        if not self.database_url:
            errors.append("DATABASE_URL is not configured")
        if not self.auction_channel_id:
            errors.append("AUCTION_CHANNEL_ID is not configured")
        if not self.discussion_chat_id:
            errors.append("DISCUSSION_CHAT_ID is not configured")
        if not self.uid_hash_key:
            errors.append("UID_HASH_KEY is not configured")
        if not self.uid_enc_key:
            errors.append("UID_ENC_KEY is not configured")
        return tuple(errors)


settings = Settings.from_env()

# Compatibility exports. New composition code should inject ``settings``.
BOT_TOKEN = settings.bot_token
ADMINS = list(settings.admins)
ADMINS_OWNERS = list(settings.admin_owners)
AUCTION_CHANNEL_ID = settings.auction_channel_id
AUCTION_CHANNEL_USERNAME = settings.auction_channel_username
DISCUSSION_CHAT_ID = settings.discussion_chat_id
DATABASE_URL = settings.database_url
DATABASE_POOL_MIN_SIZE = settings.database_pool_min_size
DATABASE_POOL_MAX_SIZE = settings.database_pool_max_size
ADMIN_SECRET = ""
LEGACY_BRIDGE_SECRET = settings.legacy_bridge_secret
LEGACY_BRIDGE_MAX_SKEW_SECONDS = settings.legacy_bridge_max_skew_seconds
LEGACY_BRIDGE_NONCE_CACHE_SIZE = settings.legacy_bridge_nonce_cache_size
ADMIN_LOG_CHATS = list(settings.admin_log_chats)
ADMIN_LOG_CHAT_1 = ADMIN_LOG_CHATS[0] if ADMIN_LOG_CHATS else 0
LOG_CHAT_ID = settings.log_chat_id
LUXURY_CHAT_ID = settings.luxury_chat_id
LUXURY_CHAT_ID_LVL2 = settings.luxury_chat_id_lvl2
TG_API_ID = settings.tg_api_id
TG_API_HASH = settings.tg_api_hash
TG_SESSION = settings.tg_session
USERBOT_API_ID = settings.userbot_api_id
USERBOT_API_HASH = settings.userbot_api_hash
USERBOT_SESSION = settings.userbot_session
BACKFILL_LIMIT_POSTS = settings.backfill_limit_posts
AUTOBID_SET_PASSWORD = ""
WINNER_NOTIFY_DEADLINE_MINUTES = settings.winner_notify_deadline_minutes
RUNTIME_DIR = settings.runtime_dir
