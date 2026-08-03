from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QUALITY_DIR = ROOT / "quality"
ARTIFACT_DIR = ROOT / "var" / "quality"
RUNTIME_ROOTS = ("bot/", "database/", "db/", "userbot/")
TYPE_SCOPES = {
    "domain_application": (
        "bot/application_models.py",
        "bot/application_ports.py",
        "bot/domain",
        "bot/use_cases",
    ),
    "repositories": (
        "bot/repositories",
        "db/repositories",
        "userbot/repositories.py",
    ),
}
DOMAIN_APPLICATION_PREFIXES = (
    "bot/application.py",
    "bot/application_models.py",
    "bot/application_ports.py",
    "bot/domain/",
    "bot/use_cases/",
)
RUFF_RULES = "E,F,I,B,BLE,ASYNC,S,SIM,UP,C4,PIE,RUF,C90,ARG"


def run(args: Sequence[str], *, timeout: int | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, timeout=timeout)


def capture(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def git_output(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def resolve_base(explicit_base: str | None) -> str:
    if explicit_base:
        return explicit_base
    base_ref = os.getenv("GITHUB_BASE_REF")
    if os.getenv("GITHUB_EVENT_NAME") == "pull_request" and base_ref:
        candidate = f"origin/{base_ref}"
        subprocess.run(
            ("git", "fetch", "--no-tags", "--depth=1", "origin", base_ref),
            cwd=ROOT,
            check=True,
        )
        return candidate
    return "HEAD^"


def changed_python_files(base: str) -> list[str]:
    merge_base = git_output("merge-base", base, "HEAD")
    output = git_output(
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{merge_base}...HEAD",
        "--",
        "*.py",
    )
    return [path for path in output.splitlines() if path and (ROOT / path).is_file()]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def base_json(base: str, path: Path) -> dict[str, Any] | None:
    relative = path.relative_to(ROOT).as_posix()
    result = capture(("git", "show", f"{base}:{relative}"))
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def assert_baseline_not_relaxed(
    *,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    keys: Sequence[str],
) -> None:
    if previous is None:
        return
    relaxed = [key for key in keys if float(current[key]) < float(previous[key])]
    if relaxed:
        raise SystemExit("Ratchet baseline cannot decrease for: " + ", ".join(relaxed))


def assert_error_baseline_not_relaxed(
    *,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    keys: Sequence[str],
) -> None:
    if previous is None:
        return
    relaxed = [key for key in keys if int(current[key]) > int(previous[key])]
    if relaxed:
        raise SystemExit("Error-count baseline cannot increase for: " + ", ".join(relaxed))


def command_changed(base: str) -> None:
    files = changed_python_files(base)
    if not files:
        print("No changed Python files; changed-file ratchet has nothing to check.")
        return
    run((sys.executable, "-m", "black", "--check", *files))
    run(
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            RUFF_RULES,
            "--output-format=concise",
            "--ignore",
            "S101,S603,S607",
            *files,
        )
    )
    runtime_files = [path for path in files if path.startswith(RUNTIME_ROOTS)]
    if not runtime_files:
        print("No changed runtime modules; strict changed-file mypy and async checks skipped.")
        return
    run(
        (
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--no-error-summary",
            "--namespace-packages",
            "--explicit-package-bases",
            *runtime_files,
        )
    )
    run((sys.executable, "scripts/check_async_blocking.py", *runtime_files))


def mypy_error_count(paths: Sequence[str]) -> int:
    result = capture(
        (
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--no-error-summary",
            "--namespace-packages",
            "--explicit-package-bases",
            *paths,
        )
    )
    print(result.stdout, end="")
    return len(re.findall(r"^.+:\d+: error:", result.stdout, flags=re.MULTILINE))


def command_typing(base: str) -> None:
    baseline_path = QUALITY_DIR / "mypy-baseline.json"
    baseline = load_json(baseline_path)
    assert_error_baseline_not_relaxed(
        current=baseline,
        previous=base_json(base, baseline_path),
        keys=tuple(TYPE_SCOPES),
    )
    actual = {name: mypy_error_count(paths) for name, paths in TYPE_SCOPES.items()}
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "mypy-results.json").write_text(
        json.dumps(actual, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    failures = [
        f"{name}: {count} errors > baseline {baseline[name]}"
        for name, count in actual.items()
        if count > int(baseline[name])
    ]
    print("mypy ratchet:", json.dumps(actual, sort_keys=True))
    if failures:
        raise SystemExit("\n".join(failures))


def command_architecture() -> None:
    for script in (
        "scripts/check_persistence_exceptions.py",
        "scripts/check_database_boundaries.py",
        "scripts/check_telegram_boundary.py",
        "scripts/check_handler_import_boundaries.py",
    ):
        run((sys.executable, script))
    run(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_architecture_boundaries.py",
            "tests/test_handler_sql_boundary.py",
        ),
        timeout=180,
    )


def write_test_metrics(junit_path: Path, suite: str) -> None:
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals: dict[str, int | float | str] = {
        "tests": sum(int(item.attrib.get("tests", 0)) for item in suites),
        "failures": sum(int(item.attrib.get("failures", 0)) for item in suites),
        "errors": sum(int(item.attrib.get("errors", 0)) for item in suites),
        "skipped": sum(int(item.attrib.get("skipped", 0)) for item in suites),
        "seconds": round(sum(float(item.attrib.get("time", 0)) for item in suites), 3),
    }
    flaky = len(root.findall(".//flakyFailure")) + len(root.findall(".//rerunFailure"))
    test_count = int(totals["tests"])
    totals["flaky_tests"] = flaky
    totals["flaky_rate_percent"] = round((flaky / test_count * 100) if test_count else 0.0, 4)
    totals["suite"] = suite
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / f"{suite}-metrics.json").write_text(
        json.dumps(totals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("test metrics:", json.dumps(totals, sort_keys=True))


def pytest_with_metrics(
    *,
    suite: str,
    pytest_args: Sequence[str],
    timeout: int,
) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    junit_path = ARTIFACT_DIR / f"{suite}.xml"
    run(
        (
            sys.executable,
            "-m",
            "pytest",
            *pytest_args,
            f"--junitxml={junit_path}",
        ),
        timeout=timeout,
    )
    write_test_metrics(junit_path, suite)


def command_unit() -> None:
    pytest_with_metrics(
        suite="unit",
        pytest_args=("-vv", "-m", "not integration"),
        timeout=300,
    )


def command_integration() -> None:
    pytest_with_metrics(
        suite="integration",
        pytest_args=("-q", "-m", "integration", "tests/integration"),
        timeout=600,
    )


def coverage_scope_percent(payload: dict[str, Any]) -> float:
    statements = 0
    covered = 0
    for raw_path, data in payload["files"].items():
        path = raw_path.replace("\\", "/")
        if not any(
            path == prefix or path.startswith(prefix) for prefix in DOMAIN_APPLICATION_PREFIXES
        ):
            continue
        summary = data["summary"]
        statements += int(summary["num_statements"])
        covered += int(summary["covered_lines"])
    if statements == 0:
        raise SystemExit("Coverage report contains no domain/application files.")
    return covered / statements * 100


def command_coverage(base: str) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    coverage_path = ARTIFACT_DIR / "coverage.json"
    junit_path = ARTIFACT_DIR / "coverage.xml"
    run(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "not integration",
            "--cov=bot",
            "--cov=db",
            "--cov=userbot",
            "--cov-branch",
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=json:{coverage_path}",
            f"--junitxml={junit_path}",
        ),
        timeout=300,
    )
    write_test_metrics(junit_path, "coverage")
    payload = load_json(coverage_path)
    actual = {
        "overall": float(payload["totals"]["percent_covered"]),
        "domain_application": coverage_scope_percent(payload),
    }
    baseline_path = QUALITY_DIR / "coverage-baseline.json"
    baseline = load_json(baseline_path)
    assert_baseline_not_relaxed(
        current=baseline,
        previous=base_json(base, baseline_path),
        keys=("overall", "domain_application"),
    )
    (ARTIFACT_DIR / "coverage-results.json").write_text(
        json.dumps(actual, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    failures = [
        f"{name}: {value:.2f}% < baseline {float(baseline[name]):.2f}%"
        for name, value in actual.items()
        if value + 1e-9 < float(baseline[name])
    ]
    print(
        "coverage ratchet:",
        ", ".join(f"{name}={value:.2f}%" for name, value in actual.items()),
    )
    if failures:
        raise SystemExit("\n".join(failures))


def command_security() -> None:
    # Security-adjacent Ruff rules run on changed files in command_changed.
    # These full-tree contracts are the stable project-specific security baseline.
    run((sys.executable, "scripts/check_persistence_exceptions.py"))
    run((sys.executable, "scripts/check_telegram_boundary.py"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproducible project quality gates.")
    parser.add_argument(
        "command",
        choices=(
            "changed",
            "typing",
            "architecture",
            "coverage",
            "security",
            "unit",
            "integration",
            "all",
        ),
    )
    parser.add_argument("--base", help="Git ref used by ratchet comparisons.")
    parser.add_argument(
        "--include-integration",
        action="store_true",
        help="Include destructive PostgreSQL integration tests in the all command.",
    )
    args = parser.parse_args()
    base = resolve_base(args.base)

    if args.command == "changed":
        command_changed(base)
    elif args.command == "typing":
        command_typing(base)
    elif args.command == "architecture":
        command_architecture()
    elif args.command == "coverage":
        command_coverage(base)
    elif args.command == "security":
        command_security()
    elif args.command == "unit":
        command_unit()
    elif args.command == "integration":
        command_integration()
    elif args.command == "all":
        command_changed(base)
        command_typing(base)
        command_architecture()
        command_security()
        command_coverage(base)
        if args.include_integration:
            command_integration()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
