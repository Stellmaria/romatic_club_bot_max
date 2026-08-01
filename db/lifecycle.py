"""Database startup and shutdown orchestration."""

from __future__ import annotations

from bot.core.settings import DatabaseSettings
from db.migrator import apply_migrations
from db.pool import close_db_pool, configure_database


async def init_db(settings: DatabaseSettings) -> None:
    """Initialize the pool and apply migrations before serving work."""

    from db.core import get_db_pool, logger

    configure_database(settings)
    pool = await get_db_pool(settings)
    if not settings.auto_migrate:
        logger.warning("Automatic migrations are disabled: DB_AUTO_MIGRATE=false")
        return
    try:
        await apply_migrations(pool)
    except Exception:
        logger.exception("Database migration failed")
        await close_db()
        raise


async def close_db() -> None:
    from db.core import db_pool, logger

    if db_pool.pool is None:
        return
    await close_db_pool()
    db_pool.clear()
    try:
        from db import legacy_impl

        legacy_impl.db_pool = None
    except ImportError:
        pass
    logger.info("Database pool closed")
