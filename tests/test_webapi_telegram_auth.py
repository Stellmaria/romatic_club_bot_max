from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from webapi.telegram_auth import TelegramAuthError, validate_init_data

BOT_TOKEN = "123456:TEST_TOKEN"
NOW = 1_800_000_000


def _signed_init_data(*, auth_date: int = NOW, username: str = "stellmaria") -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": json.dumps(
            {
                "id": 123456789,
                "first_name": "Stel",
                "last_name": "Test",
                "username": username,
                "language_code": "ru",
                "is_premium": True,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


def test_validate_init_data_accepts_valid_payload() -> None:
    result = validate_init_data(_signed_init_data(), BOT_TOKEN, now=NOW)

    assert result.user.id == 123456789
    assert result.user.username == "stellmaria"
    assert result.user.full_name == "Stel Test"
    assert result.user.is_premium is True
    assert result.query_id == "AAEAAAE"


def test_validate_init_data_rejects_tampering() -> None:
    payload = _signed_init_data().replace("stellmaria", "attacker")

    with pytest.raises(TelegramAuthError, match="signature mismatch"):
        validate_init_data(payload, BOT_TOKEN, now=NOW)


def test_validate_init_data_rejects_stale_payload() -> None:
    payload = _signed_init_data(auth_date=NOW - 3601)

    with pytest.raises(TelegramAuthError, match="stale"):
        validate_init_data(payload, BOT_TOKEN, now=NOW, max_age_seconds=3600)


def test_validate_init_data_rejects_future_payload() -> None:
    payload = _signed_init_data(auth_date=NOW + 31)

    with pytest.raises(TelegramAuthError, match="future"):
        validate_init_data(payload, BOT_TOKEN, now=NOW, future_skew_seconds=30)


def test_validate_init_data_rejects_duplicate_fields() -> None:
    payload = _signed_init_data() + "&auth_date=1"

    with pytest.raises(TelegramAuthError, match="duplicate"):
        validate_init_data(payload, BOT_TOKEN, now=NOW)


def test_validate_init_data_rejects_malformed_query_string() -> None:
    with pytest.raises(TelegramAuthError, match="malformed"):
        validate_init_data("not-a-pair", BOT_TOKEN, now=NOW)
