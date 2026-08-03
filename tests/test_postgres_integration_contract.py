from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_postgres_integration_has_dedicated_ci_job_and_local_runner() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    integration_runner = (ROOT / "scripts/run_postgres_integration.py").read_text(encoding="utf-8")
    quality_runner = (ROOT / "scripts/quality.py").read_text(encoding="utf-8")

    assert "postgres-integration:" in workflow
    assert "image: postgres:17-alpine" in workflow
    assert 'POSTGRES_INTEGRATION_CONFIRM: "1"' in workflow
    assert "python scripts/quality.py integration" in workflow
    assert "python scripts/quality.py unit" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "postgres-integration-results" in workflow
    assert "unit-test-metrics" in workflow

    assert 'DEFAULT_IMAGE = "postgres:17-alpine"' in integration_runner
    assert '"POSTGRES_INTEGRATION_CONFIRM": "1"' in integration_runner
    assert '"tests/integration"' in integration_runner
    assert "_dump_failed_databases" in integration_runner

    assert 'pytest_args=("-q", "-m", "integration", "tests/integration")' in quality_runner
    assert "write_test_metrics(junit_path, suite)" in quality_runner
    assert 'totals["flaky_rate_percent"]' in quality_runner


def test_all_postgres_scenarios_use_the_explicit_integration_marker() -> None:
    integration_dir = ROOT / "tests/integration"
    scenario_files = sorted(integration_dir.glob("test_*.py"))

    assert scenario_files
    for path in scenario_files:
        source = path.read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.integration" in source, path
        assert "skipUnless" not in source, path
        assert "OUTBOX_INTEGRATION_CONFIRM" not in source, path
