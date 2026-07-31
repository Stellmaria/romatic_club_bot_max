from __future__ import annotations

import ast
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.repositories.uid_identity_admin import UIDIdentityAdminRepository  # noqa: E402
from bot.repositories.uid_verification import UIDVerificationRepository  # noqa: E402


ADMIN_HANDLERS = tuple(
    ROOT / "bot/handlers/admin" / name
    for name in (
        "uid_verification_admin.py",
        "uid_admin_shared.py",
        "uid_admin_resolvers.py",
        "uid_admin_presentation.py",
        "uid_admin_bans.py",
        "uid_verification_review.py",
        "uid_whois.py",
        "uid_verification_revision.py",
        "telegram_user_bans.py",
        "master_ban.py",
    )
)
REPOSITORIES = (
    ROOT / "bot/repositories/uid_verification.py",
    ROOT / "bot/repositories/uid_identity_admin.py",
)
SQL_PATTERN = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.IGNORECASE)


def test_uid_admin_handlers_have_no_database_or_sql_boundary_leaks() -> None:
    for path in ADMIN_HANDLERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("db") for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("db"), path
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert SQL_PATTERN.search(node.value) is None, (path, node.lineno)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in {"fetch", "fetchrow", "fetchval", "execute"}, path
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {"acquire", "transaction"}, path


def test_public_uid_handler_uses_service_boundary_for_new_operations() -> None:
    source = (ROOT / "bot/handlers/uid_verification.py").read_text(encoding="utf-8")
    assert "UIDVerificationService" in source
    assert "UIDVerificationRepository" not in source


def test_uid_repositories_require_an_explicit_pool() -> None:
    for path in REPOSITORIES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "db.pool" not in imports


class _Transaction:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_entries += 1

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._connection.transaction_exits += 1


class _Acquire:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self) -> "_FakeConnection":
        return self._connection

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


class _FakePool:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self._connection)


class _FakeConnection:
    def __init__(
        self,
        fetchrow_results: list[dict[str, Any] | None],
        *,
        fetchval_results: list[Any] | None = None,
    ) -> None:
        self._fetchrow_results = list(fetchrow_results)
        self._fetchval_results = list(fetchval_results or [])
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_entries = 0
        self.transaction_exits = 0

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_results.pop(0) if self._fetchrow_results else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        return self._fetchval_results.pop(0) if self._fetchval_results else None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"


@pytest.mark.asyncio
async def test_confirmation_cleanup_is_owned_by_repository() -> None:
    connection = _FakeConnection([])
    repository = UIDVerificationRepository(_FakePool(connection))  # type: ignore[arg-type]

    await repository.delete_confirmation_for_counterparty(14, " New_Name ")

    assert len(connection.execute_calls) == 1
    assert "DELETE FROM public.uid_verification_confirmations" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1] == (14, "new_name")


@pytest.mark.asyncio
async def test_confirmation_request_lookup_is_owned_by_repository() -> None:
    connection = _FakeConnection([], fetchval_results=[91])
    repository = UIDVerificationRepository(_FakePool(connection))  # type: ignore[arg-type]

    assert await repository.confirmation_request_id(8) == 91
    assert connection.fetchval_calls[0][1] == (8,)


@pytest.mark.asyncio
async def test_master_ban_is_atomic_and_never_persists_plain_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UID_HASH_KEY", "test-only-hmac-key")
    monkeypatch.setenv(
        "UID_ENC_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    uid = "0123456789abcdef01234567"
    connection = _FakeConnection([{"user_id": 25}, {"uid_last4": "4567"}])
    repository = UIDIdentityAdminRepository(_FakePool(connection))  # type: ignore[arg-type]

    result = await repository.apply_master_ban(
        uid=uid,
        user_id=31,
        banned_by=9,
        reason="policy",
        uid_banned_until=None,
        user_banned_until=datetime.fromisoformat("2030-01-01T00:00:00+00:00"),
    )

    assert result.owner_user_id == 25
    assert connection.transaction_entries == connection.transaction_exits == 1
    persisted_args = [
        value
        for _, args in (*connection.fetchrow_calls, *connection.execute_calls)
        for value in args
    ]
    assert uid not in persisted_args
    assert any(value == "4567" for value in persisted_args)
    assert len(connection.execute_calls) == 2
