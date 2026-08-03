"""Executable composition root for the production Telethon userbot."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.logging import configure_logging
from bot.core.settings import ConfigurationError, UserbotProcessSettings
from userbot.session import UserbotSessionError

logger = logging.getLogger("userbot")


async def _main(config: UserbotProcessSettings) -> None:
    from userbot.application import run_userbot_application

    await run_userbot_application(config)


def run() -> int:
    project_root = resolve_project_root()
    load_project_environment(project_root)
    configure_logging("INFO", structured=True)
    try:
        config = UserbotProcessSettings.from_env(project_root=project_root)
    except ConfigurationError as error:
        logger.error("Configuration error: %s", error)
        return 2

    try:
        asyncio.run(_main(config))
    except KeyboardInterrupt:
        logger.info("Userbot stopped by operator")
        return 130
    except UserbotSessionError as error:
        logger.error("Userbot session is not ready: %s", error)
        return 3
    except Exception:
        logger.exception("Userbot process failed")
        return 1
    return 0


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(run())
