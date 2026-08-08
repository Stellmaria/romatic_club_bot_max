"""Validation of Telegram Mini App initData."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class TelegramAuthError(ValueError):
    """Raised when Telegram Mini App authorization data is invalid."""


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False
    photo_url: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()


@dataclass(frozen=True, slots=True)
class ValidatedInitData:
    user: TelegramUser
    auth_date: int
    query_id: str = ""
    start_param: str = ""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 3600,
    now: int | None = None,
    future_skew_seconds: int = 30,
) -> ValidatedInitData:
    """Validate Telegram Mini App initData using Telegram's HMAC contract."""

    if not init_data or not bot_token:
        raise TelegramAuthError("missing Telegram authorization data")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise TelegramAuthError("malformed Telegram authorization data") from error

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise TelegramAuthError("duplicate field in Telegram authorization data")
        fields[key] = value

    received_hash = fields.pop("hash", "")
    if len(received_hash) != 64:
        raise TelegramAuthError("invalid Telegram authorization hash")
    try:
        bytes.fromhex(received_hash)
    except ValueError as error:
        raise TelegramAuthError("invalid Telegram authorization hash") from error

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        raise TelegramAuthError("Telegram authorization signature mismatch")

    auth_date = _parse_auth_date(fields.get("auth_date"))
    current_time = int(time.time()) if now is None else int(now)
    if auth_date > current_time + future_skew_seconds:
        raise TelegramAuthError("Telegram authorization timestamp is in the future")
    if current_time - auth_date > max_age_seconds:
        raise TelegramAuthError("Telegram authorization data is stale")

    user = _parse_user(fields.get("user"))
    return ValidatedInitData(
        user=user,
        auth_date=auth_date,
        query_id=fields.get("query_id", ""),
        start_param=fields.get("start_param", ""),
    )


def _parse_auth_date(value: str | None) -> int:
    if value is None:
        raise TelegramAuthError("Telegram authorization timestamp is missing")
    try:
        auth_date = int(value)
    except ValueError as error:
        raise TelegramAuthError("Telegram authorization timestamp is invalid") from error
    if auth_date <= 0:
        raise TelegramAuthError("Telegram authorization timestamp is invalid")
    return auth_date


def _parse_user(value: str | None) -> TelegramUser:
    if not value:
        raise TelegramAuthError("Telegram user is missing")
    try:
        payload: Any = json.loads(value)
    except json.JSONDecodeError as error:
        raise TelegramAuthError("Telegram user payload is invalid") from error
    if not isinstance(payload, dict):
        raise TelegramAuthError("Telegram user payload is invalid")

    try:
        user_id = int(payload["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise TelegramAuthError("Telegram user id is invalid") from error
    if user_id <= 0:
        raise TelegramAuthError("Telegram user id is invalid")

    first_name = str(payload.get("first_name", "")).strip()
    if not first_name:
        raise TelegramAuthError("Telegram user first_name is missing")

    return TelegramUser(
        id=user_id,
        first_name=first_name,
        last_name=str(payload.get("last_name", "")).strip(),
        username=str(payload.get("username", "")).strip().lstrip("@"),
        language_code=str(payload.get("language_code", "")).strip(),
        is_premium=bool(payload.get("is_premium", False)),
        photo_url=str(payload.get("photo_url", "")).strip(),
    )


__all__ = [
    "TelegramAuthError",
    "TelegramUser",
    "ValidatedInitData",
    "validate_init_data",
]
