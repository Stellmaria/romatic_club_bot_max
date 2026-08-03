from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from userbot import application
from userbot.provision import ProvisioningSettings
from userbot.session import (
    UserbotSessionError,
    prepare_session_storage,
    secure_session_files,
    session_file_path,
    validate_session_storage,
)


def test_production_application_has_no_interactive_authentication() -> None:
    source = inspect.getsource(application)
    assert "input(" not in source
    assert "getpass" not in source


def test_provisioning_settings_do_not_require_database_or_bot_secret(tmp_path: Path) -> None:
    settings = ProvisioningSettings.from_env(
        {
            "USERBOT_API_ID": "12345",
            "USERBOT_API_HASH": "hash",
            "USERBOT_SESSION": "private/userbot",
        },
        project_root=tmp_path,
    )
    assert settings.api_id == 12345
    assert settings.session == str(tmp_path / "private" / "userbot")


def test_missing_session_fails_without_creating_files(tmp_path: Path) -> None:
    configured = tmp_path / "session" / "userbot"
    with pytest.raises(UserbotSessionError, match="does not exist"):
        validate_session_storage(configured)
    assert not configured.parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_session_permissions_are_enforced(tmp_path: Path) -> None:
    configured = tmp_path / "session" / "userbot"
    session_file = prepare_session_storage(configured)
    session_file.write_text("sqlite", encoding="utf-8")
    session_file.chmod(0o644)

    with pytest.raises(UserbotSessionError, match="permissions are too broad"):
        validate_session_storage(configured)

    secure_session_files(configured)
    assert validate_session_storage(configured) == session_file
    assert session_file_path(configured) == session_file
    assert session_file.stat().st_mode & 0o777 == 0o600
    assert session_file.parent.stat().st_mode & 0o777 == 0o700
