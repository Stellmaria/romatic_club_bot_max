from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from bot.core.logging import JsonLogFormatter
from bot.core.privacy import is_sensitive_key, redact, redact_text
from scripts.privacy_cleanup import (
    InventoryError,
    build_parser,
    load_inventory,
    validate_inventory,
)

INVENTORY_PATH = Path("docs/privacy/data_inventory.json")


def test_inventory_is_machine_validated_and_covers_known_personal_data_tables() -> None:
    inventory = load_inventory(INVENTORY_PATH)
    validate_inventory(inventory)

    tables = {table for dataset in inventory["datasets"] for table in dataset["tables"]}
    expected = {
        "users",
        "admins",
        "auction_owners",
        "bids",
        "autobids",
        "exchange_batches",
        "telegram_outbox",
        "uid_bans",
        "user_uids",
        "uid_verification_requests",
        "uid_verification_confirmations",
        "uid_verification_events",
        "user_appeals",
        "user_bans",
        "user_warnings",
        "audit_logs",
        "schedule_setup_sessions",
    }

    assert expected <= tables
    assert "schedule_setup_deck_scopes" not in tables
    assert inventory["backup_policy"]["local_retention_days"] == 14
    assert inventory["backup_policy"]["offsite_retention_days"] == 90


def test_inventory_enables_only_the_approved_temporary_cleanup() -> None:
    inventory = load_inventory(INVENTORY_PATH)
    validate_inventory(inventory)

    policies = inventory["retention_classes"]
    assert policies["temporary_7d"]["destructive_enabled"] is True
    assert all(
        policy["destructive_enabled"] is False
        for name, policy in policies.items()
        if name != "temporary_7d"
    )

    enabled_rules = [
        rule
        for dataset in inventory["datasets"]
        for rule in dataset.get("cleanup_rules", [])
        if rule["destructive_enabled"] is True
    ]
    assert enabled_rules == [
        {
            "id": "expired_schedule_setup_sessions",
            "planner_key": "schedule_setup_sessions",
            "status": "approved",
            "destructive_enabled": True,
        }
    ]


def test_inventory_rejects_unallowlisted_destructive_policy_enablement() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    inventory["retention_classes"]["operational_30d"]["destructive_enabled"] = True

    with pytest.raises(InventoryError, match="not allowlisted"):
        validate_inventory(inventory)


def test_cleanup_cli_requires_explicit_apply_confirmation() -> None:
    parser = build_parser()

    offline = parser.parse_args(["plan", "--offline"])
    assert offline.command == "plan"
    assert offline.offline is True

    with pytest.raises(SystemExit):
        parser.parse_args(["apply"])

    apply = parser.parse_args(["apply", "--confirm", "APPLY:example"])
    assert apply.command == "apply"
    assert apply.confirm == "APPLY:example"
    assert apply.batch_limit == 1_000


def test_privacy_redaction_masks_structured_and_free_text_identifiers() -> None:
    raw_uid = "".join(("a1b2c3d4", "e5f60718", "293a4b5c"))
    sample_token = ":".join(("123456789", "A" * 35))
    payload = {
        "user_id": 123456789,
        "target_username": "alice_example",
        "profile_proof_file_id": "telegram-file-token",
        "safe": {
            "auction_id": 3797,
            "message_id": 5948,
            "operation_id": "auction:publish",
        },
        "error": (
            f"user_id=123456789 @{'alice_example'} uid={raw_uid} "
            f"token={sample_token} phone=+7 (999) 000-00-00"
        ),
    }

    redacted = redact(payload)

    assert redacted["user_id"] == "[REDACTED]"
    assert redacted["target_username"] == "[REDACTED]"
    assert redacted["profile_proof_file_id"] == "[REDACTED]"
    assert redacted["safe"] == {
        "auction_id": 3797,
        "message_id": 5948,
        "operation_id": "auction:publish",
    }
    assert "123456789" not in redacted["error"]
    assert "alice_example" not in redacted["error"]
    assert raw_uid not in redacted["error"]
    assert "AAAAAAAA" not in redacted["error"]
    assert "+7" not in redacted["error"]


def test_sensitive_key_contract_preserves_operational_identifiers() -> None:
    for key in (
        "user_id",
        "target_user_id",
        "moderator_username",
        "uid_hash",
        "telethon_session",
        "profile_file_id",
        "created_by",
    ):
        assert is_sensitive_key(key) is True

    for key in ("auction_id", "message_id", "operation_id", "correlation_id"):
        assert is_sensitive_key(key) is False


def test_json_formatter_redacts_message_extras_and_exception_traceback() -> None:
    raw_uid = "".join(("abcdef01", "23456789", "abcdef01"))
    try:
        raise RuntimeError(f"failed for user_id=123456789 uid={raw_uid} @alice_example")
    except RuntimeError:
        exc_info = __import__("sys").exc_info()

    record = logging.LogRecord(
        name="auction_bot.privacy",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="delivery failed for user_id=123456789 @alice_example",
        args=(),
        exc_info=exc_info,
    )
    record.correlation_id = "cid-safe"
    record.operation_id = "telegram:delivery"
    record.owner_id = 123456789
    record.proof_file_id = "telegram-file-token"

    rendered = JsonLogFormatter().format(record)

    assert "123456789" not in rendered
    assert "alice_example" not in rendered
    assert raw_uid not in rendered
    assert "telegram-file-token" not in rendered
    assert '"correlation_id": "cid-safe"' in rendered
    assert '"operation_id": "telegram:delivery"' in rendered
    assert "[REDACTED" in rendered


def test_redact_text_preserves_non_personal_operational_context() -> None:
    rendered = redact_text("auction_id=3797 message_id=5948 status=finished")

    assert rendered == "auction_id=3797 message_id=5948 status=finished"
