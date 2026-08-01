"""Owner authorization compatibility helpers.

Telegram messages are not a safe transport for reusable passwords. The legacy
secret-based functions remain temporarily import-compatible, but shared secrets
can no longer authorize any Telegram action.
"""

from __future__ import annotations

from collections.abc import Collection

from bot.core.settings import ADMINS_OWNERS


def admin_secret_matches(
    candidate: str | None,
    *,
    configured_secret: str | None = None,
) -> bool:
    """Return ``False`` for the retired Telegram shared-secret mechanism."""

    del candidate, configured_secret
    return False


def is_owner_or_valid_secret(
    user_id: int | None,
    candidate: str | None,
    *,
    owner_ids: Collection[int] | None = None,
    configured_secret: str | None = None,
) -> bool:
    """Authorize only configured owners; secret arguments are ignored.

    The historical name is preserved while legacy handlers are migrated to the
    explicit owner policy. Passing a former password never grants access.
    """

    del candidate, configured_secret
    owners = ADMINS_OWNERS if owner_ids is None else owner_ids
    return user_id is not None and int(user_id) in {int(value) for value in owners}
