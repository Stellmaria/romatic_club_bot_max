from __future__ import annotations

from pathlib import Path

from scripts.check_async_blocking import check_file


def test_async_blocking_checker_rejects_sync_sleep(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text(
        "import time\n\nasync def worker() -> None:\n    time.sleep(1)\n",
        encoding="utf-8",
    )

    violations = check_file(path)

    assert len(violations) == 1
    assert "time.sleep" in violations[0]


def test_async_blocking_checker_allows_reviewed_exception(tmp_path: Path) -> None:
    path = tmp_path / "allowed.py"
    path.write_text(
        "import time\n\nasync def worker() -> None:\n"
        "    time.sleep(1)  # quality: allow-blocking: isolated legacy adapter\n",
        encoding="utf-8",
    )

    assert check_file(path) == []


def test_async_blocking_checker_ignores_sync_functions(tmp_path: Path) -> None:
    path = tmp_path / "sync.py"
    path.write_text(
        "import time\n\ndef worker() -> None:\n    time.sleep(1)\n",
        encoding="utf-8",
    )

    assert check_file(path) == []
