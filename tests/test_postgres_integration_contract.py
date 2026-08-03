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
    assert "test-shards:" in workflow
    assert "python scripts/ci_test_shard.py" in workflow
    assert "python scripts/ci_coverage_report.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "postgres-integration-results" in workflow
    assert "test-coverage-shard-" in workflow
    assert "uv pip install --system -r requirements.lock" in workflow

    assert 'DEFAULT_IMAGE = "postgres:17-alpine"' in integration_runner
    assert '"POSTGRES_INTEGRATION_CONFIRM": "1"' in integration_runner
    assert '"tests/integration"' in integration_runner
    assert "_dump_failed_databases" in integration_runner

    assert 'pytest_args=("-q", "-m", "integration", "tests/integration")' in quality_runner
    assert "write_test_metrics(junit_path, suite)" in quality_runner
    assert 'totals["flaky_rate_percent"]' in quality_runner


def test_parallel_unit_plan_keeps_required_check_names_and_single_execution() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    quality_workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    sharder = (ROOT / "scripts/ci_test_shard.py").read_text(encoding="utf-8")
    coverage_reporter = (ROOT / "scripts/ci_coverage_report.py").read_text(encoding="utf-8")

    assert "name: test" in workflow
    assert "name: coverage" in workflow
    assert "shard: [0, 1, 2, 3]" in workflow
    assert "python scripts/quality.py unit" not in workflow
    assert "python scripts/quality.py coverage" not in quality_workflow
    assert "partition_test_files" in sharder
    assert '"not integration"' in sharder
    assert "--cov-branch" in sharder
    assert "coverage combine" not in workflow
    assert '"coverage", "combine"' in coverage_reporter
    assert "coverage_scope_percent" in coverage_reporter


def test_pull_requests_do_not_run_duplicate_push_ci() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "github.event.pull_request.number || github.ref" in workflow


def test_all_postgres_scenarios_use_the_explicit_integration_marker() -> None:
    integration_dir = ROOT / "tests/integration"
    scenario_files = sorted(integration_dir.glob("test_*.py"))

    assert scenario_files
    for path in scenario_files:
        source = path.read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.integration" in source, path
        assert "skipUnless" not in source, path
        assert "OUTBOX_INTEGRATION_CONFIRM" not in source, path
