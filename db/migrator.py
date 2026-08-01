from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

logger = logging.getLogger("auction_bot.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_NAME_RE = re.compile(r"^(?P<version>\d{3,})_[a-z0-9_]+\.sql$")
MIGRATION_LOCK_ID = 7_423_102_026_071_5
MIGRATION_TABLE = "schema_migrations"


@dataclass(frozen=True, slots=True)
class Migration:
    filename: str
    version: int
    path: Path
    sql: str
    checksum: str
    compatible_checksums: frozenset[str]


def _migration_checksums(raw: bytes) -> tuple[str, frozenset[str]]:
    """Return a stable checksum and compatible historical line-ending hashes."""
    lf_bytes = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    canonical = hashlib.sha256(lf_bytes).hexdigest()
    return canonical, frozenset(
        {
            canonical,
            hashlib.sha256(raw).hexdigest(),
            hashlib.sha256(crlf_bytes).hexdigest(),
        }
    )


def _load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    if not directory.is_dir():
        raise RuntimeError(f"Каталог миграций не найден: {directory}")

    migrations: list[Migration] = []
    seen_versions: set[int] = set()

    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME_RE.fullmatch(path.name)
        if not match:
            raise RuntimeError(
                f"Неверное имя миграции {path.name!r}. "
                "Ожидается формат 001_description.sql"
            )

        version = int(match.group("version"))
        if version in seen_versions:
            raise RuntimeError(f"Повторяется номер миграции: {version}")
        seen_versions.add(version)

        raw = path.read_bytes()
        sql = raw.decode("utf-8-sig").strip()
        if not sql:
            raise RuntimeError(f"Пустая миграция: {path.name}")

        checksum, compatible_checksums = _migration_checksums(raw)
        migrations.append(
            Migration(
                filename=path.name,
                version=version,
                path=path,
                sql=sql,
                checksum=checksum,
                compatible_checksums=compatible_checksums,
            )
        )

    if not migrations:
        raise RuntimeError(f"В каталоге {directory} нет SQL-миграций")

    return migrations


async def _table_exists(conn: asyncpg.Connection, table_name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL",
            f"public.{table_name}",
        )
    )


async def _migration_table_columns(
    conn: asyncpg.Connection,
    table_name: str = MIGRATION_TABLE,
) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
        """,
        table_name,
    )
    return {str(row["column_name"]): str(row["udt_name"]) for row in rows}


def _is_current_migration_table(columns: dict[str, str]) -> bool:
    required = {
        "filename": {"text", "varchar"},
        "version": {"int2", "int4", "int8"},
        "checksum": {"text", "varchar"},
        "applied_at": {"timestamp", "timestamptz"},
        "execution_ms": {"int2", "int4", "int8"},
        "postgres_version": {"text", "varchar"},
    }
    return all(
        name in columns and columns[name] in accepted_types
        for name, accepted_types in required.items()
    )


async def _next_legacy_table_name(conn: asyncpg.Connection) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"schema_migrations_legacy_{stamp}"
    candidate = base
    suffix = 1

    while await _table_exists(conn, candidate):
        suffix += 1
        candidate = f"{base}_{suffix}"

    return candidate


async def _quote_identifier(conn: asyncpg.Connection, identifier: str) -> str:
    quoted = await conn.fetchval("SELECT quote_ident($1)", identifier)
    return str(quoted)


async def _archive_legacy_migration_table(conn: asyncpg.Connection) -> str:
    legacy_name = await _next_legacy_table_name(conn)
    quoted_old = await _quote_identifier(conn, MIGRATION_TABLE)
    quoted_new = await _quote_identifier(conn, legacy_name)
    await conn.execute(
        f"ALTER TABLE public.{quoted_old} RENAME TO {quoted_new}"
    )
    return legacy_name


async def _ensure_migration_table(conn: asyncpg.Connection) -> None:
    """
    Создаёт таблицу нового формата.

    В старых версиях проекта уже могла существовать public.schema_migrations
    с колонками вроде version/applied_at, но без filename/checksum. PostgreSQL
    не изменяет такую таблицу при CREATE TABLE IF NOT EXISTS, поэтому прежний
    мигратор падал на SELECT filename. Несовместимая таблица сохраняется под
    именем schema_migrations_legacy_*, а новая создаётся рядом. Данные проекта
    при этом не затрагиваются.
    """
    await conn.execute("CREATE SCHEMA IF NOT EXISTS public")

    if await _table_exists(conn, MIGRATION_TABLE):
        columns = await _migration_table_columns(conn)
        if not _is_current_migration_table(columns):
            legacy_name = await _archive_legacy_migration_table(conn)
            logger.warning(
                "Обнаружена старая таблица public.%s с несовместимой структурой. "
                "Она сохранена как public.%s; создан новый журнал миграций.",
                MIGRATION_TABLE,
                legacy_name,
            )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            filename          text PRIMARY KEY,
            version           integer NOT NULL UNIQUE,
            checksum          text NOT NULL,
            applied_at        timestamptz NOT NULL DEFAULT now(),
            execution_ms      integer NOT NULL,
            postgres_version  text NOT NULL
        )
        """
    )


async def _applied_migrations(conn: asyncpg.Connection) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """
        SELECT filename, version, checksum, applied_at
        FROM public.schema_migrations
        ORDER BY version
        """
    )
    return {str(row["filename"]): row for row in rows}


async def apply_migrations(
    pool: asyncpg.Pool,
    *,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Применяет отсутствующие миграции и возвращает их имена."""
    migrations = _load_migrations(directory)
    applied_now: list[str] = []

    async with pool.acquire() as conn:
        await conn.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await _ensure_migration_table(conn)
            applied = await _applied_migrations(conn)
            max_applied_version = max(
                (int(row["version"]) for row in applied.values()),
                default=0,
            )

            for migration in migrations:
                previous = applied.get(migration.filename)
                if previous is not None:
                    previous_checksum = str(previous["checksum"])
                    if previous_checksum not in migration.compatible_checksums:
                        raise RuntimeError(
                            "Уже применённая миграция была изменена: "
                            f"{migration.filename}. Создай новую миграцию вместо "
                            "редактирования старой."
                        )
                    continue

                if migration.version < max_applied_version:
                    raise RuntimeError(
                        "Обнаружена новая миграция с номером ниже уже применённых: "
                        f"{migration.filename}. Добавляй миграции только в конец истории."
                    )

                conflicting = next(
                    (
                        row
                        for row in applied.values()
                        if int(row["version"]) == migration.version
                    ),
                    None,
                )
                if conflicting is not None:
                    raise RuntimeError(
                        f"Номер {migration.version} уже занят миграцией "
                        f"{conflicting['filename']}"
                    )

                started = time.perf_counter()
                logger.info("Применяется миграция %s", migration.filename)

                async with conn.transaction():
                    await conn.execute(migration.sql)
                    elapsed_ms = max(
                        0,
                        round((time.perf_counter() - started) * 1000),
                    )
                    pg_version = await conn.fetchval("SHOW server_version")
                    await conn.execute(
                        """
                        INSERT INTO public.schema_migrations (
                            filename,
                            version,
                            checksum,
                            execution_ms,
                            postgres_version
                        )
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        migration.filename,
                        migration.version,
                        migration.checksum,
                        elapsed_ms,
                        str(pg_version),
                    )

                applied_now.append(migration.filename)
                applied[migration.filename] = {
                    "filename": migration.filename,
                    "version": migration.version,
                    "checksum": migration.checksum,
                }
                max_applied_version = migration.version
                logger.info(
                    "Миграция %s применена за %d мс",
                    migration.filename,
                    elapsed_ms,
                )
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)

    if applied_now:
        logger.info("Применены миграции: %s", ", ".join(applied_now))
    else:
        logger.info("Схема базы данных уже актуальна")

    return applied_now


async def migrate_database_url(database_url: str) -> list[str]:
    if not database_url:
        raise RuntimeError("DATABASE_URL не задан")

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        return await apply_migrations(pool)
    finally:
        await pool.close()


async def _main() -> None:
    from bot.core.legacy_config import legacy_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await migrate_database_url(legacy_config.DATABASE_URL)


if __name__ == "__main__":
    asyncio.run(_main())
