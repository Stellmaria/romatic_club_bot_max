from __future__ import annotations

from dataclasses import dataclass


class ApplicationError(Exception):
    """Stable error exposed by application use cases to delivery adapters."""

    code = "application_error"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        details: dict[str, object] | None = None,
    ):
        self.code = (code or self.code).strip()
        super().__init__(message or self.code)
        self.details = dict(details or {})


class ApplicationValidationError(ApplicationError):
    code = "validation_error"


class ApplicationNotFound(ApplicationError):
    code = "not_found"


class ApplicationPermissionDenied(ApplicationError):
    code = "permission_denied"


class ApplicationConflict(ApplicationError):
    code = "conflict"


class ApplicationInvalidState(ApplicationError):
    code = "invalid_state"


class ApplicationTimeout(ApplicationError):
    code = "timeout"


@dataclass(frozen=True, slots=True)
class EffectFailure:
    """A non-transactional side effect that failed after a successful commit."""

    effect: str
    detail: str
