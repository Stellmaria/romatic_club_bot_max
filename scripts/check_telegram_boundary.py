"""Static security contract for the Telegram adapter boundary."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot" / "handlers"
CALLBACK_LIMIT_BYTES = 64


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

    if violations:
        print("Telegram boundary violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Telegram boundary contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
