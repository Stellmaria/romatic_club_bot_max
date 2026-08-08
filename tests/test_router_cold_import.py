from __future__ import annotations

import subprocess
import sys


def test_router_bootstrap_imports_in_fresh_python_process() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import bot.bootstrap.routers"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
