from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger("auction_bot.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_NAME_RE = re.compile(r"^(?P<version>\d{3,})_[a-z0-9_]+\.sql$")
MIGRATION_POLICY_RE = re.compile(
    r"^--\s*(?P<key>compatibility|rollback|note)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
MIGRATION_LOCK_ID = 7_423_102_026_071_5
MIGRATION_TABLE = "schema_migrations"
LEGACY_POLICY_MAX_VERSION = 19
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class MigrationCompatibility(StrEnum):
    LEGACY = "legacy"
    EXPAND = "expand"
    CONTRACT = "contract"


class RollbackStrategy(StrEnum):
    CODE_ONLY_SAFE = "code-only-safe"
    FORWARD_FIX = "forward-fix"
    RESTORE_REQUIRED = "restore-required"


@dataclass(frozen=True, slots=True)
class MigrationPolicy:
    compatibility: MigrationCompatibility
    rollback: RollbackStrategy
    note: str

    @property
    def code_rollback_safe(self) -> bool:
        return self.rollback is RollbackStrategy.CODE_ONLY_SAFE


@dataclass(frozen=True, slots=True)
class Migration:
    filename: str
    version: int
    path: Path
    sql: str
    checksum: str
    compatible_checksums: frozenset[str]
    policy: MigrationPolicy


@dataclass(frozen=True, slots=True)
class MigrationPlanItem:
    filename: str
    version: int
    checksum: str
    state: str
    compatibility: str
    rollback: str
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "version": self.version,
            "checksum": self.checksum,
            "state": self.state,
            "compatibility": self.compatibility,
            "rollback": self.rollback,
            "note": self.note,
        }


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


def _legacy_policy(filename: str) -> MigrationPolicy:
    return MigrationPolicy(
        compatibility=MigrationCompatibility.LEGACY,
        rollback=RollbackStrategy.RESTORE_REQUIRED,
        note=(
            "Immutable legacy production migration. Code rollback is not assumed; "
            f"restore the verified pre-deploy backup if {filename} must be reversed."
        ),
    )


def _parse_migration_policy(*, filename: str, version: int, sql: str) -> MigrationPolicy:
    metadata: dict[str, str] = {}
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = MIGRATION_POLICY_RE.fullmatch(stripped)
        if match:
            metadata[match.group("key").lower()] = match.group("value").strip()
            continue
        if stripped.startswith("--"):
            continue
        break

    if not metadata and version <= LEGACY_POLICY_MAX_VERSION:
        return _legacy_policy(filename)

    missing = sorted({"compatibility", "rollback", "note"} - metadata.keys())
    if missing:
        raise RuntimeError(
            f"Migration {filename} has no required policy metadata: {', '.join(missing)}. "
            "Add leading SQL comments '-- compatibility: expand|contract', "
            "'-- rollback: code-only-safe|forward-fix|restore-required' and '-- note: ...'."
        )

    try:
        compatibility = MigrationCompatibility(metadata["compatibility"].casefold())
    except ValueError as error:
        raise RuntimeError(
            f"Migration {filename} has unsupported compatibility policy: "
            f"{metadata['compatibility']!r}"
        ) from error
    if compatibility is MigrationCompatibility.LEGACY:
        raise RuntimeError(
            f"Migration {filename} cannot declare compatibility=legacy; "
            "new migrations must be expand or contract."
        )

    try:
        rollback = RollbackStrategy(metadata["rollback"].casefold())
    except ValueError as error:
        raise RuntimeError(
            f"Migration {filename} has unsupported rollback policy: " f"{metadata['rollback']!r}"
        ) from error

    note = metadata["note"].strip()
    if len(note) < 12:
        raise RuntimeError(f"Migration {filename} policy note is too short")

    return MigrationPolicy(
        compatibility=compatibility,
        rollback=rollback,
        note=note,
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
                f"Неверное имя миграции {path.name!r}. " "Ожидается формат 001_description.sql"
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
                policy=_parse_migration_policy(
                    filename=path.name,
                    version=version,
                    sql=sql,
                ),
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
    await conn.execute(f"ALTER TABLE public.{quoted_old} RENAME TO {quoted_new}")
    return legacy_name


async def _ensure_migration_table(conn: asyncpg.Connection) -> None:
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

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            filename          text PRIMARY KEY,
            version           integer NOT NULL UNIQUE,
            checksum          text NOT NULL,
            applied_at        timestamptz NOT NULL DEFAULT now(),
            execution_ms      integer NOT NULL,
            postgres_version  text NOT NULL
        )
        """)


async def _applied_migrations(conn: asyncpg.Connection) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch("""
        SELECT filename, version, checksum, applied_at
        FROM public.schema_migrations
        ORDER BY version
        """)
    return {str(row["filename"]): row for row in rows}


def _validate_applied_history(
    migrations: list[Migration],
    applied: dict[str, asyncpg.Record],
) -> None:
    by_version = {migration.version: migration for migration in migrations}
    for filename, row in applied.items():
        version = int(row["version"])
        migration = next((item for item in migrations if item.filename == filename), None)
        if migration is None:
            raise RuntimeError(
                f"Applied migration is absent from target source: {filename} (version {version})"
            )
        if migration.version != version:
            raise RuntimeError(
                f"Applied migration version mismatch for {filename}: "
                f"database={version}, source={migration.version}"
            )
        if str(row["checksum"]) not in migration.compatible_checksums:
            raise RuntimeError(
                "Уже применённая миграция была изменена: "
                f"{filename}. Создай новую миграцию вместо редактирования старой."
            )
        if by_version.get(version) is not migration:
            raise RuntimeError(f"Migration version collision detected: {version}")


def _plan_items(
    migrations: list[Migration],
    applied: dict[str, asyncpg.Record],
) -> list[MigrationPlanItem]:
    return [
        MigrationPlanItem(
            filename=migration.filename,
            version=migration.version,
            checksum=migration.checksum,
            state="applied" if migration.filename in applied else "pending",
            compatibility=migration.policy.compatibility.value,
            rollback=migration.policy.rollback.value,
            note=migration.policy.note,
        )
        for migration in migrations
    ]


async def migration_plan(
    pool: asyncpg.Pool,
    *,
    directory: Path = MIGRATIONS_DIR,
) -> dict[str, Any]:
    migrations = _load_migrations(directory)
    async with pool.acquire() as conn:
        if not await _table_exists(conn, MIGRATION_TABLE):
            applied: dict[str, asyncpg.Record] = {}
        else:
            columns = await _migration_table_columns(conn)
            if not _is_current_migration_table(columns):
                raise RuntimeError(
                    "public.schema_migrations has an incompatible layout; "
                    "run the controlled migration runner instead of planning from app startup"
                )
            applied = await _applied_migrations(conn)

    _validate_applied_history(migrations, applied)
    items = _plan_items(migrations, applied)
    pending = [item for item in items if item.state == "pending"]
    return {
        "current_version": max(
            (int(row["version"]) for row in applied.values()),
            default=0,
        ),
        "target_version": max(item.version for item in items),
        "pending_count": len(pending),
        "pending": [item.as_dict() for item in pending],
        "migrations": [item.as_dict() for item in items],
        "code_rollback_safe": all(
            item.rollback == RollbackStrategy.CODE_ONLY_SAFE.value for item in pending
        ),
        "requires_contract_approval": any(
            item.compatibility == MigrationCompatibility.CONTRACT.value for item in pending
        ),
        "rollback_strategies": sorted({item.rollback for item in pending}),
    }


def _allow_contract_from_env() -> bool:
    return os.environ.get("ROMATIC_ALLOW_CONTRACT_MIGRATION", "").strip().casefold() in _TRUE_VALUES


async def apply_migrations(
    pool: asyncpg.Pool,
    *,
    directory: Path = MIGRATIONS_DIR,
    allow_contract: bool = False,
) -> list[str]:
    """Apply missing migrations through the single controlled runner."""
    migrations = _load_migrations(directory)
    applied_now: list[str] = []

    async with pool.acquire() as conn:
        await conn.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await _ensure_migration_table(conn)
            applied = await _applied_migrations(conn)
            _validate_applied_history(migrations, applied)
            max_applied_version = max(
                (int(row["version"]) for row in applied.values()),
                default=0,
            )

            for migration in migrations:
                previous = applied.get(migration.filename)
                if previous is not None:
                    continue

                if migration.version < max_applied_version:
                    raise RuntimeError(
                        "Обнаружена новая миграция с номером ниже уже применённых: "
                        f"{migration.filename}. Добавляй миграции только в конец истории."
                    )

                conflicting = next(
                    (row for row in applied.values() if int(row["version"]) == migration.version),
                    None,
                )
                if conflicting is not None:
                    raise RuntimeError(
                        f"Номер {migration.version} уже занят миграцией "
                        f"{conflicting['filename']}"
                    )

                if (
                    migration.policy.compatibility is MigrationCompatibility.CONTRACT
                    and not allow_contract
                ):
                    raise RuntimeError(
                        f"Contract migration requires explicit approval: {migration.filename}. "
                        "Set ROMATIC_ALLOW_CONTRACT_MIGRATION=true only during the documented "
                        "contract phase after old code is no longer in service."
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


async def _with_database_runtime(database_url: str, callback: Any) -> Any:
    if not database_url:
        raise RuntimeError("DATABASE_URL не задан")

    from bot.core.settings import DatabaseSettings
    from db.pool import DatabaseRuntime

    runtime = DatabaseRuntime(
        DatabaseSettings(
            database_url,
            auto_migrate=False,
            pool_min_size=1,
            pool_max_size=2,
        )
    )
    pool = await runtime.start()
    try:
        return await callback(pool)
    finally:
        await runtime.close()


async def migrate_database_url(
    database_url: str,
    *,
    allow_contract: bool = False,
) -> list[str]:
    async def run(pool: asyncpg.Pool) -> list[str]:
        return await apply_migrations(pool, allow_contract=allow_contract)

    return await _with_database_runtime(database_url, run)


async def plan_database_url(database_url: str) -> dict[str, Any]:
    return await _with_database_runtime(database_url, migration_plan)


async def _execute_command(command: str) -> dict[str, Any]:
    from bot.core.environment import load_project_environment
    from bot.core.settings import DatabaseSettings

    load_project_environment()
    settings = DatabaseSettings.from_env()

    if command == "plan":
        return await plan_database_url(settings.url)

    before = await plan_database_url(settings.url)
    if command == "verify":
        if before["pending_count"]:
            raise RuntimeError(
                f"Database schema is behind target by {before['pending_count']} migration(s)"
            )
        return before

    applied = await migrate_database_url(
        settings.url,
        allow_contract=_allow_contract_from_env(),
    )
    after = await plan_database_url(settings.url)
    applied_policies = [item for item in before["pending"] if item["filename"] in set(applied)]
    return {
        "applied": applied,
        "applied_count": len(applied),
        "applied_policies": applied_policies,
        "code_rollback_safe": all(
            item["rollback"] == RollbackStrategy.CODE_ONLY_SAFE.value for item in applied_policies
        ),
        "requires_forward_fix": any(
            item["rollback"] != RollbackStrategy.CODE_ONLY_SAFE.value for item in applied_policies
        ),
        "current_version": after["current_version"],
        "target_version": after["target_version"],
        "pending_count": after["pending_count"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled PostgreSQL migration runner")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("plan", "apply", "verify"),
        default="apply",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def _main() -> None:
    arguments = _parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr if arguments.as_json else sys.stdout,
    )
    result = await _execute_command(arguments.command)
    if arguments.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
