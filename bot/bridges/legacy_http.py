"""Authenticated HTTP compatibility bridge for deleted-bid notifications.

The Flask endpoint is intentionally isolated from the Telegram application's
database pool.  Flask serves synchronous requests in its own worker thread;
running a pool created by the bot's event loop from that thread would cross
event-loop ownership and is unsafe.  A bridge-specific gateway therefore owns
one short-lived connection on the temporary loop created by :func:`asyncio.run`.

Importing this module only constructs a WSGI application.  It never starts a
server; deployment code must call :func:`run_flask` explicitly (or serve
``app`` with a WSGI server).  The endpoint fails closed when its secret is not
configured, authenticates the exact raw body, limits request size and accepts
only the configured discussion chat.  Every request also carries a fresh Unix
timestamp and a one-time request identifier; both are covered by the HMAC and
claimed in a bounded, thread-safe replay cache.
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from flask import Flask, request

from bot.core.settings import (
    BOT_TOKEN,
    DATABASE_URL,
    DISCUSSION_CHAT_ID,
    LEGACY_BRIDGE_MAX_SKEW_SECONDS,
    LEGACY_BRIDGE_NONCE_CACHE_SIZE,
    LEGACY_BRIDGE_SECRET,
)
from bot.presentation.warnings import WARN_TEXTS
from bot.repositories.legacy_bridge import LegacyBridgeWarningGateway
from bot.security import (
    NonceReplayCache,
    bridge_timestamp_is_fresh,
    verify_bridge_signature,
)
from bot.security.bridge import normalize_bridge_request_id, normalize_bridge_timestamp

logger = logging.getLogger("auction.legacy_http")

MAX_REQUEST_BYTES = 16 * 1024
SIGNATURE_HEADER = "X-Auction-Signature"
TIMESTAMP_HEADER = "X-Auction-Timestamp"
REQUEST_ID_HEADER = "X-Auction-Request-Id"


@dataclass(frozen=True, slots=True)
class LegacyBridgeConfig:
    secret: str
    allowed_chat_id: int | None
    bot_token: str
    database_url: str
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_timestamp_skew_seconds: int = 300
    nonce_cache_size: int = 4096

    @classmethod
    def from_settings(cls) -> "LegacyBridgeConfig":
        return cls(
            secret=LEGACY_BRIDGE_SECRET,
            allowed_chat_id=DISCUSSION_CHAT_ID,
            bot_token=BOT_TOKEN,
            database_url=DATABASE_URL,
            max_timestamp_skew_seconds=LEGACY_BRIDGE_MAX_SKEW_SECONDS,
            nonce_cache_size=LEGACY_BRIDGE_NONCE_CACHE_SIZE,
        )


async def _warning_count(config: LegacyBridgeConfig, user_id: int | None) -> int:
    if user_id is None or not config.database_url:
        return 1

    # Do not replace this gateway with db.pool.get_db_pool(): that pool is owned
    # by the Telegram event loop, while this coroutine runs on Flask's
    # short-lived per-request loop (see the module docstring).
    gateway = LegacyBridgeWarningGateway(config.database_url)
    value = await gateway.warning_count(int(user_id))
    return value if value is not None else 1


async def deliver_bid_deleted(
    data: Mapping[str, Any],
    config: LegacyBridgeConfig,
) -> None:
    """Deliver the two historical Telegram messages for a validated request."""

    user_id = int(data["user_id"]) if data.get("user_id") is not None else None
    warnings = await _warning_count(config, user_id)

    username = html.escape(str(data.get("username") or "пользователь"))
    amount = html.escape(str(data.get("amount") or "—"))
    chat_id = int(data["chat_id"])
    reply_to_message_id = int(data["reply_to_message_id"])

    legacy_bot = Bot(token=config.bot_token)
    try:
        deleted = await legacy_bot.send_message(
            chat_id=chat_id,
            text=(f"❗️ <b>Ставка удалена</b>\n@{username}, ваша ставка удалена. (сумма: {amount})"),
            reply_to_message_id=reply_to_message_id,
            parse_mode="HTML",
        )
        await legacy_bot.send_message(
            chat_id=chat_id,
            text=random.choice(WARN_TEXTS).format(
                username=username,
                warnings=warnings,
            ),
            reply_to_message_id=deleted.message_id,
            parse_mode="HTML",
        )
    finally:
        await legacy_bot.session.close()


Delivery = Callable[[Mapping[str, Any], LegacyBridgeConfig], Awaitable[None]]


def create_legacy_http_app(
    config: LegacyBridgeConfig | None = None,
    *,
    delivery: Delivery = deliver_bid_deleted,
    replay_cache: NonceReplayCache | None = None,
    clock: Callable[[], float] = time.time,
) -> Flask:
    """Build the WSGI app without opening a socket or starting a thread."""

    effective_config = config or LegacyBridgeConfig.from_settings()
    application = Flask(__name__)
    application.config["MAX_CONTENT_LENGTH"] = max(
        1,
        int(effective_config.max_request_bytes),
    )
    effective_replay_cache = replay_cache or NonceReplayCache(
        effective_config.nonce_cache_size,
    )

    @application.post("/notify_bid_deleted")
    def notify_bid_deleted() -> tuple[str, int] | str:
        raw_body = request.get_data(cache=True)
        signature = request.headers.get(SIGNATURE_HEADER)
        timestamp = request.headers.get(TIMESTAMP_HEADER)
        request_id = request.headers.get(REQUEST_ID_HEADER)
        if not effective_config.secret.strip():
            logger.error("Legacy bridge request rejected: LEGACY_BRIDGE_SECRET is not configured")
            return "bridge disabled", 503

        current_time = clock()
        if not bridge_timestamp_is_fresh(
            timestamp,
            max_skew_seconds=effective_config.max_timestamp_skew_seconds,
            now=current_time,
        ):
            logger.warning("Legacy bridge request rejected: invalid timestamp")
            return "unauthorized", 401
        if not verify_bridge_signature(
            raw_body,
            signature,
            effective_config.secret,
            timestamp=timestamp,
            request_id=request_id,
        ):
            logger.warning("Legacy bridge request rejected: invalid authentication")
            return "unauthorized", 401

        normalized_timestamp = normalize_bridge_timestamp(timestamp)
        normalized_request_id = normalize_bridge_request_id(request_id)
        # Signature verification above can only succeed for normalized metadata.
        if normalized_timestamp is None or normalized_request_id is None:
            logger.warning("Legacy bridge request rejected: invalid authentication metadata")
            return "unauthorized", 401
        _, timestamp_value = normalized_timestamp
        if not effective_replay_cache.claim(
            normalized_request_id,
            expires_at=timestamp_value + effective_config.max_timestamp_skew_seconds,
            now=current_time,
        ):
            logger.warning("Legacy bridge request rejected: replay protection triggered")
            return "unauthorized", 401

        data = request.get_json(silent=True) or {}
        required = {"chat_id", "reply_to_message_id"}
        if not isinstance(data, dict) or not required <= data.keys():
            return "missing required fields", 400

        try:
            chat_id = int(data["chat_id"])
            reply_to_message_id = int(data["reply_to_message_id"])
            user_id = int(data["user_id"]) if data.get("user_id") is not None else None
        except (TypeError, ValueError):
            return "invalid identifiers", 400

        if not effective_config.allowed_chat_id or chat_id != effective_config.allowed_chat_id:
            return "chat is not allowed", 403
        if reply_to_message_id <= 0 or (user_id is not None and user_id <= 0):
            return "invalid identifiers", 400

        try:
            asyncio.run(delivery(data, effective_config))
        except Exception as exc:  # noqa: BLE001 - translates infrastructure failure to HTTP
            # Avoid formatting the exception itself: third-party exceptions may
            # embed request fields.  Neither request bodies nor secrets are logged.
            logger.error(
                "Legacy bridge failed to deliver bid-deletion notification (%s)",
                type(exc).__name__,
            )
            return "delivery failed", 502
        return "ok"

    return application


app = create_legacy_http_app()
notify_bid_deleted = app.view_functions["notify_bid_deleted"]


def run_flask() -> None:
    """Explicit development entrypoint; importing this module never calls it."""

    app.run("127.0.0.1", 8002)


__all__ = [
    "LegacyBridgeConfig",
    "REQUEST_ID_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "app",
    "create_legacy_http_app",
    "deliver_bid_deleted",
    "notify_bid_deleted",
    "run_flask",
]
