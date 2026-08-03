"""Container health probe for the file-backed userbot readiness state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import time
from typing import Sequence


def check_health(path: Path, *, max_age_seconds: float) -> tuple[bool, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"health file is missing: {path}"
    except (OSError, json.JSONDecodeError) as error:
        return False, f"health file cannot be read: {error}"

    if payload.get("status") != "ready":
        return False, f"userbot status is {payload.get('status')!r}"
    try:
        age = time() - float(payload["updated_at_epoch"])
    except (KeyError, TypeError, ValueError):
        return False, "health timestamp is missing or invalid"
    if age < -5:
        return False, "health timestamp is in the future"
    if age > max_age_seconds:
        return False, f"health file is stale: {age:.1f}s"
    if payload.get("connected") is not True or payload.get("authorized") is not True:
        return False, "Telegram client is not connected and authorized"
    return True, "ready"


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--max-age", type=float, default=30.0)
    args = parser.parse_args(argv)
    ok, message = check_health(args.file, max_age_seconds=args.max_age)
    if not ok:
        print(message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
