from __future__ import annotations

import logging

from bot.core.settings import LogLevel


def configure_logging(
    level_name: str | LogLevel = LogLevel.INFO,
    *,
    aiogram_debug: bool = False,
) -> None:
    """Configure process logging from an already validated settings model."""

    normalized = level_name.value if isinstance(level_name, LogLevel) else str(level_name)
    level = logging.getLevelNamesMapping().get(normalized.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        force=True,
    )

    for logger_name in ("auction", "auction_bot"):
        project_logger = logging.getLogger(logger_name)
        project_logger.handlers.clear()
        project_logger.propagate = True

    logging.getLogger("aiogram").setLevel(
        logging.DEBUG if aiogram_debug else logging.WARNING
    )


__all__ = ["configure_logging"]
