from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from bot.core.settings import Settings

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


def _isolated_process_environment(project: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "BOT_TOKEN",
        "DATABASE_URL",
        "AUCTION_CHANNEL_ID",
        "DISCUSSION_CHAT_ID",
        "UID_HASH_KEY",
        "UID_ENC_KEY",
    ):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(project)
    return environment


def test_importing_settings_does_not_read_project_dotenv(tmp_path: Path) -> None:
    project = _copy_bootstrap_package(tmp_path)
    (project / ".env").write_text("BOT_TOKEN=temporary-import-marker\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "import os; import bot.core.settings as value; assert 'BOT_TOKEN' not in os.environ; assert value.BOT_TOKEN == ''"],
        cwd=project,
        env=_isolated_process_environment(project),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_main_bootstrap_loads_dotenv_before_settings_singleton(tmp_path: Path) -> None:
    project = _copy_bootstrap_package(tmp_path)
    shutil.copy2(ROOT / "main.py", project / "main.py")
    (project / ".env").write_text("BOT_TOKEN=temporary-bootstrap-marker\n", encoding="utf-8")
    (project / "bot/application.py").write_text(
        """from bot.core.settings import BOT_TOKEN

if BOT_TOKEN != "temporary-bootstrap-marker":
    raise RuntimeError("settings singleton was created before environment bootstrap")

class ApplicationConfigurationError(RuntimeError):
    pass

async def run_bot():
    return None
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=project,
        env=_isolated_process_environment(project),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_installed_package_uses_process_cwd_as_runtime_root(tmp_path: Path) -> None:
    installed_root = tmp_path / "site-packages"
    runtime_root = tmp_path / "deployment"
    runtime_root.mkdir()
    for relative_path in (
        "bot/__init__.py",
        "bot/core/__init__.py",
        "bot/core/environment.py",
        "bot/core/settings.py",
    ):
        source = ROOT / relative_path
        destination = installed_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (runtime_root / ".env").write_text("BOT_TOKEN=temporary-installed-marker\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; import bot.core.environment as environment; environment.load_project_environment(); import bot.core.settings as settings; assert environment.PROJECT_ROOT == Path.cwd(); assert settings.PROJECT_ROOT == Path.cwd(); assert settings.BOT_TOKEN == 'temporary-installed-marker'"],
        cwd=runtime_root,
        env=_isolated_process_environment(installed_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _top_level_call_index(tree: ast.Module, function_name: str) -> int:
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == function_name:
            return index
    raise AssertionError(f"top-level call {function_name}() is missing")


def _top_level_import_index(tree: ast.Module, module_name: str) -> int:
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            return index
        if isinstance(node, ast.Import) and any(alias.name == module_name for alias in node.names):
            return index
    raise AssertionError(f"top-level import {module_name!r} is missing")


def test_process_entrypoints_bootstrap_before_application_imports() -> None:
    # Test executable process/module entrypoints. Historical helper wrappers
    # such as find_discussion_id.py contain their own standalone implementation
    # and are not production process composition roots.
    boundaries = {
        "main.py": "bot.application",
        "config.py": "bot.core.settings",
        "userbot/entrypoint.py": "bot.core.settings",
        "scripts/backfill_bids.py": "bot.core.settings",
        "scripts/migrate_uid_encryption.py": "bot.uid_crypto",
        "bot/tools/refresh_users.py": "bot.core.settings",
        "bot/tools/import_post_scans.py": "bot.core.settings",
    }
    for relative_path, guarded_import in boundaries.items():
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        assert _top_level_call_index(tree, "load_project_environment") < _top_level_import_index(tree, guarded_import), relative_path


def test_settings_from_env_reads_the_current_environment_each_time() -> None:
    with patch.dict(os.environ, {"BOT_TOKEN": "first-marker"}, clear=True):
        first = Settings.from_env()
    with patch.dict(os.environ, {"BOT_TOKEN": "second-marker"}, clear=True):
        second = Settings.from_env()
    assert first.bot_token == "first-marker"
    assert second.bot_token == "second-marker"
