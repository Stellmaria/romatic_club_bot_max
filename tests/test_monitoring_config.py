from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_monitoring import REQUIRED_ALERTS, REQUIRED_PANELS, validate

ROOT = Path(__file__).resolve().parents[1]


def test_monitoring_contract_is_complete() -> None:
    assert validate(ROOT) == []


def test_dashboard_separates_core_and_userbot_panels() -> None:
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/observability-dashboard.json").read_text(encoding="utf-8")
    )
    titles = {panel["title"] for panel in dashboard["panels"]}

    assert titles >= REQUIRED_PANELS
    assert any(title.startswith("Core ") for title in titles)
    assert any(title.startswith("Userbot ") for title in titles)


def test_every_alert_links_to_slo_and_runbook() -> None:
    alerts = json.loads((ROOT / "monitoring/prometheus/alerts.yml").read_text(encoding="utf-8"))
    rules = [rule for group in alerts["groups"] for rule in group["rules"]]

    assert {rule["alert"] for rule in rules} == REQUIRED_ALERTS
    for rule in rules:
        assert rule["annotations"]["slo_url"].startswith("docs/slo.md#")
        assert rule["annotations"]["runbook_url"].startswith(
            "docs/runbooks/observability-alerts.md#"
        )
        assert rule["for"].endswith("m")
        assert ">" in rule["expr"]


def test_monitoring_diagnostics_are_excluded_from_runtime_context() -> None:
    dockerignore = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {"monitoring/", "docs/", "tests/", ".github/"} <= dockerignore
