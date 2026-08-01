"""Executable composition root for the Telethon userbot process."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.settings import ConfigurationError, UserbotProcessSettings

logger = logging.getLogger("userbot")


async def main(config: UserbotProcessSettings) -> None:
    from userbot.application import run_userbot_application

    await run_userbot_application(config)


def run() -> int:
    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        config = UserbotProcessSettings.from_env(project_root=project_root)
    except ConfigurationError as error:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", error)
        return 2

    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        logger.info("Userbot stopped by operator")
    return 0


__all__ = ["main", "run"]


if __name__ == "__main__":
    raise SystemExit(run())
