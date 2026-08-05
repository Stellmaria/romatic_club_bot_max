from __future__ import annotations

import json

import asyncpg
import pytest

from bot.core.time import SystemClock
from bot.repositories.privacy_requests import (
    PrivacyRequestBlocked,
    PrivacyRequestConflict,
    PrivacyRequestRepository,
)
from bot.services.privacy_requests import PrivacyRequestService

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_reviewed_privacy_request_anonymizes_optional_data_and_keeps_holds(
    postgres_pool: asyncpg.Pool,
) -> None:
    user_id = 920000001
    async with postgres_pool.acquire() as connection:
        deck_id = await connection.fetchval(
            "INSERT INTO public.decks (name) VALUES ('Privacy Deck') RETURNING id"
        )
        await connection.execute(
            """
            INSERT INTO public.users (
                user_id, username, full_name, is_luxury, is_trusted, pm_opened
            ) VALUES ($1, 'privacy_delete_fixture', 'Privacy Delete Fixture', TRUE, TRUE, TRUE)
            """,
            user_id,
        )
        await connection.execute("INSERT INTO public.settings (user_id) VALUES ($1)", user_id)
        await connection.execute(
            "INSERT INTO public.unreachable_users (user_id, reason) VALUES ($1, 'blocked')",
            user_id,
        )
        await connection.execute(
            "INSERT INTO public.trusted_usernames (username) VALUES ('privacy_delete_fixture')"
        )
        await connection.execute(
            """
            INSERT INTO public.user_uids (
                uid, user_id, uid_hash, uid_enc, uid_last4
            ) VALUES ('raw-uid-value', $1, 'digest-value', 'cipher-value', '1234')
            """,
            user_id,
        )
        uid_request_id = await connection.fetchval(
            """
            INSERT INTO public.uid_verification_requests (
                user_id, uid, challenge_code, profile_file_id,
                verification_code, status, uid_hash, uid_enc, uid_last4
            ) VALUES (
                $1, 'raw-uid-value', 'challenge', 'profile-file',
                'verification', 'approved', 'digest-value', 'cipher-value', '1234'
            ) RETURNING id
            """,
            user_id,
        )
        await connection.execute(
            """
            INSERT INTO public.uid_verification_confirmations (
                request_id, counterparty_username, status, message_chat_id, message_id
            ) VALUES ($1, 'counterparty', 'confirmed', 100, 200)
            """,
            uid_request_id,
        )
        await connection.execute(
            """
            INSERT INTO public.uid_verification_events (
                request_id, actor_id, actor_username, event_type, details
            ) VALUES ($1, $2, 'privacy_delete_fixture', 'approved', '{"secret":"value"}')
            """,
            uid_request_id,
            user_id,
        )
        await connection.execute(
            """
            INSERT INTO public.user_appeals (
                user_id, username, topic, description, participants,
                media_message_ids, origin_chat_id, status
            ) VALUES (
                $1, 'privacy_delete_fixture', 'topic', 'personal text', 'person',
                ARRAY[1, 2], 555, 'resolved'
            )
            """,
            user_id,
        )
        listing_id = await connection.fetchval(
            """
            INSERT INTO public.market_listings (
                seller_id, status, description, cover_file_id, channel_id,
                message_id, proof_file_id, proof_by_card
            ) VALUES (
                $1, 'archived', 'personal listing', 'cover', 1, 2, 'proof',
                '{"1":"proof"}'::jsonb
            ) RETURNING listing_id
            """,
            user_id,
        )
        card_id = await connection.fetchval(
            """
            INSERT INTO public.cards (
                deck_id, num, hero_name, rarity, story, card_name
            ) VALUES ($1, 1, 'Hero', 'common', 'Story', 'Card')
            RETURNING card_id
            """,
            deck_id,
        )
        await connection.execute(
            """
            INSERT INTO public.market_listing_items (listing_id, card_id, proof_file_id)
            VALUES ($1, $2, 'item-proof')
            """,
            listing_id,
            card_id,
        )
        await connection.execute(
            """
            INSERT INTO public.exchange_batches (
                user_id, deck_id, mode, currency, price, comment,
                proof_photo_id, status, posted_chat_id, posted_message_id
            ) VALUES ($1, $2, 'deck', 'cups', 1, 'personal comment',
                      'proof-photo', 'approved', 10, 20)
            """,
            user_id,
            deck_id,
        )
        audit_id = await connection.fetchval(
            """
            INSERT INTO public.audit_logs (user_id, action_type, details)
            VALUES ($1, 'profile.updated', 'personal-link')
            RETURNING id
            """,
            user_id,
        )

    service = PrivacyRequestService(
        PrivacyRequestRepository(postgres_pool),
        clock=SystemClock(),
    )
    request = await service.request_self(
        actor_user_id=user_id,
        subject_user_id=user_id,
    )
    plan = await service.plan_operator(request.request_id)
    assert plan.blocking_holds == ()
    assert "business-history-retained" in plan.retained_holds
    assert "security-history-retained" in plan.retained_holds

    approved = await service.approve_operator(
        request_id=request.request_id,
        expected_plan_sha256=plan.plan_sha256,
        operator_identity="integration-approver",
    )
    with pytest.raises(PrivacyRequestConflict, match="different from the approver"):
        await service.execute_operator(
            request_id=request.request_id,
            expected_plan_sha256=approved.plan_sha256,
            operator_identity="integration-approver",
            confirmation=f"APPLY:{request.request_id}:{approved.plan_sha256[:12]}",
        )

    completed = await service.execute_operator(
        request_id=request.request_id,
        expected_plan_sha256=approved.plan_sha256,
        operator_identity="integration-executor",
        confirmation=f"APPLY:{request.request_id}:{approved.plan_sha256[:12]}",
    )
    assert completed.status == "completed_with_holds"

    async with postgres_pool.acquire() as connection:
        user = await connection.fetchrow("SELECT * FROM public.users WHERE user_id = $1", user_id)
        assert user is None
        surrogate = await connection.fetchrow("""
            SELECT user_id, username, full_name, is_subscribed, is_luxury, is_trusted
            FROM public.users
            WHERE user_id < 0
            ORDER BY user_id
            LIMIT 1
            """)
        assert surrogate is not None
        surrogate_user_id = int(surrogate["user_id"])
        assert surrogate_user_id != user_id
        assert surrogate["username"] is None
        assert surrogate["full_name"] is None
        assert surrogate["is_subscribed"] is False
        assert surrogate["is_luxury"] is False
        assert surrogate["is_trusted"] is False
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM public.settings WHERE user_id = $1", user_id
            )
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM public.unreachable_users WHERE user_id = $1", user_id
            )
            == 0
        )

        uid_row = await connection.fetchrow(
            """
            SELECT uid, uid_hash, uid_enc, uid_last4, status
            FROM public.user_uids
            WHERE user_id = $1
            """,
            surrogate_user_id,
        )
        assert uid_row is not None
        assert str(uid_row["uid"]).startswith("redacted:")
        assert uid_row["uid_hash"] == "digest-value"
        assert uid_row["uid_enc"] is None
        assert uid_row["uid_last4"] is None
        assert uid_row["status"] == "revoked"

        assert (
            await connection.fetchval(
                "SELECT seller_id FROM public.market_listings WHERE listing_id = $1",
                listing_id,
            )
            == surrogate_user_id
        )
        assert (
            await connection.fetchval(
                "SELECT user_id FROM public.exchange_batches ORDER BY batch_id DESC LIMIT 1"
            )
            == surrogate_user_id
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM public.users WHERE user_id = $1", user_id
            )
            == 0
        )

        assert (
            await connection.fetchval(
                "SELECT user_id FROM public.audit_logs WHERE id = $1", audit_id
            )
            is None
        )
        request_row = await connection.fetchrow(
            """
            SELECT subject_user_id, status, retained_holds
            FROM public.privacy_requests
            WHERE request_id = $1
            """,
            request.request_id,
        )
        assert request_row is not None
        assert request_row["subject_user_id"] is None
        assert request_row["status"] == "completed_with_holds"

        audits = await connection.fetch(
            """
            SELECT action_type, details
            FROM public.audit_logs
            WHERE action_type LIKE 'privacy.request.%'
              AND details::jsonb ->> 'request_id' = $1
            ORDER BY id
            """,
            str(request.request_id),
        )
        assert {row["action_type"] for row in audits} == {
            "privacy.request.created",
            "privacy.request.approved",
            "privacy.request.completed",
        }
        for row in audits:
            details = json.loads(row["details"])
            assert details["contains_personal_values"] is False
            assert str(user_id) not in row["details"]

        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "UPDATE public.privacy_requests SET status = 'failed' WHERE request_id = $1",
                request.request_id,
            )
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "DELETE FROM public.privacy_requests WHERE request_id = $1",
                request.request_id,
            )


@pytest.mark.asyncio
async def test_active_market_listing_blocks_privacy_request_approval(
    postgres_pool: asyncpg.Pool,
) -> None:
    user_id = 920000002
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO public.users (user_id, username) VALUES ($1, 'privacy_blocked')",
            user_id,
        )
        await connection.execute(
            "INSERT INTO public.market_listings (seller_id, status) VALUES ($1, 'active')",
            user_id,
        )

    service = PrivacyRequestService(
        PrivacyRequestRepository(postgres_pool),
        clock=SystemClock(),
    )
    request = await service.request_self(
        actor_user_id=user_id,
        subject_user_id=user_id,
    )
    plan = await service.plan_operator(request.request_id)
    assert plan.executable is False
    assert "active-market-listing" in plan.blocking_holds

    with pytest.raises(PrivacyRequestBlocked):
        await service.approve_operator(
            request_id=request.request_id,
            expected_plan_sha256=plan.plan_sha256,
            operator_identity="integration-operator",
        )
