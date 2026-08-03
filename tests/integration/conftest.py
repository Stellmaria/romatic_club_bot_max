"""Disposable PostgreSQL fixtures for destructive integration tests."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio

from bot.uid_crypto import configure_uid_crypto, reset_uid_crypto_for_testing
from db.migrator import apply_migrations


_SAFE_DATABASE_RE = re.compile(r"(?:test|testing|integration|ci)", re.IGNORECASE)
_CONFIRM_ENV = "POSTGRES_INTEGRATION_CONFIRM"
_DATABASE_ENV = "TEST_DATABASE_URL"
_KEEP_FAILED_ENV = "POSTGRES_KEEP_FAILED_DATABASES"
_ARTIFACT_DIR_ENV = "POSTGRES_INTEGRATION_ARTIFACT_DIR"


@dataclass(frozen=True, slots=True)
class IntegrationDatabase:
    dsn: str
    name: str


def _replace_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            f"/{database}",
            parts.query,
            parts.fragment,
        )
    )


def _required_base_dsn() -> str:
    dsn = os.getenv(_DATABASE_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{_DATABASE_ENV} is required for PostgreSQL integration tests")
    if os.getenv(_CONFIRM_ENV) != "1":
        pytest.fail(
            f"{_CONFIRM_ENV}=1 is required because the integration suite creates "
            "and drops disposable databases"
        )
    database = urlsplit(dsn).path.rsplit("/", 1)[-1]
    if not _SAFE_DATABASE_RE.search(database):
        pytest.fail(
            f"refusing destructive integration tests against unsafe database {database!r}; "
            "its name must contain test, testing, integration or ci"
        )
    return dsn


def _artifact_dir() -> Path:
    path = Path(
        os.getenv(
            _ARTIFACT_DIR_ENV,
            "var/integration-artifacts",
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_failed_database(database: IntegrationDatabase, nodeid: str) -> None:
    target = _artifact_dir() / "failed-databases.txt"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"{database.name}\t{nodeid}\n")


async def _drop_database(admin: asyncpg.Connection, database_name: str) -> None:
    await admin.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = $1
          AND pid <> pg_backend_pid()
        """,
        database_name,
    )
    await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(scope="session", autouse=True)
def configured_uid_crypto() -> None:
    hash_key = os.getenv("UID_HASH_KEY", "").strip()
    encryption_key = os.getenv("UID_ENC_KEY", "").strip()
    if not hash_key or not encryption_key:
        pytest.fail("UID_HASH_KEY and UID_ENC_KEY are required for integration tests")
    configure_uid_crypto(hash_key, encryption_key)
    try:
        yield
    finally:
        reset_uid_crypto_for_testing()


@pytest_asyncio.fixture
async def empty_database(request: pytest.FixtureRequest):
    """Create one isolated sibling database and remove it after the test."""

    base_dsn = _required_base_dsn()
    admin_dsn = _replace_database(base_dsn, "postgres")
    database_name = f"romatic_it_{uuid.uuid4().hex}"
    database = IntegrationDatabase(
        dsn=_replace_database(base_dsn, database_name),
        name=database_name,
    )

    admin = await asyncpg.connect(admin_dsn)
    try:
        version_num = int(await admin.fetchval("SHOW server_version_num"))
        if version_num // 10_000 != 17:
            pytest.fail(
                f"PostgreSQL 17 is required, server reports {version_num // 10_000}"
            )
        await admin.execute(
            f'CREATE DATABASE "{database_name}" TEMPLATE template0 ENCODING \'UTF8\''
        )
    finally:
        await admin.close()

    try:
        yield database
    finally:
        report = getattr(request.node, "rep_call", None)
        failed = bool(report and report.failed)
        if failed:
            _record_failed_database(database, request.node.nodeid)
        if failed and os.getenv(_KEEP_FAILED_ENV) == "1":
            return

        admin = await asyncpg.connect(admin_dsn)
        try:
            await _drop_database(admin, database_name)
        finally:
            await admin.close()


@pytest_asyncio.fixture
async def empty_pool(empty_database: IntegrationDatabase):
    pool = await asyncpg.create_pool(
        empty_database.dsn,
        min_size=1,
        max_size=12,
        command_timeout=30,
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def postgres_pool(empty_pool: asyncpg.Pool):
    """Return an isolated pool with the complete migration history applied."""

    await apply_migrations(empty_pool)
    yield empty_pool
