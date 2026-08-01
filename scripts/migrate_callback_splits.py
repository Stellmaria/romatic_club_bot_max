"""Migrate ad-hoc callback ``split`` calls to the shared parser.

The transformation is deliberately narrow: only ``split``/``rsplit`` calls
whose receiver references callback data are changed.  Source slices are used
instead of unparsing whole modules, preserving comments and formatting.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot" / "handlers"
IMPORT_MODULE = "bot.telegram.callback_parser"


@dataclass(frozen=True, slots=True)
class Replacement:
    start: int
    end: int
    text: str


def _segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _looks_like_callback_expression(source: str, node: ast.AST) -> bool:
    text = _segment(source, node).lower().replace(" ", "")
    return any(
        marker in text
        for marker in (".data", "callback_data", "call_data", "payload")
    )


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _absolute_offset(
    source: str,
    offsets: list[int],
    lineno: int,
    byte_column: int,
) -> int:
    line_start = offsets[lineno - 1]
    line_end = source.find("\n", line_start)
    if line_end < 0:
        line_end = len(source)
    line = source[line_start:line_end]
    prefix = line.encode("utf-8")[:byte_column].decode("utf-8")
    return line_start + len(prefix)


def _candidate_calls(source: str, tree: ast.AST) -> list[ast.Call]:
    result: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"split", "rsplit"}:
            continue
        if _looks_like_callback_expression(source, node.func.value):
            result.append(node)
    return result


def _span_contains(source: str, outer: ast.Call, inner: ast.Call) -> bool:
    offsets = _line_offsets(source)
    outer_start = _absolute_offset(source, offsets, outer.lineno, outer.col_offset)
    outer_end = _absolute_offset(
        source,
        offsets,
        outer.end_lineno or outer.lineno,
        outer.end_col_offset or outer.col_offset,
    )
    inner_start = _absolute_offset(source, offsets, inner.lineno, inner.col_offset)
    inner_end = _absolute_offset(
        source,
        offsets,
        inner.end_lineno or inner.lineno,
        inner.end_col_offset or inner.col_offset,
    )
    return outer_start <= inner_start and inner_end <= outer_end


def _replace_innermost_calls(source: str) -> tuple[str, set[str]]:
    tree = ast.parse(source)
    candidates = _candidate_calls(source, tree)
    if not candidates:
        return source, set()

    innermost = [
        node
        for node in candidates
        if not any(
            node is not other and _span_contains(source, node, other)
            for other in candidates
        )
    ]
    offsets = _line_offsets(source)
    replacements: list[Replacement] = []
    required: set[str] = set()
    for node in innermost:
        assert isinstance(node.func, ast.Attribute)
        function_name = (
            "rsplit_callback_data" if node.func.attr == "rsplit" else "split_callback_data"
        )
        required.add(function_name)
        receiver = _segment(source, node.func.value)
        arguments = [_segment(source, argument) for argument in node.args]
        arguments.extend(
            f"{keyword.arg}={_segment(source, keyword.value)}"
            for keyword in node.keywords
            if keyword.arg is not None
        )
        call_arguments = ", ".join([receiver, *arguments])
        start = _absolute_offset(source, offsets, node.lineno, node.col_offset)
        end = _absolute_offset(
            source,
            offsets,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        )
        replacements.append(
            Replacement(start=start, end=end, text=f"{function_name}({call_arguments})")
        )

    for replacement in sorted(replacements, key=lambda item: item.start, reverse=True):
        source = source[: replacement.start] + replacement.text + source[replacement.end :]
    return source, required


def _existing_imports(tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == IMPORT_MODULE:
            imported.update(alias.name for alias in node.names)
    return imported


def _import_insertion_line(tree: ast.Module) -> int:
    index = 0
    last_line = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        last_line = tree.body[0].end_lineno or tree.body[0].lineno
        index = 1
    while index < len(tree.body) and isinstance(tree.body[index], (ast.Import, ast.ImportFrom)):
        node = tree.body[index]
        last_line = node.end_lineno or node.lineno
        index += 1
    return last_line


def _add_imports(source: str, required: set[str]) -> str:
    if not required:
        return source
    tree = ast.parse(source)
    missing = sorted(required - _existing_imports(tree))
    if not missing:
        return source
    statement = f"from {IMPORT_MODULE} import {', '.join(missing)}\n"
    line = _import_insertion_line(tree)
    if line == 0:
        return statement + source
    lines = source.splitlines(keepends=True)
    lines.insert(line, statement)
    return "".join(lines)


def migrate_source(source: str) -> tuple[str, int]:
    original = source
    required: set[str] = set()
    for _ in range(1000):
        source, pass_required = _replace_innermost_calls(source)
        required.update(pass_required)
        if not pass_required:
            break
    else:
        raise RuntimeError("callback split migration did not converge")
    source = _add_imports(source, required)
    count = len(_candidate_calls(original, ast.parse(original)))
    return source, count


def migrate_tree(*, write: bool) -> int:
    changed_files = 0
    changed_calls = 0
    for path in sorted(HANDLERS.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        migrated, count = migrate_source(source)
        if migrated == source:
            continue
        changed_files += 1
        changed_calls += count
        if write:
            path.write_text(migrated, encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: migrated {count} callback split call(s)")
    print(f"Migrated {changed_calls} callback split call(s) in {changed_files} file(s).")
    return changed_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    changed = migrate_tree(write=args.write)
    return 0 if args.write or changed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
