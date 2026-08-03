"""Correlation, latency and failure telemetry for Telegram updates."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types

from bot.core.observability import (
    MetricsRegistry,
    bind_observation_context,
    new_correlation_id,
    reset_observation_context,
)

logger = logging.getLogger("auction_bot.telegram_updates")

type UpdateMetadata = tuple[str, int | None, int | None, int | None, str]


class ObservabilityMiddleware(BaseMiddleware):
    """Bind one correlation context and record bounded update metadata."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    @staticmethod
    def _update(event: Any) -> types.Update | None:
        return event if isinstance(event, types.Update) else None

    @classmethod
    def _message(cls, event: Any) -> types.Message | None:
        if isinstance(event, types.Message):
            return event
        update = cls._update(event)
        if update is None:
            return None
        return update.message or update.edited_message

    @classmethod
    def _callback(cls, event: Any) -> types.CallbackQuery | None:
        if isinstance(event, types.CallbackQuery):
            return event
        update = cls._update(event)
        return None if update is None else update.callback_query

    @classmethod
    def _metadata(cls, event: Any) -> UpdateMetadata:
        update = cls._update(event)
        update_id = None if update is None else int(update.update_id)
        callback = cls._callback(event)
        if callback is not None:
            payload = callback.data or ""
            action = payload.split(":", 1)[0].split("|", 1)[0][:32] or "callback"
            raw_chat_id = getattr(getattr(callback.message, "chat", None), "id", None)
            chat_id = int(raw_chat_id) if isinstance(raw_chat_id, int) else None
            return (
                "callback_query",
                update_id,
                int(callback.from_user.id),
                chat_id,
                action,
            )

        message = cls._message(event)
        if message is not None:
            user_id = None if message.from_user is None else int(message.from_user.id)
            text = message.text or ""
            action = "message"
            if text.startswith("/"):
                action = text.split(maxsplit=1)[0].split("@", 1)[0][:32]
            return "message", update_id, user_id, int(message.chat.id), action

        return type(event).__name__.casefold(), update_id, None, None, "update"

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        update_type, update_id, user_id, chat_id, action = self._metadata(event)
        correlation_id = (
            f"telegram-update-{update_id}"
            if update_id is not None
            else new_correlation_id("telegram")
        )
        operation_id = f"{update_type}:{action}"
        tokens = bind_observation_context(
            correlation_id=correlation_id,
            operation_id=operation_id,
        )
        metrics = data.get("metrics_registry")
        registry = metrics if isinstance(metrics, MetricsRegistry) else None
        started_at = self._clock()
        if registry is not None:
            registry.increment(
                "telegram_updates_total",
                update_type=update_type,
                action=action,
            )
        logger.info(
            "Telegram update received",
            extra={
                "event": "telegram.update_received",
                "update_id": update_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "update_type": update_type,
                "action": action,
            },
        )
        try:
            result = await handler(event, data)
        # This is the outer adapter boundary: record the failure, then re-raise it.
        except Exception as error:  # noqa: BLE001
            if registry is not None:
                registry.increment(
                    "telegram_update_errors_total",
                    update_type=update_type,
                    action=action,
                    error_type=type(error).__name__,
                )
            logger.exception(
                "Telegram update failed",
                extra={
                    "event": "telegram.update_failed",
                    "update_id": update_id,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "update_type": update_type,
                    "action": action,
                    "error_type": type(error).__name__,
                },
            )
            raise
        finally:
            duration = max(0.0, self._clock() - started_at)
            if registry is not None:
                registry.observe(
                    "telegram_update_latency_seconds",
                    duration,
                    update_type=update_type,
                    action=action,
                )
            reset_observation_context(tokens)
        return result


__all__ = ["ObservabilityMiddleware"]
