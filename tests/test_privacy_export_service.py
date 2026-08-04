from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bot.services.privacy_exports import (
    PrivacyExportAuthorizationError,
    PrivacyExportService,
)
from bot.uid_crypto import configure_uid_crypto, reset_uid_crypto_for_testing


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 5, tzinfo=UTC)


class _Transaction:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, *_args: object) -> None:
        self._connection.transaction_exit_exceptions.append(exc_type)


class _Connection:
    def __init__(self) -> None:
        self.transaction_exit_exceptions: list[object] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self)


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


class _Repository:
    def __init__(self) -> None:
        self._pool = _Pool()
        self.audits: list[tuple[str, dict[str, Any]]] = []

    async def collect(self, _connection: object, subject_user_id: int) -> dict[str, Any]:
        return {
            "identity_profiles": {
                "users": [{"user_id": subject_user_id, "username": "tester"}],
            },
        }

    def acquire(self) -> _Acquire:
        return self._pool.acquire()

    async def append_audit(
        self,
        _connection: object,
        *,
        action_type: str,
        details: str,
    ) -> None:
        self.audits.append((action_type, json.loads(details)))


@pytest.fixture(autouse=True)
def _uid_crypto() -> None:
    configure_uid_crypto(
        "test-hash-key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    try:
        yield
    finally:
        reset_uid_crypto_for_testing()


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_self_export_contains_allowlisted_data_and_pseudonymous_audit(
    inventory: Path,
) -> None:
    repository = _Repository()
    service = PrivacyExportService(
        repository,  # type: ignore[arg-type]
        clock=_Clock(),
        inventory_path=inventory,
    )

    result = await service.export_self(actor_user_id=42, subject_user_id=42)

    payload = json.loads(result.payload)
    assert payload["subject"] == {"telegram_user_id": 42}
    assert payload["datasets"]["identity_profiles"]["users"][0]["username"] == "tester"
    assert repository.audits[0][0] == "privacy.export.generated"
    audit = repository.audits[0][1]
    assert audit["actor_digest"] != "42"
    assert audit["subject_digest"] != "42"
    assert "username" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_cross_subject_export_is_denied_after_audit_commit(inventory: Path) -> None:
    repository = _Repository()
    service = PrivacyExportService(
        repository,  # type: ignore[arg-type]
        clock=_Clock(),
        inventory_path=inventory,
    )

    with pytest.raises(PrivacyExportAuthorizationError):
        await service.export_self(actor_user_id=42, subject_user_id=43)

    assert repository.audits[0][0] == "privacy.export.denied"
    assert repository.audits[0][1]["reason"] == "self-service-subject-mismatch"
    assert repository._pool.connection.transaction_exit_exceptions == [None]
