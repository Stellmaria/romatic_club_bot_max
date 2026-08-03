from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW_MARKER = "quality: allow-blocking:"
BLOCKED_CALLS = {
    "open",
    "os.system",
    "pathlib.Path.open",
    "pathlib.Path.read_bytes",
    "pathlib.Path.read_text",
    "pathlib.Path.write_bytes",
    "pathlib.Path.write_text",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
    "time.sleep",
}
BLOCKED_PREFIXES = ("requests.",)


def call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


class BlockingCallVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path, source_lines: list[str]) -> None:
        self.path = path
        self.source_lines = source_lines
        self.async_depth = 0
        self.violations: list[str] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.async_depth += 1
        self.generic_visit(node)
        self.async_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if self.async_depth:
            name = call_name(node)
            line = self.source_lines[node.lineno - 1]
            allowed = ALLOW_MARKER in line
            blocked = name in BLOCKED_CALLS or name.startswith(BLOCKED_PREFIXES)
            if blocked and not allowed:
                try:
                    relative = self.path.relative_to(ROOT)
                except ValueError:
                    relative = self.path
                self.violations.append(
                    f"{relative}:{node.lineno}: blocking call {name!r} inside async code"
                )
        self.generic_visit(node)


def check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    visitor = BlockingCallVisitor(path=path, source_lines=source.splitlines())
    visitor.visit(ast.parse(source, filename=str(path)))
    return visitor.violations


def main(argv: list[str]) -> int:
    paths = [ROOT / value for value in argv]
    violations = [
        violation
        for path in paths
        if path.suffix == ".py" and path.is_file()
        for violation in check_file(path)
    ]
    if violations:
        print("\n".join(violations))
        print(
            f"Use asynchronous I/O or add {ALLOW_MARKER} <reason> on the call line "
            "for a reviewed exception."
        )
        return 1
    print(f"Async blocking-call contract passed for {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
