from __future__ import annotations

import subprocess
import sys


def test_changed_preorder_files_match_black() -> None:
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "black",
            "--check",
            "--diff",
            "bot/repositories/auction_submission.py",
            "tests/test_auction_submission_split.py",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
