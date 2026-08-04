from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "bot/handlers/admin/action_support/transport.py"


def test_bot_transport_does_not_import_telethon() -> None:
    """The aiogram bot image must not require the userbot-only Telethon graph."""

    source = TRANSPORT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TRANSPORT))

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)

    assert not [
        module
        for module in imported_modules
        if module == "telethon" or module.startswith("telethon.")
    ]
    assert "await client_api.send_message(entity, text)" in source
