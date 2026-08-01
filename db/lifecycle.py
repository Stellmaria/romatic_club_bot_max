"""Database startup and shutdown orchestration."""

from __future__ import annotations

from bot.core.settings import DatabaseSettings
from db.migrator import apply_migrations
from db.pool import DatabaseRuntime


async def init_db(
    runtime_or_settings: DatabaseRuntime | DatabaseSettings,
) -> DatabaseRuntime:
    """Start one explicit runtime and apply migrations before serving work."""

    from db.core import (
        current_database_runtime,
        install_database_runtime,
        logger,
    )

    if isinstance(runtime_or_settings, DatabaseRuntime):
        runtime = runtime_or_settings
    else:
        runtime = current_database_runtime() or DatabaseRuntime(runtime_or_settings)
        runtime.configure(runtime_or_settings)

    install_database_runtime(runtime)
    pool = await runtime.start()
    settings = runtime.settings
    if settings is None:
        raise RuntimeError("database runtime has no settings")
    if not settings.auto_migrate:
        logger.warning("Automatic migrations are disabled: DB_AUTO_MIGRATE=false")
        return runtime

    try:
        await apply_migrations(pool)
    except Exception:
        logger.exception("Database migration failed")
        await close_db(runtime)
        raise
    return runtime


async def close_db(runtime: DatabaseRuntime | None = None) -> None:
    """Close exactly the runtime owned by this application lifecycle."""

    from db.core import (
        current_database_runtime,
        logger,
        uninstall_database_runtime,
    )

    target = runtime or current_database_runtime()
    if target is None:
        return
    await target.close()
    uninstall_database_runtime(target)
    logger.info("Database runtime closed")


__all__ = ["close_db", "init_db"]
