"""Authentication and secret-handling helpers."""

from .access import admin_secret_matches, is_owner_or_valid_secret
from .admin_access import configured_admin_ids, is_admin_user
from .bridge import (
    NonceReplayCache,
    bridge_timestamp_is_fresh,
    build_bridge_signature_payload,
    verify_bridge_signature,
)

__all__ = [
    "admin_secret_matches",
    "bridge_timestamp_is_fresh",
    "build_bridge_signature_payload",
    "configured_admin_ids",
    "is_admin_user",
    "is_owner_or_valid_secret",
    "NonceReplayCache",
    "verify_bridge_signature",
]
