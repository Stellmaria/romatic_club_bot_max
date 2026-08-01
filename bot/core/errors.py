"""Application-facing error hierarchy.

These exceptions are safe to propagate across adapters.  Their messages name
only the failed operation and never include SQL text, bind parameters or
personal data.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors an application adapter may handle explicitly."""

    user_message = "Операцию не удалось выполнить. Попробуйте позже."


class TemporaryApplicationError(ApplicationError):
    """A transient failure for which retrying later may succeed."""


class ExternalDependencyError(TemporaryApplicationError):
    """A failure in a database, network API or another external dependency."""


class PersistenceError(ExternalDependencyError):
    """Safe base error for PostgreSQL operations."""

    user_message = "База данных временно недоступна. Попробуйте позже."

    def __init__(self, operation: str, *, error_code: str = "database_error") -> None:
        self.operation = (operation or "database.operation").strip()
        self.error_code = (error_code or "database_error").strip()
        super().__init__(f"Persistence operation failed: {self.operation} [{self.error_code}]")


class PersistenceUnavailableError(PersistenceError):
    """The database connection or pool is unavailable."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation, error_code="database_unavailable")


class PersistenceConflictError(PersistenceError):
    """The operation must be retried or conflicts with concurrent work."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation, error_code="database_conflict")


class PersistenceIntegrityError(PersistenceConflictError):
    """A database constraint rejected the requested state."""

    def __init__(self, operation: str) -> None:
        PersistenceError.__init__(self, operation, error_code="database_integrity")


class PersistenceOperationError(PersistenceError):
    """A non-connectivity PostgreSQL operation failed."""


__all__ = [
    "ApplicationError",
    "ExternalDependencyError",
    "PersistenceConflictError",
    "PersistenceError",
    "PersistenceIntegrityError",
    "PersistenceOperationError",
    "PersistenceUnavailableError",
    "TemporaryApplicationError",
]
