"""Explicit process-environment bootstrap helpers.

Configuration modules remain safe to import in tests, migrations and library
consumers. Executable composition roots call :func:`load_project_environment`
and then construct the required process settings explicitly.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _is_source_checkout(path: Path) -> bool:
    """Return whether ``path`` looks like the project source checkout."""

    return (path / "pyproject.toml").is_file() or (path / ".env").is_file()


def resolve_project_root(
    project_root: str | PathLike[str] | Path | None = None,
) -> Path:
    """Resolve runtime paths for source checkouts and installed distributions.

    Source execution keeps the historical repository root.  A wheel does not
    contain ``pyproject.toml`` or the deployment ``.env`` beside its package,
    so its writable runtime root is the process working directory instead of
    ``site-packages``.  Callers may always provide an explicit deployment root.
    """

    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    if _is_source_checkout(_PACKAGE_ROOT):
        return _PACKAGE_ROOT
    return Path.cwd().resolve()


PROJECT_ROOT = resolve_project_root()


def load_project_environment(
    project_root: str | PathLike[str] | Path | None = None,
) -> bool:
    """Load ``<project_root>/.env`` without overriding the process environment.

    The dependency is imported lazily so importing this helper does not inspect
    the filesystem or mutate :data:`os.environ`.
    """

    global PROJECT_ROOT

    selected_root = resolve_project_root(project_root)
    # ``PROJECT_ROOT`` remains only as a path compatibility export. Strict
    # settings loaders receive the selected root explicitly from the caller.
    PROJECT_ROOT = selected_root

    from dotenv import load_dotenv

    return bool(
        load_dotenv(
            dotenv_path=selected_root / ".env",
            override=False,
            encoding="utf-8",
        )
    )


__all__ = ["PROJECT_ROOT", "load_project_environment", "resolve_project_root"]
