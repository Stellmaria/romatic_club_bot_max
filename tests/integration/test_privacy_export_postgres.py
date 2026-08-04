from __future__ import annotations

import json

import asyncpg
import pytest

from bot.core.time import SystemClock
from bot.repositories.privacy_exports import PrivacyExportRepository
from bot.services.privacy_exports import (
    PrivacyExportAuthorizationError,
    PrivacyExportService,
)


@pytest.mark.asyncio
async def test_privacy_export_is_read_only_except_append_only_audit(
    postgres_pool: asyncpg.Pool,
) -> None:
    user_id = 910000001
    async with postgres_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.users (user_id, username, full_name)
            VALUES ($1, 'privacy_fixture', 'Privacy Fixture')
            """,
            user_id,
        )
        await connection.execute(
            """
            INSERT INTO public.settings (user_id, notify_daily_today)
            VALUES ($1, TRUE)
            """,
            user_id,
        )
        before = await connection.fetchrow(
            "SELECT * FROM public.users WHERE user_id = $1",
            user_id,
        )

    repository = PrivacyExportRepository(postgres_pool)
    service = PrivacyExportService(repository, clock=SystemClock())
    result = await service.export_self(actor_user_id=user_id, subject_user_id=user_id)
    payload = json.loads(result.payload)

    assert payload["datasets"]["identity_profiles"]["users"][0]["username"] == (
        "privacy_fixture"
    )
    serialized = result.payload.decode("utf-8")
    for forbidden in ("uid_hash", "uid_enc", "proof_file_id", "proof_photo_id"):
        assert forbidden not in serialized

    async with postgres_pool.acquire() as connection:
        after = await connection.fetchrow(
            "SELECT * FROM public.users WHERE user_id = $1",
            user_id,
        )
        audit = await connection.fetchrow(
            """
            SELECT id, user_id, action_type, auction_id, details
            FROM public.audit_logs
            WHERE action_type = 'privacy.export.generated'
              AND details::jsonb ->> 'correlation_id' = $1
            """,
            str(result.correlation_id),
        )
        assert dict(before or {}) == dict(after or {})
        assert audit is not None
        assert audit["user_id"] is None
        assert audit["auction_id"] is None
        details = json.loads(audit["details"])
        assert details["contains_personal_values"] is False
        assert str(user_id) not in audit["details"]

        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "UPDATE public.audit_logs SET details = '{}' WHERE id = $1",
                audit["id"],
            )
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await connection.execute(
                "DELETE FROM public.audit_logs WHERE id = $1",
                audit["id"],
            )


@pytest.mark.asyncio
async def test_denied_privacy_export_audit_survives_authorization_error(
    postgres_pool: asyncpg.Pool,
) -> None:
    actor_user_id = 910000002
    subject_user_id = 910000003
    service = PrivacyExportService(
        PrivacyExportRepository(postgres_pool),
        clock=SystemClock(),
    )

    with pytest.raises(PrivacyExportAuthorizationError):
        await service.export_self(
            actor_user_id=actor_user_id,
            subject_user_id=subject_user_id,
        )

    async with postgres_pool.acquire() as connection:
        audit = await connection.fetchrow(
            """
            SELECT action_type, details
            FROM public.audit_logs
            WHERE action_type = 'privacy.export.denied'
            ORDER BY id DESC
            LIMIT 1
            """
        )

    assert audit is not None
    details = json.loads(audit["details"])
    assert details["outcome"] == "denied"
    assert details["reason"] == "self-service-subject-mismatch"
    assert details["contains_personal_values"] is False
    assert str(actor_user_id) not in audit["details"]
    assert str(subject_user_id) not in audit["details"]
