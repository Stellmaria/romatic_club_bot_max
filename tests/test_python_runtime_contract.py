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

    python_jobs = (
        "server-supervisor-contract",
        "test",
        "postgres-integration",
    )
    for index, job_name in enumerate(python_jobs):
        start = ci.index(f"  {job_name}:")
        end = (
            ci.index(f"  {python_jobs[index + 1]}:")
            if index + 1 < len(python_jobs)
            else len(ci)
        )
        job = ci[start:end]
        assert job.count('python-version: "3.13"') == 1

    assert ci.count('python-version: "3.13"') == len(python_jobs)
    assert "matrix.python-version" not in ci
