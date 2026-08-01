from __future__ import annotations

import ast
import builtins
import symtable
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE_DIR = ROOT / "bot/handlers/auction/exchange"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _handler_functions(path: Path) -> list[str]:
    tree = ast.parse(_source(path))
    result: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [ast.unparse(item) for item in node.decorator_list]
        if any("router." in item for item in decorators):
            result.append(node.name)
    return result


def test_exchange_monolith_is_a_compatibility_package() -> None:
    assert not (ROOT / "bot/handlers/auction/exchange.py").exists()
    expected = {
        "__init__.py",
        "common.py",
        "notifications.py",
        "submission.py",
        "moderation.py",
        "catalog.py",
        "editor.py",
    }
    assert expected <= {path.name for path in EXCHANGE_DIR.glob("*.py")}
    assert (EXCHANGE_DIR / "diagnostics" / "__init__.py").exists()

    facade = _source(EXCHANGE_DIR / "__init__.py")
    for router_name in (
        "submission_router",
        "moderation_router",
        "catalog_router",
        "editor_router",
        "diagnostics_router",
    ):
        assert f"router.include_router({router_name})" in facade


def test_exchange_handlers_are_distributed_without_duplicates() -> None:
    by_module = {
        path.relative_to(EXCHANGE_DIR).as_posix(): _handler_functions(path)
        for path in EXCHANGE_DIR.rglob("*.py")
        if path.name not in {"__init__.py", "common.py", "notifications.py"}
    }
    assert len(by_module["submission.py"]) == 12
    assert len(by_module["moderation.py"]) == 11
    assert len(by_module["catalog.py"]) == 18
    assert len(by_module["editor.py"]) == 11
    diagnostics_count = sum(
        len(names)
        for path, names in by_module.items()
        if path.startswith("diagnostics/")
    )
    assert diagnostics_count == 10

    all_handlers = [name for names in by_module.values() for name in names]
    expected_total = 12 + 11 + 18 + 11 + diagnostics_count
    assert len(all_handlers) == expected_total
    assert not [name for name, count in Counter(all_handlers).items() if count > 1]


def test_exchange_catalog_uses_service_repository_boundary() -> None:
    handler = _source(EXCHANGE_DIR / "catalog.py")
    service = _source(ROOT / "bot/services/exchange_catalog.py")
    repository = _source(ROOT / "bot/repositories/exchange_catalog.py")

    assert "ExchangeCatalogService.create()" in handler
    assert "SELECT " not in handler
    assert "INSERT " not in handler
    assert "UPDATE " not in handler
    assert "ExchangeCatalogRepository" in service
    assert "SELECT " in repository
    assert "public.exchange_batches" in repository


def test_exchange_components_have_resolved_globals() -> None:
    known = set(dir(builtins)) | {
        "__doc__",
        "__file__",
        "__name__",
        "__package__",
        "__conditional_annotations__",
    }
    for path in sorted(EXCHANGE_DIR.rglob("*.py")):
        table = symtable.symtable(_source(path), str(path), "exec")
        defined = {
            name
            for name in table.get_identifiers()
            if (
                table.lookup(name).is_assigned()
                or table.lookup(name).is_imported()
                or table.lookup(name).is_namespace()
            )
        }
        referenced: set[str] = set()
        pending = [table]
        while pending:
            current = pending.pop()
            referenced.update(
                name
                for name in current.get_identifiers()
                if current.lookup(name).is_global() and current.lookup(name).is_referenced()
            )
            pending.extend(current.get_children())
        assert not (referenced - defined - known), path.name


def test_exchange_components_have_bounded_size() -> None:
    limits = {
        "common.py": 1_100,
        "notifications.py": 550,
        "submission.py": 1_000,
        "moderation.py": 1_350,
        "catalog.py": 1_300,
        "editor.py": 650,
    }
    for filename, limit in limits.items():
        assert len(_source(EXCHANGE_DIR / filename).splitlines()) < limit

    diagnostic_limits = {
        "common.py": 300,
        "media.py": 100,
        "delivery.py": 300,
        "reports.py": 550,
        "reconciliation.py": 600,
    }
    for filename, limit in diagnostic_limits.items():
        assert len(_source(EXCHANGE_DIR / "diagnostics" / filename).splitlines()) < limit
