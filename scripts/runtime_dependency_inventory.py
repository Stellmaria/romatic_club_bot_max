from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "quality/runtime-dependency-policy.json"
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


class MarkerPolicy(TypedDict):
    path: str
    value: str
    minimum_occurrences: int


class ServicePolicy(TypedDict):
    requirements_input: str
    requirements_lock: str
    source_roots: list[str]
    direct_distributions: list[str]
    forbidden_distributions: list[str]
    distribution_imports: dict[str, list[str]]
    build_markers: list[MarkerPolicy]


class RuntimeDependencyPolicy(TypedDict):
    schema_version: int
    services: dict[str, ServicePolicy]


def normalize_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def requirement_names(path: Path) -> list[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "--")):
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            names.add(normalize_distribution(match.group(0)))
    return sorted(names)


def _policy_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> RuntimeDependencyPolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime dependency policy must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported runtime dependency policy schema")
    services = payload.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("runtime dependency policy must define services")
    return cast(RuntimeDependencyPolicy, payload)


def _python_files(source_roots: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for raw_root in source_roots:
        path = ROOT / raw_root
        if path.is_file():
            if path.suffix == ".py":
                files.add(path)
            continue
        if path.is_dir():
            files.update(item for item in path.rglob("*.py") if item.is_file())
    return sorted(files)


def imported_modules(source_roots: Iterable[str]) -> tuple[list[str], int]:
    modules: set[str] = set()
    files = _python_files(source_roots)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".", 1)[0])
    return sorted(modules), len(files)


def _marker_errors(marker: MarkerPolicy) -> list[str]:
    marker_path = ROOT / marker["path"]
    if not marker_path.is_file():
        return [f"build marker file not found: {marker_path.relative_to(ROOT)}"]
    count = marker_path.read_text(encoding="utf-8").count(marker["value"])
    expected = int(marker["minimum_occurrences"])
    if count < expected:
        return [
            f"{marker_path.relative_to(ROOT)} must contain {marker['value']!r} "
            f"at least {expected} time(s), found {count}"
        ]
    return []


def validate_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> list[str]:
    policy = load_policy(policy_path)
    errors: list[str] = []
    for service_name, service in sorted(policy["services"].items()):
        input_path = ROOT / service["requirements_input"]
        lock_path = ROOT / service["requirements_lock"]
        if not input_path.is_file():
            errors.append(f"{service_name}: requirements input not found: {input_path}")
            continue
        if not lock_path.is_file():
            errors.append(f"{service_name}: requirements lock not found: {lock_path}")
            continue

        declared = requirement_names(input_path)
        expected = sorted(
            {normalize_distribution(item) for item in service["direct_distributions"]}
        )
        if declared != expected:
            errors.append(
                f"{service_name}: direct dependency policy drift; "
                f"expected={expected}, actual={declared}"
            )

        locked = set(requirement_names(lock_path))
        missing_from_lock = sorted(set(declared) - locked)
        if missing_from_lock:
            errors.append(
                f"{service_name}: direct dependencies missing from lock: "
                + ", ".join(missing_from_lock)
            )

        forbidden = {normalize_distribution(item) for item in service["forbidden_distributions"]}
        forbidden_direct = sorted(forbidden & set(declared))
        forbidden_locked = sorted(forbidden & locked)
        if forbidden_direct:
            errors.append(
                f"{service_name}: forbidden direct dependencies: " + ", ".join(forbidden_direct)
            )
        if forbidden_locked:
            errors.append(
                f"{service_name}: forbidden packages present in runtime lock: "
                + ", ".join(forbidden_locked)
            )

        for raw_root in service["source_roots"]:
            if not (ROOT / raw_root).exists():
                errors.append(f"{service_name}: source root not found: {raw_root}")

        for marker in service["build_markers"]:
            errors.extend(f"{service_name}: {error}" for error in _marker_errors(marker))
    return errors


def build_report(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    policy = load_policy(policy_path)
    services: dict[str, object] = {}
    for service_name, service in sorted(policy["services"].items()):
        input_path = ROOT / service["requirements_input"]
        lock_path = ROOT / service["requirements_lock"]
        direct = requirement_names(input_path)
        locked = requirement_names(lock_path)
        imports, file_count = imported_modules(service["source_roots"])

        observed_modules = set(imports)
        direct_set = set(direct)
        not_observed: list[str] = []
        for distribution, module_names in sorted(service["distribution_imports"].items()):
            if normalize_distribution(distribution) not in direct_set:
                continue
            if not observed_modules.intersection(module_names):
                not_observed.append(normalize_distribution(distribution))

        services[service_name] = {
            "requirements_input": service["requirements_input"],
            "requirements_lock": service["requirements_lock"],
            "requirements_input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "requirements_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "direct_distributions": direct,
            "locked_distributions": locked,
            "locked_distribution_count": len(locked),
            "source_roots": service["source_roots"],
            "source_file_count": file_count,
            "imported_modules": imports,
            "direct_distributions_not_observed_in_static_imports": sorted(not_observed),
            "note": (
                "Static non-observation is review evidence only; it is not proof "
                "that a package is safe to remove."
            ),
        }
    return {
        "schema_version": 1,
        "policy_path": str(policy_path.relative_to(ROOT)),
        "policy_sha256": _policy_sha256(policy_path),
        "services": services,
    }


def _write_report(report: dict[str, object], output: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    print(f"Runtime dependency inventory written: {output}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and report per-service runtime dependency boundaries."
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the runtime dependency policy JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    report = subparsers.add_parser("report")
    report.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    policy_path = args.policy.resolve()
    if args.command == "validate":
        errors = validate_policy(policy_path)
        if errors:
            print("Runtime dependency policy failed:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        report = build_report(policy_path)
        services = cast(dict[str, object], report["services"])
        print("Runtime dependency policy passed: " + ", ".join(sorted(services)))
        return 0
    if args.command == "report":
        _write_report(build_report(policy_path), args.output)
        return 0
    raise AssertionError(f"unexpected command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
