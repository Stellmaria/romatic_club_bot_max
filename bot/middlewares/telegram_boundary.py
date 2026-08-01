"""Global validation, replay protection and rate limiting for Telegram updates."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types

from bot.telegram.boundary import TelegramBoundaryError, validate_callback_payload
from bot.telegram.callbacks import safe_callback_answer

logger = logging.getLogger("auction_bot.telegram_boundary")
_ACTION_RE = re.compile(r"^[^:|]{1,24}")


class TelegramBoundaryMiddleware(BaseMiddleware):
    """Reject malformed, duplicate and abusive callbacks before handlers run."""

    def __init__(
        self,
        *,
        rate_limit: int = 12,
        rate_window_seconds: float = 2.0,
        duplicate_window_seconds: float = 1.0,
        retention_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_limit = max(2, int(rate_limit))
        self.rate_window_seconds = max(0.25, float(rate_window_seconds))
        self.duplicate_window_seconds = max(0.1, float(duplicate_window_seconds))
        self.retention_seconds = max(self.rate_window_seconds, float(retention_seconds))
        self._clock = clock
        self._rate_events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._seen_updates: dict[str, float] = {}
        self._recent_payloads: dict[tuple[int, str], float] = {}
        self._last_cleanup = 0.0

    @staticmethod
    def _callback_from_event(event: Any) -> types.CallbackQuery | None:
        if isinstance(event, types.CallbackQuery):
            return event
        if isinstance(event, types.Update):
            return event.callback_query
        return None

    @staticmethod
    def _update_key(event: Any, callback: types.CallbackQuery) -> str:
        if isinstance(event, types.Update):
            return f"update:{event.update_id}"
        return f"callback:{callback.id}"

    @staticmethod
    def _action_key(payload: str) -> str:
        match = _ACTION_RE.match(payload)
        return match.group(0) if match else "callback"

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < min(30.0, self.retention_seconds / 2):
            return
        cutoff = now - self.retention_seconds
        self._seen_updates = {
            key: seen for key, seen in self._seen_updates.items() if seen >= cutoff
        }
        self._recent_payloads = {
            key: seen for key, seen in self._recent_payloads.items() if seen >= cutoff
        }
        for key, events in tuple(self._rate_events.items()):
            while events and events[0] < cutoff:
                events.popleft()
            if not events:
                self._rate_events.pop(key, None)
        self._last_cleanup = now

    def _accept(self, event: Any, callback: types.CallbackQuery, payload: str) -> str | None:
        now = self._clock()
        self._cleanup(now)
        user_id = int(callback.from_user.id)
        update_key = self._update_key(event, callback)
        if update_key in self._seen_updates:
            return "duplicate_update"
        self._seen_updates[update_key] = now

        payload_key = (user_id, payload)
        previous = self._recent_payloads.get(payload_key)
        self._recent_payloads[payload_key] = now
        if previous is not None and now - previous < self.duplicate_window_seconds:
            return "duplicate_callback"

        rate_key = (user_id, self._action_key(payload))
        events = self._rate_events[rate_key]
        cutoff = now - self.rate_window_seconds
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= self.rate_limit:
            return "rate_limited"
        events.append(now)
        return None

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        callback = self._callback_from_event(event)
        if callback is None:
            return await handler(event, data)

        try:
            payload = validate_callback_payload(callback.data)
        except TelegramBoundaryError as error:
            logger.info(
                "Malformed callback rejected user_id=%s code=%s",
                callback.from_user.id,
                error.code,
            )
            await safe_callback_answer(callback, error.user_message, show_alert=True)
            return None

        rejection = self._accept(event, callback, payload)
        if rejection is not None:
            messages = {
                "duplicate_update": "Запрос уже обработан.",
                "duplicate_callback": "Кнопка уже нажата.",
                "rate_limited": "Слишком много действий. Подождите немного.",
            }
            logger.info(
                "Callback rejected user_id=%s reason=%s action=%s",
                callback.from_user.id,
                rejection,
                self._action_key(payload),
            )
            await safe_callback_answer(
                callback,
                messages[rejection],
                show_alert=rejection == "rate_limited",
            )
            return None

        try:
            return await handler(event, data)
        except TelegramBoundaryError as error:
            logger.info(
                "Telegram boundary error user_id=%s code=%s action=%s",
                callback.from_user.id,
                error.code,
                self._action_key(payload),
            )
            await safe_callback_answer(callback, error.user_message, show_alert=True)
            return None


__all__ = ["TelegramBoundaryMiddleware"]
