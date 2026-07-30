from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.repositories.uid_verification import UIDApprovalResult, UIDVerificationRepository
from bot.repositories.uid_identity_admin import UIDIdentityAdminRepository
from db.core import get_db_pool

# Legacy UID workflows are still exercised by the administrative routers.  The
# repository service above owns new operations; these explicit re-exports keep
# the existing workflow API stable until its final migration.
from db.uid import (  # noqa: E402
    approve_uid_verification_request,
    ban_user,
    get_uid_profile_binding,
    get_uid_verification_request,
    get_user_verified_uid,
    get_whois_admin_payload,
    list_uid_bans,
    list_uid_verification_requests,
    reject_uid_verification_request,
    remove_uid_ban,
    set_uid_verification_request_revision,
    unban_user,
    upsert_uid_ban,
)


async def list_active_user_bans(*, limit: int = 50) -> list[dict[str, Any]]:
    return await UIDIdentityAdminRepository(await get_db_pool()).list_active_user_bans(limit=limit)


async def apply_master_ban(**kwargs: Any) -> Any:
    return await UIDIdentityAdminRepository(await get_db_pool()).apply_master_ban(**kwargs)


async def apply_master_unban(**kwargs: Any) -> Any:
    return await UIDIdentityAdminRepository(await get_db_pool()).apply_master_unban(**kwargs)


async def get_user_basic_info_by_username(username: str) -> dict[str, Any] | None:
    """Compatibility facade while UID reads move behind a repository."""

    from db.uid import get_user_basic_info_by_username as legacy_lookup

    return await legacy_lookup(username)


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    from db.users import get_user_by_username as legacy_lookup

    return await legacy_lookup(username)


async def get_user_id_by_username(username: str) -> int | None:
    from db.users import get_user_id_by_username as legacy_lookup

    return await legacy_lookup(username)


async def get_username_by_user_id(user_id: int) -> str | None:
    from db.users import get_username_by_user_id as legacy_lookup

    return await legacy_lookup(user_id)


async def get_user_id_by_uid_any(uid: str) -> int | None:
    from db.uid import get_user_id_by_uid_any as legacy_lookup

    return await legacy_lookup(uid)


@dataclass(slots=True)
class UIDVerificationService:
    repository: UIDVerificationRepository

    @classmethod
    async def create(cls) -> "UIDVerificationService":
        return cls(UIDVerificationRepository(await get_db_pool()))

    async def get_verified_uid(self, user_id: int) -> str | None:
        return await self.repository.get_verified_uid_for_user(user_id)

    async def get_latest_request(self, user_id: int) -> dict[str, Any] | None:
        return await self.repository.get_latest_request(user_id)

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        return await self.repository.get_request(request_id)

    async def create_request(self, **kwargs: Any) -> int:
        return await self.repository.create_request(**kwargs)

    async def approve_request(self, **kwargs: Any) -> UIDApprovalResult:
        return await self.repository.approve_request(**kwargs)

    async def reject_request(self, **kwargs: Any) -> UIDApprovalResult:
        return await self.repository.reject_request(**kwargs)

    async def claim_due_reminders(self, *, stage_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        return await self.repository.claim_due_reminders(
            stage_h=stage_h,
            minimum_confirmations=minimum_confirmations,
        )

    async def expire_due_requests(self, *, ttl_h: int, minimum_confirmations: int) -> list[dict[str, Any]]:
        return await self.repository.expire_due_requests(
            ttl_h=ttl_h,
            minimum_confirmations=minimum_confirmations,
        )
