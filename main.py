"""Executable composition root for the Telegram bot process."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.settings import BotProcessSettings, ConfigurationError

logger = logging.getLogger("auction_bot")


def main() -> int:
    """Load, validate and inject the bot process configuration."""

    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        config = BotProcessSettings.from_env(project_root=project_root)
    except ConfigurationError as error:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", error)
        return 2

    from bot.application import run_bot

    try:
        asyncio.run(run_bot(config))
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
