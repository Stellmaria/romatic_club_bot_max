from __future__ import annotations

import subprocess
import sys


def test_submission_support_matches_black() -> None:
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "black",
            "--check",
            "--diff",
            "bot/handlers/auction/submission_support.py",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
