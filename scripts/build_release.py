"""Build a source release that cannot include known runtime credentials."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "dump",
    "dumps",
    "htmlcov",
    "private",
    "var",
}
EXCLUDED_NAMES = {".env", ".envrc", "db.sql"}
EXCLUDED_SUFFIXES = {
    ".7z",
    ".bak",
    ".backup",
    ".csv",
    ".dump",
    ".log",
    ".pgdump",
    ".pyc",
    ".rar",
    ".session",
    ".session-journal",
    ".sql.gz",
    ".tar.gz",
    ".whl",
    ".zip",
}
ALLOWED_DATABASE_SQL = {
    "database/bootstrap.sql",
    "database/pgadmin_schema.sql",
    "database/reference_schema.sql",
}
MIGRATION_NAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


def should_include(relative: Path) -> bool:
    parts = PurePosixPath(relative.as_posix()).parts
    normalized_parts = tuple(part.lower() for part in parts)
    if any(
        part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info")
        for part in normalized_parts[:-1]
    ):
        return False
    name = relative.name
    normalized_name = name.lower()
    if normalized_name in EXCLUDED_NAMES or normalized_name.startswith(".env."):
        return name == ".env.example"
    if any(normalized_name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return False
    if ".session-" in normalized_name:
        return False
    if relative.suffix.lower() == ".sql":
        normalized_path = "/".join(normalized_parts)
        is_migration = (
            len(normalized_parts) == 3
            and normalized_parts[:2] == ("database", "migrations")
            and MIGRATION_NAME_RE.fullmatch(normalized_parts[2]) is not None
        )
        if normalized_path not in ALLOWED_DATABASE_SQL and not is_migration:
            return False
    return True


def build_release(destination: Path) -> int:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    included = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PROJECT_ROOT.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(PROJECT_ROOT)
            if path.resolve() == destination or not should_include(relative):
                continue
            archive.write(path, relative.as_posix())
            included += 1
    return included


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "dist" / "auction-bot-source.zip",
    )
    args = parser.parse_args()
    count = build_release(args.destination)
    print(f"Created {args.destination.resolve()} with {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
