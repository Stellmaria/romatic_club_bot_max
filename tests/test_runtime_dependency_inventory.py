from __future__ import annotations

import json
from pathlib import Path

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
