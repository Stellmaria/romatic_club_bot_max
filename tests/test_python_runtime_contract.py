from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_313_is_the_only_supported_runtime() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    supervisor = (ROOT / "Dockerfile.server-supervisor-proxy").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.13,<3.14"' in pyproject
    assert 'target-version = "py313"' in pyproject
    assert 'target-version = ["py313"]' in pyproject
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert dockerfile.startswith(
        "FROM python:3.13.13-slim@sha256:aa938a849bcb82dce8f49480f056ab82bf5c1c3ebc294f0430f37b6820e7f286"
    )
    assert supervisor.startswith(
        "FROM python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0"
    )
    assert "ARG REQUIREMENTS_LOCK=requirements/bot.lock" in dockerfile
    assert "--require-hashes --no-deps" in dockerfile

    ordered_jobs = (
        "deployment-contract",
        "server-supervisor-contract",
        "preflight",
        "test-shards",
        "test",
        "coverage",
        "postgres-integration",
    )
    python_jobs = {
        "server-supervisor-contract",
        "preflight",
        "test-shards",
        "coverage",
        "postgres-integration",
    }
    for index, job_name in enumerate(ordered_jobs):
        start = ci.index(f"  {job_name}:")
        end = ci.index(f"  {ordered_jobs[index + 1]}:") if index + 1 < len(ordered_jobs) else len(ci)
        job = ci[start:end]
        expected_count = 1 if job_name in python_jobs else 0
        assert job.count('python-version: "3.13"') == expected_count

    assert ci.count('python-version: "3.13"') == len(python_jobs)
    assert "matrix.python-version" not in ci
