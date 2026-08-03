"""Filesystem contract for the production Telethon session."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UserbotSessionError(RuntimeError):
    """Raised when a Telethon session is absent, unsafe or unauthorized."""


def session_file_path(session: str | Path) -> Path:
    path = Path(session).expanduser()
    if path.suffix == ".session":
        return path
    return Path(f"{path}.session")


def _reject_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise UserbotSessionError(f"{label} must not be a symbolic link: {path}")


def _require_private_mode(path: Path, *, label: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise UserbotSessionError(
            f"{label} permissions are too broad ({mode:04o}); expected no group/other access"
        )


def validate_session_storage(session: str | Path) -> Path:
    """Validate the exact session file used by production without mutating it."""

    session_file = session_file_path(session)
    parent = session_file.parent
    if not parent.is_dir():
        raise UserbotSessionError(
            f"Userbot session directory does not exist: {parent}. "
            "Run `auction-userbot-provision authorize` first."
        )
    _reject_symlink(parent, label="Userbot session directory")
    _require_private_mode(parent, label="Userbot session directory")

    if not session_file.exists():
        raise UserbotSessionError(
            f"Userbot session file does not exist: {session_file}. "
            "Run `auction-userbot-provision authorize` first."
        )
    _reject_symlink(session_file, label="Userbot session file")
    if not session_file.is_file():
        raise UserbotSessionError(f"Userbot session path is not a regular file: {session_file}")
    _require_private_mode(session_file, label="Userbot session file")

    if hasattr(os, "getuid") and session_file.stat().st_uid != os.getuid():
        raise UserbotSessionError(
            f"Userbot session file is not owned by the running user: {session_file}"
        )
    return session_file


def prepare_session_storage(session: str | Path) -> Path:
    """Create a private session directory for the explicit provisioning command."""

    session_file = session_file_path(session)
    parent = session_file.parent
    if parent.exists():
        _reject_symlink(parent, label="Userbot session directory")
        if not parent.is_dir():
            raise UserbotSessionError(f"Userbot session parent is not a directory: {parent}")
    else:
        parent.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    return session_file


def secure_session_files(session: str | Path) -> Path:
    """Tighten SQLite session and sidecar files after provisioning."""

    session_file = session_file_path(session)
    for candidate in (
        session_file,
        Path(f"{session_file}-journal"),
        Path(f"{session_file}-shm"),
        Path(f"{session_file}-wal"),
    ):
        if candidate.exists():
            _reject_symlink(candidate, label="Userbot session file")
            candidate.chmod(0o600)
    session_file.parent.chmod(0o700)
    return session_file


def remove_session_files(session: str | Path) -> None:
    """Remove the local SQLite session and all known sidecars."""

    session_file = session_file_path(session)
    for candidate in (
        Path(f"{session_file}-journal"),
        Path(f"{session_file}-shm"),
        Path(f"{session_file}-wal"),
        session_file,
    ):
        if candidate.exists():
            _reject_symlink(candidate, label="Userbot session file")
            candidate.unlink()


__all__ = [
    "UserbotSessionError",
    "prepare_session_storage",
    "remove_session_files",
    "secure_session_files",
    "session_file_path",
    "validate_session_storage",
]
