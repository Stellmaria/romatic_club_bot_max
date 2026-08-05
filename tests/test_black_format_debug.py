from __future__ import annotations

import subprocess
import sys

import pytest


def test_print_changed_file_black_diff() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "black",
            "--diff",
            "bot/handlers/admin/presentation/exchange_pending_view.py",
            "bot/handlers/auction/exchange/__init__.py",
            "bot/handlers/auction/exchange/moderation_queue.py",
            "tests/test_exchange_pending_queue_continuation.py",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    pytest.fail(result.stdout + result.stderr)
