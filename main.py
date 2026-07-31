"""Executable entrypoint for the Telegram bot process."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment

# Runtime configuration must be loaded before bot.application imports the
# process-wide settings singleton.
load_project_environment()

from bot.application import ApplicationConfigurationError, run_bot  # noqa: E402

logger = logging.getLogger("auction_bot")


def main() -> int:
    """Run the application with deterministic configuration-error status."""

    try:
        asyncio.run(run_bot())
    except ApplicationConfigurationError as error:
        logger.error("Configuration error: %s", error)
        return 2
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
