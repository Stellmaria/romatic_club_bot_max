import asyncio
import logging
import sys
from pathlib import Path
from time import perf_counter

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

ROOT = Path(__file__).resolve().parents[2]  # E:\python\main\1
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from config import BOT_TOKEN, DISCUSSION_CHAT_ID, LUXURY_CHAT_ID
from db.db import (
    init_db,
    fetch,
    execute,
    close_db,
    add_user,
    set_subscription,
    set_luxury_status,
)

log = logging.getLogger("refresh_users")


def _clean_username(username: str | None) -> str | None:
    if not username:
        return None
    u = username.strip()
    if not u:
        return None
    return u.lstrip("@") or None


def make_full_name(first_name: str | None, last_name: str | None) -> str | None:
    full = " ".join(x for x in [first_name, last_name] if x).strip()
    return full or None


async def _normalize_users_defaults() -> None:
    """
    Подчищаем NULL-ы и приводим users к ожидаемым дефолтам, чтобы дальше
    не было сюрпризов типа bool(None) -> False.
    """
    await execute(
        """
        UPDATE public.users
        SET
            is_subscribed = COALESCE(is_subscribed, TRUE),
            is_luxury = COALESCE(is_luxury, FALSE),
            warnings_count = COALESCE(warnings_count, 0),
            is_trusted = COALESCE(is_trusted, FALSE),
            pm_opened = COALESCE(pm_opened, FALSE),
            created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            uid_verif_confirmed_count = COALESCE(uid_verif_confirmed_count, 0),
            uid_verif_rejected_count = COALESCE(uid_verif_rejected_count, 0)
        """
    )

    # Если pm_opened=TRUE, но timestamps пустые (бывает после миграций) – заполним только NULL.
    await execute(
        """
        UPDATE public.users
        SET
            first_pm_at = COALESCE(first_pm_at, NOW()),
            last_pm_at  = COALESCE(last_pm_at, NOW())
        WHERE COALESCE(pm_opened, FALSE) = TRUE
          AND (first_pm_at IS NULL OR last_pm_at IS NULL)
        """
    )


async def _recompute_warnings_count() -> None:
    """
    warnings_count = фактическое кол-во записей в user_warnings (если таблица есть).
    """
    try:
        await execute(
            """
            WITH agg AS (
                SELECT user_id, COUNT(*)::int AS cnt
                FROM public.user_warnings
                GROUP BY user_id
            ),
            all_rows AS (
                SELECT u.user_id, COALESCE(a.cnt, 0)::int AS cnt
                FROM public.users u
                LEFT JOIN agg a ON a.user_id = u.user_id
            )
            UPDATE public.users u
            SET warnings_count = r.cnt
            FROM all_rows r
            WHERE u.user_id = r.user_id
            """
        )
        log.info("Recomputed warnings_count from user_warnings.")
    except Exception:
        log.warning("Skip recompute warnings_count (no user_warnings or schema mismatch).", exc_info=True)


async def _recompute_uid_verif_stats() -> None:
    """
    uid_verif_* пересчитываем из uid_verification_confirmations (как у тебя в db.py:
    инкременты идут по counterparty_user_id при confirmed/rejected).
    """
    try:
        await execute(
            """
            WITH agg AS (
                SELECT
                    counterparty_user_id AS user_id,
                    COUNT(*) FILTER (WHERE status = 'confirmed')::int AS confirmed_cnt,
                    COUNT(*) FILTER (WHERE status = 'rejected')::int  AS rejected_cnt,
                    MAX(decided_at) FILTER (WHERE status = 'confirmed') AS last_confirmed_at,
                    MAX(decided_at) FILTER (WHERE status = 'rejected')  AS last_rejected_at
                FROM public.uid_verification_confirmations
                WHERE counterparty_user_id IS NOT NULL
                GROUP BY counterparty_user_id
            ),
            all_rows AS (
                SELECT
                    u.user_id,
                    COALESCE(a.confirmed_cnt, 0)::int AS confirmed_cnt,
                    COALESCE(a.rejected_cnt, 0)::int  AS rejected_cnt,
                    a.last_confirmed_at,
                    a.last_rejected_at
                FROM public.users u
                LEFT JOIN agg a ON a.user_id = u.user_id
            )
            UPDATE public.users u
            SET
                uid_verif_confirmed_count = r.confirmed_cnt,
                uid_verif_rejected_count  = r.rejected_cnt,
                uid_verif_last_confirmed_at = r.last_confirmed_at,
                uid_verif_last_rejected_at  = r.last_rejected_at
            FROM all_rows r
            WHERE u.user_id = r.user_id
            """
        )
        log.info("Recomputed uid_verif_* from uid_verification_confirmations.")
    except Exception:
        log.warning("Skip recompute uid_verif_* (no uid_verification_confirmations or schema mismatch).", exc_info=True)


async def try_refresh_from_pm(bot: Bot, user_id: int) -> bool:
    """
    Пытаемся получить данные из ЛС.
    Если получилось — обновляем username/full_name и поднимаем pm_opened=TRUE.
    Заодно аккуратно заполняем first_pm_at/last_pm_at, но НЕ трогаем существующие значения.
    """
    try:
        chat = await bot.get_chat(chat_id=user_id)
        username = _clean_username(getattr(chat, "username", None))
        full_name = make_full_name(getattr(chat, "first_name", None), getattr(chat, "last_name", None))

        await add_user(user_id, username, full_name)

        await execute(
            """
            UPDATE public.users
            SET
                pm_opened   = TRUE,
                first_pm_at = COALESCE(first_pm_at, NOW()),
                last_pm_at  = COALESCE(last_pm_at, NOW())
            WHERE user_id = $1
            """,
            user_id,
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def probe_chat_member(bot: Bot, chat_id: int, user_id: int) -> tuple[bool | None, dict | None]:
    """
    Возвращает:
      - is_member: True/False если удалось проверить, None если не удалось
      - user_payload: {username, full_name} если удалось достать user, иначе None
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        u = member.user

        status = getattr(member, "status", None)  # member/administrator/creator/restricted/left/kicked
        is_member = None
        if status is not None:
            is_member = status not in ("left", "kicked")

        username = _clean_username(getattr(u, "username", None))
        full_name = make_full_name(getattr(u, "first_name", None), getattr(u, "last_name", None))

        return is_member, {"username": username, "full_name": full_name}
    except (TelegramBadRequest, TelegramForbiddenError):
        return None, None


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    await init_db()
    bot = Bot(BOT_TOKEN)

    try:
        log.info("Starting refresh pass...")

        # 0) Подлечим users перед стартом
        await _normalize_users_defaults()

        log.info("Fetching users from DB...")
        t0 = perf_counter()
        rows = await asyncio.wait_for(
            fetch(
                """
                SELECT
                    user_id,
                    COALESCE(is_subscribed, TRUE) AS is_subscribed,
                    COALESCE(is_luxury, FALSE)    AS is_luxury,
                    COALESCE(pm_opened, FALSE)    AS pm_opened
                FROM public.users
                ORDER BY user_id
                """
            ),
            timeout=60,
        )
        dt = perf_counter() - t0

        users = [
            {
                "user_id": int(r["user_id"]),
                "is_subscribed": bool(r["is_subscribed"]),
                "is_luxury": bool(r["is_luxury"]),
                "pm_opened": bool(r["pm_opened"]),
            }
            for r in rows
        ]
        total = len(users)
        log.info("Users to refresh: %s (DB fetch %.2fs)", total, dt)

        names_updated = 0
        subs_changed = 0
        lux_changed = 0
        pm_promoted = 0

        for i, row in enumerate(users, start=1):
            uid = row["user_id"]

            # 1) Пытаемся обновить из ЛС (если доступно)
            pm_ok = await try_refresh_from_pm(bot, uid)
            if pm_ok and not row["pm_opened"]:
                pm_promoted += 1
                row["pm_opened"] = True

            # 2) Проверка подписки (discussion)
            is_sub, user_payload = await probe_chat_member(bot, DISCUSSION_CHAT_ID, uid)
            if user_payload is not None:
                await add_user(uid, user_payload["username"], user_payload["full_name"])
                names_updated += 1

            if is_sub is not None and is_sub != row["is_subscribed"]:
                await set_subscription(uid, is_sub)
                subs_changed += 1
                row["is_subscribed"] = is_sub

            # 3) Проверка лакшери (luxury)
            is_lux, user_payload2 = await probe_chat_member(bot, LUXURY_CHAT_ID, uid)
            if user_payload2 is not None:
                await add_user(uid, user_payload2["username"], user_payload2["full_name"])
                names_updated += 1

            if is_lux is not None and is_lux != row["is_luxury"]:
                await set_luxury_status(uid, is_lux)
                lux_changed += 1
                row["is_luxury"] = is_lux

            if i % 250 == 0 or i == total:
                log.info(
                    "Progress: %s/%s | names_updated=%s | subs_changed=%s | lux_changed=%s | pm_promoted=%s",
                    i,
                    total,
                    names_updated,
                    subs_changed,
                    lux_changed,
                    pm_promoted,
                )

            # анти-флуд
            if i % 25 == 0:
                await asyncio.sleep(0.7)
            else:
                await asyncio.sleep(0.06)

        # 4) Финальная “доклейка” того, что можно восстановить из БД
        await _recompute_warnings_count()
        await _recompute_uid_verif_stats()

        log.info(
            "Refresh done. names_updated=%s | subs_changed=%s | lux_changed=%s | pm_promoted=%s | total=%s",
            names_updated,
            subs_changed,
            lux_changed,
            pm_promoted,
            total,
        )

    except asyncio.TimeoutError:
        log.exception("DB fetch timed out (60s). Database/pool/query is stuck.")
        raise
    finally:
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())