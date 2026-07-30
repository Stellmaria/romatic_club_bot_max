from __future__ import annotations

import ast
import html
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_format_log_entry():
    path = ROOT / "bot/utils_admin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "format_log_entry"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"html": html}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["format_log_entry"]


def test_audit_log_queries_use_created_at_column() -> None:
    source = (ROOT / "db/admin.py").read_text(encoding="utf-8")

    assert 'DATE("timestamp")' not in source
    assert '"timestamp"::date' not in source
    assert 'ORDER BY "timestamp"' not in source
    assert "DATE(created_at)" in source
    assert "created_at::date" in source
    assert source.count("ORDER BY created_at DESC") >= 2


def test_get_audit_logs_uses_expected_placeholder_order() -> None:
    source = (ROOT / "db/admin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_audit_logs"
    )
    function_source = ast.get_source_segment(source, function) or ""

    assert "created_at::date = $%d" in function_source
    assert "user_id = $%d" in function_source
    assert "ORDER BY created_at DESC LIMIT $%d" in function_source
    assert function_source.index("params.append(log_date)") < function_source.index(
        "params.append(admin_id)"
    )
    assert function_source.index("params.append(admin_id)") < function_source.index(
        "params.append(limit)"
    )


def test_format_log_entry_accepts_canonical_created_at() -> None:
    format_log_entry = _load_format_log_entry()

    rendered = format_log_entry(
        {
            "created_at": datetime(2026, 7, 15, 3, 36),
            "user_id": 123,
            "action_type": "approve_lot",
            "auction_id": 456,
            "details": "ok",
        }
    )

    assert "15.07 03:36" in rendered
    assert "approve_lot" in rendered


def test_format_log_entry_keeps_legacy_timestamp_fallback() -> None:
    format_log_entry = _load_format_log_entry()

    rendered = format_log_entry(
        {
            "timestamp": datetime(2026, 7, 15, 3, 36),
            "user_id": 123,
            "action_type": "legacy",
            "auction_id": None,
            "details": "old row shape",
        }
    )

    assert "15.07 03:36" in rendered
    assert "legacy" in rendered
