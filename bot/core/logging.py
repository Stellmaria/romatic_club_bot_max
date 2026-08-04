from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from bot.core.observability import ObservationContextFilter
from bot.core.privacy import redact
from bot.core.settings import LogLevel

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class JsonLogFormatter(logging.Formatter):
    """Structured formatter with correlation context and personal-data redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging(
    level_name: str | LogLevel = LogLevel.INFO,
    *,
    aiogram_debug: bool = False,
    structured: bool = False,
) -> None:
    """Configure process logging from an already validated settings model."""

    normalized = level_name.value if isinstance(level_name, LogLevel) else str(level_name)
    level = logging.getLevelNamesMapping().get(normalized.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.addFilter(ObservationContextFilter())
    if structured:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] [cid=%(correlation_id)s] %(message)s")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for logger_name in ("auction", "auction_bot", "userbot"):
        project_logger = logging.getLogger(logger_name)
        project_logger.handlers.clear()
        project_logger.propagate = True

    logging.getLogger("aiogram").setLevel(logging.DEBUG if aiogram_debug else logging.WARNING)


__all__ = ["JsonLogFormatter", "configure_logging"]
