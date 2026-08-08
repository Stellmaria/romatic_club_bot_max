"""Executable composition root for the Telegram Mini App web process."""

from __future__ import annotations

import logging

from aiohttp import web

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.settings import ConfigurationError
from webapi.app import create_app
from webapi.settings import WebAppSettings

logger = logging.getLogger("auction_bot.webapp")


def main() -> int:
    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        settings = WebAppSettings.from_env(project_root=project_root)
    except ConfigurationError as error:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", error)
        return 2

    logging.basicConfig(level=logging.INFO)
    web.run_app(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=logger,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
