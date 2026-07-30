"""Validate per-table SQL data exports without logging row contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
CREATE_TABLE_RE = re.compile(
    r'^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?"?([a-z_][a-z0-9_]*)"?',
    re.IGNORECASE | re.MULTILINE,
)
INSERT_TARGET_RE = re.compile(
    r'^\s*insert\s+into\s+(?:"?public"?\.)?"?([a-z_][a-z0-9_]*)"?',
    re.IGNORECASE,
)
DOLLAR_QUOTE_RE = re.compile(r"\$(?:[a-z_][a-z0-9_]*)?\$", re.IGNORECASE)
STATEMENT_HEAD_RE = re.compile(r"^\s*([a-z_\\!]+)", re.IGNORECASE)
VALUES_KEYWORD_RE = re.compile(r"\bvalues\b", re.IGNORECASE)
FORBIDDEN_INSERT_TAIL_RE = re.compile(
    r"\b(?:select|with|returning|conflict|call|copy|do)\b",
    re.IGNORECASE,
)
FUNCTION_CALL_RE = re.compile(r"\b[a-z_][a-z0-9_.]*\s*\(", re.IGNORECASE)


class DumpValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DumpFileManifest:
    table: str
    file: str
    bytes: int
    insert_statements: int
    sha256: str


def _visible_sql_statements(source: str) -> list[str]:
    """Return statement structure while discarding comments and row values.

    Splitting on raw semicolons is unsafe because text and dollar-quoted values
    may contain them.  This small lexer keeps only SQL visible outside quoted
    values, which is enough to enforce an INSERT-only restore contract without
    ever placing private values in diagnostics.
    """

    statements: list[str] = []
    visible: list[str] = []
    state = "normal"
    block_depth = 0
    dollar_tag = ""
    single_backslash_escapes = False
    index = 0

    def mask(character: str) -> None:
        visible.append("\n" if character == "\n" else " ")

    def finish() -> None:
        statement = "".join(visible).strip()
        visible.clear()
        if statement and statement != ";":
            statements.append(statement)

    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "normal":
            if character == "-" and following == "-":
                visible.extend((" ", " "))
                state = "line_comment"
                index += 2
                continue
            if character == "/" and following == "*":
                visible.extend((" ", " "))
                state = "block_comment"
                block_depth = 1
                index += 2
                continue
            if character == "'":
                mask(character)
                single_backslash_escapes = (
                    index > 0
                    and source[index - 1] in {"e", "E"}
                    and (index < 2 or not (source[index - 2].isalnum() or source[index - 2] == "_"))
                )
                state = "single_quote"
                index += 1
                continue
            if character == '"':
                visible.append(character)
                state = "double_quote"
                index += 1
                continue
            if character == "$":
                match = DOLLAR_QUOTE_RE.match(source, index)
                if match:
                    dollar_tag = match.group(0)
                    visible.extend(" " for _ in dollar_tag)
                    state = "dollar_quote"
                    index = match.end()
                    continue
            visible.append(character)
            index += 1
            if character == ";":
                finish()
            continue

        if state == "line_comment":
            mask(character)
            index += 1
            if character == "\n":
                state = "normal"
            continue

        if state == "block_comment":
            if character == "/" and following == "*":
                visible.extend((" ", " "))
                block_depth += 1
                index += 2
                continue
            if character == "*" and following == "/":
                visible.extend((" ", " "))
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
                continue
            mask(character)
            index += 1
            continue

        if state == "single_quote":
            if character == "'" and following == "'":
                visible.extend((" ", " "))
                index += 2
                continue
            if single_backslash_escapes and character == "\\" and following:
                visible.extend((" ", " "))
                index += 2
                continue
            mask(character)
            index += 1
            if character == "'":
                state = "normal"
            continue

        if state == "double_quote":
            visible.append(character)
            index += 1
            if character == '"' and following == '"':
                visible.append(following)
                index += 1
            elif character == '"':
                state = "normal"
            continue

        if state == "dollar_quote":
            if source.startswith(dollar_tag, index):
                visible.extend(" " for _ in dollar_tag)
                index += len(dollar_tag)
                state = "normal"
                continue
            mask(character)
            index += 1

    if state == "line_comment":
        state = "normal"
    if state != "normal":
        raise DumpValidationError(f"unterminated SQL {state.replace('_', ' ')}")
    finish()
    return statements


def schema_tables(schema_path: Path) -> set[str]:
    source = schema_path.read_text(encoding="utf-8")
    return {match.group(1).lower() for match in CREATE_TABLE_RE.finditer(source)}


def validate_dump_file(path: Path, allowed_tables: set[str]) -> DumpFileManifest:
    table = path.stem.lower()
    if not IDENTIFIER_RE.fullmatch(table):
        raise DumpValidationError(f"unsafe dump filename: {path.name}")
    if table not in allowed_tables:
        raise DumpValidationError(f"table {table!r} is absent from the schema snapshot")

    payload = path.read_bytes()
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DumpValidationError(f"{path.name} is not UTF-8") from error

    targets: list[str] = []
    try:
        statements = _visible_sql_statements(source)
    except DumpValidationError as error:
        raise DumpValidationError(f"{path.name}: {error}") from error

    for statement in statements:
        head = STATEMENT_HEAD_RE.match(statement)
        keyword = head.group(1).lower() if head else "unknown"
        if keyword != "insert":
            raise DumpValidationError(
                f"{path.name} contains forbidden {keyword!r} statement"
            )
        target = INSERT_TARGET_RE.match(statement)
        if target is None:
            raise DumpValidationError(f"{path.name} contains malformed INSERT statement")
        values = VALUES_KEYWORD_RE.search(statement, target.end())
        if values is None:
            raise DumpValidationError(
                f"{path.name} contains non-VALUES INSERT statement"
            )
        insert_tail = statement[values.end():]
        if FORBIDDEN_INSERT_TAIL_RE.search(insert_tail) or FUNCTION_CALL_RE.search(
            insert_tail
        ):
            raise DumpValidationError(
                f"{path.name} contains executable INSERT expression"
            )
        targets.append(target.group(1).lower())

    unexpected = sorted(set(targets) - {table})
    if unexpected:
        raise DumpValidationError(
            f"{path.name} writes to unexpected tables: {', '.join(unexpected)}"
        )
    return DumpFileManifest(
        table=table,
        file=path.name,
        bytes=len(payload),
        insert_statements=len(targets),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def validate_dump(directory: Path, schema_path: Path) -> list[DumpFileManifest]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise DumpValidationError(f"dump directory does not exist: {directory}")
    allowed_tables = schema_tables(schema_path.resolve())
    if not allowed_tables:
        raise DumpValidationError("schema snapshot contains no CREATE TABLE statements")

    sql_files = sorted(directory.glob("*.sql"))
    if not sql_files:
        raise DumpValidationError(f"dump directory contains no .sql files: {directory}")
    return [validate_dump_file(path, allowed_tables) for path in sql_files]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_directory", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "database" / "pgadmin_schema.sql",
    )
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    manifest = validate_dump(args.dump_directory, args.schema)
    summary = {
        "files": len(manifest),
        "bytes": sum(item.bytes for item in manifest),
        "insert_statements": sum(item.insert_statements for item in manifest),
        "tables": [asdict(item) for item in manifest],
    }
    if args.manifest:
        args.manifest.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        f"Validated {summary['files']} table files, "
        f"{summary['insert_statements']} INSERT statements, {summary['bytes']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
