from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE = ROOT / "bot/handlers/auction/exchange"
DIAGNOSTICS = EXCHANGE / "diagnostics"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _handlers(path: Path) -> list[str]:
    tree = ast.parse(_source(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("router." in ast.unparse(dec) for dec in node.decorator_list)
    ]


def test_exchange_moderation_has_service_repository_boundary() -> None:
    handler = _source(EXCHANGE / "moderation.py")
    service = _source(ROOT / "bot/services/exchange_moderation.py")
    repository = _source(ROOT / "bot/repositories/exchange_moderation.py")

    assert "ExchangeModerationService.create()" in handler
    assert "from db.db import" not in handler
    for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "):
        assert keyword not in handler
    assert "ExchangeModerationRepository" in service
    assert "public.exchange_batches" in repository
    assert "COUNT(ei.item_id)::int" in repository


def test_pending_auction_menu_uses_presenter_not_db_query_function() -> None:
    handler = _source(EXCHANGE / "moderation.py")
    assert "await show_pendinglots(call.message)" in handler
    assert "show_pending_auction_lots(call.message)" not in handler


def test_diagnostics_is_a_router_package() -> None:
    assert not (EXCHANGE / "diagnostics.py").exists()
    expected = {
        "__init__.py",
        "common.py",
        "media.py",
        "delivery.py",
        "reports.py",
        "reconciliation.py",
    }
    assert expected <= {path.name for path in DIAGNOSTICS.glob("*.py")}

    facade = _source(DIAGNOSTICS / "__init__.py")
    for name in ("media_router", "delivery_router", "reports_router", "reconciliation_router"):
        assert f"router.include_router({name})" in facade


def test_diagnostic_handlers_are_preserved_and_split_by_responsibility() -> None:
    counts = {
        "media.py": 1,
        "delivery.py": 1,
        "reports.py": 4,
        "reconciliation.py": 4,
    }
    names: list[str] = []
    for filename, expected_count in counts.items():
        module_handlers = _handlers(DIAGNOSTICS / filename)
        assert len(module_handlers) == expected_count
        names.extend(module_handlers)
    assert len(names) == 10
    assert len(names) == len(set(names))


def test_diagnostics_handlers_do_not_own_sql_or_legacy_db_api() -> None:
    combined = "\n".join(
        _source(path)
        for path in DIAGNOSTICS.glob("*.py")
        if path.name != "common.py"
    )
    assert "ExchangeDiagnosticsService.create()" in combined
    assert "from db.db import" not in combined
    for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "):
        assert keyword not in combined

    service = _source(ROOT / "bot/services/exchange_diagnostics.py")
    repository = _source(ROOT / "bot/repositories/exchange_diagnostics.py")
    assert "ExchangeDiagnosticsRepository" in service
    assert "public.exchange_batches" in repository
    assert "assigned_items_for_winners" in repository


def test_multi_lot_dispatch_is_recorded_atomically() -> None:
    handler = _source(DIAGNOSTICS / "delivery.py")
    repository = _source(ROOT / "bot/repositories/exchange_diagnostics.py")

    assert "mark_batches_dispatched" in handler
    assert "set_exchange_manual_winner" not in handler
    assert "mark_exchange_manual_sent" not in handler
    method = repository[repository.index("async def mark_batches_dispatched"):]
    assert "async with conn.transaction()" in method
    assert "manual_winner_id" in method
    assert "manual_sent_at = COALESCE(manual_sent_at, NOW())" in method
