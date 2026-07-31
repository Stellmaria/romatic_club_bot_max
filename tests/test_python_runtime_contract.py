from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_314_is_the_only_supported_runtime() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.14,<3.15"' in pyproject
    assert 'target-version = "py314"' in pyproject
    assert 'target-version = ["py314"]' in pyproject
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14"
    assert (ROOT / "Dockerfile").read_text(encoding="utf-8").startswith(
        "FROM python:3.14-slim"
    )
    assert (ROOT / "Dockerfile.server-supervisor-proxy").read_text(
        encoding="utf-8"
    ).startswith("FROM python:3.14-alpine")
    assert ci.count('python-version: "3.14"') == 2
    assert "matrix.python-version" not in ci


def test_retired_python_runtime_mentions_do_not_return() -> None:
    forbidden = re.compile(
        r"(?:Python|CPython|python-version:|python:|py\s+-)\s*[^\n]{0,12}3\.(?:12|13)"
        r"|py(?:312|313)"
    )
    violations: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in {".py", ".md", ".toml", ".yml", ".yaml"} and path.name not in {
            "Dockerfile",
            "Dockerfile.server-supervisor-proxy",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if forbidden.search(text):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"retired Python runtime mentions: {violations}"
