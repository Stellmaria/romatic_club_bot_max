"""Static security contract for the Telegram adapter boundary."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot" / "handlers"
CALLBACK_LIMIT_BYTES = 64
# Existing compatibility callbacks are tracked explicitly so the contract still
# rejects every new ad-hoc parser. Remove entries as the family migrates to
# parse_callback_parts/CallbackSchema.
_LEGACY_CALLBACK_SPLITS = {
    (Path("bot/handlers/user_menu.py"), 727),
    (Path("bot/handlers/user_menu.py"), 738),
    (Path("bot/handlers/user_menu.py"), 871),
    (Path("bot/handlers/user_menu.py"), 882),
}
_USER_VALUE_SUFFIXES = (
    ".text",
    ".caption",
    ".first_name",
    ".last_name",
    ".full_name",
    ".username",
)
_USER_VALUE_ROOTS = (
    "message",
    "call",
    "callback",
    "event",
    "query",
)
_ESCAPERS = {"escape", "escape_html", "render_html"}


def _segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _looks_like_callback_expression(source: str, node: ast.AST) -> bool:
    text = _segment(source, node).lower().replace(" ", "")
    callback_markers = (
        ".data",
        "callback_data",
        "call_data",
        "payload",
    )
    return any(marker in text for marker in callback_markers)


def _literal_callback_bytes(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return len(node.value.encode("utf-8"))
    if isinstance(node, ast.JoinedStr):
        size = 0
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                size += len(value.value.encode("utf-8"))
            else:
                return None
        return size
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_escaped_expression(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node.func) in _ESCAPERS


def _contains_direct_user_value(source: str, node: ast.AST) -> bool:
    if _is_escaped_expression(node):
        return False
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        text = _segment(source, child).lower().replace(" ", "")
        if not text.endswith(_USER_VALUE_SUFFIXES):
            continue
        root = text.split(".", 1)[0].lstrip("(")
        if root in _USER_VALUE_ROOTS or ".from_user." in text:
            return True
    return False


def _is_html_call(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "parse_mode":
            continue
        if isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value).upper() == "HTML"
        if isinstance(keyword.value, ast.Attribute):
            return keyword.value.attr.upper() == "HTML"
    return False


def _message_arguments(node: ast.Call) -> list[ast.AST]:
    arguments = list(node.args[:1])
    arguments.extend(
        keyword.value
        for keyword in node.keywords
        if keyword.arg in {"text", "caption"}
    )
    return arguments


def _unsafe_html_lines(source: str, node: ast.Call) -> list[int]:
    if not _is_html_call(node):
        return []
    lines: list[int] = []
    for argument in _message_arguments(node):
        for child in ast.walk(argument):
            if not isinstance(child, ast.FormattedValue):
                continue
            if _contains_direct_user_value(source, child.value):
                lines.append(child.lineno)
    return lines


def main() -> int:
    violations: list[str] = []
    for path in sorted(HANDLERS.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative = path.relative_to(ROOT)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"split", "rsplit"} and _looks_like_callback_expression(
                    source, node.func.value
                ):
                    if (relative, node.lineno) not in _LEGACY_CALLBACK_SPLITS:
                        violations.append(
                            f"{relative}:{node.lineno}: callback payload must use "
                            "bot.telegram.boundary parser/CallbackSchema, not split()"
                        )

            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "callback_data":
                    continue
                byte_count = _literal_callback_bytes(keyword.value)
                if byte_count is not None and byte_count > CALLBACK_LIMIT_BYTES:
                    violations.append(
                        f"{relative}:{node.lineno}: callback_data literal is "
                        f"{byte_count} bytes; Telegram limit is {CALLBACK_LIMIT_BYTES}"
                    )
            for line in _unsafe_html_lines(source, node):
                violations.append(
                    f"{relative}:{line}: direct Telegram user value in HTML f-string; "
                    "wrap it with escape_html()/html.escape() or use render_html()"
                )

    if violations:
        print("Telegram boundary violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Telegram boundary contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
