"""Strict Telegram input, callback and HTML rendering boundary.

Handlers should parse callback payloads and user-controlled values here before
calling application services.  The module intentionally keeps trusted HTML
explicit and rejects payloads that exceed Telegram's documented limits.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence, TypeVar

TELEGRAM_CALLBACK_BYTES = 64
TELEGRAM_MESSAGE_CHARS = 4096
TELEGRAM_CAPTION_CHARS = 1024
DEFAULT_USER_TEXT_CHARS = 1000

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TelegramBoundaryError(ValueError):
    """Controlled validation error safe to show to a Telegram user."""

    def __init__(self, user_message: str, *, code: str = "invalid_input") -> None:
        self.user_message = user_message
        self.code = code
        super().__init__(user_message)


@dataclass(frozen=True, slots=True)
class TrustedHtml:
    """HTML fragment reviewed by the application rather than supplied by a user."""

    value: str


def trusted_html(value: str) -> TrustedHtml:
    return TrustedHtml(str(value))


def escape_html(value: object) -> str:
    """Escape arbitrary text for Telegram HTML parse mode."""

    return html.escape(str(value), quote=False)


def render_html(*parts: object) -> str:
    """Join fragments, escaping everything except explicit ``TrustedHtml`` values."""

    rendered: list[str] = []
    for part in parts:
        if isinstance(part, TrustedHtml):
            rendered.append(part.value)
        else:
            rendered.append(escape_html(part))
    result = "".join(rendered)
    if len(result) > TELEGRAM_MESSAGE_CHARS:
        raise TelegramBoundaryError(
            "Сообщение слишком длинное.",
            code="message_too_long",
        )
    return result


def validate_text(
    value: object,
    *,
    field: str = "Текст",
    minimum: int = 1,
    maximum: int = DEFAULT_USER_TEXT_CHARS,
    strip: bool = True,
) -> str:
    """Validate user text before persistence or HTML rendering."""

    text = str(value or "")
    if strip:
        text = text.strip()
    if _CONTROL_RE.search(text):
        raise TelegramBoundaryError(
            f"{field} содержит недопустимые управляющие символы.",
            code="control_characters",
        )
    if len(text) < minimum:
        raise TelegramBoundaryError(f"{field} не заполнен.", code="text_too_short")
    if len(text) > maximum:
        raise TelegramBoundaryError(
            f"{field} слишком длинный: максимум {maximum} символов.",
            code="text_too_long",
        )
    return text


def validate_int(
    value: object,
    *,
    field: str = "Значение",
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise TelegramBoundaryError(
            f"{field} должно быть целым числом.",
            code="invalid_integer",
        ) from exc
    if minimum is not None and result < minimum:
        raise TelegramBoundaryError(
            f"{field} должно быть не меньше {minimum}.",
            code="integer_too_small",
        )
    if maximum is not None and result > maximum:
        raise TelegramBoundaryError(
            f"{field} должно быть не больше {maximum}.",
            code="integer_too_large",
        )
    return result


EnumT = TypeVar("EnumT", bound=Enum)


def validate_enum(value: object, enum_type: type[EnumT], *, field: str = "Значение") -> EnumT:
    raw = str(value).strip()
    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise TelegramBoundaryError(
            f"{field} имеет недопустимое значение. Разрешено: {allowed}.",
            code="invalid_enum",
        ) from exc


def validate_date(
    value: object,
    *,
    field: str = "Дата",
    minimum: date | None = None,
    maximum: date | None = None,
) -> date:
    raw = str(value).strip()
    try:
        result = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise TelegramBoundaryError(
            f"{field} должна быть в формате ГГГГ-ММ-ДД.",
            code="invalid_date",
        ) from exc
    if minimum is not None and result < minimum:
        raise TelegramBoundaryError(f"{field} слишком ранняя.", code="date_too_early")
    if maximum is not None and result > maximum:
        raise TelegramBoundaryError(f"{field} слишком поздняя.", code="date_too_late")
    return result


def validate_media_type(value: object, *, allowed: Iterable[str]) -> str:
    media_type = str(value or "").strip().lower()
    allowed_values = frozenset(str(item).strip().lower() for item in allowed)
    if media_type not in allowed_values:
        raise TelegramBoundaryError(
            "Этот тип файла не поддерживается.",
            code="invalid_media_type",
        )
    return media_type


@dataclass(frozen=True, slots=True)
class CallbackField:
    name: str
    kind: str = "token"
    minimum: int | None = None
    maximum: int | None = None
    values: frozenset[str] = frozenset()

    def encode(self, value: object) -> str:
        if self.kind == "int":
            return str(
                validate_int(
                    value,
                    field=self.name,
                    minimum=self.minimum,
                    maximum=self.maximum,
                )
            )
        token = str(value).strip()
        if not token or not _SAFE_TOKEN_RE.fullmatch(token):
            raise TelegramBoundaryError(
                f"Некорректное поле callback: {self.name}.",
                code="invalid_callback_field",
            )
        if self.kind == "enum" and token not in self.values:
            raise TelegramBoundaryError(
                f"Некорректное поле callback: {self.name}.",
                code="invalid_callback_enum",
            )
        return token

    def decode(self, value: str) -> int | str:
        if self.kind == "int":
            return validate_int(
                value,
                field=self.name,
                minimum=self.minimum,
                maximum=self.maximum,
            )
        return self.encode(value)


@dataclass(frozen=True, slots=True)
class CallbackSchema:
    """Versioned strict callback schema with Telegram-size enforcement."""

    namespace: str
    action: str
    fields: tuple[CallbackField, ...] = ()
    version: str = "v1"
    separator: str = "|"

    def __post_init__(self) -> None:
        for token in (self.version, self.namespace, self.action):
            if not _SAFE_TOKEN_RE.fullmatch(token):
                raise ValueError(f"unsafe callback schema token: {token!r}")

    @property
    def prefix(self) -> str:
        return self.separator.join((self.version, self.namespace, self.action))

    def pack(self, **values: object) -> str:
        expected = {field.name for field in self.fields}
        if set(values) != expected:
            raise TelegramBoundaryError(
                "Некорректные параметры кнопки.",
                code="callback_fields_mismatch",
            )
        parts = [self.version, self.namespace, self.action]
        parts.extend(field.encode(values[field.name]) for field in self.fields)
        payload = self.separator.join(parts)
        validate_callback_payload(payload)
        return payload

    def unpack(self, payload: object) -> dict[str, int | str]:
        raw = validate_callback_payload(payload)
        parts = raw.split(self.separator)
        expected_size = 3 + len(self.fields)
        if len(parts) != expected_size or parts[:3] != [
            self.version,
            self.namespace,
            self.action,
        ]:
            raise TelegramBoundaryError(
                "Кнопка устарела или повреждена.",
                code="malformed_callback",
            )
        return {
            field.name: field.decode(value)
            for field, value in zip(self.fields, parts[3:], strict=True)
        }


def validate_callback_payload(payload: object) -> str:
    raw = str(payload or "")
    if not raw or _CONTROL_RE.search(raw):
        raise TelegramBoundaryError(
            "Кнопка устарела или повреждена.",
            code="malformed_callback",
        )
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise TelegramBoundaryError(
            "Кнопка устарела или повреждена.",
            code="malformed_callback",
        ) from exc
    if size > TELEGRAM_CALLBACK_BYTES:
        raise TelegramBoundaryError(
            "Данные кнопки превышают лимит Telegram.",
            code="callback_too_long",
        )
    return raw


def parse_callback_parts(
    payload: object,
    *,
    prefix: str,
    fields: Sequence[CallbackField],
    separator: str = "|",
) -> Mapping[str, int | str]:
    """Strictly parse an existing callback family through the shared boundary.

    This compatibility adapter removes ad-hoc parsing from handlers while a
    family is migrated to the versioned ``CallbackSchema.pack`` representation.
    """

    raw = validate_callback_payload(payload)
    parts = raw.split(separator)
    expected_size = 1 + len(fields)
    if len(parts) != expected_size or parts[0] != prefix:
        raise TelegramBoundaryError(
            "Кнопка устарела или повреждена.",
            code="malformed_callback",
        )
    return {
        field.name: field.decode(value)
        for field, value in zip(fields, parts[1:], strict=True)
    }


__all__ = [
    "CallbackField",
    "CallbackSchema",
    "DEFAULT_USER_TEXT_CHARS",
    "TELEGRAM_CALLBACK_BYTES",
    "TELEGRAM_CAPTION_CHARS",
    "TELEGRAM_MESSAGE_CHARS",
    "TelegramBoundaryError",
    "TrustedHtml",
    "escape_html",
    "parse_callback_parts",
    "render_html",
    "trusted_html",
    "validate_callback_payload",
    "validate_date",
    "validate_enum",
    "validate_int",
    "validate_media_type",
    "validate_text",
]
