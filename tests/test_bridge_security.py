from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor

from bot.security.bridge import (
    NonceReplayCache,
    bridge_timestamp_is_fresh,
    build_bridge_signature_payload,
    verify_bridge_signature,
)


def _signature(
    body: bytes,
    *,
    secret: str = "secret",
    timestamp: str = "1700000000",
    request_id: str = "request-1",
) -> str:
    payload = build_bridge_signature_payload(body, timestamp, request_id)
    assert payload is not None
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_bridge_signature_requires_secret_and_canonical_metadata() -> None:
    body = b"{}"
    signature = _signature(body)

    assert not verify_bridge_signature(
        body,
        signature,
        "",
        timestamp="1700000000",
        request_id="request-1",
    )
    assert not verify_bridge_signature(body, signature, "secret")
    assert not verify_bridge_signature(
        body,
        signature,
        "secret",
        timestamp="01700000000",
        request_id="request-1",
    )
    assert not verify_bridge_signature(
        body,
        signature,
        "secret",
        timestamp="1700000000",
        request_id="",
    )


def test_bridge_signature_authenticates_metadata_and_exact_request_body() -> None:
    body = b'{"chat_id":-100123}'
    signature = _signature(body)

    assert verify_bridge_signature(
        body,
        signature,
        "secret",
        timestamp="1700000000",
        request_id="request-1",
    )
    assert not verify_bridge_signature(
        body + b" ",
        signature,
        "secret",
        timestamp="1700000000",
        request_id="request-1",
    )
    assert not verify_bridge_signature(
        body,
        signature,
        "secret",
        timestamp="1700000001",
        request_id="request-1",
    )
    assert not verify_bridge_signature(
        body,
        signature,
        "secret",
        timestamp="1700000000",
        request_id="request-2",
    )


def test_bridge_timestamp_rejects_stale_and_future_values() -> None:
    assert bridge_timestamp_is_fresh("1000", max_skew_seconds=300, now=1300)
    assert bridge_timestamp_is_fresh("1000", max_skew_seconds=300, now=700)
    assert not bridge_timestamp_is_fresh("1000", max_skew_seconds=300, now=1301)
    assert not bridge_timestamp_is_fresh("1000", max_skew_seconds=300, now=699)
    assert not bridge_timestamp_is_fresh("not-a-time", max_skew_seconds=300, now=1000)
    assert not bridge_timestamp_is_fresh("1000", max_skew_seconds=0, now=1000)


def test_nonce_cache_claim_is_atomic_bounded_and_expires() -> None:
    cache = NonceReplayCache(max_entries=2)

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(
            executor.map(
                lambda _: cache.claim("same-nonce", expires_at=1300, now=1000),
                range(64),
            )
        )
    assert sum(results) == 1

    assert cache.claim("second-nonce", expires_at=1300, now=1000)
    # Capacity fails closed and does not evict either still-valid nonce.
    assert not cache.claim("third-nonce", expires_at=1300, now=1000)
    assert not cache.claim("same-nonce", expires_at=1300, now=1000)

    assert cache.claim("third-nonce", expires_at=1601, now=1301)
