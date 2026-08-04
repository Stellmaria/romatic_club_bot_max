#!/usr/bin/env python3
"""Validate the observability dashboard, alert rules and documentation contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "monitoring/grafana/observability-dashboard.json"
ALERTS = ROOT / "monitoring/prometheus/alerts.yml"
RUNBOOK = ROOT / "docs/runbooks/observability-alerts.md"
SLO = ROOT / "docs/slo.md"
DOCKERIGNORE = ROOT / ".dockerignore"

REQUIRED_PANELS = {
    "Core latency p50 / p95",
    "Core error rate",
    "Core scheduler lag",
    "Core readiness",
    "Userbot latency p50 / p95",
    "Userbot error rate",
    "Userbot queue depth",
}
REQUIRED_ALERTS = {
    "BotCoreLatencyP95High",
    "UserbotLatencyP95High",
    "BotCoreErrorRateHigh",
    "UserbotErrorRateHigh",
    "BotSchedulerLagHigh",
    "UserbotQueueDepthHigh",
}
REQUIRED_METRICS = {
    "telegram_update_latency_seconds_bucket",
    "telegram_update_errors_total",
    "telegram_updates_total",
    "scheduler_lag_seconds",
    "userbot_operation_latency_seconds_bucket",
    "userbot_operation_errors_total",
    "userbot_operations_total",
    "userbot_queue_depth",
}
REQUIRED_IMAGE_EXCLUDES = {"monitoring/", "docs/", "tests/", ".github/"}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def validate(root: Path = ROOT) -> list[str]:
    dashboard = _load_json(root / DASHBOARD.relative_to(ROOT))
    alerts = _load_json(root / ALERTS.relative_to(ROOT))
    runbook = (root / RUNBOOK.relative_to(ROOT)).read_text(
        encoding="utf-8"
    ).casefold()
    slo = (root / SLO.relative_to(ROOT)).read_text(encoding="utf-8").casefold()
    dockerignore = {
        line.strip()
        for line in (root / DOCKERIGNORE.relative_to(ROOT))
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    errors: list[str] = []
    panels = dashboard.get("panels", [])
    panel_titles = {panel.get("title") for panel in panels if isinstance(panel, dict)}
    missing_panels = REQUIRED_PANELS - panel_titles
    if missing_panels:
        errors.append("missing dashboard panels: " + ", ".join(sorted(missing_panels)))

    dashboard_queries = "\n".join(
        str(target.get("expr", ""))
        for panel in panels
        if isinstance(panel, dict)
        for target in panel.get("targets", [])
        if isinstance(target, dict)
    )
    alert_groups = alerts.get("groups", [])
    rules = [
        rule
        for group in alert_groups
        if isinstance(group, dict)
        for rule in group.get("rules", [])
        if isinstance(rule, dict)
    ]
    alert_names = {rule.get("alert") for rule in rules}
    missing_alerts = REQUIRED_ALERTS - alert_names
    if missing_alerts:
        errors.append("missing alert rules: " + ", ".join(sorted(missing_alerts)))

    all_queries = dashboard_queries + "\n" + "\n".join(
        str(rule.get("expr", "")) for rule in rules
    )
    missing_metrics = {
        metric for metric in REQUIRED_METRICS if metric not in all_queries
    }
    if missing_metrics:
        errors.append("missing monitored metrics: " + ", ".join(sorted(missing_metrics)))

    for rule in rules:
        name = str(rule.get("alert", ""))
        annotations = rule.get("annotations", {})
        if not rule.get("expr") or not rule.get("for"):
            errors.append(f"{name}: expression and duration are required")
        if not isinstance(annotations, dict):
            errors.append(f"{name}: annotations are required")
            continue
        runbook_url = str(annotations.get("runbook_url", ""))
        slo_url = str(annotations.get("slo_url", ""))
        if not runbook_url.startswith("docs/runbooks/observability-alerts.md#"):
            errors.append(f"{name}: invalid runbook_url")
        if not slo_url.startswith("docs/slo.md#"):
            errors.append(f"{name}: invalid slo_url")
        if name.casefold() not in runbook:
            errors.append(f"{name}: runbook section is missing")

    if (
        "latency" not in slo
        or "error rate" not in slo
        or "queue depth" not in slo
    ):
        errors.append("docs/slo.md does not define all mandatory SLI classes")
    missing_excludes = REQUIRED_IMAGE_EXCLUDES - dockerignore
    if missing_excludes:
        errors.append(
            "runtime image context includes diagnostics: "
            + ", ".join(sorted(missing_excludes))
        )
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"monitoring contract error: {error}")
        return 1
    print("monitoring contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
