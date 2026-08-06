from __future__ import annotations

import difflib
import json
from pathlib import Path

import black

from scripts.runtime_dependency_inventory import (
    DEFAULT_POLICY_PATH,
    build_report,
    normalize_distribution,
    requirement_names,
    validate_policy,
)


def test_normalize_distribution_uses_pep503_style_names() -> None:
    assert normalize_distribution("PyMorphy2_Dicts.RU") == "pymorphy2-dicts-ru"


def test_requirement_names_ignore_comments_options_and_hashes(tmp_path: Path) -> None:
    requirements = tmp_path / "runtime.in"
    requirements.write_text(
        """
        # comment
        --index-url https://example.invalid/simple
        Flask>=3,<4
        python_dateutil==2.9 \\
            --hash=sha256:deadbeef
        """,
        encoding="utf-8",
    )

    assert requirement_names(requirements) == ["flask", "python-dateutil"]


def test_runtime_dependency_policy_matches_repository() -> None:
    assert validate_policy(DEFAULT_POLICY_PATH) == []


def test_runtime_dependency_report_is_deterministic() -> None:
    first = build_report(DEFAULT_POLICY_PATH)
    second = build_report(DEFAULT_POLICY_PATH)

    assert first == second
    assert first["schema_version"] == 1
    services = first["services"]
    assert isinstance(services, dict)
    assert sorted(services) == ["bot", "userbot"]
    json.dumps(first, ensure_ascii=False, sort_keys=True)


def test_black_diagnostic_exposes_exact_runtime_inventory_diff() -> None:
    path = Path("scripts/runtime_dependency_inventory.py")
    source = path.read_text(encoding="utf-8")
    formatted = black.format_str(source, mode=black.Mode(line_length=100))
    difference = "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile="current",
            tofile="black",
        )
    )
    assert source == formatted, "\n" + difference
