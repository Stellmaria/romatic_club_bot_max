"""Validate deployment configuration without contacting external services."""

from __future__ import annotations

import argparse
import sys

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.settings import (
    BotProcessSettings,
    ConfigurationError,
    UserbotProcessSettings,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--userbot", action="store_true", help="also validate userbot settings")
    args = parser.parse_args()

    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        BotProcessSettings.from_env(project_root=project_root)
        if args.userbot:
            UserbotProcessSettings.from_env(project_root=project_root)
    except ConfigurationError as error:
        for issue in error.issues:
            print(f"ERROR: {issue.render()}", file=sys.stderr)
        return 2

    process_text = "bot and userbot" if args.userbot else "bot"
    print(f"Configuration preflight passed for {process_text}; no external connection was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
