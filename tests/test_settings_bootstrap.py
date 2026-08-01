from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _copy_bootstrap_package(tmp_path: Path) -> Path:
    project = tmp_path / "isolated-project"
    for relative_path in (
        "bot/__init__.py",
        "bot/core/__init__.py",
        "bot/core/environment.py",
        "bot/core/settings.py",
    ):
        source = ROOT / relative_path
        destination = project / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return project


def test_importing_settings_does_not_read_dotenv_or_export_runtime_values(tmp_path: Path) -> None:
    project = _copy_bootstrap_package(tmp_path)
    (project / ".env").write_text("BOT_TOKEN=temporary-import-marker\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("BOT_TOKEN", None)
    environment["PYTHONPATH"] = str(project)
    code = (
        "import os; import bot.core.settings as value; "
        "assert 'BOT_TOKEN' not in os.environ; "
        "assert not hasattr(value, 'settings'); "
        "assert not hasattr(value, 'BOT_TOKEN')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_entrypoints_have_no_top_level_environment_bootstrap() -> None:
    for relative_path in ("main.py", "userbot/entrypoint.py"):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        top_level_calls = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        assert not any(
            isinstance(call.func, ast.Name) and call.func.id == "load_project_environment"
            for call in top_level_calls
        ), relative_path


def test_application_modules_do_not_import_runtime_singletons() -> None:
    forbidden = {"settings", "default_settings", "supervisor_client"}
    for relative_path in ("bot/application.py", "userbot/application.py"):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not (imported & forbidden), relative_path


def test_only_one_legacy_compatibility_adapter_exists() -> None:
    source = (ROOT / "bot/core/legacy_config.py").read_text(encoding="utf-8")
    assert "legacy_config = LegacyConfigAdapter()" in source
    settings_source = (ROOT / "bot/core/settings.py").read_text(encoding="utf-8")
    assert "settings =" not in settings_source
