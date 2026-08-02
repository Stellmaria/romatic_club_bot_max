"""Framework-neutral application use cases.

Telegram handlers translate input into commands and render results.  Business
transitions, persistence orchestration and stable application errors live here.
"""

from .common import (
    ApplicationConflict,
    ApplicationError,
    ApplicationInvalidState,
    ApplicationNotFound,
    ApplicationPermissionDenied,
    ApplicationTimeout,
    ApplicationValidationError,
)

__all__ = [
    "ApplicationConflict",
    "ApplicationError",
    "ApplicationInvalidState",
    "ApplicationNotFound",
    "ApplicationPermissionDenied",
    "ApplicationTimeout",
    "ApplicationValidationError",
]
