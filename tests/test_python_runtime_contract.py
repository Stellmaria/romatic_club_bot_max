from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_313_is_the_only_supported_runtime() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.13,<3.14"' in pyproject
    assert 'target-version = "py313"' in pyproject
    assert 'target-version = ["py313"]' in pyproject
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert (ROOT / "Dockerfile").read_text(encoding="utf-8").startswith(
        "FROM python:3.13-slim"
    )
    assert (ROOT / "Dockerfile.server-supervisor-proxy").read_text(
        encoding="utf-8"
    ).startswith("FROM python:3.13-alpine")
    assert ci.count('python-version: "3.13"') == 2
    assert "matrix.python-version" not in ci
