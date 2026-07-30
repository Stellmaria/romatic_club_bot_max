"""Validate deployment configuration without contacting Telegram or PostgreSQL."""

from __future__ import annotations

import argparse
import sys

from bot.core.environment import load_project_environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--userbot", action="store_true", help="also validate userbot settings")
    args = parser.parse_args()

    load_project_environment()
    from bot.core.settings import Settings

    settings = Settings.from_env()
    errors = list(settings.bot_configuration_errors())
    if args.userbot:
        from userbot.application import userbot_configuration_errors

        errors.extend(userbot_configuration_errors(settings))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    print("Configuration preflight passed; no external connection was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
