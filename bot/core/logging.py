from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    """Configure one root handler and remove legacy project handlers.

    Several legacy modules attach ``StreamHandler`` objects during import.
    Once the root logger is configured, those local handlers duplicate every
    propagated record.  Centralizing cleanup keeps one output line per event.
    """

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        force=True,
    )

    for logger_name in ("auction", "auction_bot"):
        project_logger = logging.getLogger(logger_name)
        project_logger.handlers.clear()
        project_logger.propagate = True

    if os.getenv("AIOGRAM_DEBUG") == "1":
        logging.getLogger("aiogram").setLevel(logging.DEBUG)
