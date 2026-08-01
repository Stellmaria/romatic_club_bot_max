import os

from bot.core.environment import PROJECT_ROOT, load_project_environment

load_project_environment()


def parse_int_list(env_val):
    return [int(x.strip()) for x in env_val.split(",") if x.strip().isdigit()]


def get_int_list(var: str) -> list[int]:
    """Получить список int, включая отрицательные значения."""

    result: list[int] = []
    for value in os.getenv(var, "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            result.append(int(value))
        except ValueError:
            pass
    return result


def get_int(var: str, default: int = 0) -> int:
    """Получить int из окружения с безопасным значением по умолчанию."""

    try:
        return int(os.getenv(var, str(default)))
    except (ValueError, TypeError):
        return default


def get_str(var: str, default: str = "") -> str:
    """Получить очищенное строковое значение из окружения."""

    return os.getenv(var, default).strip()


def get_bool(var: str, default: bool = False) -> bool:
    value = os.getenv(var)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


# --- Telegram ---
BOT_TOKEN: str = get_str("BOT_TOKEN")
ADMINS: list[int] = get_int_list("ADMINS")
AUCTION_CHANNEL_ID: int = get_int("AUCTION_CHANNEL_ID")
AUCTION_CHANNEL_USERNAME: str = get_str("AUCTION_CHANNEL_USERNAME")
DISCUSSION_CHAT_ID: int = get_int("DISCUSSION_CHAT_ID")

# --- База данных ---
DATABASE_URL: str = get_str("DATABASE_URL")
DB_AUTO_MIGRATE: bool = get_bool("DB_AUTO_MIGRATE", True)

# --- Админки/логи ---
# Compatibility constants retained for old imports. Shared passwords sent in
# Telegram were retired and these values intentionally remain empty.
ADMIN_SECRET: str = ""
ADMIN_LOG_CHATS: list[int] = get_int_list("ADMIN_LOG_CHATS")
ADMIN_LOG_CHAT_1: int = ADMIN_LOG_CHATS[0] if ADMIN_LOG_CHATS else 0
LOG_CHAT_ID: int = get_int("LOG_CHAT_ID")
LUXURY_CHAT_ID: int = get_int("LUXURY_CHAT_ID")
ADMINS_OWNERS = parse_int_list(os.getenv("ADMINS_OWNERS", ""))
LUXURY_CHAT_ID_LVL2: int = get_int("LUXURY_CHAT_ID_LVL2")

# --- MTProto (Telethon backfill) ---
TG_API_ID: int = get_int("TG_API_ID")
TG_API_HASH: str = get_str("TG_API_HASH")
TG_SESSION: str = get_str("TG_SESSION", "backfill.session")
BACKFILL_LIMIT_POSTS: int = get_int("BACKFILL_LIMIT_POSTS", 500)
AUTOBID_SET_PASSWORD = ""

# The typed settings module is the target configuration API. Environment
# bootstrap above intentionally runs first so both APIs see the same values.
from bot.core.settings import Settings, settings  # noqa: E402
