from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger("auction_bot.db.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "database" / "migrations"
# Packaged migration milestones include "008_auction_slot_policy.sql" and
# "009_auction_end_second_59.sql".
MIGRATION_LOCK_KEY = 0x4D494752  # "MIGR"


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return ordered packaged migrations or fail before touching the database."""
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No database migrations found in {directory}")
    return files


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply ordered, idempotent SQL migrations exactly once.

    A checksum is stored with every migration. If an already-applied file is
    edited later, startup fails instead of silently running an unknown schema.
    """
    migration_paths = migration_files()

    async with pool.acquire() as conn:
        # Multiple bot replicas may start together.  Serialize the complete
        # migration scan so two processes cannot apply the same file at once.
        await conn.execute("SELECT pg_advisory_lock($1::bigint)", MIGRATION_LOCK_KEY)
        try:
            return await _apply_locked(conn, migration_paths)
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::bigint)",
                MIGRATION_LOCK_KEY,
            )


async def _apply_locked(
    conn: asyncpg.Connection,
    migration_files: list[Path],
) -> list[str]:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version text PRIMARY KEY,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    rows = await conn.fetch("SELECT version, checksum FROM public.schema_migrations")
    applied = {str(row["version"]): str(row["checksum"]) for row in rows}
    completed: list[str] = []

    for path in migration_files:
        version = path.name
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

        previous = applied.get(version)
        if previous:
            if previous != checksum:
                raise RuntimeError(
                    f"Migration {version} was modified after being applied "
                    f"(database={previous}, file={checksum})"
                )
            continue

        logger.info("Applying database migration %s", version)
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO public.schema_migrations(version, checksum) VALUES ($1, $2)",
                version,
                checksum,
            )
        completed.append(version)

    return completed
