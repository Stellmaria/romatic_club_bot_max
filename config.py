import os

from dotenv import load_dotenv

load_dotenv()

def parse_int_list(env_val):
    return [int(x.strip()) for x in env_val.split(",") if x.strip().isdigit()]

def get_int_list(var: str) -> list[int]:
    """
    Получить список int из переменной окружения, поддерживает отрицательные числа.
    """
    res = []
    for x in os.getenv(var, "").split(","):
        x = x.strip()
        if x:
            try:
                res.append(int(x))
            except ValueError:
                pass
    return res


def get_int(var: str, default: int = 0) -> int:
    """
    Получить int из переменной окружения с дефолтом, безопасно.
    """
    try:
        return int(os.getenv(var, str(default)))
    except (ValueError, TypeError):
        return default


def get_str(var: str, default: str = "") -> str:
    """
    Получить str из переменной окружения с дефолтом и trim.
    """
    return os.getenv(var, default).strip()


def get_bool(var: str, default: bool = False) -> bool:
    value = os.getenv(var)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


# --- Telegram ---
BOT_TOKEN: str = get_str("BOT_TOKEN")  # Токен бота
ADMINS: list[int] = get_int_list("ADMINS")  # Список ID админов
AUCTION_CHANNEL_ID: int = get_int("AUCTION_CHANNEL_ID")  # ID аукционного канала
AUCTION_CHANNEL_USERNAME: str = get_str("AUCTION_CHANNEL_USERNAME")  # username канала (без @)
DISCUSSION_CHAT_ID: int = get_int("DISCUSSION_CHAT_ID")  # ID чата для обсуждений

# --- База данных ---
DATABASE_URL: str = get_str("DATABASE_URL")  # Строка подключения к БД
DB_AUTO_MIGRATE: bool = get_bool("DB_AUTO_MIGRATE", True)

# --- Админки/логи ---
ADMIN_SECRET: str = get_str("ADMIN_SECRET")  # Пароль для админ-команд
ADMIN_LOG_CHATS: list[int] = get_int_list("ADMIN_LOG_CHATS")  # Чаты для логирования действий админов
ADMIN_LOG_CHAT_1: int = ADMIN_LOG_CHATS[0] if ADMIN_LOG_CHATS else 0  # Первый лог-чат (устар.)
LOG_CHAT_ID: int = get_int("LOG_CHAT_ID")  # Отдельный лог-чат (не используется)
LUXURY_CHAT_ID: int = get_int("LUXURY_CHAT_ID")  # ID VIP-чата
ADMINS_OWNERS = parse_int_list(os.getenv("ADMINS_OWNERS", ""))
LUXURY_CHAT_ID_LVL2: int = get_int("LUXURY_CHAT_ID_LVL2")  # ID VIP-чата для Лакшери 2 уровня

# --- MTProto (Telethon backfill) ---
TG_API_ID: int = get_int("TG_API_ID")
TG_API_HASH: str = get_str("TG_API_HASH")
TG_SESSION: str = get_str("TG_SESSION", "backfill.session")
BACKFILL_LIMIT_POSTS: int = get_int("BACKFILL_LIMIT_POSTS", 500)
AUTOBID_SET_PASSWORD = os.getenv("AUTOBID_SET_PASSWORD", "").strip()

# The typed settings module is the target configuration API.  Keep these
# imports here while legacy modules still import constants from ``config``.
# ``load_dotenv()`` above intentionally runs first, so Settings sees the same
# process environment as the legacy constants.
from bot.core.environment import PROJECT_ROOT  # noqa: E402
from bot.core.settings import Settings, settings  # noqa: E402
