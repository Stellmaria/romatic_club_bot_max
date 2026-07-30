"""Database startup and shutdown orchestration."""

from __future__ import annotations

from db.migrator import apply_migrations
from bot.core.legacy_config import DB_AUTO_MIGRATE


async def init_db() -> None:
    """Initialize the pool and apply migrations before serving work."""
    from db.core import get_db_pool, logger

    pool = await get_db_pool()
    if not DB_AUTO_MIGRATE:
        logger.warning("Automatic migrations are disabled: DB_AUTO_MIGRATE=0")
        return
    try:
        await apply_migrations(pool)
    except Exception:
        logger.exception("Database migration failed")
        await close_db()
        raise


async def close_db() -> None:
    from db.core import db_pool, logger

    pool = db_pool.pool
    if pool is None:
        return
    await pool.close()
    db_pool.clear()
    try:
        from db import legacy_impl

        legacy_impl.db_pool = None
    except ImportError:
        pass
    logger.info("Database pool closed")
