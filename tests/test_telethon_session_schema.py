from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from userbot.session_schema import (
    CURRENT_COLUMNS,
    TelethonSessionSchemaError,
    repair_session_schema,
)


def create_session(path: Path, *, version: int = 8, current: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE version (version integer primary key)")
        connection.execute("INSERT INTO version VALUES (?)", (version,))
        if current:
            connection.execute("""
                CREATE TABLE sessions (
                    dc_id integer primary key,
                    server_address text,
                    port integer,
                    auth_key blob,
                    tmp_auth_key blob,
                    takeout_id integer
                )
                """)
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (4, "149.154.167.91", 443, b"auth", None, 123),
            )
        else:
            connection.execute("""
                CREATE TABLE sessions (
                    dc_id integer primary key,
                    server_address text,
                    port integer,
                    auth_key blob,
                    takeout_id integer
                )
                """)
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (4, "149.154.167.91", 443, b"auth", 123),
            )
        connection.commit()
    finally:
        connection.close()


def test_repairs_known_version_eight_five_column_layout(tmp_path: Path) -> None:
    path = tmp_path / "userbot.session"
    create_session(path)

    assert repair_session_schema(path) is True

    connection = sqlite3.connect(path)
    try:
        columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)"))
        row = connection.execute(
            "SELECT dc_id, server_address, port, auth_key, tmp_auth_key, takeout_id "
            "FROM sessions"
        ).fetchone()
        version = connection.execute("SELECT version FROM version").fetchone()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()

    assert columns == CURRENT_COLUMNS
    assert row == (4, "149.154.167.91", 443, b"auth", None, 123)
    assert version == (8,)
    assert quick_check == ("ok",)


def test_current_layout_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "userbot.session"
    create_session(path, current=True)

    assert repair_session_schema(path) is False
    assert repair_session_schema(path) is False


def test_unknown_layout_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "userbot.session"
    create_session(path, version=7)

    with pytest.raises(TelethonSessionSchemaError, match="Unsupported"):
        repair_session_schema(path)


def test_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.session"
    link = tmp_path / "link.session"
    create_session(source)
    link.symlink_to(source)

    with pytest.raises(TelethonSessionSchemaError, match="symbolic link"):
        repair_session_schema(link)
