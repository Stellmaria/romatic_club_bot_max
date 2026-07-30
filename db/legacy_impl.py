import logging
from datetime import date, datetime
from typing import Dict, Callable, Iterable
from typing import List, Tuple
from typing import Optional, Any
from zoneinfo import ZoneInfo
from datetime import date as _date
import asyncpg
from bot.uid_crypto import (
    mask_uid,
    mask_uid_by_last4,
    norm_uid,
    uid_decrypt,
    uid_encrypt,
    uid_hash,
    uid_last4,
)

from db.types import Owner
from bot.core.legacy_config import DATABASE_URL, DB_AUTO_MIGRATE
from db.migrator import apply_migrations

logger = logging.getLogger("auction_bot")

db_pool: Optional[asyncpg.Pool] = None


# db.py

async def fetchall(query: str, *args):
    """
    Возвращает список dict-строк.
    Совместимо с твоим стилем fetchrow()/execute() через global db_pool.
    """
    global db_pool
    if db_pool is None:
        raise RuntimeError("db_pool is not initialized")

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def get_db_pool() -> asyncpg.Pool:
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан в .env")

    if db_pool is None:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
            logger.info("Database pool initialized")
        except Exception as e:
            logger.error(f"Не удалось создать пул соединений с БД: {e}")
            raise
    return db_pool


def require_db_pool(func: Callable) -> Callable:
    async def wrapper(*args, **kwargs):
        global db_pool
        if db_pool is None:
            logger.error("Database pool not initialized!")
            raise RuntimeError("Database pool not initialized!")
        return await func(*args, **kwargs)

    return wrapper


async def init_db() -> None:
    pool = await get_db_pool()
    if not DB_AUTO_MIGRATE:
        logger.warning("Автоматические миграции отключены: DB_AUTO_MIGRATE=0")
        return

    try:
        await apply_migrations(pool)
        logger.info("Database startup complete")
    except Exception:
        logger.exception("Database migration failed")
        await close_db()
        raise


async def close_db() -> None:
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None
        logger.info("Database pool closed")


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args) -> Optional[asyncpg.Record]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args) -> Any:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


@require_db_pool
async def add_user(user_id: int, username: str, full_name: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                               INSERT INTO users (user_id, username, full_name)
                               VALUES ($1, $2, $3)
                               ON CONFLICT (user_id) DO UPDATE
                                   SET username  = EXCLUDED.username,
                                       full_name = EXCLUDED.full_name
                               """, user_id, username, full_name)
    except Exception as e:
        logger.error(f"Error adding user {user_id}: {e}")


@require_db_pool
async def set_subscription(user_id: int, value: bool) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_subscribed = $1 WHERE user_id = $2",
                value, user_id
            )
    except Exception as e:
        logger.error(f"Error setting subscription for user {user_id}: {e}")


@require_db_pool
async def is_subscribed(user_id: int) -> Optional[bool]:
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT is_subscribed FROM users WHERE user_id = $1", user_id
            )
    except Exception as e:
        logger.error(f"Error checking subscription for user {user_id}: {e}")
        return None


@require_db_pool
async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, username, full_name FROM users WHERE user_id = $1", user_id
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return None


@require_db_pool


@require_db_pool
async def set_luxury_status(user_id: int, is_luxury: bool) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_luxury = $1 WHERE user_id = $2", is_luxury, user_id
            )
    except Exception as e:
        logger.error(f"Error setting luxury status for user {user_id}: {e}")


@require_db_pool
async def add_auction(card_name: str, hero_name: str, image_id: str, owner_id: int,
                      start_price: int, currency: str, start_time: datetime, end_time: datetime,
                      status: str, comment: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            # Сначала вставляем сам аукцион (без owner_id!)
            row = await conn.fetchrow(
                """INSERT INTO auctions
                   (card_name, hero_name, image_id, start_price, currency, start_time, end_time, status, comment)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   RETURNING auction_id
                """,
                card_name, hero_name, image_id, start_price, currency, start_time, end_time, status, comment
            )
            auction_id = row["auction_id"]
            # Затем добавляем связь с владельцем
            await conn.execute(
                "INSERT INTO auction_owners (auction_id, user_id) VALUES ($1, $2)",
                auction_id, owner_id
            )
    except Exception as e:
        logger.error(f"Error adding auction: {e}")


@require_db_pool
async def get_lots_by_owner(user_id: int) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.*
                FROM auctions a
                         JOIN auction_owners ao ON a.auction_id = ao.auction_id
                WHERE ao.user_id = $1
                ORDER BY a.start_time DESC
                """,
                user_id
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting lots by owner {user_id}: {e}")
        return []


@require_db_pool
async def list_auctions(statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            if statuses:
                q = """
                    SELECT a.*, c.card_id
                    FROM auctions a
                             LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                    WHERE a.status = ANY ($1::varchar[])
                    ORDER BY a.start_time, a.auction_id
                    """
                return [dict(r) for r in await conn.fetch(q, statuses)]
            else:
                q = """
                    SELECT a.*, c.card_id
                    FROM auctions a
                             LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                    ORDER BY a.start_time \
                    """
                return [dict(r) for r in await conn.fetch(q)]

    except Exception as e:
        logger.error(f"Error listing auctions: {e}")
        return []


@require_db_pool
async def get_pending_auctions(
        auction_kind: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return every application that still requires moderator scheduling.

    Historical code filtered only the literal ``pending`` status.  Several
    creation/moderation paths legitimately use ``draft``, ``moderation`` or
    ``approved`` before a slot is assigned, which made real submissions vanish
    from the admin screen even though they remained in PostgreSQL.
    """

    review_statuses = ("draft", "moderation", "pending", "approved")
    args: List[Any] = [list(review_statuses)]
    where = ["a.status = ANY($1::text[])"]

    if auction_kind:
        where.append("a.auction_kind=$%d" % (len(args) + 1))
        args.append(auction_kind)

    where_sql = " AND ".join(where)

    args.append(int(limit))
    limit_i = len(args)
    args.append(int(offset))
    offset_i = len(args)

    sql = f"""
        SELECT
            a.auction_id,
            a.status,
            a.card_name,
            a.hero_name,
            COALESCE(NULLIF(a.image_id, ''), c.image_id) AS image_id,
            a.start_price,
            a.currency,
            a.comment,
            a.created_at,
            a.proof_photo_id,
            a.craft_uid_possible,
            a.auction_kind,
            COALESCE(a.card_id, c.card_id) AS card_id,
            c.num AS card_num,
            c.deck_id,
            d.name AS deck_name,
            c.rarity,
            c.obtain_type,
            c.obtain_amount,
            c.story,
            c.quote,
            c.image_id AS card_image_id
        FROM public.auctions a
        LEFT JOIN LATERAL (
            SELECT c0.*
            FROM public.cards c0
            WHERE c0.card_id = a.card_id
               OR (
                    a.card_id IS NULL
                    AND lower(trim(c0.card_name)) = lower(trim(a.card_name))
                    AND lower(trim(coalesce(c0.hero_name, ''))) =
                        lower(trim(coalesce(a.hero_name, '')))
                  )
            ORDER BY (c0.card_id = a.card_id) DESC, c0.card_id
            LIMIT 1
        ) c ON TRUE
        LEFT JOIN public.decks d ON d.id = c.deck_id
        WHERE {where_sql}
        ORDER BY a.created_at ASC, a.auction_id ASC
        LIMIT ${limit_i} OFFSET ${offset_i}
    """

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


@require_db_pool
async def get_auctions_by_date(selected_date: date) -> List[Dict[str, Any]]:
    SQL = """
          SELECT a.auction_id,
                 a.card_name,
                 a.hero_name,
                 a.start_time,
                 a.end_time,
                 a.currency,
                 a.status,
                 a.message_id,
                 c.card_id,
                 c.deck_id
          FROM public.auctions a
                   LEFT JOIN LATERAL (
              SELECT candidate.card_id, candidate.deck_id
              FROM public.cards candidate
              WHERE lower(trim(candidate.card_name)) = lower(trim(a.card_name))
                AND lower(trim(coalesce(candidate.hero_name, ''))) =
                    lower(trim(coalesce(a.hero_name, '')))
              ORDER BY candidate.card_id
              LIMIT 1
              ) c ON true
          WHERE CASE
                  WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                    THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                  ELSE a.start_time::date
                END = $1
            AND a.status IN ('approved', 'scheduled', 'publishing', 'active', 'finished')
          ORDER BY a.start_time, a.auction_id
          """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(SQL, selected_date)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting auctions by date {selected_date}: {e}")
        return []


@require_db_pool
async def update_auction_status(auction_id: int, new_status: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE auctions SET status = $1 WHERE auction_id = $2",
                new_status, auction_id
            )
    except Exception as e:
        logger.error(f"Error updating auction status for {auction_id}: {e}")


@require_db_pool
async def update_lot_field(lot_id: int, field: str, value: Any) -> None:
    try:
        async with db_pool.acquire() as conn:
            if field == "start_time":
                await conn.execute(
                    "UPDATE auctions SET start_time=$1, notified_card_subs=false WHERE auction_id=$2",
                    value, lot_id
                )
            else:
                await conn.execute(
                    f"UPDATE auctions SET {field}=$1 WHERE auction_id=$2",
                    value, lot_id
                )
    except Exception as e:
        logger.error(f"Error updating lot field {field} for lot {lot_id}: {e}")


@require_db_pool
async def delete_lot(lot_id: int) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM auctions WHERE auction_id = $1", lot_id)
    except Exception as e:
        logger.error(f"Error deleting lot {lot_id}: {e}")


@require_db_pool
async def get_occupied_slots(selected_date: date) -> List[Tuple]:
    """Return occupied schedule slots as naive Moscow wall-clock times."""
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT start_time, end_time
                FROM auctions
                WHERE CASE
                        WHEN pg_typeof(start_time)::text = 'timestamp with time zone'
                          THEN (start_time AT TIME ZONE 'Europe/Moscow')::date
                        ELSE start_time::date
                      END = $1
                  AND status IN ('approved', 'scheduled', 'publishing', 'active')
                """,
                selected_date,
            )
            from bot.core.time import to_moscow_wall
            return [
                (
                    to_moscow_wall(row['start_time']).time(),
                    to_moscow_wall(row['end_time']).time(),
                )
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Error getting occupied slots for {selected_date}: {e}")
        return []


@require_db_pool
async def update_auction_time_status(auction_id: int, start_time: datetime, end_time: datetime, status: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE auctions
                SET start_time = $1,
                    end_time   = $2,
                    status     = $3
                WHERE auction_id = $4
                """,
                start_time, end_time, status, auction_id
            )
    except Exception as e:
        logger.error(f"Error updating auction time/status for {auction_id}: {e}")


@require_db_pool
async def schedule_auction_time_if_available(
        auction_id: int,
        start_time: datetime,
        end_time: datetime,
        status: str = "scheduled",
) -> tuple[bool, Optional[int]]:
    """
    Атомарно закрепляет получасовой слот за лотом.

    Конфликтом считается только другой лот с той же картой, тем же
    владельцем и тем же временем начала. Соседний слот через 30 минут
    разрешён, даже если приём ставок у предыдущего лота продолжается до
    последней секунды конечной минуты. Та же карта другого владельца также
    разрешена по действующим правилам расписания.

    Возвращает ``(True, None)`` при успехе или
    ``(False, conflict_auction_id)`` при конфликте.
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            lot = await conn.fetchrow(
                """
                SELECT card_id, card_name, hero_name
                FROM public.auctions
                WHERE auction_id = $1
                FOR UPDATE
                """,
                auction_id,
            )
            if not lot:
                return False, None

            card_id = int(lot["card_id"]) if lot["card_id"] is not None else None
            card_name = str(lot["card_name"] or "").strip()
            hero_name = str(lot["hero_name"] or "").strip()
            identity = str(card_id) if card_id is not None else (
                f"{hero_name.casefold()}|{card_name.casefold()}"
            )
            lock_key = f"auction-slot|{identity}|{start_time:%Y-%m-%dT%H:%M}"
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
                lock_key,
            )

            conflict_id = await conn.fetchval(
                """
                SELECT a.auction_id
                FROM public.auctions a
                WHERE a.auction_id <> $1
                  AND a.status IN ('approved', 'scheduled', 'publishing', 'active')
                  AND (
                      ($2::integer IS NOT NULL AND a.card_id = $2)
                      OR (
                          lower(trim(a.card_name)) = lower(trim($3))
                          AND lower(trim(coalesce(a.hero_name, '')))
                              = lower(trim(coalesce($4, '')))
                      )
                  )
                  AND date_trunc('minute', a.start_time)
                      = date_trunc('minute', $5::timestamp)
                  AND EXISTS (
                      SELECT 1
                      FROM public.auction_owners existing_owner
                      JOIN public.auction_owners current_owner
                        ON current_owner.auction_id = $1
                       AND current_owner.user_id = existing_owner.user_id
                      WHERE existing_owner.auction_id = a.auction_id
                  )
                ORDER BY a.auction_id
                LIMIT 1
                """,
                auction_id,
                card_id,
                card_name,
                hero_name,
                start_time,
            )
            if conflict_id is not None:
                return False, int(conflict_id)

            result = await conn.execute(
                """
                UPDATE public.auctions
                SET start_time = $1,
                    end_time   = $2,
                    status     = $3
                WHERE auction_id = $4
                """,
                start_time,
                end_time,
                status,
                auction_id,
            )
            return result == "UPDATE 1", None


@require_db_pool
async def is_admin(user_id: int) -> bool:
    try:
        async with db_pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT 1 FROM admins WHERE user_id = $1", user_id
            ))
    except Exception as e:
        logger.error(f"Error checking admin status for user {user_id}: {e}")
        return False


@require_db_pool
async def add_admin(user_id: int, username: str = None, added_by: int = None) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admins (user_id, username, added_by)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """, user_id, username, added_by
            )
    except Exception as e:
        logger.error(f"Error adding admin {user_id}: {e}")


@require_db_pool
async def remove_admin(user_id: int) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM admins WHERE user_id = $1", user_id
            )
    except Exception as e:
        logger.error(f"Error removing admin {user_id}: {e}")


@require_db_pool
async def list_admins() -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username FROM admins ORDER BY added_at"
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error listing admins: {e}")
        return []


@require_db_pool
async def log_admin_action(
        user_id: Optional[int] = None,
        action_type: str = "",
        auction_id: Optional[int] = None,
        details: str = "",
        admin_id: Optional[int] = None,
) -> None:
    """Записывает действие в audit_logs.

    Исторически в коде использовались оба имени параметра: user_id и admin_id.
    Чтобы не ловить TypeError, поддерживаем оба (приоритет у user_id).
    """
    uid = user_id if user_id is not None else admin_id
    if uid is None:
        # Не ломаем поток заявок из-за логов: просто пропускаем.
        logging.warning("log_admin_action: user_id/admin_id is None; skip")
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.audit_logs (user_id, action_type, auction_id, details)
            VALUES ($1, $2, $3, $4)
            """,
            uid,
            action_type,
            auction_id,
            details or "",
        )


@require_db_pool
async def get_admin_logs(limit: int = 10, log_date: str | None = None, admin_id: int | None = None) -> list[dict]:
    where = []
    params: list[Any] = []
    if log_date:
        where.append(f'DATE("timestamp") = ${len(params) + 1}')
        params.append(log_date)
    if admin_id:
        where.append(f"user_id = ${len(params) + 1}")
        params.append(admin_id)

    sql = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "timestamp" DESC'
    if not log_date:
        sql += f" LIMIT {limit}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


@require_db_pool
async def add_card_db_func(deck_id, card_name, num, hero_name, image_id, rarity, story, quote):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cards (deck_id, card_name, num, hero_name, image_id, rarity, story, quote)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                deck_id, card_name, num, hero_name, image_id, rarity, story, quote
            )
    except Exception as e:
        logger.error(f"Ошибка добавления карты: {e}")


@require_db_pool
async def get_cards_by_deck(deck_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM cards WHERE deck_id = $1 ORDER BY num", deck_id)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения карт по deck_id={deck_id}: {e}")
        return []


@require_db_pool
async def get_card_by_id(card_id: int):
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM cards WHERE card_id = $1", card_id)
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка получения карты по card_id={card_id}: {e}")
        return None


@require_db_pool
async def get_all_cards():
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM cards ORDER BY deck_id, num")
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения всех карт: {e}")
        return []


@require_db_pool
async def search_cards(query: str):
    try:
        async with db_pool.acquire() as conn:
            pattern = f"%{query}%"
            rows = await conn.fetch("""
                                    SELECT *
                                    FROM cards
                                    WHERE hero_name ILIKE $1
                                       OR story ILIKE $1
                                       OR quote ILIKE $1
                                    ORDER BY deck_id, num
                                    """, pattern)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка поиска карт: {e}")
        return []


@require_db_pool
async def update_card_field(card_id: int, field: str, value):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(f"UPDATE cards SET {field} = $1 WHERE card_id = $2", value, card_id)
    except Exception as e:
        logger.error(f"Ошибка обновления поля {field} для карты {card_id}: {e}")


@require_db_pool
async def delete_card_by_id(card_id: int):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM cards WHERE card_id = $1", card_id)
    except Exception as e:
        logger.error(f"Ошибка удаления карты {card_id}: {e}")


@require_db_pool
async def add_user_subscription(user_id: int, card_id: int, *_ignored):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_subscriptions (user_id, card_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, card_id) DO NOTHING
                """,
                user_id, card_id
            )
    except Exception as e:
        logger.error(f"Error adding user subscription: {e}")


@require_db_pool
async def get_user_subscriptions(user_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM user_subscriptions WHERE user_id = $1", user_id
            )
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        return []


@require_db_pool
async def remove_user_subscription(sub_id: int, user_id: int):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_subscriptions WHERE id = $1 AND user_id = $2", sub_id, user_id
            )
    except Exception as e:
        logger.error(f"Error removing subscription: {e}")


async def get_lot_by_id(auction_id: int) -> Optional[Dict[str, Any]]:
    def _infer_any_rarity_from_title(title: str) -> str | None:
        t = (title or "").strip().lower()
        if "бронз" in t:
            return "bronze"
        if "сереб" in t:
            return "silver"
        if "золот" in t:
            return "gold"
        if "алмаз" in t or "эпик" in t:
            return "diamond"
        return None

    def _is_any_lot(title: str) -> bool:
        t = (title or "").strip().lower()
        return ("любая" in t) or ("любой" in t)

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.*,
                       c.card_id,
                       c.num      AS card_num,
                       c.deck_id,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount,
                       c.story,
                       c.quote,
                       c.image_id AS card_image_id,
                       d.name     AS deck_name
                FROM public.auctions a
                         LEFT JOIN public.cards c
                                   ON lower(trim(c.card_name)) = lower(trim(a.card_name))
                                       AND
                                      lower(trim(coalesce(c.hero_name, ''))) = lower(trim(coalesce(a.hero_name, '')))
                         LEFT JOIN public.decks d
                                   ON d.id = c.deck_id
                WHERE a.auction_id = $1
                """,
                auction_id,
            )
            if not row:
                return None

            lot = dict(row)

            # --- ДОП.ИНФА ДЛЯ ЛОТОВ "ЛЮБАЯ ..." (которые не матчатся на cards) ---
            title = str(lot.get("card_name") or "").strip()
            if (not lot.get("card_id")) and _is_any_lot(title):
                any_rarity = _infer_any_rarity_from_title(title)

                deck_rows = await conn.fetch(
                    """
                    SELECT DISTINCT c.deck_id
                    FROM public.cards c
                    WHERE c.deck_id IS NOT NULL
                      AND ($1::text IS NULL OR lower(c.rarity) = lower($1))
                    ORDER BY c.deck_id
                    """,
                    any_rarity,
                )

                lot["any_rarity"] = any_rarity
                lot["possible_deck_ids"] = [int(r["deck_id"]) for r in deck_rows if r["deck_id"] is not None]

            return lot

    except Exception as e:
        logger.error(f"Error getting lot by id {auction_id}: {e}")
        return None


@require_db_pool
async def get_auctions_by_day(chosen_date: date) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                                    SELECT *
                                    FROM auctions
                                    WHERE start_time::date = $1
                                    ORDER BY start_time
                                    """, chosen_date)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения аукционов на день: {e}")
        return []


@require_db_pool


@require_db_pool
async def get_user_id_by_username(username: str) -> int | None:
    uname = username.strip().lstrip("@").lower()
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM users WHERE LOWER(username) = $1",
                uname
            )
            return row['user_id'] if row else None
    except Exception as e:
        logger.error(f"Error getting user_id by username {username}: {e}")
        return None


@require_db_pool
async def get_users_by_ids(user_ids: list[int]) -> list[dict]:
    if not user_ids:
        return []
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name FROM users WHERE user_id = ANY($1)", user_ids
            )
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения пользователей по ids: {e}")
        return []


@require_db_pool
async def count_new_users():
    try:
        async with db_pool.acquire() as conn:
            today = date.today()
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at::date = $1", today
            )
    except Exception as e:
        logger.error(f"Ошибка подсчёта новых пользователей: {e}")
        return 0


@require_db_pool
async def count_new_auctions():
    try:
        async with db_pool.acquire() as conn:
            today = date.today()
            return await conn.fetchval(
                "SELECT COUNT(*) FROM auctions WHERE created_at::date = $1", today
            )
    except Exception as e:
        logger.error(f"Ошибка подсчёта новых аукционов: {e}")
        return 0


@require_db_pool
async def get_settings(user_id: int) -> Optional[Dict[str, bool]]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                                      SELECT notify_auction_start,
                                             notify_bid_reminder,
                                             notify_auction_end,
                                             notify_daily_today
                                      FROM settings
                                      WHERE user_id = $1
                                      """, user_id)
            if row:
                return {
                    "notify_auction_start": bool(row["notify_auction_start"]),
                    "notify_bid_reminder": bool(row["notify_bid_reminder"]),
                    "notify_auction_end": bool(row["notify_auction_end"]),
                    "notify_daily_today": bool(row["notify_daily_today"]),
                }
            # дефолты, когда строки ещё нет
            return {
                "notify_auction_start": True,
                "notify_bid_reminder": True,
                "notify_auction_end": True,
                "notify_daily_today": True,
            }
    except Exception as e:
        logger.error(f"Ошибка получения настроек для пользователя {user_id}: {e}")
        return None


@require_db_pool
async def set_settings(user_id: int, **kwargs):
    fields = [
        "notify_auction_start",
        "notify_bid_reminder",
        "notify_auction_end",
        "notify_daily_today",
    ]
    current = await get_settings(user_id) or {}
    data = {f: kwargs.get(f, current.get(f)) for f in fields}
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                               INSERT INTO settings (user_id,
                                                     notify_auction_start,
                                                     notify_bid_reminder,
                                                     notify_auction_end,
                                                     notify_daily_today)
                               VALUES ($1, $2, $3, $4, $5)
                               ON CONFLICT (user_id) DO UPDATE
                                   SET notify_auction_start = EXCLUDED.notify_auction_start,
                                       notify_bid_reminder  = EXCLUDED.notify_bid_reminder,
                                       notify_auction_end   = EXCLUDED.notify_auction_end,
                                       notify_daily_today   = EXCLUDED.notify_daily_today
                               """,
                               user_id,
                               bool(data["notify_auction_start"]),
                               bool(data["notify_bid_reminder"]),
                               bool(data["notify_auction_end"]),
                               bool(data["notify_daily_today"]),
                               )
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек для пользователя {user_id}: {e}")


@require_db_pool
async def add_bid(auction_id: int, bidder_id: int, amount: int, discussion_message_id: int) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bids (auction_id, bidder_id, amount, discussion_message_id)
                VALUES ($1, $2, $3, $4)
                """,
                auction_id, bidder_id, amount, discussion_message_id
            )
    except Exception as e:
        logger.error(f"Ошибка добавления ставки: {e}")


async def get_bid_auction_by_discussion_id(discussion_message_id: int) -> int | None:
    """Find an auction through a bid reply message for admin lifecycle commands."""
    row = await fetchrow(
        "SELECT auction_id FROM public.bids WHERE discussion_message_id = $1",
        discussion_message_id,
    )
    return int(row["auction_id"]) if row and row.get("auction_id") else None


async def mark_user_private_chat_opened(user_id: int) -> None:
    await execute(
        """
        UPDATE users
        SET pm_opened = TRUE,
            first_pm_at = COALESCE(first_pm_at, NOW()),
            last_pm_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
    )


async def mark_user_private_chat_closed(user_id: int) -> None:
    await execute(
        "UPDATE users SET pm_opened = FALSE, last_pm_at = NOW() WHERE user_id = $1",
        user_id,
    )


async def get_all_decks() -> list[dict]:
    rows = await fetch(
        """
        SELECT id   AS deck_id,
               name AS deck_name,
               deck_type
        FROM public.decks
        ORDER BY id ASC
        """
    )
    return [dict(r) for r in rows]


async def get_delete_request(request_id: int):
    return await fetchrow("SELECT * FROM delete_requests WHERE id = $1", request_id)


async def update_delete_request_status(request_id: int, status: str):
    await execute("UPDATE delete_requests SET status = $1 WHERE id = $2", status, request_id)


async def get_all_trusted_users():
    rows = await fetch("""
                       SELECT u.username, u.user_id, u.is_luxury
                       FROM users u
                       WHERE u.is_trusted = true
                         AND u.username IS NOT NULL
                       UNION
                       SELECT t.username, NULL as user_id, NULL as is_luxury
                       FROM trusted_usernames t
                       WHERE NOT EXISTS (SELECT 1 FROM users u2 WHERE u2.username = t.username AND u2.is_trusted = true)
                       ORDER BY username
                       """)
    return rows


async def is_luxury_user(user_id: int) -> bool:
    row = await fetchrow("SELECT is_luxury FROM users WHERE user_id = $1", user_id)
    if not row:
        return False
    return bool(row["is_luxury"])


@require_db_pool
async def get_lot_owners(lot_id: int) -> list[Owner]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury,
                       u.is_trusted
                FROM public.auction_owners ao
                         JOIN public.users u ON u.user_id = ao.user_id
                WHERE ao.auction_id = $1
                ORDER BY COALESCE(NULLIF(u.username, ''), u.full_name, u.user_id::text)
                """,
                lot_id,
            )
            result: list[Owner] = [
                {
                    "user_id": int(r["user_id"]),
                    "username": (r["username"] or None),
                    "full_name": (r["full_name"] or None),
                    "is_luxury": bool(r["is_luxury"]),
                    "is_trusted": bool(r["is_trusted"]),
                }
                for r in rows
            ]
            return result
    except Exception as e:
        logger.error(f"Error getting lot owners for lot {lot_id}: {e}")
        return []


async def get_last_nonempty_card_deck_id() -> int:
    """Return the latest deck represented by a card without leaking SQL to handlers."""
    row = await fetchrow("SELECT COALESCE(MAX(deck_id), 0) AS mx FROM cards")
    try:
        return int(row["mx"]) if row and row["mx"] is not None else 0
    except (KeyError, TypeError, IndexError):
        return int(row[0]) if row else 0


@require_db_pool
async def list_pending_delete_requests(
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
) -> list[dict]:
    kind = (kind or "").strip().lower() or None

    async with db_pool.acquire() as conn:
        if kind:
            rows = await conn.fetch(
                """
                SELECT dr.id, dr.lot_id, dr.user_id, dr.reason, dr.created_at, dr.status
                FROM public.delete_requests dr
                         LEFT JOIN public.auctions a ON a.auction_id = dr.lot_id
                WHERE dr.status = 'pending'
                  AND COALESCE(a.auction_kind, 'standard') = $1
                ORDER BY dr.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                kind, limit, offset,
            )
            return [dict(r) for r in rows]

        rows = await conn.fetch(
            """
            SELECT id, lot_id, user_id, reason, created_at, status
            FROM public.delete_requests
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        return [dict(r) for r in rows]


async def set_trusted_status(user_id: int, is_trusted: bool):
    await execute(
        "UPDATE users SET is_trusted = $2 WHERE user_id = $1",
        user_id, is_trusted
    )


async def get_all_users():
    return await fetch("SELECT user_id, username, is_luxury FROM users")


async def get_card_by_num(num: int):
    row = await fetchrow("SELECT * FROM cards WHERE num = $1", num)
    return row


@require_db_pool
async def get_audit_logs(limit: int = 20, log_date: date | None = None, admin_id: int | None = None):
    query = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs WHERE true'
    params: list[Any] = []
    if log_date:
        query += ' AND "timestamp"::date = $%d' % (len(params) + 1)
        params.append(log_date)
    if admin_id:
        query += ' AND user_id = $%d' % (len(params) + 1)
        params.append(admin_id)
    query += ' ORDER BY "timestamp" DESC LIMIT $%d' % (len(params) + 1)
    params.append(limit)
    return await fetch(query, *params)


@require_db_pool
async def add_pending_auction(
        card_name: str,
        hero_name: str,
        image_id: str,
        start_price: int,
        currency: str,
        owner_id: int,
        accepted_currencies: Optional[list[str]] = None,
        custom_offer_terms: Optional[str] = None,
        comment: str = "",
        auction_kind: str = "standard",
        proof_photo_id: Optional[str] = None,
        craft_uid_possible: bool | None = None,
) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.auctions
            (card_name, hero_name, image_id, start_price, start_time, end_time,
             status, created_at, currency, accepted_currencies, custom_offer_terms, comment, auction_kind, proof_photo_id, craft_uid_possible)
            VALUES ($1, $2, $3, $4, NOW(), NOW(),
                    'pending', NOW(), $5, $6, $7, $8, $9, $10, $11)
            RETURNING auction_id
            """,
            card_name,
            hero_name,
            image_id,
            int(start_price),
            currency,
            list(accepted_currencies or [currency]),
            (custom_offer_terms or "").strip() or None,
            comment or "",
            auction_kind or "standard",
            proof_photo_id,
            craft_uid_possible,
        )

        auction_id = int(row["auction_id"])

        await conn.execute(
            """
            INSERT INTO public.auction_owners (auction_id, user_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            auction_id,
            int(owner_id),
        )

        return auction_id


@require_db_pool
async def get_bids_for_auction(auction_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT b.*
                FROM public.bids b
                JOIN public.auctions a ON a.auction_id = b.auction_id
                WHERE b.auction_id = $1
                ORDER BY
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse' THEN b.amount END ASC,
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse' THEN b.amount END DESC,
                    b.placed_at ASC,
                    b.bid_id ASC
                """,
                auction_id
            )
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[ERROR] Ошибка получения ставок по лоту: {e}")
        return []


@require_db_pool
async def auction_exists(auction_id: int) -> bool:
    async with db_pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM auctions WHERE auction_id = $1",
            auction_id
        ))


@require_db_pool
async def log_audit_action(*args: Any, **kwargs: Any) -> None:
    action = kwargs.pop("action", None)
    action_type = kwargs.pop("action_type", None)
    event = kwargs.pop("event", None)
    admin_id = kwargs.pop("admin_id", None)
    user_id = kwargs.pop("user_id", None)
    details = kwargs.pop("details", None)
    auction_id = kwargs.pop("auction_id", None)

    # если кто-то передал лишние kwargs (entity/entity_id и т.д.) не выбрасываем, добавим в details
    extra_kwargs = dict(kwargs)

    if args:
        if action is None and len(args) >= 1:
            action = args[0]
        if admin_id is None and len(args) >= 2:
            admin_id = args[1]
        if details is None and len(args) >= 3:
            details = args[2]
        if auction_id is None and len(args) >= 4:
            auction_id = args[3]
        if user_id is None and len(args) >= 5:
            user_id = args[4]

    name = (action or action_type or event or "unknown")
    uid = (user_id or admin_id)

    if auction_id is not None:
        try:
            if not await auction_exists(int(auction_id)):
                auction_id = None
        except Exception:
            auction_id = None

    if uid is None:
        uid = 0

    # ---- ВОТ ЭТО ТЕБЯ И СПАСАЕТ ----
    payload = details
    if extra_kwargs:
        if isinstance(payload, dict):
            payload = {**payload, **extra_kwargs}
        else:
            payload = {"details": payload, **extra_kwargs}

    if payload is None:
        details_str = ""
    elif isinstance(payload, str):
        details_str = payload
    else:
        # dict/list/что угодно -> JSON строка
        details_str = json.dumps(payload, ensure_ascii=False, default=str)

    await execute(
        """
        INSERT INTO audit_logs (user_id, action_type, auction_id, details)
        VALUES ($1, $2, $3, $4)
        """,
        int(uid), str(name), auction_id, details_str
    )

async def add_card(
        card_name: str,
        num: int,
        hero_name: str,
        image_id: str,
        rarity: str,
        deck_id: int,
        story: str,
        quote: str | None,
        *,
        gift_cups: int = 0,
        gift_diamonds: int = 0,
        obtain_type: str | None = None,  # 'diamonds' | 'tea' (enum obtain_type)
        obtain_amount: int | None = None
) -> None:
    # нормализация значений
    gift_cups = max(0, int(gift_cups or 0))
    gift_diamonds = max(0, int(gift_diamonds or 0))
    if obtain_amount is not None:
        obtain_amount = max(0, int(obtain_amount))

    # базовые колонки обязательные всегда
    columns = ["card_name", "num", "hero_name", "image_id", "rarity", "deck_id", "story", "quote"]
    values = [card_name, num, hero_name, image_id, rarity, deck_id, story, quote]

    if obtain_type is not None:
        columns.append("obtain_type");
        values.append(obtain_type)
    if obtain_amount is not None:
        columns.append("obtain_amount");
        values.append(obtain_amount)

    # плейсхолдеры $1..$N под asyncpg
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    sql = f"""
        INSERT INTO cards ({", ".join(columns)})
        VALUES ({placeholders})
    """

    await execute(sql, *values)


@require_db_pool
async def add_deck(deck_name: str) -> bool:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO decks (name) VALUES ($1)", deck_name)
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления колоды: {e}")
        return False


@require_db_pool
async def update_auction_currency(auction_id: int, currency: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE auctions SET currency = $1 WHERE auction_id = $2",
                currency, auction_id
            )
    except Exception as e:
        logger.error(f"Error updating currency for auction {auction_id}: {e}")


@require_db_pool
async def update_auction_price(auction_id: int, price: int) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE auctions SET start_price = $1 WHERE auction_id = $2",
                price, auction_id
            )
    except Exception as e:
        logger.error(f"Error updating price for auction {auction_id}: {e}")


@require_db_pool
async def add_delete_request(user_id: int, lot_id: int, reason: str):
    logger.info(f"Trying to add delete_request: user_id={user_id}, lot_id={lot_id}, reason={reason!r}")
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO delete_requests (user_id, lot_id, reason, status, created_at) VALUES ($1, $2, $3, 'pending', now())",
                user_id, lot_id, reason
            )
        logger.info(f"Delete request for lot_id={lot_id} from user_id={user_id} added successfully.")
    except Exception as e:
        logger.error(f"Ошибка создания заявки на удаление: {e}")
        raise


async def has_pending_delete_request(lot_id: int):
    req = await fetchrow("SELECT 1 FROM delete_requests WHERE lot_id = $1 AND status = 'pending'", lot_id)
    return req is not None


async def sync_trusted_status(user_id: int, username: str = None):
    if not username:
        return
    uname = username.lstrip("@")
    exists = await fetchval("SELECT 1 FROM trusted_usernames WHERE username = $1", uname)
    if exists:
        await set_trusted_status(user_id, True)
    else:
        await set_trusted_status(user_id, False)


@require_db_pool
async def get_cards_by_deck_id(deck_id: int):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id, card_name, hero_name, num, image_id, rarity, story, quote FROM cards WHERE deck_id = $1 ORDER BY num",
            deck_id
        )
        return [dict(row) for row in rows]


@require_db_pool
async def get_stats():
    try:
        async with db_pool.acquire() as conn:
            users_total = await conn.fetchval("SELECT COUNT(*) FROM users")
            auctions_total = await conn.fetchval("SELECT COUNT(*) FROM auctions")
            cards_total = await conn.fetchval("SELECT COUNT(*) FROM cards")
            bids_total = await conn.fetchval("SELECT COUNT(*) FROM bids")
            admins_total = await conn.fetchval("SELECT COUNT(*) FROM admins")
            luxury_total = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_luxury = TRUE")
            trusted_total = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_trusted = TRUE")
            return {
                "users_total": users_total,
                "auctions_total": auctions_total,
                "cards_total": cards_total,
                "bids_total": bids_total,
                "admins_total": admins_total,
                "luxury_total": luxury_total,
                "trusted_total": trusted_total,
            }
    except Exception as e:
        logger.error(f"Ошибка сбора статистики: {e}")
        return None


async def get_auction_owner_id(auction_id: int) -> int | None:
    row = await fetchrow("SELECT user_id FROM auction_owners WHERE auction_id = $1", auction_id)
    return row['user_id'] if row else None


@require_db_pool
async def add_warning(user_id: int, reason: str, message_id: int = None, details: str = None):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_warnings (user_id, reason, issued_at, details) VALUES ($1, $2, NOW(), $3)",
                user_id, reason, details
            )
            await conn.execute(
                "UPDATE users SET warnings_count = warnings_count + 1 WHERE user_id = $1",
                user_id
            )
    except Exception as e:
        print(f"[ERROR] Ошибка добавления предупреждения: {e}")


@require_db_pool
async def upsert_autobid(
        *,
        auction_id: int,
        target_user_id: int,
        target_username: str | None,
        max_amount: int,
        step: int,
        created_by: int,
        is_active: bool = True,
) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.autobids
            (auction_id, target_user_id, target_username, max_amount, step, is_active, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (auction_id, target_user_id)
                DO UPDATE SET target_username = EXCLUDED.target_username,
                              max_amount      = EXCLUDED.max_amount,
                              step            = EXCLUDED.step,
                              is_active       = EXCLUDED.is_active,
                              updated_at      = now()
            RETURNING *
            """,
            int(auction_id),
            int(target_user_id),
            target_username,
            int(max_amount),
            int(step),
            bool(is_active),
            int(created_by),
        )
        return dict(row) if row else None


@require_db_pool
async def get_top_bid_for_auction(auction_id: int) -> tuple[int | None, int | None]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.bidder_id, b.amount
            FROM public.bids b
            JOIN public.auctions a ON a.auction_id = b.auction_id
            WHERE b.auction_id = $1
            ORDER BY
                CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse' THEN b.amount END ASC,
                CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse' THEN b.amount END DESC,
                b.placed_at ASC,
                b.bid_id ASC
            LIMIT 1
            """,
            int(auction_id),
        )

    if not row:
        return None, None
    return int(row["amount"]), int(row["bidder_id"])


@require_db_pool
async def disable_autobid(*, auction_id: int, target_user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE public.autobids
            SET is_active  = FALSE,
                updated_at = NOW()
            WHERE auction_id = $1
              AND target_user_id = $2
              AND is_active = TRUE
            """,
            int(auction_id),
            int(target_user_id),
        )
    return res.endswith("UPDATE 1")


@require_db_pool
async def list_autobids(
        auction_id: int | None = None,
        *,
        only_active: bool = True,
) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ab.autobid_id,
                   ab.auction_id,
                   ab.target_user_id,
                   ab.target_username,
                   ab.max_amount,
                   ab.step,
                   ab.is_active,
                   a.currency AS auction_currency
            FROM public.autobids ab
                     LEFT JOIN public.auctions a
                               ON a.auction_id = ab.auction_id
            WHERE ($1::int IS NULL OR ab.auction_id = $1)
              AND ($2::bool = FALSE OR ab.is_active = TRUE)
            ORDER BY ab.auction_id DESC,
                     ab.max_amount DESC,
                     ab.autobid_id DESC
            """,
            int(auction_id) if auction_id is not None else None,
            bool(only_active),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def pick_best_autobid_candidate(
        auction_id: int,
        current_max: int | None = None,
        current_leader_id: int | None = None,
) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT autobid_id,
                   auction_id,
                   target_user_id,
                   target_username,
                   max_amount,
                   step,
                   is_active
            FROM public.autobids
            WHERE auction_id = $1
              AND is_active = TRUE
              AND ($2::bigint IS NULL OR target_user_id <> $2)
            ORDER BY max_amount DESC, autobid_id DESC
            LIMIT 1
            """,
            int(auction_id),
            int(current_leader_id) if current_leader_id is not None else None,
        )
        return dict(row) if row else None


@require_db_pool
async def record_autobid_action(
        autobid_id: int,
        auction_id: int,
        target_user_id: int,
        amount: int | None = None,
        discussion_message_id: int | None = None,
        *,
        bid_amount: int | None = None,
        bid_msg_id: int | None = None,
) -> dict | None:
    real_amount = amount if amount is not None else bid_amount
    real_msg_id = discussion_message_id if discussion_message_id is not None else bid_msg_id
    if not real_amount or not real_msg_id:
        return None

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.autobid_actions
                (autobid_id, auction_id, target_user_id, amount, discussion_message_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (discussion_message_id)
                DO NOTHING
            RETURNING *
            """,
            int(autobid_id),
            int(auction_id),
            int(target_user_id),
            int(real_amount),
            int(real_msg_id),
        )
        return dict(row) if row else None


@require_db_pool
async def get_autobid_action_by_msg_id(discussion_message_id: int) -> Optional[dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM public.autobid_actions
            WHERE discussion_message_id = $1
            """,
            int(discussion_message_id),
        )
    return dict(row) if row else None

@require_db_pool
async def get_auction_by_discussion_id(discussion_msg_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM auctions WHERE discussion_message_id = $1",
            discussion_msg_id
        )
        return dict(row) if row else None


@require_db_pool
async def get_active_auction_ids():
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT auction_id, discussion_message_id, end_time, status FROM auctions WHERE status = 'active'")
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[ERROR] Ошибка поиска активных аукционов: {e}")
        return []


@require_db_pool
async def get_warnings_count(user_id: int) -> int:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT warnings_count FROM users WHERE user_id = $1",
                user_id
            )
            return row["warnings_count"] if row and row["warnings_count"] is not None else 0
    except Exception as e:
        print(f"[ERROR] Ошибка получения warnings_count: {e}")
        return 0


@require_db_pool
async def ban_user(user_id: int, reason: str = "4 warnings"):
    banned_until = datetime.now() + timedelta(days=365 * 10)
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_bans (user_id, banned_until, reason) VALUES ($1, $2, $3)",
                user_id, banned_until, reason
            )
    except Exception as e:
        print(f"[ERROR] Ошибка при бане пользователя: {e}")


from datetime import datetime


def _mask_uid(uid: str) -> str:
    s = (uid or "").strip()
    if len(s) <= 8:
        return s
    return f"{s[:4]}…{s[-4:]}"


@require_db_pool
async def upsert_uid_ban(
        uid: str,
        *,
        banned_by: int | None,
        reason: str = "",
        banned_until: Optional[datetime] = None,
) -> dict:
    row = await fetchrow(
        """
        INSERT INTO public.uid_bans(uid, reason, banned_by, banned_until, banned_at)
        VALUES ($1, NULLIF($2, ''), $3, $4, NOW())
        ON CONFLICT (uid)
            DO UPDATE SET reason       = EXCLUDED.reason,
                          banned_by    = EXCLUDED.banned_by,
                          banned_until = EXCLUDED.banned_until,
                          banned_at    = NOW()
        RETURNING uid, reason, banned_by, banned_at, banned_until
        """,
        _norm_uid(uid),
        (reason or "").strip(),
        int(banned_by) if banned_by is not None else None,
        banned_until,
    )
    return dict(row) if row else {}


@require_db_pool
async def remove_uid_ban(uid: str) -> bool:
    row = await fetchrow(
        """
        DELETE
        FROM public.uid_bans
        WHERE uid = $1
        RETURNING uid
        """,
        _norm_uid(uid),
    )
    return bool(row)


@require_db_pool
async def get_uid_ban(uid: str) -> Optional[dict]:
    row = await fetchrow(
        """
        SELECT uid, reason, banned_by, banned_at, banned_until
        FROM public.uid_bans
        WHERE uid = $1
        """,
        _norm_uid(uid),
    )
    return dict(row) if row else None


@require_db_pool
async def list_uid_bans(*, limit: int = 50, offset: int = 0, only_active: bool = True) -> list[dict]:
    where = "WHERE (banned_until IS NULL OR banned_until > NOW())" if only_active else ""
    rows = await fetch(
        f"""
        SELECT uid_hash, uid_last4, reason, banned_by, banned_at, banned_until
        FROM public.uid_bans
        {where}
        ORDER BY banned_at DESC
        LIMIT $1 OFFSET $2
        """,
        int(limit),
        int(offset),
    )
    return [dict(r) for r in (rows or [])]
@require_db_pool
async def is_user_banned_now(user_id: int) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.user_bans
        WHERE user_id = $1
          AND banned_until > NOW()
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)


@require_db_pool
async def is_identity_blocked(user_id: int | None = None, username: str | None = None, uid: str | None = None) -> tuple[bool, str | None]:
    # 1. бан по user_id
    if user_id:
        if await is_user_banned_now(int(user_id)):
            return True, "user_id"

        # если у юзера уже есть verified uid — проверяем и его
        try:
            verified_uid = await get_user_verified_uid(int(user_id))
        except Exception:
            verified_uid = None

        if verified_uid and await is_uid_banned(str(verified_uid)):
            return True, "verified_uid"

    # 2. бан по username -> если username есть в базе, проверим user_id
    uname = (username or "").strip().lstrip("@")
    if uname:
        user = await get_user_by_username(uname)
        if user:
            uid_num = int(user["user_id"])
            if await is_user_banned_now(uid_num):
                return True, "username_user_id"

            try:
                verified_uid = await get_user_verified_uid(uid_num)
            except Exception:
                verified_uid = None

            if verified_uid and await is_uid_banned(str(verified_uid)):
                return True, "username_verified_uid"

    # 3. бан по UID, который пытаются ввести
    if uid:
        if await is_uid_banned(str(uid)):
            return True, "uid"

    return False, None
@require_db_pool
async def is_user_banned(user_id: int) -> bool:
    now = datetime.now()
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT banned_until FROM user_bans WHERE user_id = $1 ORDER BY id DESC LIMIT 1",
                int(user_id),
            )
            if bool(row and row["banned_until"] and row["banned_until"] > now):
                return True
    except Exception as e:
        print(f"[ERROR] Ошибка проверки is_banned: {e}")

    # ✅ доп. блокировка по UID (если UID в ЧС — считаем, что пользователь в бане)
    try:
        # функция ниже в этом же файле у тебя уже есть
        if await is_user_uid_banned(int(user_id)):
            return True
    except Exception:
        pass

    return False


async def get_expected_auction_for_now():
    conn = await asyncpg.connect(dsn=DATABASE_URL)
    row = await conn.fetchrow("""
                              SELECT auction_id, card_name, start_time
                              FROM auctions
                              WHERE discussion_message_id IS NULL
                                AND start_time <= now() + interval '10 minutes'
                              ORDER BY start_time ASC
                              LIMIT 1
                              """)
    await conn.close()
    return dict(row) if row else None


async def add_pending_auction_by_card_id(
        card_id: int,
        owner_id: int,
        start_price: int,
        currency: str,
        accepted_currencies: Optional[list[str]] = None,
        custom_offer_terms: Optional[str] = None,
        comment: str = "",
        image_id: Optional[str] = None,
        auction_kind: str = "standard",
        proof_photo_id: Optional[str] = None,
        craft_uid_possible: bool | None = None,
) -> int | None:
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            card = await conn.fetchrow(
                "SELECT card_name, hero_name, image_id FROM public.cards WHERE card_id = $1",
                int(card_id),
            )
            if not card:
                raise RuntimeError(f"Card not found: card_id={card_id}")

            final_image_id = image_id or card["image_id"]
            now = datetime.now()

            fields = [
                "card_name",
                "hero_name",
                "image_id",
                "start_price",
                "start_time",
                "end_time",
                "status",
                "currency",
                "accepted_currencies",
                "custom_offer_terms",
                "comment",
            ]
            values = [
                card["card_name"],
                card["hero_name"],
                final_image_id,
                int(start_price),
                now,
                now,
                "pending",
                currency,
                list(accepted_currencies or [currency]),
                (custom_offer_terms or "").strip() or None,
                comment or "",
            ]

            # ✅ created_at (если колонка есть)
            if await _has_column(conn, "auctions", "created_at"):
                fields.append("created_at")
                values.append(now)

            # новые колонки, если существуют
            if await _has_column(conn, "auctions", "auction_kind"):
                fields.append("auction_kind")
                values.append(auction_kind or "standard")

            if await _has_column(conn, "auctions", "craft_uid_possible"):
                fields.append("craft_uid_possible")
                values.append(craft_uid_possible)

            if await _has_column(conn, "auctions", "proof_photo_id"):
                fields.append("proof_photo_id")
                values.append(proof_photo_id)

            # (опционально) если есть card_id в auctions, тоже ставим
            if await _has_column(conn, "auctions", "card_id"):
                fields.append("card_id")
                values.append(int(card_id))

            cols_sql = ", ".join(fields)
            ph_sql = ", ".join(f"${i}" for i in range(1, len(values) + 1))

            row = await conn.fetchrow(
                f"""
                INSERT INTO public.auctions ({cols_sql})
                VALUES ({ph_sql})
                RETURNING auction_id
                """,
                *values,
            )
            if not row:
                return None

            auction_id = int(row["auction_id"])

            await conn.execute(
                """
                INSERT INTO public.auction_owners (auction_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                auction_id,
                int(owner_id),
            )

            return auction_id

    except Exception:
        # ✅ так ты наконец увидишь РЕАЛЬНУЮ причину падения
        logger.exception("add_pending_auction_by_card_id error")
        return None


@require_db_pool
async def get_user_by_username(username: str) -> dict | None:
    un = (username or "").strip().lstrip("@").lower()
    if not un:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.users WHERE LOWER(username)=LOWER($1) LIMIT 1",
            un,
        )
    return dict(row) if row else None

async def unban_user(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_bans WHERE user_id = $1",
            user_id
        )


async def reset_warnings(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_warnings WHERE user_id = $1", user_id
        )
        await conn.execute(
            "UPDATE users SET warnings_count = 0 WHERE user_id = $1", user_id
        )


@require_db_pool
async def get_auction_winner(auction_id: int) -> dict | None:
    try:
        async with db_pool.acquire() as conn:
            # Сначала получаем end_time лота
            auction_row = await conn.fetchrow(
                "SELECT end_time FROM auctions WHERE auction_id = $1",
                auction_id
            )
            if not auction_row or not auction_row['end_time']:
                return None
            end_time = auction_row['end_time']
            from datetime import timedelta
            end_dt = end_time + timedelta(minutes=1) - timedelta(seconds=1)
            row = await conn.fetchrow(
                """
                SELECT u.username, b.amount AS bid
                FROM public.bids b
                         JOIN public.users u ON b.bidder_id = u.user_id
                         JOIN public.auctions a ON a.auction_id = b.auction_id
                WHERE b.auction_id = $1
                  AND b.created_at <= $2
                ORDER BY
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse' THEN b.amount END ASC,
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse' THEN b.amount END DESC,
                    b.created_at ASC,
                    b.bid_id ASC
                LIMIT 1
                """,
                auction_id, end_dt
            )
            if row:
                return {"username": row["username"], "bid": row["bid"]}
            return None
    except Exception as e:
        logger.error(f"Ошибка получения победителя аукциона {auction_id}: {e}")
        return None


@require_db_pool
async def get_valid_bid_msg_ids(auction_id: int) -> list[int]:
    try:
        async with db_pool.acquire() as conn:
            auction_row = await conn.fetchrow(
                "SELECT end_time FROM auctions WHERE auction_id = $1", auction_id
            )
            if not auction_row or not auction_row["end_time"]:
                return []
            end_time = auction_row["end_time"]
            end_dt = end_time + timedelta(minutes=1) - timedelta(seconds=1)
            rows = await conn.fetch(
                """
                SELECT discussion_message_id, amount
                FROM bids
                WHERE auction_id = $1
                  AND created_at <= $2
                  AND amount ~ '^\\d+$' -- Только цифры!
                """,
                auction_id, end_dt
            )
            return [row["discussion_message_id"] for row in rows if row["discussion_message_id"]]
    except Exception as e:
        logger.error(f"Ошибка получения валидных message_id ставок для аукциона {auction_id}: {e}")
        return []


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    u = str(username).strip().lstrip("@")
    return u or None


@require_db_pool
async def add_user_if_not_exists(user_id: int, username: str, full_name: str = "") -> None:
    username = _normalize_username(username)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id, username, full_name
        )


@require_db_pool
async def release_stale_unpublished_lots(user_id: int | None = None) -> list[int]:
    """Release scheduled lots that missed publication and still have no post.

    A failed publisher used to put the row back into ``scheduled`` forever.
    Such a row blocked non-Luxury users from submitting another lot even after
    the scheduled time had passed.  Ten minutes is enough for normal Telegram
    retries while still preventing a dead row from trapping the owner all day.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.auctions AS a
            SET status = 'publication_failed'
            WHERE a.message_id IS NULL
              AND a.status IN ('scheduled', 'publishing')
              AND a.start_time < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
              AND (
                    $1::bigint IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM public.auction_owners AS ao
                        WHERE ao.auction_id = a.auction_id
                          AND ao.user_id = $1
                    )
                  )
            RETURNING a.auction_id
            """,
            int(user_id) if user_id is not None else None,
        )
        return [int(row["auction_id"]) for row in rows]


@require_db_pool
async def has_pending_lot(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.auctions AS a
            SET status = 'publication_failed'
            WHERE a.message_id IS NULL
              AND a.status IN ('scheduled', 'publishing')
              AND a.start_time < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
              AND EXISTS (
                    SELECT 1
                    FROM public.auction_owners AS ao
                    WHERE ao.auction_id = a.auction_id
                      AND ao.user_id = $1
                  )
            """,
            int(user_id),
        )
        result = await conn.fetchval(
            """
            SELECT EXISTS (SELECT 1
                           FROM auctions a
                                    JOIN auction_owners ao ON a.auction_id = ao.auction_id
                           WHERE ao.user_id = $1
                             AND a.status IN (
                                 'draft', 'moderation', 'pending', 'approved',
                                 'scheduled', 'publishing', 'active'
                             ))
            """,
            user_id
        )
        return bool(result)


@require_db_pool
async def cancel_owner_unpublished_lots(user_id: int) -> list[int]:
    """Cancel the owner's not-yet-published submissions.

    Active channel posts are deliberately untouched.  Future scheduled lots
    can still be managed by moderators, while missed unposted slots and pending
    moderation rows can be withdrawn by the owner without admin intervention.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.auctions AS a
            SET status = 'cancelled'
            WHERE a.message_id IS NULL
              AND a.status IN (
                    'draft', 'moderation', 'pending', 'approved',
                    'publication_failed'
                  )
              AND EXISTS (
                    SELECT 1
                    FROM public.auction_owners AS ao
                    WHERE ao.auction_id = a.auction_id
                      AND ao.user_id = $1
                  )
            RETURNING a.auction_id
            """,
            int(user_id),
        )
        return [int(row["auction_id"]) for row in rows]


@require_db_pool
async def get_card_subscribers(card_id: int) -> list[int]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM user_subscriptions WHERE card_id = $1", card_id
            )
            return [row["user_id"] for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения подписчиков карты {card_id}: {e}")
        return []


@require_db_pool
async def get_lot_by_message_id(message_id: int) -> Optional[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM auctions
            WHERE message_id = $1
            """,
            message_id
        )
        return dict(row) if row else None


@require_db_pool
async def get_current_auction() -> Optional[dict]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM auctions
                WHERE start_time <= NOW()
                  AND end_time >= NOW()
                  AND status = 'active'
                ORDER BY end_time ASC
                LIMIT 1
                """
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка получения текущего аукциона: {e}")
        return None


async def get_lot_approval_info(auction_id: int) -> tuple[str, datetime | None]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, created_at AS created_at
                FROM audit_logs
                WHERE auction_id = $1
                  AND action_type = 'approve_lot'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                auction_id
            )
            if row:
                admin_id = row["user_id"]
                admin_user = await get_user(admin_id)
                admin_username = admin_user["username"] if admin_user and admin_user.get(
                    "username") else f"id{admin_id}"
                approved_at = row["created_at"]
                return admin_username, approved_at
            return "-", None
    except Exception as e:
        logger.error(f"Ошибка поиска approval_info для лота {auction_id}: {e}")
        return "-", None


RU2DECK = {
    "рулеточная": "roulette",
    "ресурсная": "resource",
}
RU2OBTAIN = {
    "алмазы": "diamonds",
    "алмаз": "diamonds",
    "чай": "tea",
    "чашки": "tea",
    "чашка": "tea",
}


def _norm_deck_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2DECK.get(v, v)


def _norm_obtain_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2OBTAIN.get(v, v)


@require_db_pool
async def update_deck_type(deck_id: int, deck_type: str) -> None:
    deck_type = _norm_deck_type(deck_type)
    if deck_type not in {"roulette", "resource"}:
        raise ValueError("deck_type must be 'roulette' or 'resource'")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE decks SET deck_type = $2 WHERE id = $1",
            deck_id, deck_type
        )

@require_db_pool
async def _pg_column_exists(table: str, column: str) -> bool:
    async with db_pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            select 1
            from information_schema.columns
            where table_schema = 'public'
              and table_name = $1
              and column_name = $2
            """,
            table, column
        ))


@require_db_pool
async def _pg_table_exists(table: str) -> bool:
    async with db_pool.acquire() as conn:
        # to_regclass вернёт NULL, если таблицы нет
        return bool(await conn.fetchval("select to_regclass($1)", f"public.{table}"))


@require_db_pool
async def get_auctions_by_card_ref(query: str, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    st = statuses or ["pending", "scheduled", "active"]
    qraw = (query or "").strip()

    # Попытка распарсить card_id
    card_id: Optional[int] = None
    try:
        card_id = int(qraw)
    except Exception:
        pass

    # Попытка распарсить формат "(Герой) Название"
    hero_exact = name_exact = None
    if "(" in qraw and ")" in qraw and qraw.index("(") < qraw.index(")"):
        try:
            hero_exact = qraw[qraw.index("(") + 1: qraw.index(")")].strip() or None
            name_exact = qraw[qraw.index(")") + 1:].strip() or None
        except Exception:
            hero_exact = name_exact = None

    async with db_pool.acquire() as conn:
        rows = []
        if card_id is not None:
            sql = """
                  SELECT DISTINCT ON (a.auction_id) a.auction_id,
                                                    a.start_time,
                                                    a.end_time,
                                                    a.status,
                                                    a.currency,
                                                    a.start_price,
                                                    c.card_id                                       AS card_id,
                                                    COALESCE(NULLIF(a.card_name, '-'), c.card_name) AS card_name,
                                                    COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) AS hero_name,
                                                    COALESCE(a.image_id, c.image_id)                AS image_id,
                                                    c.deck_id
                  FROM auctions a
                           LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                  WHERE a.status = ANY ($1::varchar[])
                    AND c.card_id = $2
                  ORDER BY a.auction_id, a.start_time \
                  """
            rows = await conn.fetch(sql, st, card_id)

        elif hero_exact or name_exact:
            where_parts, params = [], [st]
            if hero_exact:
                where_parts.append(f"COALESCE(NULLIF(a.hero_name,'-'), c.hero_name) ILIKE ${len(params) + 1}")
                params.append(f"%{hero_exact}%")
            if name_exact:
                where_parts.append(f"COALESCE(NULLIF(a.card_name,'-'), c.card_name) ILIKE ${len(params) + 1}")
                params.append(f"%{name_exact}%")

            sql = f"""
                SELECT DISTINCT ON (a.auction_id)
                    a.auction_id, a.start_time, a.end_time, a.status, a.currency, a.start_price,
                    c.card_id AS card_id,
                    COALESCE(NULLIF(a.card_name,'-'), c.card_name) AS card_name,
                    COALESCE(NULLIF(a.hero_name,'-'),  c.hero_name)  AS hero_name,
                    COALESCE(a.image_id, c.image_id) AS image_id,
                    c.deck_id
                FROM auctions a
                LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                WHERE a.status = ANY ($1::varchar[])
                  AND {" AND ".join(where_parts)}
                ORDER BY a.auction_id, a.start_time
            """
            rows = await conn.fetch(sql, *params)

        else:
            patt = f"%{qraw}%"
            sql = """
                  SELECT DISTINCT ON (a.auction_id) a.auction_id,
                                                    a.start_time,
                                                    a.end_time,
                                                    a.status,
                                                    a.currency,
                                                    a.start_price,
                                                    c.card_id                                       AS card_id,
                                                    COALESCE(NULLIF(a.card_name, '-'), c.card_name) AS card_name,
                                                    COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) AS hero_name,
                                                    COALESCE(a.image_id, c.image_id)                AS image_id,
                                                    c.deck_id
                  FROM auctions a
                           LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                  WHERE a.status = ANY ($1::varchar[])
                    AND (
                      COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) ILIKE $2
                          OR COALESCE(NULLIF(a.card_name, '-'), c.card_name) ILIKE $2
                      )
                  ORDER BY a.auction_id, a.start_time \
                  """
            rows = await conn.fetch(sql, st, patt)

        return [dict(r) for r in rows]


@require_db_pool
async def get_auctions_in_range(start_dt: datetime, end_dt: datetime, statuses: Optional[List[str]] = None) -> List[
    Dict[str, Any]]:
    st = statuses or ["pending", "scheduled", "active"]
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT auction_id,
                   start_time,
                   end_time,
                   status
            FROM auctions
            WHERE start_time >= $1
              AND start_time < $2
              AND status = ANY ($3::varchar[])
            ORDER BY start_time
            """,
            start_dt, end_dt, st
        )
        return [dict(r) for r in rows]


async def get_auctions_for_local_day(day: _date, tzname: str = "Europe/Moscow") -> list[dict]:
    pool = await get_db_pool()
    tz = tzname

    sql = f"""
    WITH src AS (
      SELECT
        a,
        COALESCE(
          NULLIF(to_jsonb(a)->>'starts_at', '')::timestamptz,
          NULLIF(to_jsonb(a)->>'start_time', '')::timestamptz,
          NULLIF(to_jsonb(a)->>'dt', '' )::timestamptz,
          NULLIF(to_jsonb(a)->>'ts', '' )::timestamptz
        ) AS ts_utc
      FROM auctions a
    )
    SELECT
      (ts_utc AT TIME ZONE '{tz}')::time AS time,
      COALESCE(
        NULLIF(to_jsonb(a)->>'title', ''),
        NULLIF(to_jsonb(a)->>'name', ''),
        NULLIF(to_jsonb(a)->>'lot_title', ''),
        NULLIF(to_jsonb(a)->>'caption', ''),
        NULLIF(to_jsonb(a)->>'text', ''),
        'Лот'
      ) AS title
    FROM src
    WHERE ts_utc IS NOT NULL
      AND (ts_utc AT TIME ZONE '{tz}')::date = $1::date
    ORDER BY ts_utc;
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, day)

    return [dict(r) for r in rows]


@require_db_pool
async def get_auctions_by_date_with_owners(day: date) -> List[Dict[str, Any]]:
    """Return the current schedule snapshot for a Moscow calendar day.

    The admin ``Расписание`` button must describe slots that are still part of
    the live workflow.  ``finished`` rows are history and must not reserve a
    slot, while ``approved`` and ``publishing`` rows are already committed to
    the schedule even before their final status becomes ``scheduled``.

    A lateral single-card lookup avoids multiplying one auction when legacy
    card rows contain duplicate names.
    """

    sql = """
          SELECT a.*,
                 c.card_id,
                 c.deck_id,
                 COALESCE(o.owners_json, '[]'::json) AS owners_json
          FROM public.auctions a
                   LEFT JOIN LATERAL (
              SELECT json_agg(
                     json_build_object('user_id', ao.user_id, 'username', u.username)
                     ORDER BY ao.id
                             ) FILTER (WHERE ao.user_id IS NOT NULL) AS owners_json
              FROM public.auction_owners ao
                       LEFT JOIN public.users u ON u.user_id = ao.user_id
              WHERE ao.auction_id = a.auction_id
              ) o ON true
                   LEFT JOIN LATERAL (
              SELECT candidate.card_id, candidate.deck_id
              FROM public.cards candidate
              WHERE lower(trim(candidate.card_name)) = lower(trim(a.card_name))
                AND lower(trim(coalesce(candidate.hero_name, ''))) =
                    lower(trim(coalesce(a.hero_name, '')))
              ORDER BY candidate.card_id
              LIMIT 1
              ) c ON true
          WHERE CASE
                  WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                    THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                  ELSE a.start_time::date
                END = $1
            AND a.status IN ('approved', 'scheduled', 'publishing', 'active')
          ORDER BY a.start_time, a.auction_id
          """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(sql, day)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_auctions_by_date_with_owners failed: %s", e)
        return []


@require_db_pool
async def _audit_created_col() -> str:
    if await _pg_column_exists("audit_logs", "created_at"):
        return "created_at"
    return '"timestamp"'


async def _get_card_basic(card_id: int) -> Optional[dict]:
    row = await fetchrow(
        """
        SELECT card_id, card_name, image_id
        FROM cards
        WHERE card_id = $1
        """,
        card_id,
    )
    return dict(row) if row else None


async def _update_card_image(card_id: int, file_id: str) -> Optional[dict]:
    row = await fetchrow(
        """
        UPDATE cards
        SET image_id=$2
        WHERE card_id = $1
        RETURNING card_id, card_name, image_id
        """,
        card_id,
        file_id,
    )
    return dict(row) if row else None


async def get_deck(deck_id: int) -> Optional[Dict[str, Any]]:
    row = await fetchrow("SELECT id, name, deck_type FROM decks WHERE id=$1", deck_id)
    return dict(row) if row else None


async def set_deck_type(deck_id: int, deck_type: str) -> Tuple[Optional[str], str]:
    deck_type = norm_deck_type(deck_type)
    if deck_type not in {"roulette", "resource"}:
        raise ValueError("deck_type must be roulette|resource")
    before = await fetchrow("SELECT deck_type FROM decks WHERE id=$1", deck_id)
    from db.legacy import execute
    await execute("UPDATE decks SET deck_type=$2 WHERE id=$1", deck_id, deck_type)
    return (before["deck_type"] if before else None, deck_type)


async def get_card(card_id: int) -> Optional[Dict[str, Any]]:
    row = await fetchrow("""
                         SELECT card_id, card_name, obtain_type, obtain_amount
                         FROM cards
                         WHERE card_id = $1
                         """, card_id)
    return dict(row) if row else None


async def set_card_obtain(card_id: int, obtain_type: str, amount: int) -> Tuple[Tuple[str, int], Tuple[str, int]]:
    ot = norm_obtain_type(obtain_type)
    if ot not in {"diamonds", "tea"}:
        raise ValueError("obtain_type must be diamonds|tea")
    amount = int(amount)
    if amount < 0:
        raise ValueError("amount must be >= 0")
    before = await fetchrow("SELECT obtain_type, obtain_amount FROM cards WHERE card_id=$1", card_id)
    await execute("UPDATE cards SET obtain_type=$2, obtain_amount=$3 WHERE card_id=$1", card_id, ot, amount)
    b = (str(before["obtain_type"]), int(before["obtain_amount"])) if before else ("diamonds", 0)
    return b, (ot, amount)


def norm_deck_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2DECK.get(v, v)


def norm_obtain_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2OBTAIN.get(v, v)


async def mark_card_day_notified(user_id: int, card_id: int, day: date) -> bool:
    q = """
        INSERT INTO card_day_notifications(user_id, card_id, day)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING \
        """
    async with db_pool.acquire() as conn:
        res = await conn.execute(q, user_id, card_id, day)
        return res.endswith("1")


ALLOWED_PREFS = {
    "notify_auction_start": "notify_auction_start",
    "notify_bid_reminder": "notify_bid_reminder",
    "notify_auction_end": "notify_auction_end",
    "notify_daily_today": "notify_daily_today",
}


@require_db_pool
@require_db_pool
async def disable_all_notifications(user_id: int) -> None:
    q = """
        UPDATE settings
        SET notify_auction_start = FALSE,
            notify_bid_reminder  = FALSE,
            notify_auction_end   = FALSE,
            notify_daily_today   = FALSE
        WHERE user_id = $1 \
        """
    async with db_pool.acquire() as conn:
        await conn.execute(q, user_id)


@require_db_pool
async def clear_all_card_subscriptions(user_id: int) -> None:
    q = "DELETE FROM user_subscriptions WHERE user_id = $1"
    async with db_pool.acquire() as conn:
        await conn.execute(q, user_id)


@require_db_pool
async def mark_user_unreachable(user_id: int, reason: str) -> None:
    q = """
        INSERT INTO unreachable_users(user_id, reason, last_seen)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET reason    = EXCLUDED.reason,
                last_seen = NOW() \
        """
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(q, user_id, reason)
        except Exception:
            pass


@require_db_pool
async def get_users_with_pref(pref: str) -> List[int]:
    col = ALLOWED_PREFS.get(pref)
    if not col:
        return []
    q = f"SELECT user_id FROM settings WHERE COALESCE({col}, TRUE) = TRUE"
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(q)
    return [int(r["user_id"]) for r in rows]


async def unsubscribe_subscription(sub_id: int, user_id: int) -> bool:
    """
    Удаляет запись подписки по id, только если она принадлежит user_id.
    Возвращает True, если удалили.
    """
    sql = """
          DELETE
          FROM user_subscriptions
          WHERE id = $1
            AND user_id = $2
          RETURNING id \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, sub_id, user_id)
    return row is not None


@require_db_pool
async def get_top_subscribed_cards(
        limit: int = 20,
        offset: int = 0,
        only_luxury: bool = False,
) -> Tuple[list[dict], int]:
    async with db_pool.acquire() as conn:
        subs_sql = """
                   WITH subs AS (SELECT us.card_id, COUNT(*) AS subs_count
                                 FROM user_subscriptions us
                                          JOIN users u ON u.user_id = us.user_id
                                 WHERE us.card_id IS NOT NULL
                                   AND ($3::bool IS FALSE OR u.is_luxury = TRUE)
                                 GROUP BY us.card_id),
                        sched AS (SELECT LOWER(a.card_name) AS cn,
                                         LOWER(a.hero_name) AS hn,
                                         COUNT(*)           AS scheduled_count
                                  FROM auctions a
                                  WHERE a.status IN ('scheduled', 'active', 'approved')
                                  GROUP BY LOWER(a.card_name), LOWER(a.hero_name))
                   SELECT c.card_id,
                          c.card_name,
                          c.hero_name,
                          c.deck_id,
                          s.subs_count,
                          COALESCE(sc.scheduled_count, 0) AS scheduled_count
                   FROM subs s
                            JOIN cards c ON c.card_id = s.card_id
                            LEFT JOIN sched sc
                                      ON sc.cn = LOWER(c.card_name)
                                          AND sc.hn = LOWER(c.hero_name)
                   ORDER BY s.subs_count DESC, c.card_name ASC
                   LIMIT $1 OFFSET $2 \
                   """
        total_sql = """
                    WITH subs AS (SELECT us.card_id
                                  FROM user_subscriptions us
                                           JOIN users u ON u.user_id = us.user_id
                                  WHERE us.card_id IS NOT NULL
                                    AND ($1::bool IS FALSE OR u.is_luxury = TRUE)
                                  GROUP BY us.card_id)
                    SELECT COUNT(*)::int
                    FROM subs \
                    """
        rows = await conn.fetch(subs_sql, limit, offset, only_luxury)
        total = await conn.fetchval(total_sql, only_luxury)
        return [dict(r) for r in rows], int(total)


async def subscribe_preset(user_id: int, key: str) -> None:
    await execute(
        """
        INSERT INTO user_preset_subscriptions(user_id, preset_id)
        SELECT $1, p.id
        FROM presets p
        WHERE p.key = $2
        ON CONFLICT DO NOTHING
        """,
        user_id, key
    )


async def list_my_preset_subs(user_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT ups.id, p.key, p.title, ups.created_at
        FROM user_preset_subscriptions ups
                 JOIN presets p ON p.id = ups.preset_id
        WHERE ups.user_id = $1
        ORDER BY ups.id DESC
        """,
        user_id
    )
    return [dict(r) for r in rows]


async def unsubscribe_preset(sub_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM user_preset_subscriptions WHERE id=$1 AND user_id=$2",
        sub_id, user_id
    )


async def subscribers_for_lot_title(lot_title: str) -> List[int]:
    rows = await fetch(
        """
        SELECT DISTINCT ups.user_id
        FROM user_preset_subscriptions ups
                 JOIN preset_aliases a ON a.preset_id = ups.preset_id
        WHERE LOWER(a.alias) = LOWER($1)
        """,
        lot_title
    )
    return [r["user_id"] for r in rows]


@require_db_pool
async def list_broadcast_targets() -> List[int]:
    """
    Все пользователи, которым можно писать в ЛС:
      • не помечены как недоступные (unreachable_users)
      • глобально не отписаны (is_subscribed != FALSE)
      • открывали ЛС с ботом (pm_opened = TRUE)
    """
    sql = """
          SELECT u.user_id
          FROM users u
                   LEFT JOIN unreachable_users uu ON uu.user_id = u.user_id
          WHERE uu.user_id IS NULL
            AND COALESCE(u.is_subscribed, TRUE) = TRUE
            AND COALESCE(u.pm_opened, FALSE) = TRUE \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [int(r["user_id"]) for r in rows]


@require_db_pool
async def list_user_card_subs(user_id: int) -> list[dict]:
    """
    Список подписок пользователя с названиями карт и героев.
    Пустые строки в cards.* считаем отсутствием данных.
    """
    sql = """
          SELECT us.id                   AS id,
                 us.card_id,
                 NULLIF(c.card_name, '') AS card_name,
                 NULLIF(c.hero_name, '') AS hero_name,
                 us.last_confirmed_at
          FROM user_subscriptions us
                   JOIN cards c ON c.card_id = us.card_id
          WHERE us.user_id = $1
          ORDER BY c.card_name, c.hero_name \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]


async def mark_subscription_confirmed(sub_id: int, user_id: int) -> bool:
    """
    Ставит отметку подтверждения этой подписки на сейчас.
    """
    sql = """
          UPDATE user_subscriptions
          SET last_confirmed_at = now()
          WHERE id = $1
            AND user_id = $2
          RETURNING id \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, sub_id, user_id)
    return row is not None


async def mark_unreachable_user(user_id: int, reason: str) -> None:
    """
    Сохраняем факт недоступности пользователя (заблокировал, запрет ЛС и т.д.)
    """
    sql = """
          INSERT INTO unreachable_users (user_id, reason, last_seen)
          VALUES ($1, $2, now())
          ON CONFLICT (user_id) DO UPDATE
              SET reason    = EXCLUDED.reason,
                  last_seen = now() \
          """
    async with db_pool.acquire() as conn:
        await conn.execute(sql, user_id, reason)


async def get_card_full_by_id(card_id: int) -> Optional[Dict[str, Any]]:
    sql = """
          SELECT c.*, d.name AS deck_name
          FROM cards c
                   LEFT JOIN decks d ON d.id = c.deck_id
          WHERE c.card_id = $1
          LIMIT 1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, card_id)
    return dict(row) if row else None


async def find_card_by_name_hero(card_name: str, hero_name: str) -> Optional[Dict[str, Any]]:
    sql = """
          SELECT c.*, d.name AS deck_name
          FROM cards c
                   LEFT JOIN decks d ON d.id = c.deck_id
          WHERE lower(c.card_name) = lower($1)
            AND lower(c.hero_name) = lower($2)
          LIMIT 1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, card_name, hero_name)
    return dict(row) if row else None


async def _preset_user_ids_by_key_or_alias(key: str) -> List[int]:
    """
    Возвращает список user_id, подписанных на пресет с заданным ключом
    или любой из его алиасов.
    """
    sql = """
          SELECT ups.user_id
          FROM user_preset_subscriptions ups
                   JOIN presets p ON p.id = ups.preset_id
                   LEFT JOIN preset_aliases pa ON pa.preset_id = p.id
          WHERE lower(p.key) = lower($1)
             OR lower(pa.alias) = lower($1) \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, key)
    return [int(r["user_id"]) for r in rows]


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _rarity_slug(r: Optional[str]) -> Optional[str]:
    r = _norm(r)
    if not r:
        return None
    # русские прилагательные, существительные и англ
    mapping = {
        "бронзовая": "bronze", "бронза": "bronze", "bronze": "bronze",
        "серебряная": "silver", "серебро": "silver", "silver": "silver",
        "золотая": "gold", "золото": "gold", "gold": "gold",
        "алмазная": "diamond", "алмазы": "diamond", "алмаз": "diamond",
        "diamond": "diamond", "diamonds": "diamond",
    }
    return mapping.get(r, r)  # если пришло что-то экзотическое — используем как есть


async def subscribers_for_rarity(rarity: Optional[str]) -> List[int]:
    """
    rarity= 'золотая'/'gold'/... -> ищем пресет с ключом 'rarity:<slug>'
    и его алиасы.
    """
    slug = _rarity_slug(rarity)
    if not slug:
        return []
    key = f"rarity:{slug}"
    return await _preset_user_ids_by_key_or_alias(key)


async def subscribers_for_deck(deck_id: Optional[int], deck_name: Optional[str]) -> List[int]:
    """
    Ищем два вида ключей:
      - 'deck:<id>'
      - 'deck:<имя колоды>' (в нижнем регистре)
    Любой из них может быть основным ключом или алиасом.
    """
    out: List[int] = []
    if deck_id:
        out += await _preset_user_ids_by_key_or_alias(f"deck:{int(deck_id)}")
    if deck_name:
        out += await _preset_user_ids_by_key_or_alias(f"deck:{_norm(deck_name)}")
    # убираем возможные дубли
    return list({int(x) for x in out})


@require_db_pool
async def get_cards_meta_bulk(card_ids: Iterable[int]) -> Dict[int, dict]:
    """
    Возвращает мета-инфу по картам пачкой:
      hero_name, deck_id, rarity, gifts_cups/gifts_diamonds.
    gifts_* считаются из obtain_type/obtain_amount.
    """
    ids = [int(x) for x in set(card_ids) if x is not None]
    if not ids:
        return {}

    sql = """
          SELECT c.card_id,
                 c.hero_name,
                 c.deck_id,
                 c.rarity,
                 CASE
                     WHEN c.obtain_type::text IN ('tea', 'cups', 'cup') THEN c.obtain_amount
                     ELSE 0
                     END AS gifts_cups,
                 CASE
                     WHEN c.obtain_type::text IN ('diamonds', 'diamond') THEN c.obtain_amount
                     ELSE 0
                     END AS gifts_diamonds
          FROM cards c
          WHERE c.card_id = ANY ($1::int[]) \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, ids)

    out: Dict[int, dict] = {}
    for r in rows:
        out[int(r["card_id"])] = {
            "hero_name": r["hero_name"],
            "deck_id": r["deck_id"],
            "rarity": r["rarity"],
            "gifts_cups": int(r["gifts_cups"] or 0),
            "gifts_diamonds": int(r["gifts_diamonds"] or 0),
        }
    return out


@require_db_pool
async def get_obtain_variants_for_rarity(rarity_norm: str | None) -> set[str]:
    """
    Возвращает множество {'diamonds','cups'} для заданной редкости
    (учитываем рус/англ синонимы). Если rarity_norm=None — по всем картам.
    """
    rar_aliases = {
        "bronze": ["bronze", "бронза", "бронзовая"],
        "silver": ["silver", "серебро", "серебряная"],
        "gold": ["gold", "золото", "золотая"],
        "diamond": ["diamond", "diamonds", "алмаз", "алмазы", "алмазная"],
    }

    where = ""
    params: list = []
    if rarity_norm:
        arr = [x.lower() for x in rar_aliases.get(rarity_norm, [rarity_norm])]
        where = "WHERE lower(c.rarity) = ANY($1::text[])"
        params = [arr]

    sql = f"""
        SELECT DISTINCT
               CASE
                   WHEN lower(c.obtain_type::text) IN ('diamonds','diamond','алмазы','алмаз') THEN 'diamonds'
                   WHEN lower(c.obtain_type::text) IN ('tea','cups','cup','чай','чашки','чашка') THEN 'cups'
                   ELSE NULL
               END AS t
        FROM cards c
        {where}
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    out: set[str] = set()
    for r in rows:
        t = r["t"]
        if t:
            out.add(t)
    return out


# --- суммы «что даёт» по колоде ---
@require_db_pool
async def get_deck_obtain_totals(deck_id: int) -> dict:
    """
    Возвращает {'diamonds': X, 'cups': Y} по всей колоде.
    """
    sql = """
          SELECT COALESCE(SUM(CASE
                                  WHEN lower(c.obtain_type::text) IN ('diamonds', 'diamond', 'алмазы', 'алмаз')
                                      THEN c.obtain_amount
                                  ELSE 0 END), 0) AS diamonds,
                 COALESCE(SUM(CASE
                                  WHEN lower(c.obtain_type::text) IN ('tea', 'cups', 'cup', 'чай', 'чашки', 'чашка')
                                      THEN c.obtain_amount
                                  ELSE 0 END), 0) AS cups
          FROM cards c
          WHERE c.deck_id = $1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, deck_id)

    return {
        "diamonds": int(row["diamonds"] or 0) if row else 0,
        "cups": int(row["cups"] or 0) if row else 0,
    }


# db.py


# максимум, что "даёт" карта для заданной редкости (для "Любая карта <редкость>")
@require_db_pool
async def get_max_obtain_for_rarity(rarity_norm: str | None) -> dict:
    """
    Возвращает максимальные значения по типам для редкости:
    {'cups': X, 'diamonds': Y}
    rarity_norm: 'bronze'|'silver'|'gold'|'diamond' (или None — тогда по всем).
    """
    rar_aliases = {
        "bronze": ["bronze", "бронза", "бронзовая"],
        "silver": ["silver", "серебро", "серебряная"],
        "gold": ["gold", "золото", "золотая"],
        "diamond": ["diamond", "diamonds", "алмаз", "алмазы", "алмазная"],
    }
    where = ""
    params: list = []
    if rarity_norm:
        arr = [x.lower() for x in rar_aliases.get(rarity_norm, [rarity_norm])]
        where = "WHERE lower(c.rarity) = ANY($1::text[])"
        params = [arr]

    sql = f"""
        SELECT
            COALESCE(MAX(CASE WHEN lower(c.obtain_type::text) IN ('tea','cups','cup','чай','чашки','чашка')
                              THEN c.obtain_amount END), 0) AS cups_max,
            COALESCE(MAX(CASE WHEN lower(c.obtain_type::text) IN ('diamonds','diamond','алмазы','алмаз')
                              THEN c.obtain_amount END), 0) AS diamonds_max
        FROM cards c
        {where}
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)

    return {
        "cups": int(row["cups_max"] or 0) if row else 0,
        "diamonds": int(row["diamonds_max"] or 0) if row else 0,
    }


# сумма "сокровищ" по всей колоде (для "Вся колода №X")
@require_db_pool
async def get_deck_treasure_sum(deck_id: int) -> int:
    """
    Возвращает сумму сокровищ по колоде согласно мапе редкостей:
    bronze=10, silver=20, gold=40, diamond=60.
    """
    sql = """
          SELECT COALESCE(SUM(
                                  CASE
                                      WHEN lower(c.rarity) IN ('diamond', 'алмаз', 'алмазы', 'алмазная') THEN 60
                                      WHEN lower(c.rarity) IN ('gold', 'золото', 'золотая') THEN 40
                                      WHEN lower(c.rarity) IN ('silver', 'серебро', 'серебряная') THEN 20
                                      WHEN lower(c.rarity) IN ('bronze', 'бронза', 'бронзовая') THEN 10
                                      ELSE 0
                                      END
                          ), 0) AS t_sum
          FROM cards c
          WHERE c.deck_id = $1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, deck_id)
    return int(row["t_sum"] or 0) if row else 0


# db/db.py — MARKETPLACE

from typing import Any, Optional
from typing import Iterable


@require_db_pool
async def market_count_active(user_id: int, include_hidden: bool = False) -> int:
    async with db_pool.acquire() as conn:
        if include_hidden:
            q = "SELECT COUNT(*) FROM market_listings WHERE seller_id=$1 AND status IN ('active','hidden')"
        else:
            q = "SELECT COUNT(*) FROM market_listings WHERE seller_id=$1 AND status='active'"
        return int(await conn.fetchval(q, user_id) or 0)


@require_db_pool
async def market_create_listing(
        seller_id: int,
        currency_type: str,
        price_num: float,
        cash_code: Optional[str],
        description: Optional[str],
) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO market_listings (seller_id, currency_type, price_num, cash_code, description)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING listing_id
            """,
            seller_id, currency_type, price_num, cash_code, description,
        )
        return int(row["listing_id"])


@require_db_pool
async def market_add_items(listing_id: int, card_ids: Iterable[int]) -> int:
    async with db_pool.acquire() as conn:
        total = 0
        for cid in card_ids:
            try:
                await conn.execute(
                    "INSERT INTO market_listing_items (listing_id, card_id) VALUES ($1, $2)",
                    listing_id, int(cid),
                )
                total += 1
            except Exception:
                continue
        return total


@require_db_pool
async def market_get_listing(listing_id: int) -> Optional[dict]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM market_listings WHERE listing_id=$1", listing_id)
        return dict(row) if row else None


# db/db.py

@require_db_pool
async def market_my_listings(user_id: int) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ml.listing_id, ml.status, ml.description, ml.cover_file_id
            FROM market_listings ml
            WHERE ml.seller_id = $1
              AND ml.status = ANY ($2::listing_status[])
              AND EXISTS (SELECT 1
                          FROM market_listing_items mli
                          WHERE mli.listing_id = ml.listing_id)
            ORDER BY ml.updated_at DESC
            """,
            user_id,
            ['active', 'hidden', 'sold', 'archived'],
        )
        return [dict(r) for r in rows]


# db/db.py
@require_db_pool
async def market_my_listing_counts(user_id: int) -> dict[str, int]:
    allowed = ['active', 'hidden', 'sold', 'archived']
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH base(status) AS (SELECT ml.status
                                  FROM public.market_listings ml
                                  WHERE ml.seller_id = $1
                                    AND ml.status = ANY ($2::listing_status[])
                                    AND EXISTS (SELECT 1
                                                FROM public.market_listing_items mli
                                                WHERE mli.listing_id = ml.listing_id))
            SELECT COUNT(*)                                                    AS all,
                   COUNT(*) FILTER (WHERE status = 'active'::listing_status)   AS active,
                   COUNT(*) FILTER (WHERE status = 'hidden'::listing_status)   AS hidden,
                   COUNT(*) FILTER (WHERE status = 'sold'::listing_status)     AS sold,
                   COUNT(*) FILTER (WHERE status = 'archived'::listing_status) AS archived
            FROM base
            """,
            user_id, allowed,
        )
        d = {
            "all": int(row["all"] or 0),
            "active": int(row["active"] or 0),
            "hidden": int(row["hidden"] or 0),
            "sold": int(row["sold"] or 0),
            "archived": int(row["archived"] or 0),
        }
        d["all"] = d["active"] + d["hidden"] + d["sold"] + d["archived"]
        return d


@require_db_pool
async def market_set_status(listing_id: int, status: str) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET status=$2 WHERE listing_id=$1",
            listing_id, status,
        )


# ---------- v2 extensions ----------

@require_db_pool
async def market_add_rate_tiers(listing_id: int, tiers: list[dict]) -> int:
    async with db_pool.acquire() as conn:
        total = 0
        for t in tiers:
            await conn.execute(
                """
                INSERT INTO market_rate_tiers(listing_id, label, qty, pay_type, cash_code, price, sort_order)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                listing_id, t.get("label"), t.get("qty"), t["pay_type"],
                t.get("cash_code"), t["price"], int(t.get("sort_order") or 0),
            )
            total += 1
        return total


@require_db_pool
async def market_get_rate_tiers(listing_id: int) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM market_rate_tiers WHERE listing_id=$1 ORDER BY sort_order, id",
            listing_id,
        )
        return [dict(r) for r in rows]


@require_db_pool
async def market_set_offer_kind(listing_id: int, offer_kind: str) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET offer_kind=$2 WHERE listing_id=$1",
            listing_id, offer_kind,
        )


@require_db_pool
async def market_toggle_actual(listing_id: int) -> str:
    async with db_pool.acquire() as conn:
        st = await conn.fetchval("SELECT status FROM market_listings WHERE listing_id=$1", listing_id)
        new_st = "hidden" if st == "active" else "active"
        await conn.execute("UPDATE market_listings SET status=$2 WHERE listing_id=$1", listing_id, new_st)
        return new_st


@require_db_pool
async def market_bump(listing_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE market_listings SET updated_at=now() WHERE listing_id=$1", listing_id)


@require_db_pool
async def market_bind_channel_message(listing_id: int, channel_id: int, message_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET channel_id=$2, message_id=$3 WHERE listing_id=$1",
            listing_id, channel_id, message_id,
        )


# db/db.py

async def market_get_item_qty(listing_id: int) -> int:
    sql = """
          SELECT quantity
          FROM market_listing_items
          WHERE listing_id = $1
          LIMIT 1 \
          """
    row = await db_pool.fetchrow(sql, listing_id)
    return int(row["quantity"]) if row else 0


@require_db_pool
async def market_set_item_qty(listing_id: int, qty: int) -> None:
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            if qty <= 0:
                # qty == 0 в таблице запрещён → просто удаляем позицию
                await conn.execute(
                    "DELETE FROM market_listing_items WHERE listing_id=$1",
                    listing_id,
                )
            else:
                await conn.execute(
                    "UPDATE market_listing_items SET quantity=$2 WHERE listing_id=$1",
                    listing_id, int(qty),
                )


@require_db_pool
async def market_dec_item_qty(listing_id: int, dec: int) -> int:
    """Атомарно уменьшает остаток. Если уходит в ноль — удаляет строку и возвращает 0."""
    dec = max(1, int(dec))
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT quantity FROM market_listing_items WHERE listing_id=$1 FOR UPDATE",
                listing_id,
            )
            if not row:
                return 0
            cur = int(row["quantity"] or 0)
            new = cur - dec
            if new > 0:
                row2 = await conn.fetchrow(
                    "UPDATE market_listing_items SET quantity=$2 WHERE listing_id=$1 RETURNING quantity",
                    listing_id, new,
                )
                return int(row2["quantity"]) if row2 else 0
            else:
                # qty <= 0 — строку убираем, никакого 0 в колонке с CHECK>0
                await conn.execute(
                    "DELETE FROM market_listing_items WHERE listing_id=$1",
                    listing_id,
                )
                return 0


@require_db_pool
async def _has_column(conn, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        """
        select exists(select 1
                      from information_schema.columns
                      where table_schema = 'public'
                        and table_name = $1
                        and column_name = $2) as has
        """,
        table, column
    )
    return bool(row and row["has"])


def _json_to_str_map(x) -> dict[str, str]:
    """
    Превращает что угодно в словарь {str(card_id): str(file_id)}.
    Поддерживает: dict, JSON-строку, список пар, список объектов.
    """
    if not x:
        return {}
    if isinstance(x, dict):
        return {str(k): str(v) for k, v in x.items() if v is not None}
    if isinstance(x, str):
        try:
            j = json.loads(x)
        except Exception:
            return {}
        return _json_to_str_map(j)
    if isinstance(x, (list, tuple)):
        out: dict[str, str] = {}
        for item in x:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                k, v = item
            elif isinstance(item, dict):
                k = item.get("card_id") or item.get("id") or item.get("key")
                v = item.get("proof_file_id") or item.get("value") or item.get("file_id")
            else:
                continue
            if k is not None and v is not None:
                out[str(k)] = str(v)
        return out
    # остальное нам не интересно
    return {}


@require_db_pool
async def _get_listing_core(listing_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        # какие колонки реально есть
        has_ml_proof_one = await _has_column(conn, "market_listings", "proof_file_id")
        has_ml_proof_map = await _has_column(conn, "market_listings", "proof_by_card")
        has_cover = await _has_column(conn, "market_listings", "cover_file_id")
        has_item_proof = await _has_column(conn, "market_listing_items", "proof_file_id")

        proof_one = None
        if has_ml_proof_one:
            row = await conn.fetchrow(
                "select proof_file_id, cover_file_id from market_listings where listing_id=$1",
                listing_id
            )
            if not row:
                return None
            proof_one = row["proof_file_id"] or row["cover_file_id"]
        elif has_cover:
            row = await conn.fetchrow(
                "select cover_file_id from market_listings where listing_id=$1",
                listing_id
            )
            if not row:
                return None
            proof_one = row["cover_file_id"]

        result: dict = {"listing_id": listing_id, "proof_file_id": proof_one, "proof_by_card": {}}

        # если есть JSONB в listings — используем его
        if has_ml_proof_map:
            row2 = await conn.fetchrow(
                "select proof_by_card from market_listings where listing_id=$1",
                listing_id
            )
            if row2 and row2["proof_by_card"]:
                result["proof_by_card"] = _json_to_str_map(row2["proof_by_card"])
                return result

        # иначе соберём по item-строкам
        if has_item_proof:
            rows = await conn.fetch(
                "select card_id, proof_file_id from market_listing_items where listing_id=$1 and proof_file_id is not null",
                listing_id
            )
            if rows:
                result["proof_by_card"] = {str(r["card_id"]): str(r["proof_file_id"]) for r in rows if
                                           r["proof_file_id"]}
        return result


async def market_add_listing_item(listing_id: int, card_id: int, qty: int = 1, proof_file_id: str | None = None):
    await execute(
        """
        INSERT INTO public.market_listing_items (listing_id, card_id, quantity, proof_file_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (listing_id, card_id) DO UPDATE
            SET quantity      = EXCLUDED.quantity,
                proof_file_id = COALESCE(EXCLUDED.proof_file_id, public.market_listing_items.proof_file_id)
        """,
        listing_id, card_id, max(1, qty), proof_file_id
    )


# -----------------------------
# Биржа (exchange_batches / exchange_items)
# -----------------------------

@require_db_pool
async def count_exchange_cards_for_deck(deck_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM public.cards WHERE deck_id=$1",
            int(deck_id),
        )
        return int(row["cnt"])


@require_db_pool
async def get_exchange_cards_for_deck(
        deck_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Карты колоды для экранов биржи (пагинация)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.card_id,
                   c.card_name,
                   c.hero_name,
                   c.rarity,
                   c.obtain_type,
                   c.obtain_amount
            FROM public.cards c
            WHERE c.deck_id = $1
            ORDER BY c.card_id
            LIMIT $2 OFFSET $3
            """,
            int(deck_id),
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def create_exchange_batch(
        user_id: int,
        deck_id: int,
        exchange_kind: str | None = None,  # алиас
        currency: str = "алмазы",
        amount: int | None = None,  # алиас
        comment: str = "",
        proof_photo_id: str | None = None,
        *,
        # Back-compat алиасы (старые имена)
        mode: str | None = None,
        price: int | None = None,
        **_: Any,
) -> int:
    """
    ТВОЯ схема: exchange_batches(user_id, deck_id, mode, currency, price, comment, proof_photo_id, status)

    proof_photo_id NOT NULL => если нет пруфа, пишем 'NO_PROOF'
    """
    proof_photo_id = (proof_photo_id or "").strip() or "NO_PROOF"
    m = (mode or exchange_kind or "").strip() or "card"
    cur = (currency or "").strip() or "алмазы"

    raw_price = price if price is not None else amount
    try:
        p = int(raw_price or 0)
    except Exception:
        p = 0

    com = (comment or "").strip()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.exchange_batches
            (user_id, deck_id, mode, currency, price, comment, proof_photo_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
            RETURNING batch_id
            """,
            int(user_id),
            int(deck_id),
            m,
            cur,
            int(p),
            com,
            proof_photo_id,
        )
        return int(row["batch_id"])


@require_db_pool
async def create_exchange_items(batch_id: int, card_ids: list[int]) -> int:
    """
    Добавляет cards в exchange_items для batch_id.
    Возвращает сколько строк вставили.
    """
    if not card_ids:
        return 0

    ids = [int(x) for x in card_ids]

    async with db_pool.acquire() as conn:
        res = await conn.execute(
            """
            INSERT INTO public.exchange_items (batch_id, card_id)
            SELECT $1, x
            FROM UNNEST($2::int[]) AS t(x)
            """,
            int(batch_id),
            ids,
        )
    # res типа: "INSERT 0 N"
    try:
        return int(res.split()[-1])
    except Exception:
        return len(ids)


@require_db_pool
async def get_exchange_batches_for_card(
        card_id: int,
        status: str = "approved",
) -> list[dict[str, Any]]:
    """
    Возвращает список заявок (batch_id), в которых есть card_id:
    batch_id, user_id, username, qty
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT eb.batch_id,
                   eb.user_id,
                   u.username    AS username,
                   COUNT(*)::int AS qty
            FROM public.exchange_items ei
                     JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                     LEFT JOIN public.users u ON u.user_id = eb.user_id
            WHERE ei.card_id = $1
              AND eb.status = $2
            GROUP BY eb.batch_id, eb.user_id, u.username
            ORDER BY qty DESC, eb.batch_id DESC
            """,
            int(card_id),
            status,
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "batch_id": int(r["batch_id"]),
                "user_id": int(r["user_id"]),
                "username": (r["username"] or "").strip() or None,
                "qty": int(r["qty"]),
            }
        )
    return out


@require_db_pool
async def add_exchange_item_for_card(
        *,
        batch_id: int,
        card_id: int,
) -> None:
    """Добавляет одну карту в exchange_items (по card_id подтягивает имя/героя)."""
    async with db_pool.acquire() as conn:
        card = await conn.fetchrow(
            "SELECT card_name, hero_name FROM public.cards WHERE card_id=$1",
            int(card_id),
        )
        if not card:
            raise ValueError(f"Card not found: card_id={card_id}")

        await conn.execute(
            """
            INSERT INTO public.exchange_items (batch_id, card_id, card_name, hero_name)
            VALUES ($1, $2, $3, $4)
            """,
            int(batch_id),
            int(card_id),
            card["card_name"],
            card["hero_name"],
        )


@require_db_pool
async def add_exchange_items_for_deck(
        *,
        batch_id: int,
        deck_id: int,
) -> int:
    """Добавляет все карты колоды в exchange_items. Возвращает количество добавленных."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id, card_name, hero_name FROM public.cards WHERE deck_id=$1 ORDER BY card_id",
            int(deck_id),
        )
        if not rows:
            return 0

        await conn.executemany(
            """
            INSERT INTO public.exchange_items (batch_id, card_id, card_name, hero_name)
            VALUES ($1, $2, $3, $4)
            """,
            [
                (int(batch_id), int(r["card_id"]), r["card_name"], r["hero_name"])
                for r in rows
            ],
        )
        return len(rows)


@require_db_pool
async def get_pending_exchange_batches(
        *,
        limit: int = 30,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Список заявок биржи со статусом pending (для модерации)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT eb.batch_id,
                   eb.user_id,
                   u.username,
                   eb.deck_id,
                   d.name                                                                   AS deck_name,
                   eb.mode,
                   eb.currency,
                   eb.price,
                   eb.comment,
                   eb.proof_photo_id, -- ✅ ВОТ ЭТО
                   eb.created_at,
                   (SELECT COUNT(*) FROM exchange_items ei WHERE ei.batch_id = eb.batch_id) AS items_count
            FROM exchange_batches eb
                     LEFT JOIN users u ON u.user_id = eb.user_id
                     LEFT JOIN decks d ON d.id = eb.deck_id
            WHERE COALESCE(eb.status, 'pending') = 'pending'
            ORDER BY eb.created_at DESC
            LIMIT $1 OFFSET $2;

            """,
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def count_pending_exchange_batches() -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*)::int AS n FROM public.exchange_batches WHERE status='pending'"
        )
        return int(row["n"] or 0)


@require_db_pool
async def get_exchange_batch_by_id(batch_id: int) -> dict[str, Any] | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT eb.*, u.username AS username
            FROM public.exchange_batches eb
                     LEFT JOIN public.users u ON u.user_id = eb.user_id
            WHERE eb.batch_id = $1
            """,
            int(batch_id),
        )
    return dict(row) if row else None


@require_db_pool
async def get_exchange_cards_for_batch(batch_id: int) -> list[dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.card_id,
                   c.hero_name,
                   c.card_name,
                   COUNT(*)::int AS qty
            FROM public.exchange_items ei
                     JOIN public.cards c ON c.card_id = ei.card_id
            WHERE ei.batch_id = $1
            GROUP BY c.card_id, c.hero_name, c.card_name
            ORDER BY c.hero_name NULLS LAST, c.card_name
            """,
            int(batch_id),
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_print_stats(batch_id: int) -> dict[str, Any] | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.exchange_print_stats WHERE batch_id=$1",
            int(batch_id),
        )
    return dict(row) if row else None


@require_db_pool
async def upsert_exchange_print_stats(
        batch_id: int,
        *,
        winner_id: int | None = None,
        winner_name: str | None = None,
        price: int | None = None,
        link: str | None = None,
        updated_by: int | None = None,
) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.exchange_print_stats
            (batch_id, manual_winner_id, manual_winner_name, manual_price, manual_link, updated_by, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (batch_id) DO UPDATE SET manual_winner_id   = COALESCE(EXCLUDED.manual_winner_id,
                                                                               public.exchange_print_stats.manual_winner_id),
                                                 manual_winner_name = COALESCE(EXCLUDED.manual_winner_name,
                                                                               public.exchange_print_stats.manual_winner_name),
                                                 manual_price       = COALESCE(EXCLUDED.manual_price,
                                                                               public.exchange_print_stats.manual_price),
                                                 manual_link        = COALESCE(EXCLUDED.manual_link,
                                                                               public.exchange_print_stats.manual_link),
                                                 updated_by         = COALESCE(EXCLUDED.updated_by,
                                                                               public.exchange_print_stats.updated_by),
                                                 updated_at         = now()
            """,
            int(batch_id),
            winner_id,
            (winner_name or "").strip() or None,
            price,
            (link or "").strip() or None,
            int(updated_by) if updated_by is not None else None,
        )


@require_db_pool
async def reset_exchange_print_stats(batch_id: int, *, updated_by: int | None = None) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.exchange_print_stats
            (batch_id, manual_winner_id, manual_winner_name, manual_price, manual_link, updated_by, updated_at)
            VALUES ($1, NULL, NULL, NULL, NULL, $2, now())
            ON CONFLICT (batch_id) DO UPDATE SET manual_winner_id=NULL,
                                                 manual_winner_name=NULL,
                                                 manual_price=NULL,
                                                 manual_link=NULL,
                                                 updated_by=$2,
                                                 updated_at=now()
            """,
            int(batch_id),
            int(updated_by) if updated_by is not None else None,
        )


@require_db_pool
async def get_exchange_items_by_batch_id(batch_id: int) -> List[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT item_id, batch_id, card_id, card_name, hero_name, created_at
            FROM public.exchange_items
            WHERE batch_id = $1
            ORDER BY item_id
            """,
            int(batch_id),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def set_exchange_batch_status(batch_id: int, status: str) -> bool:
    """Меняет статус заявки на биржу.

    Важно: возвращаем bool, иначе хендлеры считают, что обновление не удалось.
    asyncpg.execute() возвращает строку вида 'UPDATE 1'.
    """
    async with db_pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE public.exchange_batches SET status=$2 WHERE batch_id=$1",
            int(batch_id),
            status,
        )
        try:
            n = int(str(res).split()[-1])
            return n > 0
        except Exception:
            return True


@require_db_pool
@require_db_pool
async def set_exchange_batch_moderation(
        batch_id: int,
        status: str,
        *,
        moderator_id: Optional[int] = None,
        moderator_username: Optional[str] = None,
        moderator_comment: Optional[str] = None,
) -> bool:
    """
    Обновляет статус биржи + метаданные модерации.
    Поддерживает обе схемы колонок (старые moderator_* и новые moderated_*), если они есть в БД.
    """
    status = (status or "").strip().lower()
    moderator_username = (moderator_username or "").strip() or None
    moderator_comment = (moderator_comment or "").strip() or None
    moderator_id = int(moderator_id) if moderator_id is not None else None

    async with db_pool.acquire() as conn:
        sets: list[str] = ["status = $2"]
        args: list[Any] = [int(batch_id), status]
        p = 3

        # новые колонки
        if await _has_column(conn, "exchange_batches", "moderated_at"):
            sets.append("moderated_at = NOW()")

        if moderator_id is not None and await _has_column(conn, "exchange_batches", "moderated_by"):
            sets.append(f"moderated_by = ${p}")
            args.append(moderator_id)
            p += 1

        if await _has_column(conn, "exchange_batches", "moderated_username"):
            sets.append(f"moderated_username = ${p}")
            args.append(moderator_username)
            p += 1

        if await _has_column(conn, "exchange_batches", "moderated_comment"):
            sets.append(f"moderated_comment = ${p}")
            args.append(moderator_comment)
            p += 1

        # старые колонки
        if moderator_id is not None and await _has_column(conn, "exchange_batches", "moderator_id"):
            sets.append(f"moderator_id = ${p}")
            args.append(moderator_id)
            p += 1

        if await _has_column(conn, "exchange_batches", "moderator_username"):
            sets.append(f"moderator_username = ${p}")
            args.append(moderator_username)
            p += 1

        if await _has_column(conn, "exchange_batches", "moderator_comment"):
            sets.append(f"moderator_comment = ${p}")
            args.append(moderator_comment)
            p += 1

        q = f"UPDATE public.exchange_batches SET {', '.join(sets)} WHERE batch_id = $1"
        res = await conn.execute(q, *args)
        # res вида "UPDATE 1"
        return res.strip().endswith("1")


@require_db_pool
async def set_exchange_batch_posted(
        batch_id: int,
        *,
        chat_id: int,
        message_id: int,
) -> None:
    """Сохраняем, что биржа уже опубликована (рассылка)."""
    async with db_pool.acquire() as conn:
        sets: list[str] = []
        args: list[Any] = [int(batch_id)]
        p = 2

        if await _has_column(conn, "exchange_batches", "posted_chat_id"):
            sets.append(f"posted_chat_id = ${p}")
            args.append(int(chat_id))
            p += 1

        if await _has_column(conn, "exchange_batches", "posted_message_id"):
            sets.append(f"posted_message_id = ${p}")
            args.append(int(message_id))
            p += 1

        if await _has_column(conn, "exchange_batches", "posted_at"):
            sets.append("posted_at = NOW()")

        if not sets:
            return

        q = f"UPDATE public.exchange_batches SET {', '.join(sets)} WHERE batch_id = $1"
        await conn.execute(q, *args)


@require_db_pool
async def set_exchange_batch_deleted(
        batch_id: int,
) -> None:
    """Помечаем удаление (мягко)."""
    async with db_pool.acquire() as conn:
        if not await _has_column(conn, "exchange_batches", "deleted_at"):
            return
        await conn.execute(
            "UPDATE public.exchange_batches SET deleted_at = NOW() WHERE batch_id = $1",
            int(batch_id),
        )


@require_db_pool
async def get_exchange_deck_overview(*, status: str = "approved") -> List[Dict[str, Any]]:
    """Сводка: сколько карточек на бирже по колодам."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.deck_id,
                   COALESCE(d.name, ('#' || c.deck_id::text)) AS deck_name,
                   COUNT(*)::int                              AS items_count,
                   COUNT(DISTINCT eb.batch_id)::int           AS batches_count
            FROM public.exchange_batches eb
                     JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                     JOIN public.cards c ON c.card_id = ei.card_id
                     LEFT JOIN public.decks d ON d.id = c.deck_id
            WHERE COALESCE(eb.status, 'pending') = $1
            GROUP BY c.deck_id, deck_name
            ORDER BY items_count DESC, c.deck_id
            """,
            status,
        )
        return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_owners_for_cards(
        card_ids: list[int],
        status: str = "approved",
) -> dict[int, list[dict[str, Any]]]:
    """
    Возвращает по card_id список владельцев (по batches/items):
      - user_id, username, qty, batch_id
    qty = SUM(qty) если колонка есть, иначе COUNT(*)
    """
    if not card_ids:
        return {}

    ids = [int(x) for x in card_ids]
    st = (status or "approved").strip()

    async with db_pool.acquire() as conn:
        has_qty = await conn.fetchval(
            """
            SELECT EXISTS (SELECT 1
                           FROM information_schema.columns
                           WHERE table_schema = 'public'
                             AND table_name = 'exchange_items'
                             AND column_name = 'qty')
            """
        )

        if has_qty:
            rows = await conn.fetch(
                """
                SELECT ei.card_id,
                       eb.batch_id,
                       eb.user_id,
                       u.username                    AS username,
                       COALESCE(SUM(ei.qty), 0)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = ANY ($1::int[])
                GROUP BY ei.card_id, eb.batch_id, eb.user_id, u.username
                ORDER BY ei.card_id, qty DESC, username NULLS LAST
                """,
                ids, st
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ei.card_id,
                       eb.batch_id,
                       eb.user_id,
                       u.username    AS username,
                       COUNT(*)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = ANY ($1::int[])
                GROUP BY ei.card_id, eb.batch_id, eb.user_id, u.username
                ORDER BY ei.card_id, qty DESC, username NULLS LAST
                """,
                ids, st
            )

    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        cid = int(r["card_id"])
        out.setdefault(cid, []).append(
            {
                "batch_id": int(r["batch_id"]),
                "user_id": int(r["user_id"]),
                "username": (r["username"] or "").strip() or None,
                "qty": int(r["qty"]),
            }
        )
    return out


@require_db_pool
async def get_exchange_owner_batches_for_card(
        card_id: int,
        status: str = "approved",
) -> list[dict[str, Any]]:
    cid = int(card_id)

    async with db_pool.acquire() as conn:
        try:
            # noinspection SqlNoDataSourceInspection,SqlResolve
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username                    AS username,
                       COALESCE(SUM(ei.qty), 0)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = $1
                GROUP BY eb.batch_id, eb.user_id, u.username
                ORDER BY qty DESC, eb.batch_id DESC
                """,
                cid,
                status,
            )
        except asyncpg.UndefinedColumnError:
            # noinspection SqlNoDataSourceInspection,SqlResolve
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username    AS username,
                       COUNT(*)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = $1
                GROUP BY eb.batch_id, eb.user_id, u.username
                ORDER BY qty DESC, eb.batch_id DESC
                """,
                cid,
                status,
            )

    return [
        {
            "batch_id": int(r["batch_id"]),
            "user_id": int(r["user_id"]),
            "username": (r["username"] or "").strip() or None,
            "qty": int(r["qty"]),
        }
        for r in rows
    ]


@require_db_pool
async def get_exchange_inventory_cards_for_deck(
        deck_id: int,
        *,
        status: str = "approved",
        limit: int = 30,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Карты на бирже по выбранной колоде (агрегация)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH approved AS (SELECT eb.batch_id, eb.currency, eb.price
                              FROM public.exchange_batches eb
                              WHERE COALESCE(eb.status, 'pending') = $2),
                 per_card AS (SELECT ei.card_id,
                                     COUNT(*)::int AS qty
                              FROM public.exchange_items ei
                                       JOIN approved a ON a.batch_id = ei.batch_id
                                       JOIN public.cards c ON c.card_id = ei.card_id
                              WHERE c.deck_id = $1
                              GROUP BY ei.card_id)
            SELECT c.card_id,
                   c.card_name,
                   c.hero_name,
                   c.rarity,
                   c.obtain_type,
                   c.obtain_amount,
                   p.qty
            FROM per_card p
                     JOIN public.cards c ON c.card_id = p.card_id
            ORDER BY p.qty DESC, c.card_id
            LIMIT $3 OFFSET $4
            """,
            int(deck_id),
            status,
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


MSK = ZoneInfo("Europe/Moscow")


@require_db_pool
async def get_print_win_missed_for_day(target_date: date) -> List[Dict[str, Any]]:
    sql = """
          WITH day_lots AS (SELECT a.auction_id,
                                   a.start_time,
                                   a.status,
                                   a.hero_name,
                                   a.card_name
                            FROM public.auctions a
                            WHERE a.start_time IS NOT NULL
                              AND CASE
                                    WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                                      THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                                    ELSE a.start_time::date
                                  END = $1
                              AND a.status IN ('scheduled', 'active', 'finished', 'approved')
                            ORDER BY a.start_time, a.auction_id),
               mailed_full AS (SELECT m.auction_id
                               FROM public.auction_win_mailings m
                               GROUP BY m.auction_id
                               HAVING SUM(CASE WHEN m.target IN ('owner', 'both') THEN 1 ELSE 0 END) > 0
                                  AND SUM(CASE WHEN m.target IN ('winner', 'both') THEN 1 ELSE 0 END) > 0),
               bids_cnt AS (SELECT b.auction_id,
                                   COUNT(*)::int AS bids_count
                            FROM public.bids b
                            GROUP BY b.auction_id)
          SELECT d.auction_id,
                 d.start_time,
                 d.status,
                 d.hero_name,
                 d.card_name,
                 COALESCE(bc.bids_count, 0) AS bids_count
          FROM day_lots d
                   LEFT JOIN mailed_full mf ON mf.auction_id = d.auction_id
                   LEFT JOIN bids_cnt bc ON bc.auction_id = d.auction_id
          WHERE mf.auction_id IS NULL
          ORDER BY d.start_time, d.auction_id \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, target_date)
    return [dict(r) for r in rows]


@require_db_pool
@require_db_pool
@require_db_pool
@require_db_pool
async def get_exchange_batch(batch_id: int, **_: Any) -> Optional[Dict[str, Any]]:
    """Back-compat: старое имя. Возвращает batch по id."""
    return await get_exchange_batch_by_id(batch_id)


@require_db_pool
@require_db_pool
@require_db_pool
@require_db_pool
@require_db_pool
async def auto_finish_old_lots_for_owner(user_id: int) -> int:
    """
    Закрываем старые лоты владельца, чтобы /my_lots показывал актуал.
    Возвращает количество закрытых лотов.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE auctions a
            SET status = 'finished'
            WHERE a.status = 'active'
              AND a.end_time IS NOT NULL
              AND a.end_time < NOW()
              AND EXISTS (SELECT 1
                          FROM auction_owners ao
                          WHERE ao.auction_id = a.auction_id
                            AND ao.user_id = $1)
            RETURNING a.auction_id
            """,
            user_id,
        )
        finished_ids = [int(r["auction_id"]) for r in rows]
        if not finished_ids:
            return 0

        # Если есть папки у владельца, переместим в archived
        col = None
        if await _has_column(conn, "auction_owners", "owner_folder"):
            col = "owner_folder"
        elif await _has_column(conn, "auction_owners", "folder"):
            col = "folder"

        if col:
            await conn.execute(
                f"""
                UPDATE auction_owners
SET {col} = 'archived'
                 WHERE user_id = $1
                   AND auction_id = ANY($2::int[])
                """,
                user_id,
                finished_ids,
            )

        return len(finished_ids)


@require_db_pool
async def set_owner_lot_folder(user_id: int, auction_id: int, folder: str) -> None:
    """
    Ставит "папку" (категорию) конкретного лота для владельца.
    Поддерживает owner_folder / folder (что реально в схеме).
    """
    f = (folder or "").strip().lower()
    if f == "archive":
        f = "archived"
    if f not in {"default", "payable", "archived"}:
        f = "default"

    async with db_pool.acquire() as conn:
        col = None
        if await _has_column(conn, "auction_owners", "owner_folder"):
            col = "owner_folder"
        elif await _has_column(conn, "auction_owners", "folder"):
            col = "folder"

        if not col:
            logger.warning(
                "auction_owners has no owner_folder/folder column; set_owner_lot_folder is a no-op"
            )
            return

        await conn.execute(
            f"""
            UPDATE auction_owners
               SET {col} = $3
             WHERE user_id = $1
               AND auction_id = $2
            """,
            int(user_id),
            int(auction_id),
            f,
        )


from typing import Sequence, Optional


async def get_lots_by_owner_view(
        owner_id: int,
        folder: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 20,
        offset: int = 0,
):
    """
    Лоты владельца с фильтром по статусам + папке (auction_owners.folder / owner_folder).
    folder:
      - None -> без фильтра по папке
      - default -> только default (и NULL тоже считаем default)
      - payable -> только payable
      - archived -> archived + старое archive
    """
    # статусы -> единый список
    all_statuses: list[str] = []
    if statuses:
        all_statuses.extend([s for s in statuses if s])
    if status:
        all_statuses.append(status)

    seen = set()
    all_statuses = [s for s in all_statuses if not (s in seen or seen.add(s))]

    async with db_pool.acquire() as conn:
        # какая колонка папки реально есть
        folder_col = None
        if await _has_column(conn, "auction_owners", "folder"):
            folder_col = "folder"
        elif await _has_column(conn, "auction_owners", "owner_folder"):
            folder_col = "owner_folder"

        where = ["ao.user_id = $1"]
        params: list[object] = [int(owner_id)]
        idx = 2

        if all_statuses:
            ph = []
            for s in all_statuses:
                ph.append(f"${idx}")
                params.append(s)
                idx += 1
            where.append(f"a.status IN ({', '.join(ph)})")

        # фильтр по папке
        f = (folder or "").strip().lower() if folder is not None else None
        if f == "archive":
            f = "archived"

        if f is not None and folder_col:
            if f == "default":
                where.append(f"COALESCE(ao.{folder_col}, 'default') = 'default'")
            elif f == "payable":
                where.append(f"ao.{folder_col} = 'payable'")
            elif f == "archived":
                where.append(f"ao.{folder_col} IN ('archived', 'archive')")
            else:
                where.append(f"COALESCE(ao.{folder_col}, 'default') = 'default'")

        params.append(int(limit))
        params.append(int(offset))

        folder_select = (
            f"COALESCE(ao.{folder_col}, 'default') AS folder"
            if folder_col
            else "'default'::text AS folder"
        )

        q = f"""
            SELECT
                a.auction_id,
                a.card_name,
                a.hero_name,
                a.image_id,
                a.start_price,
                a.start_time,
                a.end_time,
                a.status,
                a.currency,
                a.comment,
                a.message_id,
                a.discussion_message_id,
                a.proof_photo_id,
                a.created_at,
                {folder_select}
            FROM public.auctions a
            JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
            WHERE {" AND ".join(where)}
            ORDER BY COALESCE(a.start_time, a.created_at) DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        return await conn.fetch(q, *params)


@require_db_pool
async def show_pending_auction_lots(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """
    Для админки: pending-аукционы.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT auction_id,
                   hero_name,
                   card_name,
                   start_price,
                   currency,
                   accepted_currencies,
                   custom_offer_terms,
                   created_at,
                   comment,
                   auction_kind
            FROM auctions
            WHERE status = 'pending'
            ORDER BY created_at DESC, auction_id DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        return [dict(r) for r in rows]


@require_db_pool
async def count_sold_same_card(hero_name: str, card_name: str) -> int:
    """
    Сколько раз такая карта продавалась/закрывалась.
    """
    async with db_pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM auctions
                WHERE hero_name = $1
                  AND card_name = $2
                  AND status IN ('finished', 'sold', 'paid')
                """,
                hero_name, card_name,
            )
            or 0
        )


@require_db_pool
async def count_sold_by_card_id(card_id: int) -> int:
    """
    По card_id (из cards) считаем продажи через совпадение hero_name+card_name в auctions.
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hero_name, card_name FROM cards WHERE card_id=$1",
            card_id,
        )
        if not row:
            return 0
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM auctions
                WHERE hero_name = $1
                  AND card_name = $2
                  AND status IN ('finished', 'sold', 'paid')
                """,
                row["hero_name"], row["card_name"],
            )
            or 0
        )


@require_db_pool
async def count_pending_delete_requests_by_kind() -> dict[str, int]:
    """
    Админ-меню: сколько pending заявок на удаление по типам аукциона.
    Если в delete_requests нет колонки kind — считаем через auctions.auction_kind.
    """
    async with db_pool.acquire() as conn:
        kind_col = "kind" if await _has_column(conn, "delete_requests", "kind") else None

        if kind_col:
            rows = await conn.fetch(
                f"""
                SELECT {kind_col} AS kind, COUNT(*) AS cnt
                FROM delete_requests
                WHERE status='pending'
                GROUP BY {kind_col}
                """
            )
            out: dict[str, int] = {str(r["kind"]): int(r["cnt"]) for r in rows}
        else:
            rows = await conn.fetch(
                """
                SELECT COALESCE(a.auction_kind, 'standard') AS kind, COUNT(*) AS cnt
                FROM delete_requests dr
                         LEFT JOIN auctions a ON a.auction_id = dr.lot_id
                WHERE dr.status = 'pending'
                GROUP BY COALESCE(a.auction_kind, 'standard')
                """
            )
            out = {str(r["kind"]): int(r["cnt"]) for r in rows}

        # чтобы меню было стабильным
        for k in ("standard", "reverse", "fast", "free", "black", "exchange"):
            out.setdefault(k, 0)
        return out


async def get_deck_by_id(deck_id: int):
    return await get_deck(deck_id)


import asyncpg


@require_db_pool
async def set_card_video_by_id(
        card_id: int,
        video_file_id: str,
        *,
        unique_id: Optional[str] = None,
        thumb_file_id: Optional[str] = None,
) -> dict:
    """
    Ставит видео на карту:
      - cards.image_id = video_file_id
      - cards.media_type='video' (если колонка есть)

    И пытается обновить auctions.image_id для лотов этой карты
    (по hero_name + card_name; плюс fallback, если hero_name в auctions пустой).

    Важно: auctions может падать на CHECK по времени (NOT VALID, но enforced on UPDATE).
    Поэтому:
      - сначала пробуем апдейт с допуском <=2 сек (если ты поменял CHECK на допуск)
      - если упали на CheckViolationError, повторяем апдейт только для строго валидных строк
        (end_time = start_time + 31 minutes), чтобы не падать.
    """
    video_file_id = (video_file_id or "").strip()
    if not video_file_id:
        return {"ok": False, "reason": "empty_file_id"}

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow(
            "SELECT card_id, hero_name, card_name FROM public.cards WHERE card_id=$1",
            int(card_id),
        )
        if not card:
            return {"ok": False, "reason": "card_not_found"}

        hero_name = (card["hero_name"] or "").strip()
        card_name = (card["card_name"] or "").strip()

        has_media_type = await _has_column(conn, "cards", "media_type")

        # 1) обновляем карту
        if has_media_type:
            res_card = await conn.execute(
                "UPDATE public.cards SET image_id=$1, media_type='video' WHERE card_id=$2",
                video_file_id,
                int(card_id),
            )
        else:
            res_card = await conn.execute(
                "UPDATE public.cards SET image_id=$1 WHERE card_id=$2",
                video_file_id,
                int(card_id),
            )

        card_updated = int(res_card.split()[-1]) if res_card else 0

        # 2) обновляем auctions.image_id (аккуратно)
        auctions_updated_strict = 0
        auctions_updated_fallback = 0
        auctions_error = None

        async def _update_auctions_strict(where_time_sql: str) -> int:
            res = await conn.execute(
                f"""
                UPDATE public.auctions
                   SET image_id = $1
                 WHERE lower(trim(card_name)) = lower(trim($2))
                   AND lower(trim(coalesce(hero_name,''))) = lower(trim(coalesce($3,'')))
                   {where_time_sql}
                """,
                video_file_id,
                card_name,
                hero_name,
            )
            return int(res.split()[-1]) if res else 0

        async def _update_auctions_fallback(where_time_sql: str) -> int:
            # fallback только если card_name уникален среди cards
            cnt_same_name = await conn.fetchval(
                "SELECT COUNT(*) FROM public.cards WHERE lower(trim(card_name)) = lower(trim($1))",
                card_name,
            )
            if int(cnt_same_name or 0) != 1:
                return 0

            res = await conn.execute(
                f"""
                UPDATE public.auctions
                   SET image_id = $1
                 WHERE lower(trim(card_name)) = lower(trim($2))
                   AND (hero_name IS NULL OR trim(hero_name) = '')
                   {where_time_sql}
                """,
                video_file_id,
                card_name,
            )
            return int(res.split()[-1]) if res else 0

        # Пытаемся “мягко” (если у тебя CHECK уже с допуском)
        try:
            where_time_soft = """
              AND abs(extract(epoch from (end_time - (start_time + interval '31 minutes')))) <= 2
            """
            auctions_updated_strict = await _update_auctions_strict(where_time_soft)
            auctions_updated_fallback = await _update_auctions_fallback(where_time_soft)

        except asyncpg.exceptions.CheckViolationError as e:
            # CHECK строгий (или строки грязные) -> повторяем только на строго валидных строках
            auctions_error = str(e).splitlines()[0]

            try:
                where_time_strict = " AND end_time = (start_time + interval '31 minutes') "
                auctions_updated_strict = await _update_auctions_strict(where_time_strict)
                auctions_updated_fallback = await _update_auctions_fallback(where_time_strict)
            except Exception as e2:
                auctions_error = auctions_error or str(e2).splitlines()[0]

        return {
            "ok": True,
            "card_id": int(card_id),
            "hero_name": hero_name,
            "card_name": card_name,
            "card_updated": card_updated,
            "auctions_updated_strict": auctions_updated_strict,
            "auctions_updated_fallback": auctions_updated_fallback,
            "auctions_error": auctions_error,
            "has_media_type": has_media_type,
            "unique_id": unique_id,
            "thumb_file_id": thumb_file_id,
        }


# -------------------- post backfill stats --------------------

@require_db_pool
async def get_post_months() -> list[dict]:
    """
    Возвращает список месяцев (YYYY-MM) по таблице auction_posts_backfill.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT to_char(date_trunc('month', post_date_msk), 'YYYY-MM') AS ym,
                   COUNT(*)::int                                          AS cnt,
                   SUM(CASE WHEN s.checked THEN 1 ELSE 0 END)::int        AS checked_cnt
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE post_date_msk IS NOT NULL
              AND COALESCE(s.excluded, FALSE) = FALSE
            GROUP BY ym
            ORDER BY ym DESC
            """
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_post_days(ym: str) -> list[dict]:
    """
    ym: 'YYYY-MM'
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT (b.post_date_msk::date)                         AS day,
                   COUNT(*)::int                                   AS cnt,
                   SUM(CASE WHEN s.checked THEN 1 ELSE 0 END)::int AS checked_cnt
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE to_char(date_trunc('month', b.post_date_msk), 'YYYY-MM') = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            GROUP BY day
            ORDER BY day DESC
            """,
            ym,
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_posts_for_day(day: _date, offset: int = 0, limit: int = 12) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.post_id,
                   b.post_link,
                   b.post_date_msk,
                   b.end_time_msk,
                   b.deadline_msk,
                   b.thread_valid,
                   b.max_thread_valid,
                   b.winner_id,
                   COALESCE(s.checked, FALSE) AS checked
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_date_msk::date = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            ORDER BY b.post_date_msk DESC NULLS LAST
            OFFSET $2 LIMIT $3
            """,
            day, offset, limit,
        )
    return [dict(r) for r in rows]


@require_db_pool
async def count_posts_for_day(day: _date) -> int:
    async with db_pool.acquire() as conn:
        v = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_date_msk::date = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            """,
            day,
        )
    return int(v or 0)


@require_db_pool
async def get_post_details(post_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.*,
                   COALESCE(s.checked, FALSE)  AS checked,
                   COALESCE(s.excluded, FALSE) AS excluded,
                   s.excluded_by,
                   s.excluded_at,
                   s.excluded_reason,
                   s.checked_by,
                   s.checked_at,
                   s.manual_winner_id,
                   s.manual_max_bid,
                   s.manual_valid_bids,
                   s.manual_total_bids,
                   s.manual_note,
                   s.ordinal_no,
                   s.manual_date,
                   s.manual_time,
                   s.deck_no,
                   s.card_title,
                   s.bidders_count,
                   s.min_bid,
                   s.owner_id,
                   s.manual_link

            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_id = $1
            """,
            int(post_id),
        )
    return dict(row) if row else None


@require_db_pool
async def set_post_checked(post_id: int, checked: bool, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, checked, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET checked    = EXCLUDED.checked,
                                                checked_by = EXCLUDED.checked_by,
                                                checked_at = EXCLUDED.checked_at
            """,
            int(post_id), bool(checked), int(admin_id),
        )


@require_db_pool
async def set_post_manual_note(post_id: int, note: str | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, manual_note, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET manual_note = EXCLUDED.manual_note,
                                                checked_by  = EXCLUDED.checked_by,
                                                checked_at  = EXCLUDED.checked_at
            """,
            int(post_id), note, int(admin_id),
        )


@require_db_pool
async def set_post_manual_field(post_id: int, field: str, value: int | None, admin_id: int) -> None:
    """
    field: winner|max|valid|total
    value: int or None (очистить)
    """
    allowed = {
        "winner": "manual_winner_id",
        "max": "manual_max_bid",
        "valid": "manual_valid_bids",
        "total": "manual_total_bids",
    }
    col = allowed.get(field)
    if not col:
        raise ValueError(f"Unknown field: {field}")

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.auction_posts_stats(post_id, {col}, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET
                {col} = EXCLUDED.{col},
                checked_by = EXCLUDED.checked_by,
                checked_at = EXCLUDED.checked_at
            """,
            int(post_id),
            value,
            int(admin_id),
        )


@require_db_pool
async def set_post_excluded(post_id: int, excluded: bool, admin_id: int, reason: str | None = None) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, excluded, excluded_by, excluded_at, excluded_reason)
            VALUES ($1, $2, $3, NOW(), $4)
            ON CONFLICT (post_id) DO UPDATE SET excluded        = EXCLUDED.excluded,
                                                excluded_by     = EXCLUDED.excluded_by,
                                                excluded_at     = EXCLUDED.excluded_at,
                                                excluded_reason = EXCLUDED.excluded_reason
            """,
            int(post_id), bool(excluded), int(admin_id), reason,
        )


_FIELD_LABELS = {
    "ordinal": "Порядковый номер",
    "date": "Дата (ДД.ММ.ГГГГ)",
    "time": "Время выхода (ЧЧ:ММ или ЧЧ:ММ:СС)",
    "deck": "Номер колоды",
    "card": "Название карты (текст)",
    "bidders": "Кол-во участников ставок (число людей)",
    "min": "Минимальная ставка (число)",
    "max": "Максимальная ставка (число)",
    "owner": "Хозяин карты (user_id)",
    "winner": "Победитель (user_id)",
    "link": "Ссылка на аукцион (текст)",
}


@require_db_pool
async def set_post_stat_value(post_id: int, field: str, value, admin_id: int) -> None:
    """
    value может быть: int | str | date | time | None
    field - ключ из allowed
    """
    allowed = {
        # INT
        "ordinal": ("ordinal_no", "any"),
        "deck": ("deck_no", "any"),
        "bidders": ("bidders_count", "any"),
        "min": ("min_bid", "any"),
        "owner": ("owner_id", "any"),
        "winner": ("manual_winner_id", "any"),  # уже было
        "max": ("manual_max_bid", "any"),  # уже было

        # DATE/TIME
        "date": ("manual_date", "any"),
        "time": ("manual_time", "any"),

        # TEXT
        "card": ("card_title", "any"),
        "link": ("manual_link", "any"),
    }

    col = allowed.get(field, (None, None))[0]
    if not col:
        raise ValueError(f"Unknown field: {field}")

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.auction_posts_stats(post_id, {col}, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET
                {col} = EXCLUDED.{col},
                checked_by = EXCLUDED.checked_by,
                checked_at = EXCLUDED.checked_at
            """,
            int(post_id),
            value,
            int(admin_id),
        )


@require_db_pool
async def get_auction_ids_ended_on(day: date) -> list[int]:
    sql = """
          SELECT a.auction_id
          FROM public.auctions a
          WHERE a.end_time IS NOT NULL
            AND (a.end_time AT TIME ZONE 'Europe/Moscow')::date = $1
          ORDER BY a.end_time ASC \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, day)
    return [int(r["auction_id"]) for r in rows]


@require_db_pool
async def set_exchange_manual_winner(batch_id: int, winner_id: int | None, winner_username: str | None,
                                     admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_winner_id       = $2,
                manual_winner_username = $3,
                manual_set_by          = $4,
                manual_set_at          = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(winner_id) if winner_id is not None else None,
            (winner_username or "").strip() or None,
            int(admin_id),
        )


@require_db_pool
async def set_exchange_manual_price(batch_id: int, price: int | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_price  = $2,
                manual_set_by = $3,
                manual_set_at = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(price) if price is not None else None,
            int(admin_id),
        )


@require_db_pool
async def set_exchange_manual_link(batch_id: int, link: str | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_link   = $2,
                manual_set_by = $3,
                manual_set_at = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            (link or "").strip() or None,
            int(admin_id),
        )


@require_db_pool
async def reset_exchange_manual(batch_id: int, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_winner_id       = NULL,
                manual_winner_username = NULL,
                manual_price           = NULL,
                manual_link            = NULL,
                manual_set_by          = $2,
                manual_set_at          = NOW(),
                manual_sent_at         = NULL
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(admin_id),
        )


@require_db_pool
async def mark_exchange_manual_sent(batch_id: int) -> bool:
    """
    Атомарно лочит лот биржи (manual_sent_at).
    True  -> мы первые, залочили
    False -> уже кто-то залочил раньше
    """
    async with db_pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_sent_at = NOW()
            WHERE batch_id = $1
              AND manual_sent_at IS NULL
            """,
            int(batch_id),
        )

    # asyncpg возвращает строку вида "UPDATE 0" или "UPDATE 1"
    try:
        cnt = int(str(res).split()[-1])
    except Exception:
        cnt = 0
    return cnt == 1


@require_db_pool
async def get_cards_by_ids(card_ids: list[int]) -> list[dict]:
    if not card_ids:
        return []

    ids = [int(x) for x in card_ids if x is not None]
    if not ids:
        return []

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT card_id,
                   hero_name,
                   card_name,
                   rarity,
                   deck_id,
                   image_id,
                   story,
                   quote,
                   obtain_type,
                   obtain_amount,
                   media_type,
                   media_file_id,
                   media_unique_id,
                   thumb_file_id
            FROM public.cards
            WHERE card_id = ANY ($1::int[])
            """,
            ids,
        )

    m: dict[int, dict] = {}
    for r in rows:
        d = dict(r)

        # ✅ совместимость со старым названием полей
        d["gift_currency"] = d.get("obtain_type")
        d["gift_value"] = d.get("obtain_amount")

        # ✅ если где-то используешь image_id как “медиа”, подстрахуемся
        if not (d.get("image_id") or "").strip():
            mf = (d.get("media_file_id") or "").strip()
            if mf:
                d["image_id"] = mf

        m[int(d["card_id"])] = d

    return [m[cid] for cid in ids if cid in m]


@require_db_pool
async def get_cards_ids_by_deck(deck_id: int) -> list[int]:
    deck_id = int(deck_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id FROM public.cards WHERE deck_id=$1 ORDER BY card_id",
            deck_id,
        )
    return [int(r["card_id"]) for r in rows]


async def get_exchange_approved_cards_by_deck(deck_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT c.card_id,
               c.card_name,
               c.hero_name,
               COUNT(DISTINCT eb.batch_id)::int AS cnt
        FROM public.exchange_batches eb
                 JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                 JOIN public.cards c ON c.card_id = ei.card_id
        WHERE COALESCE(eb.status, 'pending') = 'approved'
          AND eb.deck_id = $1
          AND COALESCE(NULLIF(eb.mode, ''), 'card') IN ('card', 'deck_split')
        GROUP BY c.card_id, c.card_name, c.hero_name
        ORDER BY c.card_name ASC, c.hero_name ASC
        """,
        int(deck_id),
    )
    return [dict(r) for r in (rows or [])]


def _norm_uid(uid: str) -> str:
    return (uid or "").strip().replace(" ", "")


@require_db_pool
async def get_user_verified_uid(user_id: int) -> Optional[str]:
    uid = await fetchval(
        """
        SELECT uid
        FROM public.user_uids
        WHERE user_id = $1
          AND status = 'verified'
        """,
        int(user_id),
    )
    return str(uid) if uid else None


@require_db_pool
async def get_uid_owner(uid: str) -> Optional[dict]:
    row = await fetchrow(
        """
        SELECT uid, user_id, status, verified_at, verified_by
        FROM public.user_uids
        WHERE uid = $1
        """,
        _norm_uid(uid),
    )
    return dict(row) if row else None


@require_db_pool
async def is_uid_banned(uid: str) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.uid_bans
        WHERE uid = $1
          AND (banned_until IS NULL OR banned_until > NOW())
        """,
        _norm_uid(uid),
    )
    return bool(row)


@require_db_pool
async def is_user_uid_banned(user_id: int) -> bool:
    uid = await get_user_verified_uid(int(user_id))
    if not uid:
        return False
    return await is_uid_banned(uid)


async def _is_user_uid_verified(user_id: int | None) -> bool:
    if not user_id:
        return False

    row = await fetchrow(
        """
        SELECT 1
        FROM public.user_uids
        WHERE user_id = $1
          AND status = 'verified'
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)


async def _users_uid_verification_counts(user_ids: list[int] | None) -> tuple[int, int, bool]:
    ids = [int(x) for x in (user_ids or []) if x]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0, 0, False

    row = await fetchrow(
        """
        SELECT COUNT(*)::int                                                               AS total,
               COALESCE(SUM(CASE WHEN uu.status = 'verified' THEN 1 ELSE 0 END), 0)::int AS verified_cnt
        FROM unnest($1::bigint[]) AS u(user_id)
                 LEFT JOIN public.user_uids uu
                           ON uu.user_id = u.user_id
        """,
        ids,
    ) or {}

    total = int(row.get("total") or 0)
    verified_cnt = int(row.get("verified_cnt") or 0)
    return total, verified_cnt, (total > 0 and verified_cnt == total)


@require_db_pool
async def get_uid_verification_request(request_id: int | None = None, *, req_id: int | None = None) -> Optional[dict]:
    rid = int(request_id or req_id or 0)
    if rid <= 0:
        return None

    r = await fetchrow(
        """
        SELECT r.*, u.username, u.full_name
        FROM uid_verification_requests r
                 LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.id = $1
        """,
        rid,
    )
    if not r:
        return None

    confs = await fetch(
        """
        SELECT *
        FROM uid_verification_confirmations
        WHERE request_id = $1
        ORDER BY id
        """,
        rid,
    )

    out = dict(r)
    out["confirmations"] = [dict(c) for c in confs]
    return out


async def approve_uid_verification_request(
        request_id: int | None = None,
        admin_id: int | None = None,
        *,
        req_id: int | None = None,
) -> tuple[bool, str | None]:
    rid = int(request_id or req_id or 0)
    aid = int(admin_id or 0)
    if rid <= 0 or aid <= 0:
        return False, "bad_args"

    req = await fetchrow(
        "SELECT user_id, uid, status FROM uid_verification_requests WHERE id=$1",
        rid,
    )
    if not req:
        return False, "not_found"

    status = (req["status"] or "").strip().lower()
    if status != "pending":
        return False, "already_processed"

    user_id = int(req["user_id"])
    uid = (req["uid"] or "").strip()
    if not uid:
        return False, "uid_empty"

    owner = await get_uid_owner(uid)
    if owner and int(owner) != user_id:
        await mark_uid_verification_request_status(
            rid,
            "conflict",
            admin_id=aid,
            comment=f"UID already verified for user_id={int(owner)}",
        )
        return False, f"conflict:{int(owner)}"

    # на случай UNIQUE(user_id)
    await execute("DELETE FROM user_uids WHERE user_id=$1", user_id)

    await execute(
        """
        INSERT INTO user_uids (uid, user_id, status, verified_by)
        VALUES ($1, $2, 'verified', $3)
        ON CONFLICT (uid) DO UPDATE
            SET user_id=EXCLUDED.user_id,
                status='verified',
                verified_by=EXCLUDED.verified_by,
                updated_at=now()
        """,
        uid,
        user_id,
        aid,
    )

    await mark_uid_verification_request_status(rid, "approved", admin_id=aid)
    return True, None


async def reject_uid_verification_request(
        request_id: int | None = None,
        admin_id: int | None = None,
        reason: str = "",
        *,
        req_id: int | None = None,
        admin_comment: str | None = None,
) -> tuple[bool, str | None]:
    rid = int(request_id or req_id or 0)
    aid = int(admin_id or 0)
    comment = (admin_comment if admin_comment is not None else reason or "").strip()

    if rid <= 0 or aid <= 0 or not comment:
        return False, "bad_args"

    req = await fetchrow("SELECT status FROM uid_verification_requests WHERE id=$1", rid)
    if not req:
        return False, "not_found"

    status = (req["status"] or "").strip().lower()
    if status != "pending":
        return False, "already_processed"

    await mark_uid_verification_request_status(rid, "rejected", admin_id=aid, comment=comment)
    return True, None


@require_db_pool
async def reject_uid_verification_request(
        *,
        request_id: int,
        admin_id: int,
        admin_comment: str,
) -> bool:
    req = await get_uid_verification_request(int(request_id))
    if not req:
        return False

    await execute(
        """
        UPDATE public.uid_verification_requests
        SET status='rejected',
            decided_at=NOW(),
            decided_by=$2,
            admin_comment=$3
        WHERE id = $1
        """,
        int(request_id), int(admin_id), (admin_comment or "").strip()
    )
    return True


from typing import Any, Optional


async def get_verified_uid_for_user(user_id: int) -> Optional[str]:
    row = await fetchrow(
        "SELECT uid FROM user_uids WHERE user_id=$1 AND status='verified' LIMIT 1",
        int(user_id),
    )
    return row["uid"] if row else None


async def create_uid_verification_request(
        *,
        user_id: int,
        uid: str,
        verification_code: str,
        profile_proof_file_id: str,
        deal_file_ids: list[str],
        counterparty_usernames: list[str],
        status: str = "pending",
        uid_proof_file_id: str | None = None,
        reg_date_proof_file_id: str | None = None,
        extra_proof_file_ids: list[str] | None = None,
) -> int:
    uid = (uid or "").strip().lower()
    verification_code = (verification_code or "").strip().upper()
    profile_proof_file_id = (profile_proof_file_id or "").strip()
    if extra_proof_file_ids is None:
        extra_proof_file_ids = []
    if reg_date_proof_file_id is None:
        reg_date_proof_file_id = profile_proof_file_id

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO uid_verification_requests
            (user_id, uid, challenge_code, verification_code,
             profile_proof_file_id, uid_proof_file_id, reg_date_proof_file_id,
             deal_file_ids, counterparty_usernames, extra_proof_file_ids, status)
            VALUES ($1, $2, $3, $3,
                    $4, $5, $6,
                    $7, $8, $9, $10)
            RETURNING id
            """,
            int(user_id),
            uid,
            verification_code,
            profile_proof_file_id,
            uid_proof_file_id,
            reg_date_proof_file_id,
            deal_file_ids,
            counterparty_usernames,
            extra_proof_file_ids,
            status,
        )
    return int(row["id"])


@require_db_pool
async def add_uid_verification_confirmation(
        *,
        request_id: int,
        counterparty_user_id: int,
        counterparty_username: str,
) -> dict:
    uname = (counterparty_username or "").strip().lstrip("@").lower()
    row = await fetchrow(
        """
        WITH ins AS (
            INSERT INTO uid_verification_confirmations
                (request_id, counterparty_user_id, counterparty_username, status)
                VALUES ($1, $2, $3, 'pending')
                ON CONFLICT DO NOTHING
                RETURNING *)
        SELECT *
        FROM ins
        UNION ALL
        SELECT *
        FROM uid_verification_confirmations
        WHERE request_id = $1
          AND lower(counterparty_username) = lower($3)
        LIMIT 1
        """,
        int(request_id),
        int(counterparty_user_id),
        uname,
    )
    return dict(row)


async def set_uid_verification_confirmation_message(
        conf_id: int,
        chat_id: int,
        message_id: int,
) -> None:
    await execute(
        """
        UPDATE uid_verification_confirmations
        SET message_chat_id=$2,
            message_id=$3
        WHERE id = $1
        """,
        int(conf_id), int(chat_id), int(message_id),
    )


async def set_uid_verification_confirmation_status(
        *,
        conf_id: int | None = None,
        confirmation_id: int | None = None,
        status: str,
) -> bool:
    status = (status or "").strip().lower()
    _id = int(conf_id or confirmation_id or 0)

    allowed = ("confirmed", "rejected", "unreachable", "expired")
    if not _id or status not in allowed:
        return False

    row = await fetchrow(
        """
        UPDATE uid_verification_confirmations
        SET status=$2,
            decided_at=now()
        WHERE id = $1
          AND status = 'pending'
        RETURNING counterparty_user_id
        """,
        _id,
        status,
    )
    if not row:
        return False

    cp_id = row["counterparty_user_id"]

    # статистику пишем только по confirmed/rejected
    if cp_id and status in ("confirmed", "rejected"):
        if status == "confirmed":
            await execute(
                """
                UPDATE users
                SET uid_verif_confirmed_count   = COALESCE(uid_verif_confirmed_count, 0) + 1,
                    uid_verif_last_confirmed_at = now()
                WHERE user_id = $1
                """,
                int(cp_id),
            )
        else:
            await execute(
                """
                UPDATE users
                SET uid_verif_rejected_count   = COALESCE(uid_verif_rejected_count, 0) + 1,
                    uid_verif_last_rejected_at = now()
                WHERE user_id = $1
                """,
                int(cp_id),
            )

    return True


async def list_uid_verification_requests(status: str, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT r.*,
               u.username,
               u.full_name,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status = 'confirmed') AS confirmed_cnt,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status = 'rejected')  AS rejected_cnt,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id)    AS total_cnt
        FROM uid_verification_requests r
                 LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.status = $1
        ORDER BY r.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        status, int(limit), int(offset),
    )
    return [dict(x) for x in rows]


async def mark_uid_verification_request_status(request_id: int, status: str, *, admin_id: Optional[int] = None,
                                               comment: str = "") -> None:
    await execute(
        """
        UPDATE uid_verification_requests
        SET status=$2,
            decided_at=CASE WHEN $2 IN ('approved', 'rejected') THEN now() ELSE decided_at END,
            decided_by=COALESCE($3, decided_by),
            admin_comment=CASE WHEN $4 <> '' THEN $4 ELSE admin_comment END
        WHERE id = $1
        """,
        int(request_id), status, int(admin_id) if admin_id else None, comment,
    )


async def get_user_admin_info(user_id: int) -> Optional[dict[str, Any]]:
    r = await fetchrow(
        """
        SELECT user_id,
               username,
               full_name,
               is_subscribed,
               is_luxury,
               warnings_count,
               created_at,
               is_trusted
        FROM users
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(r) if r else None


async def get_user_admin_info_by_username(username: str) -> Optional[dict[str, Any]]:
    uname = username.strip().lstrip("@").lower()
    r = await fetchrow(
        """
        SELECT user_id,
               username,
               full_name,
               is_subscribed,
               is_luxury,
               warnings_count,
               created_at,
               is_trusted
        FROM users
        WHERE lower(username) = $1
        """,
        uname,
    )
    return dict(r) if r else None


async def get_user_basic_info(*, user_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT user_id, username, full_name
        FROM public.users
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(row) if row else None


async def get_user_basic_info_by_username(username: str) -> dict | None:
    uname = (username or "").strip()
    if uname.startswith("@"):
        uname = uname[1:]
    uname = uname.strip().lower()
    if not uname:
        return None

    row = await fetchrow(
        """
        SELECT u.user_id,
               u.username,
               u.full_name,
               u.is_luxury,
               u.created_at                                               AS registered_at,
               u.pm_opened,
               u.first_pm_at,
               u.last_pm_at,
               EXISTS(SELECT 1 FROM admins a WHERE a.user_id = u.user_id) AS is_admin
        FROM users u
        WHERE lower(u.username) = $1
        LIMIT 1
        """,
        uname,
    )
    return dict(row) if row else None


async def get_uid_verification_confirmation(*, confirmation_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT *
        FROM public.uid_verification_confirmations
        WHERE id = $1
        """,
        int(confirmation_id),
    )
    return dict(row) if row else None

@require_db_pool
async def get_uid_profile_binding(uid: str) -> dict | None:
    """
    Для админского /whois по UID:
    - есть ли verified-привязка в user_uids
    - есть ли заявка в uid_verification_requests
    - есть ли UID-ban
    """
    h = uid_hash(uid)

    verified = await fetchrow(
        """
        SELECT uu.user_id,
               u.username,
               u.full_name,
               uu.status,
               uu.verified_at,
               uu.verified_by,
               uu.uid_last4
        FROM public.user_uids uu
        LEFT JOIN public.users u ON u.user_id = uu.user_id
        WHERE uu.uid_hash = $1
        LIMIT 1
        """,
        h,
    )

    request = await fetchrow(
        """
        SELECT r.id,
               r.user_id,
               u.username,
               u.full_name,
               r.status,
               r.created_at,
               r.decided_at,
               r.uid_last4
        FROM public.uid_verification_requests r
        LEFT JOIN public.users u ON u.user_id = r.user_id
        WHERE r.uid_hash = $1
        ORDER BY r.id DESC
        LIMIT 1
        """,
        h,
    )

    ban = await fetchrow(
        """
        SELECT uid_hash,
               uid_last4,
               reason,
               banned_at,
               banned_until,
               banned_by
        FROM public.uid_bans
        WHERE uid_hash = $1
          AND (banned_until IS NULL OR banned_until > NOW())
        LIMIT 1
        """,
        h,
    )

    if not verified and not request and not ban:
        return None

    return {
        "verified": dict(verified) if verified else None,
        "request": dict(request) if request else None,
        "ban": dict(ban) if ban else None,
        "is_banned": bool(ban),
    }


@require_db_pool
async def get_user_id_by_uid_any(uid: str) -> Optional[int]:
    h = uid_hash(uid)

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.user_uids
        WHERE uid_hash = $1
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.uid_verification_requests
        WHERE uid_hash = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    return None
async def get_whois_admin_payload(*, user_id: int) -> dict | None:
    """
    Данные для /who и /whois:
    - user: базовая инфа + флаги + счётчики подтверждений
    - lots_posted: сколько лотов выставлял
    - uid_record: verified UID + его бан-статус
    - uid_verif: последняя заявка на UID-верификацию
    - unreachable: последняя недоступность
    - user_ban: активный user-ban
    - in_blacklist: итоговый флаг (user_ban OR uid_ban)
    """
    uid = int(user_id)

    try:
        u = await fetchrow(
            """
            SELECT u.user_id,
                   u.username,
                   u.full_name,
                   u.is_luxury,
                   u.warnings_count,
                   u.created_at,
                   u.is_trusted,
                   u.pm_opened,
                   u.first_pm_at,
                   u.last_pm_at,
                   u.uid_verif_confirmed_count,
                   u.uid_verif_rejected_count,
                   u.uid_verif_last_confirmed_at,
                   u.uid_verif_last_rejected_at,
                   (a.user_id IS NOT NULL) AS is_admin
            FROM public.users u
            LEFT JOIN public.admins a ON a.user_id = u.user_id
            WHERE u.user_id = $1
            """,
            uid,
        )
    except Exception:
        u = await fetchrow(
            """
            SELECT u.user_id,
                   u.username,
                   u.full_name,
                   u.is_luxury,
                   u.warnings_count,
                   u.created_at,
                   u.is_trusted,
                   u.pm_opened,
                   u.first_pm_at,
                   u.last_pm_at,
                   (a.user_id IS NOT NULL) AS is_admin
            FROM public.users u
            LEFT JOIN public.admins a ON a.user_id = u.user_id
            WHERE u.user_id = $1
            """,
            uid,
        )

    if not u:
        return None

    try:
        lots_posted = int(
            await fetchval(
                """
                SELECT COUNT(*)
                FROM public.auction_owners ao
                WHERE ao.user_id = $1
                """,
                uid,
            ) or 0
        )
    except Exception:
        lots_posted = 0

    uid_record: dict | None = None
    try:
        r_uid = await fetchrow(
            """
            SELECT uu.user_id,
                   uu.status,
                   uu.verified_at,
                   uu.verified_by,
                   uu.updated_at,
                   uu.uid_last4,
                   (ub.uid_hash IS NOT NULL) AS is_banned,
                   ub.banned_at,
                   ub.banned_by,
                   ub.banned_until,
                   ub.reason AS ban_reason
            FROM public.user_uids uu
            LEFT JOIN public.uid_bans ub
                   ON ub.uid_hash = uu.uid_hash
                  AND (ub.banned_until IS NULL OR ub.banned_until > NOW())
            WHERE uu.user_id = $1
            LIMIT 1
            """,
            uid,
        )
        uid_record = dict(r_uid) if r_uid else None
    except Exception:
        uid_record = None

    uid_verif = None
    try:
        r_ver = await fetchrow(
            """
            SELECT r.*, u.username, u.full_name
            FROM public.uid_verification_requests r
            LEFT JOIN public.users u ON u.user_id = r.user_id
            WHERE r.user_id = $1
            ORDER BY r.id DESC
            LIMIT 1
            """,
            uid,
        )
        if r_ver:
            rid = int(r_ver["id"])
            confs = await fetch(
                """
                SELECT *
                FROM public.uid_verification_confirmations
                WHERE request_id = $1
                ORDER BY id
                """,
                rid,
            )
            uid_verif = dict(r_ver)
            uid_verif["confirmations"] = [dict(c) for c in confs]
    except Exception:
        uid_verif = None

    unreachable = None
    try:
        unr = await fetchrow(
            """
            SELECT user_id, reason, last_seen
            FROM public.unreachable_users
            WHERE user_id = $1
            LIMIT 1
            """,
            uid,
        )
        unreachable = dict(unr) if unr else None
    except Exception:
        unreachable = None

    user_ban = None
    try:
        ub = await fetchrow(
            """
            SELECT user_id, banned_until, reason, issued_at
            FROM public.user_bans
            WHERE user_id = $1
              AND banned_until > NOW()
            ORDER BY issued_at DESC
            LIMIT 1
            """,
            uid,
        )
        user_ban = dict(ub) if ub else None
    except Exception:
        user_ban = None

    uid_in_blacklist = bool((uid_record or {}).get("is_banned"))
    user_in_blacklist = bool(user_ban)
    in_blacklist = bool(uid_in_blacklist or user_in_blacklist)

    return {
        "user": dict(u),
        "lots_posted": lots_posted,
        "uid_record": uid_record,
        "uid_verif": uid_verif,
        "unreachable": unreachable,
        "user_ban": user_ban,
        "uid_in_blacklist": uid_in_blacklist,
        "user_in_blacklist": user_in_blacklist,
        "in_blacklist": in_blacklist,
    }

@require_db_pool
async def get_user_id_by_uid_any(uid: str) -> Optional[int]:
    """
    Ищем пользователя:
    1) среди verified UID в user_uids
    2) если не нашли — среди заявок uid_verification_requests по uid_hash
    """
    h = uid_hash(uid)

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.user_uids
        WHERE uid_hash = $1
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    row = await fetchrow(
        """
        SELECT user_id
        FROM public.uid_verification_requests
        WHERE uid_hash = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        h,
    )
    if row and row.get("user_id"):
        return int(row["user_id"])

    return None


@require_db_pool
async def is_user_banned_now(user_id: int) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.user_bans
        WHERE user_id = $1
          AND banned_until > NOW()
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)
@require_db_pool
async def get_user_admin_info_by_username(username: str) -> dict | None:
    return await get_user_basic_info_by_username(username)


# ==================== UID Verification: revision / rework ====================

@require_db_pool
async def set_uid_verification_request_revision(
        request_id: int,
        *,
        admin_id: int,
        admin_username: str,
        flags: list[str],
        reason: str,
) -> bool:
    flags = [str(x).strip() for x in (flags or []) if str(x).strip()]
    if not request_id or not admin_id:
        return False

    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status               = 'revision',
            revision_reason      = $2,
            revision_flags       = $3,
            revision_by          = $4,
            revision_by_username = $5,
            revision_at          = now()
        WHERE id = $1
          AND status IN ('pending', 'conflict', 'revision')
        RETURNING 1 AS ok
        """,
        int(request_id),
        (reason or '').strip(),
        flags,
        int(admin_id),
        (admin_username or '').strip(),
    )
    return bool(row)


@require_db_pool
async def clear_uid_verification_request_revision(request_id: int) -> bool:
    if not request_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status               = 'pending',
            revision_reason      = NULL,
            revision_flags       = '{}'::text[],
            revision_by          = NULL,
            revision_by_username = NULL,
            revision_at          = NULL
        WHERE id = $1
          AND status = 'revision'
        RETURNING 1 AS ok
        """,
        int(request_id),
    )
    return bool(row)


@require_db_pool
async def set_uid_verification_request_profile_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET profile_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)


@require_db_pool
async def set_uid_verification_request_uid_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET uid_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)


@require_db_pool
async def set_uid_verification_request_reg_date_proof(request_id: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id:
        return False
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET reg_date_proof_file_id = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        str(packed_file_id),
    )
    return bool(row)


@require_db_pool
async def replace_uid_verification_request_extra_proofs(request_id: int, packed_file_ids: list[str]) -> bool:
    if not request_id:
        return False
    packed_file_ids = [str(x).strip() for x in (packed_file_ids or []) if str(x).strip()]
    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET extra_proof_file_ids = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        packed_file_ids,
    )
    return bool(row)


@require_db_pool
async def set_uid_verification_request_deal_media(request_id: int, idx: int, packed_file_id: str) -> bool:
    if not request_id or not packed_file_id or idx <= 0:
        return False

    row = await fetchrow(
        "SELECT deal_file_ids FROM public.uid_verification_requests WHERE id=$1",
        int(request_id),
    )
    if not row:
        return False

    arr = list(row.get("deal_file_ids") or [])
    if idx - 1 >= len(arr):
        return False

    arr[idx - 1] = str(packed_file_id)

    ok = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET deal_file_ids = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        arr,
    )
    return bool(ok)


@require_db_pool
async def set_uid_verification_request_deal_username(request_id: int, idx: int, username: str) -> bool:
    if not request_id or idx <= 0:
        return False

    username = (username or '').strip().lstrip('@').lower()
    if not username:
        return False

    row = await fetchrow(
        "SELECT counterparty_usernames FROM public.uid_verification_requests WHERE id=$1",
        int(request_id),
    )
    if not row:
        return False

    arr = list(row.get("counterparty_usernames") or [])
    if idx - 1 >= len(arr):
        return False

    arr[idx - 1] = username

    ok = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET counterparty_usernames = $2
        WHERE id = $1
        RETURNING 1 AS ok
        """,
        int(request_id),
        arr,
    )
    return bool(ok)


# ==================== UID VERIFICATION: EVENTS + REMINDERS + PERSISTENT REVISION FLAGS (APPEND) ====================
# Этот блок можно вставлять в самый низ файла db.py.

import json
from datetime import datetime, timedelta
from typing import Any, Optional


# ---------- schema ensure ----------

async def ensure_uid_verification_revision_schema() -> None:
    """
    Делает миграцию "мягко": если нет прав или уже всё есть — просто молча пропускает.
    """
    # 1) статус revision в CHECK
    try:
        await execute(
            """
            ALTER TABLE public.uid_verification_requests
                DROP CONSTRAINT IF EXISTS uid_verification_requests_status_check;
            """
        )
        await execute(
            """
            ALTER TABLE public.uid_verification_requests
                ADD CONSTRAINT uid_verification_requests_status_check
                    CHECK (status = ANY (ARRAY [
                        'pending','approved','rejected','conflict','revision'
                        ]));
            """
        )
    except Exception:
        # не фейлим запуск бота из-за прав/схемы
        pass

    # 2) поля доработки
    try:
        await execute(
            """
            ALTER TABLE public.uid_verification_requests
                ADD COLUMN IF NOT EXISTS revision_flags       text[] DEFAULT '{}'::text[] NOT NULL,
                ADD COLUMN IF NOT EXISTS revision_reason      text,
                ADD COLUMN IF NOT EXISTS revision_by          bigint,
                ADD COLUMN IF NOT EXISTS revision_by_username text,
                ADD COLUMN IF NOT EXISTS revision_at          timestamptz,
                ADD COLUMN IF NOT EXISTS revision_returned_at timestamptz;
            """
        )
    except Exception:
        pass


async def ensure_uid_verification_events_tables() -> None:
    try:
        await execute(
            """
            CREATE TABLE IF NOT EXISTS public.uid_verification_events
            (
                id             bigserial PRIMARY KEY,
                request_id     bigint                          NOT NULL
                    REFERENCES public.uid_verification_requests (id)
                        ON DELETE CASCADE,
                actor_id       bigint,
                actor_username text,
                event_type     text                            NOT NULL,
                details        jsonb       DEFAULT '{}'::jsonb NOT NULL,
                created_at     timestamptz DEFAULT now()       NOT NULL
            );
            """
        )
        await execute(
            """
            CREATE INDEX IF NOT EXISTS idx_uidv_events_req_time
                ON public.uid_verification_events (request_id, created_at DESC);
            """
        )
    except Exception:
        pass

    # маркеры напоминаний контрагентам (12/24/48), чтобы не спамить
    try:
        await execute(
            """
            CREATE TABLE IF NOT EXISTS public.uid_verification_confirmation_reminders
            (
                conf_id bigint                    NOT NULL
                    REFERENCES public.uid_verification_confirmations (id)
                        ON DELETE CASCADE,
                stage_h int                       NOT NULL,
                sent_at timestamptz DEFAULT now() NOT NULL,
                PRIMARY KEY (conf_id, stage_h)
            );
            """
        )
    except Exception:
        pass


# ---------- events API ----------

async def add_uid_verification_event(
        request_id: int,
        *,
        actor_id: Optional[int] = None,
        actor_username: Optional[str] = None,
        event_type: str,
        details_json: Optional[dict[str, Any]] = None,
) -> bool:
    try:
        payload = json.dumps(details_json or {}, ensure_ascii=False)
        await execute(
            """
            INSERT INTO public.uid_verification_events(request_id, actor_id, actor_username, event_type, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            int(request_id),
            int(actor_id) if actor_id is not None else None,
            str(actor_username) if actor_username else None,
            str(event_type),
            payload,
        )
        return True
    except Exception:
        return False


async def list_uid_verification_events(request_id: int, *, limit: int = 30) -> list[dict]:
    try:
        rows = await fetch(
            """
            SELECT actor_id, actor_username, event_type, details, created_at
            FROM public.uid_verification_events
            WHERE request_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            int(request_id),
            int(limit),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------- reminders markers ----------

async def mark_uid_verification_confirmation_reminder_sent(conf_id: int, stage_h: int) -> bool:
    try:
        await execute(
            """
            INSERT INTO public.uid_verification_confirmation_reminders(conf_id, stage_h)
            VALUES ($1, $2)
            ON CONFLICT (conf_id, stage_h) DO NOTHING
            """,
            int(conf_id),
            int(stage_h),
        )
        return True
    except Exception:
        return False


async def has_uid_verification_confirmation_reminder_sent(conf_id: int, stage_h: int) -> bool:
    try:
        row = await fetchrow(
            """
            SELECT 1
            FROM public.uid_verification_confirmation_reminders
            WHERE conf_id = $1
              AND stage_h = $2
            """,
            int(conf_id),
            int(stage_h),
        )
        return bool(row)
    except Exception:
        return False


# ---------- persistent revision flags (чекбоксы переживают перезапуск/выход из FSM) ----------

async def _uidv_remove_revision_flag(request_id: int, flag: str) -> None:
    try:
        await execute(
            """
            UPDATE public.uid_verification_requests
            SET revision_flags = array_remove(revision_flags, $2::text)
            WHERE id = $1
            """,
            int(request_id),
            str(flag),
        )
    except Exception:
        pass


# ---------- UID verification: stable API wrappers (schema-aligned) ----------
# В твоём проекте этот кусок нужен, потому что люди (включая тебя) уже успели
# развести несколько дублей функций с разными сигнатурами/возвратами.
# Итог был предсказуем: хендлеры думают, что всё ok, даже когда БД сказала «нет».

from typing import Optional  # noqa: E402

# базовая реализация (которая уже умеет менять статус confirmation + обновлять счётчики)
_uidv_set_conf_status_impl = set_uid_verification_confirmation_status  # type: ignore[name-defined]


async def set_uid_verification_confirmation_status(  # type: ignore[override]
        confirmation_id: int | None = None,
        status: str | None = None,
        decided_by: int | None = None,
        decided_by_username: str | None = None,
        *,
        conf_id: int | None = None,
) -> bool:
    """Совместимая обёртка.

    В БД НЕТ decided_by / decided_by_username для confirmations, поэтому эти параметры
    просто принимаем и игнорируем, чтобы не падать TypeError-ами.
    """
    cid = int(confirmation_id or conf_id or 0)
    st = (status or "").strip().lower()
    if cid <= 0 or st not in {"confirmed", "rejected"}:
        return False
    return await _uidv_set_conf_status_impl(confirmation_id=cid, status=st)


async def mark_uid_verification_request_status(  # type: ignore[override]
        request_id: int,
        status: str,
        admin_id: int | None = None,
        admin_username: str | None = None,
        comment: str | None = None,
) -> bool:
    """Меняет статус заявки (requests) и пишет событие.

    В БД НЕТ decided_by_username, поэтому username идёт только в события.
    """
    rid = int(request_id or 0)
    st = (status or "").strip().lower()
    if rid <= 0 or not st:
        return False

    cmt = (comment or "").strip()

    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status=$2,
            decided_at=CASE WHEN $2 IN ('approved', 'rejected', 'conflict') THEN now() ELSE decided_at END,
            decided_by=COALESCE($3, decided_by),
            admin_comment=CASE WHEN $4 <> '' THEN $4 ELSE admin_comment END
        WHERE id = $1
        RETURNING id
        """,
        rid,
        st,
        int(admin_id) if admin_id else None,
        cmt,
    )
    ok = bool(row)

    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=admin_id,
                actor_username=admin_username,
                event_type="request_status_changed",
                details_json={"status": st, "comment": cmt},
            )
        except Exception:
            pass

    return ok


async def approve_uid_verification_request(  # type: ignore[override]
        request_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
        *,
        req_id: int | None = None,
) -> tuple[bool, str]:
    """Апрув заявки: привязка UID к пользователю + статус approved.

    Возврат всегда (ok, msg). msg пустая строка при успехе.
    """
    rid = int(request_id or req_id or 0)
    aid = int(admin_id or 0)
    if rid <= 0 or aid <= 0:
        return False, "bad_args"

    req = await fetchrow(
        "SELECT user_id, uid, status FROM public.uid_verification_requests WHERE id=$1",
        rid,
    )
    if not req:
        return False, "not_found"

    status = (req["status"] or "").strip().lower()
    if status != "pending":
        return False, "already_processed"

    user_id = int(req["user_id"] or 0)
    uid = (req["uid"] or "").strip()
    if user_id <= 0:
        return False, "user_id_empty"
    if not uid:
        return False, "uid_empty"

    owner = await get_uid_owner(uid)
    if owner and int(owner) != user_id:
        await mark_uid_verification_request_status(
            rid,
            "conflict",
            admin_id=aid,
            admin_username=admin_username,
            comment=f"UID already verified for user_id={int(owner)}",
        )
        return False, f"conflict:{int(owner)}"

    # на случай UNIQUE(user_id) (если оно у тебя есть)
    await execute("DELETE FROM public.user_uids WHERE user_id=$1", user_id)

    await execute(
        """
        INSERT INTO public.user_uids (uid, user_id, status, verified_by)
        VALUES ($1, $2, 'verified', $3)
        ON CONFLICT (uid) DO UPDATE
            SET user_id=EXCLUDED.user_id,
                status='verified',
                verified_by=EXCLUDED.verified_by,
                updated_at=now()
        """,
        uid,
        user_id,
        aid,
    )

    ok = await mark_uid_verification_request_status(
        rid,
        "approved",
        admin_id=aid,
        admin_username=admin_username,
    )
    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=aid,
                actor_username=admin_username,
                event_type="request_approved",
                details_json={},
            )
        except Exception:
            pass
        return True, ""

    return False, "db_failed"


async def reject_uid_verification_request(  # type: ignore[override]
        request_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
        reason: str | None = None,
        comment: str | None = None,
        admin_comment: str | None = None,
        *,
        req_id: int | None = None,
) -> tuple[bool, str]:
    """Реджект заявки.

    Возврат всегда (ok, msg). msg пустая строка при успехе.
    """
    rid = int(request_id or req_id or 0)
    aid = int(admin_id or 0)
    if rid <= 0 or aid <= 0:
        return False, "bad_args"

    cmt = (admin_comment or reason or comment or "").strip()

    ok = await mark_uid_verification_request_status(
        rid,
        "rejected",
        admin_id=aid,
        admin_username=admin_username,
        comment=cmt,
    )
    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=aid,
                actor_username=admin_username,
                event_type="request_rejected",
                details_json={"comment": cmt},
            )
        except Exception:
            pass
        return True, ""

    return False, "db_failed"


async def set_uid_verification_request_revision(  # type: ignore[override]
        request_id: int | None = None,
        moderator_id: int | None = None,
        moderator_username: str | None = None,
        reason: str | None = None,
        flags: list[str] | None = None,
        *,
        req_id: int | None = None,
        admin_id: int | None = None,
        admin_username: str | None = None,
) -> bool:
    """Перевод заявки в 'revision' (нужно исправить).

    В БД НЕТ revision_completed_at, поэтому используем:
      - revision_requested_at (когда запросили правки)
      - revision_returned_at (когда пользователь вернул исправления)
      - revision_at (когда админ подтвердил возврат/закрыл правки)
    """
    rid = int(request_id or req_id or 0)
    aid = int(moderator_id or admin_id or 0)
    auser = moderator_username or admin_username
    rsn = (reason or "").strip()
    flg = flags or []

    if rid <= 0 or aid <= 0:
        return False

    row = await fetchrow(
        """
        UPDATE public.uid_verification_requests
        SET status='revision',
            revision_flags=$2,
            revision_reason=$3,
            revision_requested_at=now(),
            revision_at=NULL,
            revision_returned_at=NULL,
            revision_by=$4,
            revision_by_username=$5
        WHERE id = $1
          AND status IN ('pending', 'revision')
        RETURNING id
        """,
        rid,
        flg,
        rsn if rsn else None,
        aid,
        auser,
    )
    ok = bool(row)

    if ok:
        try:
            await add_uid_verification_event(
                rid,
                actor_id=aid,
                actor_username=auser,
                event_type="request_revision_required",
                details_json={"flags": flg, "reason": rsn},
            )
        except Exception:
            pass

    return ok


# ===================== UIDV COMPAT ALIASES (append) =====================
# ВСТАВИТЬ В САМЫЙ НИЗ db.py
# Чинит "Unresolved reference update_uid_verification_confirmation_status"

async def update_uid_verification_confirmation_status(
        confirmation_id: int,
        status: str,
        decided_by: int | None = None,
        decided_by_username: str | None = None,
) -> bool:
    """
    Back-compat alias: часть кода/импортов ждала update_*,
    а реальная функция у нас set_uid_verification_confirmation_status().
    """
    return await set_uid_verification_confirmation_status(
        confirmation_id=int(confirmation_id),
        status=str(status),
        decided_by=decided_by,
        decided_by_username=decided_by_username,
    )

# =================== /UIDV COMPAT ALIASES (append) ======================
