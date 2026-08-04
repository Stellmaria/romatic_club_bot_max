from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.repair_auction_publications import (
    KNOWN_AUCTION_IDS,
    RepairPlanError,
    load_plan,
    parse_plan,
)


def _complete_plan() -> dict[str, object]:
    return {
        "repairs": [
            {
                "auction_id": 9210,
                "action": "confirm",
                "channel_message_id": 12001,
                "discussion_message_id": 1148772,
            },
            {
                "auction_id": 9217,
                "action": "confirm",
                "channel_message_id": 12002,
                "discussion_message_id": 1149339,
            },
            {
                "auction_id": 9221,
                "action": "confirm",
                "channel_message_id": 12003,
                "discussion_message_id": 1149326,
            },
            {
                "auction_id": 9243,
                "action": "requeue",
                "post_verified_absent": True,
            },
            {
                "auction_id": 3797,
                "action": "normalize_published",
                "channel_message_id": 5927,
            },
            {
                "auction_id": 7523,
                "action": "normalize_published",
                "channel_message_id": 10139,
            },
        ]
    }


def test_complete_reviewed_plan_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(_complete_plan()), encoding="utf-8")

    actions = load_plan(path)

    assert {action.auction_id for action in actions} == KNOWN_AUCTION_IDS


def test_plan_never_guesses_missing_channel_id() -> None:
    plan = _complete_plan()
    repairs = plan["repairs"]
    assert isinstance(repairs, list)
    repairs[0] = {"auction_id": 9210, "action": "confirm"}

    with pytest.raises(RepairPlanError, match="verified channel_message_id"):
        parse_plan(plan)


def test_requeue_requires_explicit_absence_confirmation() -> None:
    plan = _complete_plan()
    repairs = plan["repairs"]
    assert isinstance(repairs, list)
    repairs[3] = {"auction_id": 9243, "action": "requeue"}

    with pytest.raises(RepairPlanError, match="post_verified_absent"):
        parse_plan(plan)


def test_partial_plan_is_rejected() -> None:
    plan = _complete_plan()
    repairs = plan["repairs"]
    assert isinstance(repairs, list)
    repairs.pop()

    with pytest.raises(RepairPlanError, match="missing"):
        parse_plan(plan)


def test_cli_exposes_explicit_dry_run_apply_and_validation_modes() -> None:
    from scripts.repair_auction_publications import _parser

    parser = _parser()
    dry_run = parser.parse_args(["--plan", "issue99.json", "--dry-run"])
    apply = parser.parse_args(["--plan", "issue99.json", "--apply"])
    validate = parser.parse_args(["--validate-constraints"])

    assert dry_run.dry_run is True and dry_run.apply is False
    assert apply.apply is True and apply.dry_run is False
    assert validate.validate_constraints is True and validate.plan is None


def test_partial_runtime_plan_can_be_parsed_without_guessing() -> None:
    from bot.services.auction_publication_repair import parse_issue99_plan

    actions = parse_issue99_plan(
        {
            "repairs": [
                {
                    "auction_id": 9210,
                    "action": "confirm",
                    "channel_message_id": 12010,
                    "discussion_message_id": 1148772,
                }
            ]
        },
        require_complete=False,
    )

    assert len(actions) == 1
    assert actions[0].auction_id == 9210
    assert actions[0].channel_message_id == 12010
