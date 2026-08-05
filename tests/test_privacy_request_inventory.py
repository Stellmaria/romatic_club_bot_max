from __future__ import annotations

from scripts.privacy_cleanup import load_inventory, validate_inventory


def test_privacy_request_lifecycle_is_in_canonical_inventory() -> None:
    inventory = load_inventory()
    validate_inventory(inventory)

    datasets = {dataset["id"]: dataset for dataset in inventory["datasets"]}
    lifecycle = datasets["privacy_request_lifecycle"]

    assert lifecycle["tables"] == ["privacy_requests"]
    assert lifecycle["sensitivity"] == "restricted"
    assert lifecycle["backup_presence"] is True
    assert lifecycle["retention_class"] == "security_365d"
    assert inventory["jurisdiction_status"] == "legal-retention-periods-not-approved"
