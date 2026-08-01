from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_modules_do_not_import_removed_settings_singleton() -> None:
    offenders: list[str] = []

    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {"tests", ".venv", "venv"}:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "bot.core.settings":
                continue
            if any(alias.name == "settings" for alias in node.names):
                offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "Production modules still import the removed "
        "bot.core.settings.settings singleton: " + ", ".join(offenders)
    )


def test_full_bot_and_userbot_import_graphs_are_loadable() -> None:
    # These imports mirror the production composition roots far enough to load
    # their router/application module graphs without starting Telegram polling.
    import bot.application  # noqa: F401
    import bot.bootstrap.routers  # noqa: F401
    import userbot.application  # noqa: F401
