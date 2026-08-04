from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:"
    r"token|secret|password|phone|uid|session|username|full_name|first_name|last_name|"
    r"user_id|telegram_id|tg_user_id|chat_id|actor_id|admin_id|moderator_id|owner_id|"
    r"winner_id|bidder_id|file_id|file_unique_id"
    r")(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "created_by",
        "updated_by",
        "reviewed_by",
        "banned_by",
        "verified_by",
        "decided_by",
        "media_message_ids",
        "origin_chat_id",
        "message_chat_id",
    }
)
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_DSN_PASSWORD = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@",
    re.IGNORECASE,
)
_PHONE_NUMBER = re.compile(r"(?<!\d)\+?\d(?:[\s()-]*\d){9,14}(?!\d)")
_UID_VALUE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24}(?![0-9A-Fa-f])")
_TELEGRAM_USERNAME = re.compile(r"(?<![\w.])@[A-Za-z][A-Za-z0-9_]{4,31}\b")
_LABELED_IDENTIFIER = re.compile(
    r"(?P<label>\b(?:"
    r"user_id|telegram_id|tg_user_id|chat_id|actor_id|admin_id|moderator_id|"
    r"owner_id|winner_id|bidder_id|target_user_id|counterparty_user_id"
    r")\s*[=:]\s*)-?\d{5,20}\b",
    re.IGNORECASE,
)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().casefold()
    return normalized in _SENSITIVE_EXACT_KEYS or bool(_SENSITIVE_KEY.search(normalized))


def redact_text(value: str) -> str:
    redacted = _BOT_TOKEN.sub("[REDACTED_TOKEN]", value)
    redacted = _DSN_PASSWORD.sub(r"\g<prefix>[REDACTED]@", redacted)
    redacted = _PHONE_NUMBER.sub("[REDACTED_PHONE]", redacted)
    redacted = _UID_VALUE.sub("[REDACTED_UID]", redacted)
    redacted = _LABELED_IDENTIFIER.sub(
        lambda match: f"{match.group('label')}[REDACTED_ID]",
        redacted,
    )
    return _TELEGRAM_USERNAME.sub("[REDACTED_USERNAME]", redacted)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


__all__ = ["is_sensitive_key", "redact", "redact_text"]
