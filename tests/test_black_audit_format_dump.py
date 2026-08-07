from __future__ import annotations

import subprocess
import sys

import pytest


def test_dump_black_diff_for_audit_patch() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "black",
            "--diff",
            "bot/handlers/auction/admin_lifecycle.py",
            "bot/handlers/auction/winner_components/common.py",
            "bot/presentation/audit.py",
            "tests/test_audit_log_formatting.py",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    pytest.fail(result.stdout or result.stderr)
