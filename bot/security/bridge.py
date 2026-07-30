"""Authentication primitives for the optional legacy HTTP bridge.

The protocol deliberately signs metadata as well as the exact request body.
This prevents a captured request from being replayed with a fresh timestamp or
under a different request identifier.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

BRIDGE_SIGNATURE_VERSION = b"auction-legacy-v1"
MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._~:-]+\Z", re.ASCII)

Clock = Callable[[], float]


def normalize_bridge_timestamp(timestamp: str | None) -> tuple[str, int] | None:
    """Return a canonical Unix timestamp or ``None`` for malformed input."""

    supplied = timestamp or ""
    if not supplied or not supplied.isascii() or not supplied.isdecimal():
        return None
    try:
        parsed = int(supplied)
    except ValueError:
        return None
    if parsed <= 0 or str(parsed) != supplied:
        return None
    return supplied, parsed


def normalize_bridge_request_id(request_id: str | None) -> str | None:
    """Validate the bounded, newline-free request identifier."""

    supplied = request_id or ""
    if not 1 <= len(supplied) <= MAX_REQUEST_ID_LENGTH:
        return None
    if not supplied.isascii() or _REQUEST_ID_RE.fullmatch(supplied) is None:
        return None
    return supplied


def build_bridge_signature_payload(
    body: bytes,
    timestamp: str | None,
    request_id: str | None,
) -> bytes | None:
    """Build the canonical bytes authenticated by the bridge HMAC.

    ``None`` is returned for malformed metadata so callers cannot accidentally
    sign a request that the server would interpret differently.
    """

    normalized_timestamp = normalize_bridge_timestamp(timestamp)
    normalized_request_id = normalize_bridge_request_id(request_id)
    if normalized_timestamp is None or normalized_request_id is None:
        return None
    timestamp_text, _ = normalized_timestamp
    return b"\n".join(
        (
            BRIDGE_SIGNATURE_VERSION,
            timestamp_text.encode("ascii"),
            normalized_request_id.encode("ascii"),
            body,
        )
    )


def verify_bridge_signature(
    body: bytes,
    signature: str | None,
    secret: str | None,
    *,
    timestamp: str | None = None,
    request_id: str | None = None,
) -> bool:
    """Verify a versioned HMAC over timestamp, request-id and exact body."""

    key = (secret or "").strip().encode("utf-8")
    supplied_signature = (signature or "").strip().lower()
    payload = build_bridge_signature_payload(body, timestamp, request_id)
    if (
        not key
        or payload is None
        or len(supplied_signature) != hashlib.sha256().digest_size * 2
    ):
        return False
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied_signature, expected)


def bridge_timestamp_is_fresh(
    timestamp: str | None,
    *,
    max_skew_seconds: int,
    now: float | None = None,
) -> bool:
    """Accept timestamps no further than ``max_skew_seconds`` in either direction."""

    normalized = normalize_bridge_timestamp(timestamp)
    if normalized is None or max_skew_seconds <= 0:
        return False
    _, parsed = normalized
    current_time = time.time() if now is None else now
    return abs(current_time - parsed) <= max_skew_seconds


class NonceReplayCache:
    """Thread-safe, bounded replay cache that fails closed at capacity.

    Unexpired entries are never evicted merely to admit a new request: doing so
    would make a still-fresh captured request replayable.  Expired entries are
    removed while holding the same lock used for the atomic claim operation.
    """

    def __init__(self, max_entries: int) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def claim(self, request_id: str, *, expires_at: float, now: float | None = None) -> bool:
        """Atomically record a nonce once, returning ``False`` on replay/capacity."""

        current_time = time.time() if now is None else now
        with self._lock:
            expired = [
                nonce
                for nonce, expiry in self._entries.items()
                if expiry < current_time
            ]
            for nonce in expired:
                del self._entries[nonce]

            existing_expiry = self._entries.get(request_id)
            if existing_expiry is not None and existing_expiry >= current_time:
                return False
            if existing_expiry is not None:
                del self._entries[request_id]
            if len(self._entries) >= self._max_entries:
                return False

            self._entries[request_id] = expires_at
            return True


__all__ = [
    "BRIDGE_SIGNATURE_VERSION",
    "MAX_REQUEST_ID_LENGTH",
    "NonceReplayCache",
    "bridge_timestamp_is_fresh",
    "build_bridge_signature_payload",
    "normalize_bridge_request_id",
    "normalize_bridge_timestamp",
    "verify_bridge_signature",
]
