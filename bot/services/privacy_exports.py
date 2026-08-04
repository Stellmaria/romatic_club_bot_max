from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from bot.application_ports import Clock
from bot.repositories.privacy_exports import PrivacyExportRepository
from bot.uid_crypto import identity_digest

DEFAULT_INVENTORY_PATH = Path("docs/privacy/data_inventory.json")
_FORBIDDEN_EXPORT_KEYS = {
    "uid_hash",
    "uid_enc",
    "proof_file_id",
    "proof_photo_id",
    "file_id",
    "file_unique_id",
    "media_message_ids",
    "origin_chat_id",
    "moderator_id",
    "moderator_username",
    "discussion_message_id",
    "message_id",
    "chat_id",
    "token",
    "session",
}


class PrivacyExportAuthorizationError(PermissionError):
    """Raised when an actor asks for another subject's export."""


@dataclass(frozen=True, slots=True)
class PrivacyExportResult:
    correlation_id: UUID
    filename: str
    payload: bytes
    dataset_counts: dict[str, int]
    exported_rows: int


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(f"unsupported export value: {type(value).__name__}")


def _assert_no_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_EXPORT_KEYS or normalized.endswith("_token"):
                raise RuntimeError(f"forbidden field reached privacy export: {key}")
            _assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item)


class PrivacyExportService:
    """Generate authenticated self-service exports and immutable audit evidence."""

    def __init__(
        self,
        repository: PrivacyExportRepository,
        *,
        clock: Clock,
        inventory_path: Path = DEFAULT_INVENTORY_PATH,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._inventory_path = inventory_path

    def _policy_sha256(self) -> str:
        return hashlib.sha256(self._inventory_path.read_bytes()).hexdigest()

    async def _audit_denied_request(
        self,
        *,
        correlation_id: UUID,
        actor_digest: str,
        subject_digest: str,
        policy_sha256: str,
    ) -> None:
        details = json.dumps(
            {
                "schema_version": 1,
                "correlation_id": str(correlation_id),
                "actor_digest": actor_digest,
                "subject_digest": subject_digest,
                "policy_sha256": policy_sha256,
                "outcome": "denied",
                "reason": "self-service-subject-mismatch",
                "contains_personal_values": False,
            },
            sort_keys=True,
        )
        async with (
            self._repository.acquire() as connection,
            connection.transaction(),
        ):
            await self._repository.append_audit(
                connection,
                action_type="privacy.export.denied",
                details=details,
            )

    async def export_self(
        self,
        *,
        actor_user_id: int,
        subject_user_id: int,
    ) -> PrivacyExportResult:
        correlation_id = uuid4()
        actor_digest = identity_digest("privacy-export-actor", str(actor_user_id))
        subject_digest = identity_digest("privacy-export-subject", str(subject_user_id))
        policy_sha256 = self._policy_sha256()

        if int(actor_user_id) != int(subject_user_id):
            await self._audit_denied_request(
                correlation_id=correlation_id,
                actor_digest=actor_digest,
                subject_digest=subject_digest,
                policy_sha256=policy_sha256,
            )
            raise PrivacyExportAuthorizationError(
                "self-service export is restricted to the authenticated Telegram user"
            )

        async with (
            self._repository.acquire() as connection,
            connection.transaction(),
        ):
            datasets = await self._repository.collect(connection, subject_user_id)
            _assert_no_forbidden_keys(datasets)
            dataset_counts = {
                dataset_id: sum(len(rows) for rows in tables.values())
                for dataset_id, tables in datasets.items()
            }
            exported_rows = sum(dataset_counts.values())
            generated_at = self._clock.now()
            document = {
                "schema_version": 1,
                "export_type": "authenticated-self-service",
                "generated_at": generated_at.isoformat(),
                "correlation_id": str(correlation_id),
                "policy_sha256": policy_sha256,
                "subject": {"telegram_user_id": int(subject_user_id)},
                "datasets": datasets,
                "safety": {
                    "read_only_subject_data": True,
                    "source_data_mutated": False,
                    "excluded_secret_material": True,
                    "excluded_uid_hash_and_ciphertext": True,
                    "excluded_telegram_media_identifiers": True,
                    "audit_contains_personal_values": False,
                },
            }
            payload = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=_json_default,
            ).encode("utf-8")
            audit_details = json.dumps(
                {
                    "schema_version": 1,
                    "correlation_id": str(correlation_id),
                    "actor_digest": actor_digest,
                    "subject_digest": subject_digest,
                    "policy_sha256": policy_sha256,
                    "outcome": "generated",
                    "dataset_counts": dataset_counts,
                    "exported_rows": exported_rows,
                    "contains_personal_values": False,
                },
                sort_keys=True,
            )
            await self._repository.append_audit(
                connection,
                action_type="privacy.export.generated",
                details=audit_details,
            )

        return PrivacyExportResult(
            correlation_id=correlation_id,
            filename=f"personal-data-export-{generated_at:%Y%m%dT%H%M%SZ}.json",
            payload=payload,
            dataset_counts=dataset_counts,
            exported_rows=exported_rows,
        )


__all__ = (
    "PrivacyExportAuthorizationError",
    "PrivacyExportResult",
    "PrivacyExportService",
)
