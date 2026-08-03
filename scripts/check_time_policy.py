"""Enforce the UTC/Moscow time policy without freezing legacy file layout."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS: Final = ("bot", "db", "userbot")
POLICY_MODULE: Final = "bot/core/time.py"
BASELINE_PATH: Final = ROOT / "quality" / "time-policy-baseline.json"


class TimePolicyVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.datetime_classes: set[str] = set()
        self.date_classes: set[str] = set()
        self.datetime_modules: set[str] = set()
        self.scope: list[str] = []
        self.violations: Counter[str] = Counter()
        self.legacy_timezone_imports: Counter[str] = Counter()

    def _scope_name(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _record(self, kind: str) -> None:
        key = f"{self.relative_path}::{self._scope_name()}::{kind}"
        self.violations[key] += 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            if alias.name == "datetime":
                self.datetime_modules.add(local_name)
            if alias.name == "pytz":
                self.legacy_timezone_imports[f"{self.relative_path}::pytz"] += 1
            if alias.name in {"dateutil.tz", "dateutil"}:
                self.legacy_timezone_imports[f"{self.relative_path}::{alias.name}"] += 1
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "datetime":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name == "datetime":
                    self.datetime_classes.add(local_name)
                elif alias.name == "date":
                    self.date_classes.add(local_name)
        if node.module == "dateutil":
            for alias in node.names:
                if alias.name == "tz":
                    key = f"{self.relative_path}::dateutil.tz"
                    self.legacy_timezone_imports[key] += 1
        elif node.module == "dateutil.tz":
            self.legacy_timezone_imports[f"{self.relative_path}::dateutil.tz"] += 1
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                owner = func.value.id
                if owner in self.datetime_classes and func.attr in {
                    "fromisoformat",
                    "now",
                    "today",
                    "utcnow",
                }:
                    self._record(f"datetime.{func.attr}")
                elif owner in self.date_classes and func.attr == "today":
                    self._record("date.today")
            elif (
                isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id in self.datetime_modules
            ):
                owner = func.value.attr
                if owner == "datetime" and func.attr in {"fromisoformat", "now", "today", "utcnow"}:
                    self._record(f"datetime.{func.attr}")
                elif owner == "date" and func.attr == "today":
                    self._record("date.today")
        self.generic_visit(node)


def scan() -> dict[str, dict[str, int]]:
    direct_calls: Counter[str] = Counter()
    legacy_imports: Counter[str] = Counter()
    for root_name in RUNTIME_ROOTS:
        for path in sorted((ROOT / root_name).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            visitor = TimePolicyVisitor(relative)
            visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
            if relative != POLICY_MODULE:
                direct_calls.update(visitor.violations)
            legacy_imports.update(visitor.legacy_timezone_imports)
    return {
        "direct_datetime_calls": dict(sorted(direct_calls.items())),
        "legacy_timezone_imports": dict(sorted(legacy_imports.items())),
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "direct_datetime_calls": {
            str(key): int(value) for key, value in payload["direct_datetime_calls"].items()
        },
        "legacy_timezone_imports": {
            str(key): int(value) for key, value in payload["legacy_timezone_imports"].items()
        },
    }


def new_violations(
    actual: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> list[str]:
    failures: list[str] = []
    for category in ("direct_datetime_calls", "legacy_timezone_imports"):
        permitted = baseline[category]
        for key, count in actual[category].items():
            excess = count - permitted.get(key, 0)
            if excess > 0:
                failures.append(f"{category}: {key} (+{excess})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    actual = scan()
    if args.write_baseline:
        BASELINE_PATH.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {BASELINE_PATH.relative_to(ROOT)}")
        return 0

    failures = new_violations(actual, load_baseline())
    if failures:
        print("New time-policy violations are forbidden:")
        print("\n".join(f"- {item}" for item in failures))
        return 1
    print(
        "Time policy passed: "
        f"{sum(actual['direct_datetime_calls'].values())} grandfathered datetime calls, "
        f"{sum(actual['legacy_timezone_imports'].values())} grandfathered timezone imports."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
