from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable

from cryptography.fernet import Fernet, MultiFernet


class UIDCryptoNotConfigured(RuntimeError):
    """Raised when identity helpers are used before process composition."""


_uid_hash_key: bytes | None = None
_fernet: MultiFernet | None = None


def configure_uid_crypto(
    hash_key: str,
    encryption_key: str,
    previous_encryption_keys: Iterable[str] = (),
) -> None:
    """Install identity keys, encrypting with current and decrypting with all.

    The first Fernet key is always the active write key. Previous keys are
    decryption-only compatibility keys used during a bounded rotation window.
    """

    global _uid_hash_key, _fernet
    if not hash_key:
        raise ValueError("UID_HASH_KEY is required")
    if not encryption_key:
        raise ValueError("UID_ENC_KEY is required")
    keys = [encryption_key, *(key for key in previous_encryption_keys if key)]
    if len(keys) != len(set(keys)):
        raise ValueError("UID encryption keyring contains duplicates")
    _uid_hash_key = hash_key.encode("utf-8")
    _fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in keys])


def reset_uid_crypto_for_testing() -> None:
    global _uid_hash_key, _fernet
    _uid_hash_key = None
    _fernet = None


def _require_hash_key() -> bytes:
    if _uid_hash_key is None:
        raise UIDCryptoNotConfigured("UID crypto is not configured")
    return _uid_hash_key


def _require_fernet() -> MultiFernet:
    if _fernet is None:
        raise UIDCryptoNotConfigured("UID crypto is not configured")
    return _fernet


def identity_digest(domain: str, value: str) -> str:
    """Return a domain-separated keyed digest without exposing identity values."""

    normalized_domain = domain.strip().lower()
    if not normalized_domain:
        raise ValueError("identity digest domain must be non-empty")
    payload = f"{normalized_domain}\x00{value.strip()}".encode()
    return hmac.new(_require_hash_key(), payload, hashlib.sha256).hexdigest()


def norm_uid(uid: str | None) -> str:
    return (uid or "").strip().lower().replace(" ", "")


def uid_hash(uid: str) -> str:
    value = norm_uid(uid).encode("utf-8")
    return hmac.new(_require_hash_key(), value, hashlib.sha256).hexdigest()


def uid_encrypt(uid: str) -> str:
    value = norm_uid(uid).encode("utf-8")
    return _require_fernet().encrypt(value).decode("utf-8")


def uid_decrypt(token: str) -> str:
    return _require_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def uid_rotate_encryption(token: str) -> str:
    """Re-encrypt a token with the active key while preserving its timestamp."""

    return _require_fernet().rotate(token.encode("utf-8")).decode("utf-8")


def uid_last4(uid: str) -> str:
    value = norm_uid(uid)
    return value[-4:] if len(value) >= 4 else value


def mask_uid(uid: str) -> str:
    value = norm_uid(uid)
    if len(value) <= 8:
        return value
    return f"{value[:4]}…{value[-4:]}"


def mask_uid_by_last4(last4: str | None) -> str:
    value = (last4 or "").strip()
    return f"••••••••••••••••••••{value}" if value else "—"


__all__ = (
    "UIDCryptoNotConfigured",
    "configure_uid_crypto",
    "identity_digest",
    "mask_uid",
    "mask_uid_by_last4",
    "norm_uid",
    "reset_uid_crypto_for_testing",
    "uid_decrypt",
    "uid_encrypt",
    "uid_hash",
    "uid_last4",
    "uid_rotate_encryption",
)
