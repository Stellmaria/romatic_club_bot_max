from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.domain.auctions import (
    AuctionOwnerPermissionDenied,
    AuctionSlotConflict,
    InvalidAuctionTransition,
    InvalidExchangeTransition,
)
from bot.use_cases.auction_cancellation import (
    CancelAuctionCommand,
    CancelAuctionUseCase,
    CancelOwnedAuctionCommand,
    CancelOwnedAuctionUseCase,
)
from bot.use_cases.auction_moderation import (
    RescheduleAuctionUseCase,
    ScheduleAuctionCommand,
    ScheduleAuctionUseCase,
)
from bot.use_cases.auction_publication import PublishAuctionCommand, PublishAuctionUseCase
from bot.use_cases.auction_submission import SubmitAuctionCommand, SubmitAuctionUseCase
from bot.use_cases.common import (
    ApplicationConflict,
    ApplicationInvalidState,
    ApplicationPermissionDenied,
)
from bot.use_cases.exchange_submission import SubmitExchangeCommand, SubmitExchangeUseCase
from bot.use_cases.exchange_moderation import (
    ApproveExchangeUseCase,
    ModerateExchangeCommand,
    RejectExchangeUseCase,
)
from bot.use_cases.roles import ChangeRoleCommand, ChangeRoleUseCase, RoleKind
from bot.use_cases.uid_moderation import (
    ApproveUidVerificationUseCase,
    ModerateUidCommand,
    RejectUidVerificationUseCase,
)
from bot.use_cases.user_lot_edit import EditOwnedLotCommand, EditOwnedLotUseCase

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _schedule_use_case(*, mutate):
    async def get_lot(_auction_id: int):
        return {
            "auction_id": 10,
            "start_time": datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 8, 2, 10, 30, 59, tzinfo=UTC),
        }

    async def get_owners(_auction_id: int):
        return [{"user_id": 1}]

    async def get_user(_user_id: int):
        return {"user_id": 1, "username": "owner", "is_trusted": True}

    async def is_luxury(_user_id: int):
        return True

    return ScheduleAuctionUseCase(
        get_lot=get_lot,
        mutate=mutate,
        get_owners=get_owners,
        get_user=get_user,
        is_luxury=is_luxury,
    )


@pytest.mark.asyncio
async def test_schedule_use_case_translates_conflict() -> None:
    async def mutate(_auction_id: int, **_kwargs):
        raise AuctionSlotConflict("occupied")

    with pytest.raises(ApplicationConflict):
        await _schedule_use_case(mutate=mutate).execute(
            ScheduleAuctionCommand(
                auction_id=10,
                start_time=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                end_time=datetime(2026, 8, 2, 12, 30, 59, tzinfo=UTC),
            )
        )


@pytest.mark.asyncio
async def test_schedule_use_case_returns_owner_snapshot() -> None:
    async def mutate(_auction_id: int, *, start_time, end_time):
        return {"auction_id": 10, "start_time": start_time, "end_time": end_time}

    result = await _schedule_use_case(mutate=mutate).execute(
        ScheduleAuctionCommand(
            auction_id=10,
            start_time=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            end_time=datetime(2026, 8, 2, 12, 30, 59, tzinfo=UTC),
        )
    )
    assert result.owners_text == "👑 @owner"
    assert result.owners[0].is_trusted is True


@pytest.mark.asyncio
async def test_reschedule_rejects_repository_mismatch() -> None:
    async def get_lot(_auction_id: int):
        return {
            "auction_id": 10,
            "start_time": datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 8, 2, 10, 30, 59, tzinfo=UTC),
        }

    async def mutate(_auction_id: int, *, start_time, end_time):
        return {
            "auction_id": 10,
            "start_time": start_time.replace(hour=13),
            "end_time": end_time.replace(hour=13),
        }

    async def empty(_value: int):
        return []

    async def no_user(_value: int):
        return None

    async def not_luxury(_value: int):
        return False

    use_case = RescheduleAuctionUseCase(
        get_lot=get_lot,
        mutate=mutate,
        get_owners=empty,
        get_user=no_user,
        is_luxury=not_luxury,
    )
    with pytest.raises(ApplicationInvalidState):
        await use_case.execute(
            ScheduleAuctionCommand(
                auction_id=10,
                start_time=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                end_time=datetime(2026, 8, 2, 12, 30, 59, tzinfo=UTC),
            )
        )


@pytest.mark.asyncio
async def test_publication_marks_failure_and_skips_post_commit_effect() -> None:
    events: list[str] = []

    async def claim(_auction_id: int):
        events.append("claim")
        return {"auction_id": 7}

    async def build(_auction):
        events.append("build")
        return "payload"

    async def send(_auction, _payload):
        events.append("send")
        raise RuntimeError("telegram down")

    async def mark_published(_auction_id: int, _message_id: int):
        events.append("published")
        return True

    async def mark_failed(_auction_id: int, _error: str):
        events.append("failed")

    async def after(_auction, _message_id: int):
        events.append("after")

    use_case = PublishAuctionUseCase(
        claim=claim,
        build_payload=build,
        send=send,
        mark_published=mark_published,
        mark_failed=mark_failed,
        after_published=after,
    )
    with pytest.raises(RuntimeError):
        await use_case.execute(PublishAuctionCommand(auction_id=7))
    assert events == ["claim", "build", "send", "failed"]


@pytest.mark.asyncio
async def test_publication_effect_runs_only_after_published_commit() -> None:
    events: list[str] = []

    async def claim(_auction_id: int):
        return {"auction_id": 7}

    async def build(_auction):
        return "payload"

    async def send(_auction, _payload):
        events.append("send")
        return 99

    async def mark_published(_auction_id: int, _message_id: int):
        events.append("commit")
        return True

    async def mark_failed(_auction_id: int, _error: str):
        events.append("failed")

    async def after(_auction, _message_id: int):
        events.append("after")

    result = await PublishAuctionUseCase(
        claim=claim,
        build_payload=build,
        send=send,
        mark_published=mark_published,
        mark_failed=mark_failed,
        after_published=after,
    ).execute(PublishAuctionCommand(auction_id=7))
    assert result.message_id == 99
    assert events == ["send", "commit", "after"]


def _exchange_use_case(*, target: str, moderate):
    async def get_batch(_batch_id: int):
        return {"batch_id": 5, "deck_id": 2, "status": "pending"}

    async def get_deck(_deck_id: int):
        return {"deck_id": 2, "name": "Deck"}

    async def get_items(_batch_id: int):
        return [{"card_id": 1}, {"card_id": 2}]

    cls = ApproveExchangeUseCase if target == "approved" else RejectExchangeUseCase
    return cls(
        get_batch=get_batch,
        get_deck=get_deck,
        get_items=get_items,
        moderate=moderate,
    )


@pytest.mark.asyncio
async def test_exchange_moderation_returns_one_application_result() -> None:
    async def approve(_batch_id: int, **_kwargs):
        return {"batch_id": 5, "deck_id": 2, "status": "approved"}

    result = await _exchange_use_case(target="approved", moderate=approve).execute(
        ModerateExchangeCommand(
            batch_id=5,
            moderator_id=8,
            moderator_username="admin",
        )
    )
    assert result.batch["status"] == "approved"
    assert result.deck["name"] == "Deck"
    assert result.item_count == 2


@pytest.mark.asyncio
async def test_exchange_transition_is_stable_application_error() -> None:
    async def reject(_batch_id: int, **_kwargs):
        raise InvalidExchangeTransition(current="approved", target="rejected")

    with pytest.raises(ApplicationInvalidState) as caught:
        await _exchange_use_case(target="rejected", moderate=reject).execute(
            ModerateExchangeCommand(
                batch_id=5,
                moderator_id=8,
                moderator_username="admin",
                reason="duplicate",
            )
        )
    assert caught.value.details["current"] == "approved"


@pytest.mark.asyncio
async def test_uid_approval_enforces_confirmation_threshold() -> None:
    async def get_request(_request_id: int):
        return {
            "id": 1,
            "confirmations": [{"status": "confirmed"}, {"status": "pending"}],
        }

    async def decide(**_kwargs):
        raise AssertionError("must not mutate")

    use_case = ApproveUidVerificationUseCase(
        get_request=get_request,
        decide=decide,
        required_confirmations=2,
    )
    with pytest.raises(ApplicationConflict) as caught:
        await use_case.execute(ModerateUidCommand(request_id=1, admin_id=2))
    assert caught.value.details == {"confirmed": 1, "required": 2}


@pytest.mark.asyncio
async def test_uid_rejection_requires_reason_and_returns_reloaded_request() -> None:
    states = iter(
        [
            {"id": 1, "status": "pending", "confirmations": []},
            {"id": 1, "status": "rejected", "confirmations": []},
        ]
    )

    async def get_request(_request_id: int):
        return next(states)

    async def decide(**kwargs):
        assert kwargs["admin_comment"] == "bad proof"
        return True, None

    result = await RejectUidVerificationUseCase(
        get_request=get_request,
        decide=decide,
    ).execute(
        ModerateUidCommand(
            request_id=1,
            admin_id=2,
            reason="bad proof",
        )
    )
    assert result.request["status"] == "rejected"


@pytest.mark.asyncio
async def test_owner_edit_is_atomic_and_owner_scoped() -> None:
    observed: dict[str, object] = {}

    async def update_owned(auction_id: int, *, owner_id: int, changes):
        observed.update(auction_id=auction_id, owner_id=owner_id, changes=changes)
        return {"auction_id": auction_id, **changes}

    async def owners_text(_auction_id: int):
        return "@owner"

    result = await EditOwnedLotUseCase(
        update_owned=update_owned,
        owners_text=owners_text,
    ).execute(
        EditOwnedLotCommand(
            auction_id=4,
            owner_id=9,
            changes={"currency": "diamonds", "start_price": 10},
        )
    )
    assert observed["changes"] == {"currency": "diamonds", "start_price": 10}
    assert result.owners_text == "@owner"


@pytest.mark.asyncio
async def test_owner_edit_translates_permission_failure() -> None:
    async def update_owned(_auction_id: int, **_kwargs):
        raise AuctionOwnerPermissionDenied("not owner")

    async def owners_text(_auction_id: int):
        return ""

    with pytest.raises(ApplicationPermissionDenied):
        await EditOwnedLotUseCase(
            update_owned=update_owned,
            owners_text=owners_text,
        ).execute(
            EditOwnedLotCommand(auction_id=4, owner_id=9, changes={"comment": "x"})
        )


def _function_calls(path: str, name: str) -> set[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    calls: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            try:
                calls.add(ast.unparse(node.func))
            except Exception:
                pass
    return calls


def test_critical_handlers_delegate_to_use_cases() -> None:
    assert "_reschedule_auction_use_case" in _function_calls(
        "bot/handlers/admin/admin_panel_schedule.py", "save_edited_time"
    )
    assert "_schedule_auction_use_case" in _function_calls(
        "bot/handlers/admin/moderation_lots.py", "handle_confirm_lot"
    )
    assert "PublishAuctionUseCase" in _function_calls(
        "bot/handlers/auction/publication.py", "publish_auction_lot"
    )
    assert "_exchange_approval_use_case" in _function_calls(
        "bot/handlers/auction/exchange/moderation.py", "exchange_approve"
    )
    assert "_exchange_rejection_use_case" in _function_calls(
        "bot/handlers/auction/exchange/moderation.py", "exchange_reject_reason"
    )
    assert "_approve_uid_use_case" in _function_calls(
        "bot/handlers/admin/uid_verification_review.py", "verif_approve"
    )
    assert "_reject_uid_use_case" in _function_calls(
        "bot/handlers/admin/uid_verification_review.py", "verif_reject_reason"
    )
    assert "SubmitAuctionUseCase" in _function_calls(
        "bot/handlers/auction/submission.py", "_final_addlot_create"
    )
    assert "SubmitExchangeUseCase" in _function_calls(
        "bot/handlers/auction/exchange/submission.py", "_finalize_exchange_request"
    )
    assert "CancelAuctionUseCase" in _function_calls(
        "bot/handlers/admin/admin_panel_schedule.py", "delete_lot_final"
    )
    assert "CancelOwnedAuctionUseCase" in _function_calls(
        "bot/handlers/users.py", "user_delete_lot"
    )
    for name in ("process_edit_price", "process_currency_and_price", "process_edit_comment"):
        calls = _function_calls("bot/handlers/users.py", name)
        assert "_apply_owned_lot_edit" in calls
        assert "update_lot_field" not in calls


@pytest.mark.asyncio
async def test_auction_submission_loads_access_level_before_atomic_submit() -> None:
    calls: list[str] = []

    async def level(owner_id: int) -> int:
        calls.append(f"level:{owner_id}")
        return 3

    async def submit(**kwargs):
        calls.append(f"submit:{kwargs['luxury_level']}")
        return {"auction_id": 77, "status": "pending"}

    result = await SubmitAuctionUseCase(
        get_luxury_level=level, submit=submit
    ).execute(
        SubmitAuctionCommand(
            owner_id=5, card_id=1, hero_name="H", card_name="C",
            start_price=10, currency="diamonds", accepted_currencies=("diamonds",),
            custom_offer_terms=None, comment="", image_id=None,
            auction_kind="standard", proof_photo_id=None, craft_uid_possible=None,
        )
    )
    assert result.auction_id == 77
    assert calls == ["level:5", "submit:3"]


@pytest.mark.asyncio
async def test_owner_cancellation_rejects_non_pending_without_mutation() -> None:
    async def get_owned(_auction_id: int, *, owner_id: int):
        return {"auction_id": 9, "status": "active", "owner_id": owner_id}

    async def cancel_owned(*_args, **_kwargs):
        raise AssertionError("must not mutate")

    async def owners_text(_auction_id: int):
        return "@owner"

    with pytest.raises(ApplicationInvalidState):
        await CancelOwnedAuctionUseCase(
            get_owned=get_owned, cancel_owned=cancel_owned, get_owners_text=owners_text
        ).execute(CancelOwnedAuctionCommand(auction_id=9, owner_id=2))


@pytest.mark.asyncio
async def test_admin_cancellation_returns_post_commit_owner_snapshot() -> None:
    async def get_lot(_auction_id: int):
        return {"auction_id": 9, "status": "scheduled"}

    async def cancel(_auction_id: int):
        return {"auction_id": 9, "status": "cancelled"}

    async def owners(_auction_id: int):
        return [{"user_id": 2}]

    async def owners_text(_auction_id: int):
        return "@owner"

    result = await CancelAuctionUseCase(
        get_lot=get_lot, cancel=cancel, get_owners=owners, get_owners_text=owners_text
    ).execute(CancelAuctionCommand(auction_id=9, moderator_id=1))
    assert result.lot["status"] == "cancelled"
    assert result.owners_text == "@owner"


@pytest.mark.asyncio
async def test_exchange_split_is_persisted_in_one_submit_many_call() -> None:
    captured: list[list[dict]] = []

    async def get_ids(_deck_id: int):
        return [1, 2]

    async def get_cards(_ids: list[int]):
        return [
            {"card_id": 1, "rarity": "gold"},
            {"card_id": 2, "rarity": "silver"},
        ]

    def price(card: dict) -> int:
        return 10 if card["card_id"] == 1 else 5

    async def deck_price(_deck_id: int):
        return 15

    async def submit_many(requests):
        rows = list(requests)
        captured.append(rows)
        return [{"batch_id": 101}, {"batch_id": 102}]

    result = await SubmitExchangeUseCase(
        get_card_ids_by_deck=get_ids, get_cards=get_cards, price_for_card=price,
        price_for_deck=deck_price, submit_many=submit_many,
    ).execute(SubmitExchangeCommand(
        user_id=3, deck_id=8, mode="deck_split", currency="diamonds",
        comment="", proof_photo_id="NO_PROOF", card_ids=(), split_mode="per_card",
    ))
    assert len(captured) == 1
    assert [row["card_ids"] for row in captured[0]] == [(1,), (2,)]
    assert [item.batch_id for item in result.items] == [101, 102]


@pytest.mark.asyncio
async def test_role_use_case_mutates_and_audits_as_one_application_operation() -> None:
    events: list[str] = []

    async def is_admin(_user_id: int): return False
    async def add_admin(user_id: int, _username: str | None, actor_id: int):
        events.append(f"add:{user_id}:{actor_id}")
    async def remove_admin(_user_id: int): events.append("remove")
    async def trusted(_user_id: int, _grant: bool): events.append("trusted")
    async def audit(**kwargs): events.append(f"audit:{kwargs['action_type']}")

    result = await ChangeRoleUseCase(
        is_admin=is_admin, add_admin=add_admin, remove_admin=remove_admin,
        set_trusted=trusted, audit=audit,
    ).execute(ChangeRoleCommand(
        target_id=7, target_username="user", actor_id=1, actor_username="owner",
        role=RoleKind.ADMIN, grant=True, owner_ids=frozenset({1}),
    ))
    assert result.action_type == "add_admin"
    assert events == ["add:7:1", "audit:add_admin"]
