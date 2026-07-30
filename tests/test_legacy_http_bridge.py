from __future__ import annotations

import ast
import hashlib
import hmac
import json
from pathlib import Path

from bot.bridges.legacy_http import LegacyBridgeConfig, create_legacy_http_app
from bot.security.bridge import build_bridge_signature_payload

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_700_000_000


def _config(
    *,
    secret: str = "bridge-secret",
    chat_id: int = -100123,
    limit: int = 1024,
    skew: int = 300,
    cache_size: int = 32,
) -> LegacyBridgeConfig:
    return LegacyBridgeConfig(
        secret=secret,
        allowed_chat_id=chat_id,
        bot_token="test-token",
        database_url="",
        max_request_bytes=limit,
        max_timestamp_skew_seconds=skew,
        nonce_cache_size=cache_size,
    )


def _app(config: LegacyBridgeConfig, *, delivery):
    return create_legacy_http_app(config, delivery=delivery, clock=lambda: NOW)


def _signed_post(
    client,
    body: bytes,
    *,
    secret: str = "bridge-secret",
    timestamp: int = NOW,
    request_id: str = "request-1",
):
    timestamp_text = str(timestamp)
    payload = build_bridge_signature_payload(body, timestamp_text, request_id)
    assert payload is not None
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return client.post(
        "/notify_bid_deleted",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Auction-Signature": signature,
            "X-Auction-Timestamp": timestamp_text,
            "X-Auction-Request-Id": request_id,
        },
    )


def test_bridge_fails_closed_without_secret_or_authentication_metadata() -> None:
    disabled = _app(_config(secret=""), delivery=lambda *_: None)
    assert disabled.test_client().post("/notify_bid_deleted", data=b"{}").status_code == 503
    whitespace = _app(_config(secret="   "), delivery=lambda *_: None)
    assert whitespace.test_client().post("/notify_bid_deleted", data=b"{}").status_code == 503

    enabled = _app(_config(), delivery=lambda *_: None)
    response = enabled.test_client().post(
        "/notify_bid_deleted",
        data=b"{}",
        headers={"X-Auction-Signature": "0" * 64},
    )
    assert response.status_code == 401


def test_bridge_rejects_stale_and_future_signed_requests() -> None:
    app = _app(_config(skew=300), delivery=lambda *_: None)
    client = app.test_client()
    body = b'{"chat_id":-100123,"reply_to_message_id":7}'

    assert _signed_post(
        client,
        body,
        timestamp=NOW - 301,
        request_id="stale-request",
    ).status_code == 401
    assert _signed_post(
        client,
        body,
        timestamp=NOW + 301,
        request_id="future-request",
    ).status_code == 401


def test_bridge_rejects_replay_but_accepts_first_valid_request() -> None:
    delivered: list[dict[str, object]] = []

    async def delivery(data, _config):
        delivered.append(data)

    client = _app(_config(), delivery=delivery).test_client()
    body = b'{"chat_id":-100123,"reply_to_message_id":7}'

    assert _signed_post(client, body, request_id="one-shot").status_code == 200
    assert _signed_post(client, body, request_id="one-shot").status_code == 401
    assert delivered == [{"chat_id": -100123, "reply_to_message_id": 7}]


def test_tampered_request_does_not_consume_nonce() -> None:
    delivered: list[dict[str, object]] = []

    async def delivery(data, _config):
        delivered.append(data)

    client = _app(_config(), delivery=delivery).test_client()
    valid_body = b'{"chat_id":-100123,"reply_to_message_id":7}'
    tampered_body = b'{"chat_id":-100123,"reply_to_message_id":8}'
    timestamp_text = str(NOW)
    payload = build_bridge_signature_payload(valid_body, timestamp_text, "tamper-test")
    assert payload is not None
    signature = hmac.new(b"bridge-secret", payload, hashlib.sha256).hexdigest()

    tampered = client.post(
        "/notify_bid_deleted",
        data=tampered_body,
        headers={
            "Content-Type": "application/json",
            "X-Auction-Signature": signature,
            "X-Auction-Timestamp": timestamp_text,
            "X-Auction-Request-Id": "tamper-test",
        },
    )
    assert tampered.status_code == 401
    assert _signed_post(client, valid_body, request_id="tamper-test").status_code == 200
    assert len(delivered) == 1


def test_bridge_allows_only_validated_chat_and_identifiers() -> None:
    delivered: list[dict[str, object]] = []

    async def delivery(data, _config):
        delivered.append(data)

    client = _app(_config(), delivery=delivery).test_client()

    forbidden = json.dumps(
        {"chat_id": -999, "reply_to_message_id": 1},
        separators=(",", ":"),
    ).encode()
    assert _signed_post(client, forbidden, request_id="forbidden").status_code == 403

    invalid = json.dumps(
        {"chat_id": -100123, "reply_to_message_id": 0},
        separators=(",", ":"),
    ).encode()
    assert _signed_post(client, invalid, request_id="invalid-id").status_code == 400

    valid = json.dumps(
        {"chat_id": -100123, "reply_to_message_id": 7, "user_id": 4},
        separators=(",", ":"),
    ).encode()
    assert _signed_post(client, valid, request_id="valid").status_code == 200
    assert delivered == [{"chat_id": -100123, "reply_to_message_id": 7, "user_id": 4}]

    unconfigured = _app(_config(chat_id=0), delivery=delivery)
    no_chat = json.dumps(
        {"chat_id": 0, "reply_to_message_id": 7},
        separators=(",", ":"),
    ).encode()
    assert _signed_post(
        unconfigured.test_client(),
        no_chat,
        request_id="no-chat",
    ).status_code == 403


def test_bridge_enforces_body_limit_before_delivery() -> None:
    calls = 0

    async def delivery(_data, _config):
        nonlocal calls
        calls += 1

    app = _app(_config(limit=64), delivery=delivery)
    body = json.dumps(
        {
            "chat_id": -100123,
            "reply_to_message_id": 7,
            "padding": "x" * 256,
        }
    ).encode()
    assert _signed_post(app.test_client(), body, request_id="large").status_code == 413
    assert calls == 0


def test_bridge_delivery_failure_is_visible_and_server_is_not_started_on_import() -> None:
    async def failing_delivery(_data, _config):
        raise RuntimeError("delivery failed")

    app = _app(_config(), delivery=failing_delivery)
    body = b'{"chat_id":-100123,"reply_to_message_id":7}'
    assert _signed_post(app.test_client(), body, request_id="delivery-fails").status_code == 502

    tree = ast.parse((ROOT / "bot/bridges/legacy_http.py").read_text(encoding="utf-8"))
    top_level_run_calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "run"
    ]
    assert top_level_run_calls == []


def test_bridge_logs_neither_body_nor_secret(caplog) -> None:
    body_marker = "private-body-marker"
    secret = "private-secret-marker"
    app = _app(_config(secret=secret), delivery=lambda *_: None)

    response = _signed_post(
        app.test_client(),
        body_marker.encode(),
        secret="wrong-secret",
        request_id="log-safety",
    )
    assert response.status_code == 401
    assert body_marker not in caplog.text
    assert secret not in caplog.text
