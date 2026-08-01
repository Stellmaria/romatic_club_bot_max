"""Strict, process-scoped application configuration.

Importing this module is side-effect free: it does not read ``os.environ``,
load ``.env`` files, inspect secret files or create a process-wide settings
singleton. Executable composition roots construct one of the process models
explicitly after environment bootstrap and pass it into the application.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from bot.core.environment import resolve_project_root

_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on", "да"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off", "нет"})


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    variable: str
    message: str

    def render(self) -> str:
        return f"{self.variable}: {self.message}"


class ConfigurationError(ValueError):
    """One or more configuration variables are missing or malformed.

    Values are deliberately excluded from the message so secrets cannot leak
    into logs, CI output or Telegram administrator diagnostics.
    """

    def __init__(self, issues: tuple[ConfigurationIssue, ...] | list[ConfigurationIssue]):
        normalized = tuple(issues)
        if not normalized:
            raise ValueError("ConfigurationError requires at least one issue")
        self.issues = normalized
        super().__init__("; ".join(issue.render() for issue in normalized))


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class BidValidationMode(StrEnum):
    BOT = "bot"
    USERBOT = "userbot"
    DATABASE = "db"


@dataclass(frozen=True, slots=True)
class ConfigField:
    name: str
    processes: tuple[str, ...]
    category: str
    required: bool = False
    secret: bool = False
    default: str | None = None


# Canonical application variables. Compose-only and host Supervisor daemon
# variables intentionally live outside this schema because they are not read by
# either Python application process.
CONFIG_SCHEMA: Final[tuple[ConfigField, ...]] = (
    ConfigField("BOT_TOKEN", ("bot",), "telegram", required=True, secret=True),
    ConfigField("ADMINS", ("bot", "userbot"), "telegram"),
    ConfigField("ADMINS_OWNERS", ("bot", "userbot"), "telegram"),
    ConfigField("AUCTION_CHANNEL_ID", ("bot", "userbot"), "telegram", required=True),
    ConfigField("AUCTION_CHANNEL_USERNAME", ("bot", "userbot"), "telegram"),
    ConfigField("DISCUSSION_CHAT_ID", ("bot", "userbot"), "telegram", required=True),
    ConfigField("ADMIN_LOG_CHATS", ("bot", "userbot"), "telegram"),
    ConfigField("LOG_CHAT_ID", ("bot", "userbot"), "telegram", default="0"),
    ConfigField("LUXURY_CHAT_ID", ("bot", "userbot"), "telegram", default="0"),
    ConfigField("LUXURY_CHAT_ID_LVL2", ("bot", "userbot"), "telegram", default="0"),
    ConfigField("DATABASE_URL", ("bot", "userbot"), "database", required=True, secret=True),
    ConfigField("DB_AUTO_MIGRATE", ("bot", "userbot"), "database", default="true"),
    ConfigField("DATABASE_POOL_MIN_SIZE", ("bot", "userbot"), "database", default="1"),
    ConfigField("DATABASE_POOL_MAX_SIZE", ("bot", "userbot"), "database", default="5"),
    ConfigField("UID_HASH_KEY", ("bot", "userbot"), "identity", required=True, secret=True),
    ConfigField("UID_ENC_KEY", ("bot", "userbot"), "identity", required=True, secret=True),
    ConfigField("RUNTIME_DIR", ("bot", "userbot"), "paths", default="var"),
    ConfigField("USERBOT_API_ID", ("userbot",), "userbot", required=True),
    ConfigField("USERBOT_API_HASH", ("userbot",), "userbot", required=True, secret=True),
    ConfigField("USERBOT_SESSION", ("userbot",), "userbot", default="var/userbot_session", secret=True),
    ConfigField("TG_API_ID", ("userbot",), "backfill"),
    ConfigField("TG_API_HASH", ("userbot",), "backfill", secret=True),
    ConfigField("TG_SESSION", ("userbot",), "backfill", default="var/backfill", secret=True),
    ConfigField("BACKFILL_LIMIT_POSTS", ("userbot",), "backfill", default="500"),
    ConfigField("SCHEDULE_ANNOUNCEMENTS_ENABLED", ("userbot",), "schedule", default="true"),
    ConfigField("SCHEDULE_ANNOUNCEMENTS_HOUR", ("userbot",), "schedule", default="23"),
    ConfigField("SCHEDULE_ANNOUNCEMENTS_MINUTE", ("userbot",), "schedule", default="0"),
    ConfigField("SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI", ("userbot",), "schedule", default="true"),
    ConfigField("SCHEDULE_ANNOUNCEMENT_STATE_FILE", ("userbot",), "schedule", default="var/schedule_announcements.json"),
    ConfigField("WINNER_NOTIFY_DEADLINE_MINUTES", ("bot", "userbot"), "auction", default="5"),
    ConfigField("LOG_LEVEL", ("bot",), "runtime", default="INFO"),
    ConfigField("AIOGRAM_DEBUG", ("bot",), "runtime", default="false"),
    ConfigField("DEBUG_MW", ("bot",), "runtime", default="false"),
    ConfigField("DROP_PENDING_UPDATES", ("bot",), "runtime", default="true"),
    ConfigField("BID_VALIDATION_MODE", ("bot", "userbot"), "auction", default="userbot"),
    ConfigField("USERBOT_BID_MODERATION", ("bot", "userbot"), "auction", default="true"),
    ConfigField("LEGACY_BRIDGE_SECRET", ("bot",), "legacy_bridge", secret=True),
    ConfigField("LEGACY_BRIDGE_MAX_SKEW_SECONDS", ("bot",), "legacy_bridge", default="300"),
    ConfigField("LEGACY_BRIDGE_NONCE_CACHE_SIZE", ("bot",), "legacy_bridge", default="4096"),
    ConfigField("SUPERVISOR_ENABLED", ("bot",), "supervisor", default="false"),
    ConfigField("SUPERVISOR_TOKEN", ("bot",), "supervisor", secret=True),
    ConfigField("SUPERVISOR_TOKEN_FILE", ("bot",), "supervisor", secret=True),
    ConfigField("SUPERVISOR_BASE_URL", ("bot",), "supervisor"),
    ConfigField("SUPERVISOR_CLIENT_TIMEOUT_SECONDS", ("bot",), "supervisor", default="20"),
    ConfigField("SUPERVISOR_ACTOR", ("bot",), "supervisor", default="telegram-bot"),
)


def schema_for_process(process: str) -> tuple[ConfigField, ...]:
    return tuple(field for field in CONFIG_SCHEMA if process in field.processes)


class _Reader:
    def __init__(self, environ: Mapping[str, str], project_root: Path) -> None:
        self.environ = environ
        self.project_root = project_root
        self.issues: list[ConfigurationIssue] = []

    def _raw(self, name: str) -> str | None:
        value = self.environ.get(name)
        return None if value is None else str(value).strip()

    def issue(self, name: str, message: str) -> None:
        self.issues.append(ConfigurationIssue(name, message))

    def string(
        self,
        name: str,
        *,
        default: str = "",
        required: bool = False,
        normalize: Callable[[str], str] | None = None,
    ) -> str:
        raw = self._raw(name)
        if raw is None or raw == "":
            if required:
                self.issue(name, "is required")
            return default
        value = raw
        if normalize is not None:
            value = normalize(value)
        return value

    def first_string(
        self,
        names: tuple[str, ...],
        *,
        default: str = "",
        required_name: str | None = None,
    ) -> str:
        for name in names:
            raw = self._raw(name)
            if raw:
                return raw
        if required_name:
            self.issue(required_name, "is required")
        return default

    def integer(
        self,
        name: str,
        *,
        default: int = 0,
        required: bool = False,
        minimum: int | None = None,
        maximum: int | None = None,
        nonzero: bool = False,
    ) -> int:
        raw = self._raw(name)
        if raw is None or raw == "":
            if required:
                self.issue(name, "is required")
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.issue(name, "must be an integer")
            return default
        if nonzero and value == 0:
            self.issue(name, "must be a non-zero integer")
        if minimum is not None and value < minimum:
            self.issue(name, f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            self.issue(name, f"must be at most {maximum}")
        return value

    def first_integer(
        self,
        names: tuple[str, ...],
        *,
        default: int = 0,
        required_name: str | None = None,
        minimum: int | None = None,
    ) -> int:
        selected_name: str | None = None
        for name in names:
            raw = self._raw(name)
            if raw:
                selected_name = name
                break
        if selected_name is None:
            if required_name:
                self.issue(required_name, "is required")
            return default
        return self.integer(selected_name, default=default, minimum=minimum)

    def boolean(self, name: str, *, default: bool = False) -> bool:
        raw = self._raw(name)
        if raw is None or raw == "":
            return default
        normalized = raw.casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        self.issue(name, "must be a boolean (true/false, 1/0, yes/no, on/off)")
        return default

    def floating(
        self,
        name: str,
        *,
        default: float,
        minimum: float | None = None,
    ) -> float:
        raw = self._raw(name)
        if raw is None or raw == "":
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self.issue(name, "must be a number")
            return default
        if minimum is not None and value < minimum:
            self.issue(name, f"must be at least {minimum:g}")
        return value

    def int_list(self, name: str) -> tuple[int, ...]:
        raw = self._raw(name)
        if raw is None or raw == "":
            return ()
        result: list[int] = []
        for index, item in enumerate(raw.split(","), start=1):
            item = item.strip()
            if not item:
                self.issue(name, f"item {index} is empty")
                continue
            try:
                result.append(int(item))
            except ValueError:
                self.issue(name, f"item {index} must be an integer")
        return tuple(result)

    def choice(self, name: str, enum_type: type[StrEnum], *, default: StrEnum) -> StrEnum:
        raw = self._raw(name)
        if raw is None or raw == "":
            return default
        normalized = raw.casefold()
        for item in enum_type:
            if item.value.casefold() == normalized:
                return item
        allowed = ", ".join(item.value for item in enum_type)
        self.issue(name, f"must be one of: {allowed}")
        return default

    def path(
        self,
        name: str,
        *,
        default: Path,
        required: bool = False,
    ) -> Path:
        raw = self._raw(name)
        if raw is None or raw == "":
            if required:
                self.issue(name, "is required")
            value = default
        else:
            if "\x00" in raw:
                self.issue(name, "contains an invalid NUL character")
                return default
            value = Path(raw).expanduser()
        if not value.is_absolute():
            value = self.project_root / value
        return value.resolve(strict=False)

    def secret_from_value_or_file(
        self,
        value_name: str,
        file_name: str,
        *,
        required: bool,
    ) -> str:
        direct = self._raw(value_name) or ""
        file_raw = self._raw(file_name) or ""
        if direct and file_raw:
            self.issue(value_name, f"cannot be set together with {file_name}")
            return ""
        if file_raw:
            path = self.path(file_name, default=self.project_root / "missing-secret")
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                self.issue(file_name, "cannot be read")
                return ""
            if not value:
                self.issue(file_name, "is empty")
            return value
        if direct:
            return direct
        if required:
            self.issue(value_name, f"or {file_name} is required")
        return ""

    def raise_if_invalid(self) -> None:
        if self.issues:
            raise ConfigurationError(self.issues)


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    url: str
    auto_migrate: bool = True
    pool_min_size: int = 1
    pool_max_size: int = 5

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "DatabaseSettings":
        reader = _Reader(
            os.environ if environ is None else environ,
            resolve_project_root(project_root),
        )
        value = _parse_database(reader)
        reader.raise_if_invalid()
        return value


@dataclass(frozen=True, slots=True)
class SupervisorClientSettings:
    enabled: bool
    base_url: str = ""
    token: str = ""
    timeout_seconds: float = 20.0
    actor: str = "telegram-bot"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "SupervisorClientSettings":
        reader = _Reader(
            os.environ if environ is None else environ,
            resolve_project_root(project_root),
        )
        value = _parse_supervisor(reader)
        reader.raise_if_invalid()
        return value


@dataclass(frozen=True, slots=True)
class BotSettings:
    bot_token: str
    admins: tuple[int, ...]
    admin_owners: tuple[int, ...]
    auction_channel_id: int
    auction_channel_username: str
    discussion_chat_id: int
    admin_log_chats: tuple[int, ...]
    log_chat_id: int
    luxury_chat_id: int
    luxury_chat_id_lvl2: int
    uid_hash_key: str
    uid_enc_key: str
    winner_notify_deadline_minutes: int
    log_level: LogLevel
    aiogram_debug: bool
    debug_middleware: bool
    drop_pending_updates: bool
    bid_validation_mode: BidValidationMode
    userbot_bid_moderation: bool
    legacy_bridge_secret: str
    legacy_bridge_max_skew_seconds: int
    legacy_bridge_nonce_cache_size: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "BotSettings":
        reader = _Reader(
            os.environ if environ is None else environ,
            resolve_project_root(project_root),
        )
        value = _parse_bot(reader)
        reader.raise_if_invalid()
        return value


@dataclass(frozen=True, slots=True)
class UserbotSettings:
    api_id: int
    api_hash: str
    session: str
    admins: tuple[int, ...]
    admin_owners: tuple[int, ...]
    auction_channel_id: int
    auction_channel_username: str
    discussion_chat_id: int
    admin_log_chats: tuple[int, ...]
    log_chat_id: int
    luxury_chat_id: int
    luxury_chat_id_lvl2: int
    uid_hash_key: str
    uid_enc_key: str
    runtime_dir: Path
    backfill_api_id: int
    backfill_api_hash: str
    backfill_session: str
    backfill_limit_posts: int
    winner_notify_deadline_minutes: int
    bid_validation_mode: BidValidationMode
    userbot_bid_moderation: bool
    schedule_announcements_enabled: bool
    schedule_announcements_hour: int
    schedule_announcements_minute: int
    schedule_announcements_require_custom_emoji: bool
    schedule_announcement_state_file: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "UserbotSettings":
        root = resolve_project_root(project_root)
        reader = _Reader(os.environ if environ is None else environ, root)
        runtime_dir = reader.path("RUNTIME_DIR", default=root / "var")
        value = _parse_userbot(reader, runtime_dir)
        reader.raise_if_invalid()
        return value


@dataclass(frozen=True, slots=True)
class BotProcessSettings:
    bot: BotSettings
    database: DatabaseSettings
    supervisor: SupervisorClientSettings
    project_root: Path
    runtime_dir: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "BotProcessSettings":
        root = resolve_project_root(project_root)
        reader = _Reader(os.environ if environ is None else environ, root)
        runtime_dir = reader.path("RUNTIME_DIR", default=root / "var")
        database = _parse_database(reader)
        bot = _parse_bot(reader)
        supervisor = _parse_supervisor(reader)
        reader.raise_if_invalid()
        return cls(bot=bot, database=database, supervisor=supervisor, project_root=root, runtime_dir=runtime_dir)


@dataclass(frozen=True, slots=True)
class UserbotProcessSettings:
    userbot: UserbotSettings
    database: DatabaseSettings
    project_root: Path
    runtime_dir: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "UserbotProcessSettings":
        root = resolve_project_root(project_root)
        reader = _Reader(os.environ if environ is None else environ, root)
        runtime_dir = reader.path("RUNTIME_DIR", default=root / "var")
        database = _parse_database(reader)
        userbot = _parse_userbot(reader, runtime_dir)
        reader.raise_if_invalid()
        return cls(userbot=userbot, database=database, project_root=root, runtime_dir=runtime_dir)


@dataclass(frozen=True, slots=True)
class Settings:
    """Deprecated explicit aggregate for maintenance commands and migrations.

    There is intentionally no module-level instance. Production composition
    roots use :class:`BotProcessSettings` or :class:`UserbotProcessSettings`.
    """

    bot: BotSettings
    userbot: UserbotSettings
    database: DatabaseSettings
    supervisor: SupervisorClientSettings
    project_root: Path
    runtime_dir: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "Settings":
        root = resolve_project_root(project_root)
        reader = _Reader(os.environ if environ is None else environ, root)
        runtime_dir = reader.path("RUNTIME_DIR", default=root / "var")
        database = _parse_database(reader)
        bot = _parse_bot(reader)
        userbot = _parse_userbot(reader, runtime_dir)
        supervisor = _parse_supervisor(reader)
        reader.raise_if_invalid()
        return cls(
            bot=bot,
            userbot=userbot,
            database=database,
            supervisor=supervisor,
            project_root=root,
            runtime_dir=runtime_dir,
        )

    # Flattened read-only properties keep explicit maintenance code source
    # compatible while old module-level constants disappear.
    def __getattr__(self, name: str):
        aliases = {
            "bot_token": (self.bot, "bot_token"),
            "admins": (self.bot, "admins"),
            "admin_owners": (self.bot, "admin_owners"),
            "auction_channel_id": (self.bot, "auction_channel_id"),
            "auction_channel_username": (self.bot, "auction_channel_username"),
            "discussion_chat_id": (self.bot, "discussion_chat_id"),
            "database_url": (self.database, "url"),
            "database_pool_min_size": (self.database, "pool_min_size"),
            "database_pool_max_size": (self.database, "pool_max_size"),
            "uid_hash_key": (self.bot, "uid_hash_key"),
            "uid_enc_key": (self.bot, "uid_enc_key"),
            "admin_log_chats": (self.bot, "admin_log_chats"),
            "log_chat_id": (self.bot, "log_chat_id"),
            "luxury_chat_id": (self.bot, "luxury_chat_id"),
            "luxury_chat_id_lvl2": (self.bot, "luxury_chat_id_lvl2"),
            "tg_api_id": (self.userbot, "backfill_api_id"),
            "tg_api_hash": (self.userbot, "backfill_api_hash"),
            "tg_session": (self.userbot, "backfill_session"),
            "userbot_api_id": (self.userbot, "api_id"),
            "userbot_api_hash": (self.userbot, "api_hash"),
            "userbot_session": (self.userbot, "session"),
            "backfill_limit_posts": (self.userbot, "backfill_limit_posts"),
            "winner_notify_deadline_minutes": (self.bot, "winner_notify_deadline_minutes"),
            "log_level": (self.bot, "log_level"),
            "aiogram_debug": (self.bot, "aiogram_debug"),
            "debug_middleware": (self.bot, "debug_middleware"),
            "drop_pending_updates": (self.bot, "drop_pending_updates"),
            "bid_validation_mode": (self.bot, "bid_validation_mode"),
            "userbot_bid_moderation": (self.bot, "userbot_bid_moderation"),
            "schedule_announcements_enabled": (self.userbot, "schedule_announcements_enabled"),
            "schedule_announcements_hour": (self.userbot, "schedule_announcements_hour"),
            "schedule_announcements_minute": (self.userbot, "schedule_announcements_minute"),
            "schedule_announcements_require_custom_emoji": (self.userbot, "schedule_announcements_require_custom_emoji"),
            "schedule_announcement_state_file": (self.userbot, "schedule_announcement_state_file"),
            "legacy_bridge_secret": (self.bot, "legacy_bridge_secret"),
            "legacy_bridge_max_skew_seconds": (self.bot, "legacy_bridge_max_skew_seconds"),
            "legacy_bridge_nonce_cache_size": (self.bot, "legacy_bridge_nonce_cache_size"),
        }
        target = aliases.get(name)
        if target is None:
            if name in {"admin_secret", "autobid_set_password"}:
                return ""
            raise AttributeError(name)
        return getattr(*target)



def _validate_identity_keys(reader: _Reader, hash_key: str, encryption_key: str) -> None:
    if hash_key and len(hash_key.encode("utf-8")) < 16:
        reader.issue("UID_HASH_KEY", "must contain at least 16 bytes")
    if encryption_key:
        try:
            decoded = base64.urlsafe_b64decode(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            reader.issue("UID_ENC_KEY", "must be a URL-safe base64 Fernet key")
        else:
            if len(decoded) != 32:
                reader.issue("UID_ENC_KEY", "must decode to exactly 32 bytes")

def _parse_database(reader: _Reader) -> DatabaseSettings:
    minimum = reader.integer("DATABASE_POOL_MIN_SIZE", default=1, minimum=1)
    maximum = reader.integer("DATABASE_POOL_MAX_SIZE", default=5, minimum=1)
    if maximum < minimum:
        reader.issue("DATABASE_POOL_MAX_SIZE", "must be greater than or equal to DATABASE_POOL_MIN_SIZE")
    return DatabaseSettings(
        url=reader.string("DATABASE_URL", required=True),
        auto_migrate=reader.boolean("DB_AUTO_MIGRATE", default=True),
        pool_min_size=minimum,
        pool_max_size=maximum,
    )


def _parse_bot(reader: _Reader) -> BotSettings:
    hash_key = reader.string("UID_HASH_KEY", required=True)
    encryption_key = reader.string("UID_ENC_KEY", required=True)
    _validate_identity_keys(reader, hash_key, encryption_key)
    return BotSettings(
        bot_token=reader.string("BOT_TOKEN", required=True),
        admins=reader.int_list("ADMINS"),
        admin_owners=reader.int_list("ADMINS_OWNERS"),
        auction_channel_id=reader.integer("AUCTION_CHANNEL_ID", required=True, nonzero=True),
        auction_channel_username=reader.string("AUCTION_CHANNEL_USERNAME").lstrip("@"),
        discussion_chat_id=reader.integer("DISCUSSION_CHAT_ID", required=True, nonzero=True),
        admin_log_chats=reader.int_list("ADMIN_LOG_CHATS"),
        log_chat_id=reader.integer("LOG_CHAT_ID", default=0),
        luxury_chat_id=reader.integer("LUXURY_CHAT_ID", default=0),
        luxury_chat_id_lvl2=reader.integer("LUXURY_CHAT_ID_LVL2", default=0),
        uid_hash_key=hash_key,
        uid_enc_key=encryption_key,
        winner_notify_deadline_minutes=reader.integer("WINNER_NOTIFY_DEADLINE_MINUTES", default=5, minimum=1),
        log_level=reader.choice("LOG_LEVEL", LogLevel, default=LogLevel.INFO),
        aiogram_debug=reader.boolean("AIOGRAM_DEBUG", default=False),
        debug_middleware=reader.boolean("DEBUG_MW", default=False),
        drop_pending_updates=reader.boolean("DROP_PENDING_UPDATES", default=True),
        bid_validation_mode=reader.choice("BID_VALIDATION_MODE", BidValidationMode, default=BidValidationMode.USERBOT),
        userbot_bid_moderation=reader.boolean("USERBOT_BID_MODERATION", default=True),
        legacy_bridge_secret=reader.string("LEGACY_BRIDGE_SECRET"),
        legacy_bridge_max_skew_seconds=reader.integer("LEGACY_BRIDGE_MAX_SKEW_SECONDS", default=300, minimum=1),
        legacy_bridge_nonce_cache_size=reader.integer("LEGACY_BRIDGE_NONCE_CACHE_SIZE", default=4096, minimum=1),
    )


def _parse_userbot(reader: _Reader, runtime_dir: Path) -> UserbotSettings:
    hash_key = reader.string("UID_HASH_KEY", required=True)
    encryption_key = reader.string("UID_ENC_KEY", required=True)
    _validate_identity_keys(reader, hash_key, encryption_key)
    api_id = reader.first_integer(
        ("USERBOT_API_ID", "TELETHON_API_ID", "TG_API_ID"),
        required_name="USERBOT_API_ID",
        minimum=1,
    )
    api_hash = reader.first_string(
        ("USERBOT_API_HASH", "TELETHON_API_HASH", "TG_API_HASH"),
        required_name="USERBOT_API_HASH",
    )
    backfill_api_id = reader.first_integer(
        ("TG_API_ID", "TELETHON_API_ID", "USERBOT_API_ID"),
        default=api_id,
        minimum=1,
    )
    backfill_api_hash = reader.first_string(
        ("TG_API_HASH", "TELETHON_API_HASH", "USERBOT_API_HASH"),
        default=api_hash,
    )
    session_path = reader.path("USERBOT_SESSION", default=runtime_dir / "userbot_session")
    backfill_session = reader.path("TG_SESSION", default=runtime_dir / "backfill")
    schedule_state = reader.path(
        "SCHEDULE_ANNOUNCEMENT_STATE_FILE",
        default=runtime_dir / "schedule_announcements.json",
    )
    return UserbotSettings(
        api_id=api_id,
        api_hash=api_hash,
        session=str(session_path),
        admins=reader.int_list("ADMINS"),
        admin_owners=reader.int_list("ADMINS_OWNERS"),
        auction_channel_id=reader.integer("AUCTION_CHANNEL_ID", required=True, nonzero=True),
        auction_channel_username=reader.string("AUCTION_CHANNEL_USERNAME").lstrip("@"),
        discussion_chat_id=reader.integer("DISCUSSION_CHAT_ID", required=True, nonzero=True),
        admin_log_chats=reader.int_list("ADMIN_LOG_CHATS"),
        log_chat_id=reader.integer("LOG_CHAT_ID", default=0),
        luxury_chat_id=reader.integer("LUXURY_CHAT_ID", default=0),
        luxury_chat_id_lvl2=reader.integer("LUXURY_CHAT_ID_LVL2", default=0),
        uid_hash_key=hash_key,
        uid_enc_key=encryption_key,
        runtime_dir=runtime_dir,
        backfill_api_id=backfill_api_id,
        backfill_api_hash=backfill_api_hash,
        backfill_session=str(backfill_session),
        backfill_limit_posts=reader.integer("BACKFILL_LIMIT_POSTS", default=500, minimum=1),
        winner_notify_deadline_minutes=reader.integer("WINNER_NOTIFY_DEADLINE_MINUTES", default=5, minimum=1),
        bid_validation_mode=reader.choice("BID_VALIDATION_MODE", BidValidationMode, default=BidValidationMode.USERBOT),
        userbot_bid_moderation=reader.boolean("USERBOT_BID_MODERATION", default=True),
        schedule_announcements_enabled=reader.boolean("SCHEDULE_ANNOUNCEMENTS_ENABLED", default=True),
        schedule_announcements_hour=reader.integer("SCHEDULE_ANNOUNCEMENTS_HOUR", default=23, minimum=0, maximum=23),
        schedule_announcements_minute=reader.integer("SCHEDULE_ANNOUNCEMENTS_MINUTE", default=0, minimum=0, maximum=59),
        schedule_announcements_require_custom_emoji=reader.boolean("SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI", default=True),
        schedule_announcement_state_file=schedule_state,
    )


def _parse_supervisor(reader: _Reader) -> SupervisorClientSettings:
    enabled = reader.boolean("SUPERVISOR_ENABLED", default=False)
    base_url = reader.string("SUPERVISOR_BASE_URL").rstrip("/")
    timeout = reader.floating("SUPERVISOR_CLIENT_TIMEOUT_SECONDS", default=20.0, minimum=2.0)
    actor = reader.string("SUPERVISOR_ACTOR", default="telegram-bot") or "telegram-bot"
    if len(actor) > 64:
        reader.issue("SUPERVISOR_ACTOR", "must be at most 64 characters")
    token = reader.secret_from_value_or_file(
        "SUPERVISOR_TOKEN",
        "SUPERVISOR_TOKEN_FILE",
        required=enabled,
    )
    if enabled and not base_url:
        reader.issue("SUPERVISOR_BASE_URL", "is required when SUPERVISOR_ENABLED is true")
    if enabled and token and len(token) < 24:
        reader.issue("SUPERVISOR_TOKEN", "must contain at least 24 characters")
    return SupervisorClientSettings(
        enabled=enabled,
        base_url=base_url,
        token=token,
        timeout_seconds=timeout,
        actor=actor[:64],
    )


__all__ = (
    "BidValidationMode",
    "BotProcessSettings",
    "BotSettings",
    "CONFIG_SCHEMA",
    "ConfigField",
    "ConfigurationError",
    "ConfigurationIssue",
    "DatabaseSettings",
    "LogLevel",
    "Settings",
    "SupervisorClientSettings",
    "UserbotProcessSettings",
    "UserbotSettings",
    "schema_for_process",
)
