"""Executable composition root for the Telegram bot process."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.mini_app_settings import MiniAppConfigurationError, MiniAppSettings
from bot.core.settings import BotProcessSettings, ConfigurationError

logger = logging.getLogger("auction_bot")


async def _run(config: BotProcessSettings, mini_app_settings: MiniAppSettings) -> None:
    from bot.application import run_bot
    from bot.telegram.mini_app import configure_mini_app_menu

    await configure_mini_app_menu(config.bot.bot_token, mini_app_settings)
    await run_bot(config)


def main() -> int:
    """Load, validate and inject the bot process configuration."""

    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        config = BotProcessSettings.from_env(project_root=project_root)
        mini_app_settings = MiniAppSettings.from_env()
    except (ConfigurationError, MiniAppConfigurationError) as error:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", error)
        return 2

    try:
        asyncio.run(_run(config, mini_app_settings))
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
