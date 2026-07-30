from __future__ import annotations

import hashlib
from pathlib import Path

from db.migrator import _load_migrations, _migration_checksums


SQL_LF = b"-- portable migration\nCREATE TABLE example (id integer);\n"
SQL_CRLF = SQL_LF.replace(b"\n", b"\r\n")


def test_migration_checksum_is_stable_across_line_endings() -> None:
    lf_checksum, lf_compatible = _migration_checksums(SQL_LF)
    crlf_checksum, crlf_compatible = _migration_checksums(SQL_CRLF)

    assert lf_checksum == crlf_checksum
    assert hashlib.sha256(SQL_LF).hexdigest() in crlf_compatible
    assert hashlib.sha256(SQL_CRLF).hexdigest() in lf_compatible


def test_loader_keeps_legacy_crlf_checksum_compatible(tmp_path: Path) -> None:
    migration_path = tmp_path / "001_example.sql"
    migration_path.write_bytes(SQL_CRLF)

    migration = _load_migrations(tmp_path)[0]

    assert migration.checksum == hashlib.sha256(SQL_LF).hexdigest()
    assert hashlib.sha256(SQL_CRLF).hexdigest() in migration.compatible_checksums
