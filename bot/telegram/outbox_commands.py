from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

CURRENT_COMMAND_VERSION = 1


class OutboxCommandError(ValueError):
    """Raised when an outbox command cannot be validated or migrated."""


@dataclass(frozen=True, slots=True)
class OutboxCommand:
    command_type: str
    version: int
    payload: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "command_type": self.command_type,
            "version": self.version,
            "payload": dict(self.payload),
        }


Validator = Callable[[Mapping[str, Any]], dict[str, Any]]


def _require_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None or isinstance(value, bool):
        raise OutboxCommandError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OutboxCommandError(f"{key} must be an integer") from exc


def _validate_send_message(payload: Mapping[str, Any]) -> dict[str, Any]:
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise OutboxCommandError("send_message.text must be a non-empty string")
    result = dict(payload)
    result["text"] = text
    return result


def _validate_copy_message(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["from_chat_id"] = _require_int(payload, "from_chat_id")
    result["message_id"] = _require_int(payload, "message_id")
    return result


def _validate_refresh_auction_publication(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    auction_id = _require_int(payload, "auction_id")
    if auction_id <= 0:
        raise OutboxCommandError("refresh_auction_publication.auction_id must be positive")
    return {"auction_id": auction_id}


_REGISTRY: dict[tuple[str, int], Validator] = {
    ("send_message", 1): _validate_send_message,
    ("copy_message", 1): _validate_copy_message,
    ("refresh_auction_publication", 1): _validate_refresh_auction_publication,
}


def registered_command_types() -> frozenset[str]:
    return frozenset(command_type for command_type, _ in _REGISTRY)


def build_command(command_type: str, payload: Mapping[str, Any]) -> OutboxCommand:
    return decode_command(
        {
            "command_type": command_type,
            "version": CURRENT_COMMAND_VERSION,
            "payload": dict(payload),
        }
    )


def decode_command(raw: Mapping[str, Any], *, legacy_method: str | None = None) -> OutboxCommand:
    """Decode a typed envelope, migrating the pre-envelope payload when needed."""
    if "command_type" not in raw and legacy_method:
        raw = {
            "command_type": legacy_method,
            "version": CURRENT_COMMAND_VERSION,
            "payload": dict(raw),
        }

    command_type = raw.get("command_type")
    version = raw.get("version")
    payload = raw.get("payload")

    if not isinstance(command_type, str) or not command_type:
        raise OutboxCommandError("command_type must be a non-empty string")
    if isinstance(version, bool) or not isinstance(version, int):
        raise OutboxCommandError("command version must be an integer")
    if not isinstance(payload, Mapping):
        raise OutboxCommandError("command payload must be an object")

    validator = _REGISTRY.get((command_type, version))
    if validator is None:
        raise OutboxCommandError(f"unsupported outbox command: {command_type!r} version {version}")
    return OutboxCommand(command_type, version, validator(payload))
