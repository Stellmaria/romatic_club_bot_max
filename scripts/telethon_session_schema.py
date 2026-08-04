"""Compatibility repair for persistent Telethon SQLite sessions."""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

LEGACY_COLUMNS = (
    "dc_id",
    "server_address",
    "port",
    "auth_key",
    "takeout_id",
)
CURRENT_COLUMNS = (
    "dc_id",
    "server_address",
    "port",
    "auth_key",
    "tmp_auth_key",
    "takeout_id",
)
KNOWN_BROKEN_VERSION = 8


class TelethonSessionSchemaError(RuntimeError):
    """Raised when a persistent Telethon session has an unsupported schema."""


def _columns(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)"))


def _version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT version FROM version").fetchone()
    if row is None:
        raise TelethonSessionSchemaError("Telethon session version row is missing")
    return int(row[0])


def _quick_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise TelethonSessionSchemaError(f"Telethon session quick_check failed: {result!r}")


def repair_session_schema(path: str | Path) -> bool:
    """Repair Telethon 1.44's known version-8 five-column session mismatch.

    Returns ``True`` when the table was rebuilt and ``False`` when the session
    was already compatible. Unknown layouts fail closed instead of guessing.
    """

    session_path = Path(path)
    if session_path.is_symlink():
        raise TelethonSessionSchemaError(
            f"Telethon session must not be a symbolic link: {session_path}"
        )
    if not session_path.is_file():
        raise TelethonSessionSchemaError(f"Telethon session file does not exist: {session_path}")

    connection = sqlite3.connect(session_path)
    try:
        _quick_check(connection)
        version = _version(connection)
        columns = _columns(connection)

        if columns == CURRENT_COLUMNS:
            return False
        if columns != LEGACY_COLUMNS or version != KNOWN_BROKEN_VERSION:
            raise TelethonSessionSchemaError(
                "Unsupported Telethon session schema: " f"version={version}, columns={columns!r}"
            )

        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("""
                CREATE TABLE sessions_new (
                    dc_id INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port INTEGER,
                    auth_key BLOB,
                    tmp_auth_key BLOB,
                    takeout_id INTEGER
                )
                """)
            connection.execute("""
                INSERT INTO sessions_new (
                    dc_id,
                    server_address,
                    port,
                    auth_key,
                    tmp_auth_key,
                    takeout_id
                )
                SELECT
                    dc_id,
                    server_address,
                    port,
                    auth_key,
                    NULL,
                    takeout_id
                FROM sessions
                """)
            connection.execute("DROP TABLE sessions")
            connection.execute("ALTER TABLE sessions_new RENAME TO sessions")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        _quick_check(connection)
        repaired_columns = _columns(connection)
        if repaired_columns != CURRENT_COLUMNS:
            raise TelethonSessionSchemaError(
                f"Telethon session repair produced {repaired_columns!r}"
            )
        return True
    except sqlite3.DatabaseError as error:
        raise TelethonSessionSchemaError(f"Telethon session SQLite failure: {error}") from error
    finally:
        connection.close()


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args(argv)

    changed = repair_session_schema(args.session)
    print("Telethon session schema repaired" if changed else "Telethon session schema OK")
    return 0


__all__ = [
    "CURRENT_COLUMNS",
    "KNOWN_BROKEN_VERSION",
    "LEGACY_COLUMNS",
    "TelethonSessionSchemaError",
    "repair_session_schema",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(run())
