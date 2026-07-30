from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from bot.repositories.outbox import TelegramOutboxRepository
from db.core import get_db_pool

_BR_RE = re.compile(r"(?i)<br\s*/?>")
_MAX_TELEGRAM_TEXT = 4096
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    claimed: bool
    queued: int


class TelegramOutboxService:
    def __init__(self, repository: TelegramOutboxRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "TelegramOutboxService":
        return cls(TelegramOutboxRepository(await get_db_pool()))

    @staticmethod
    def _normalize_messages(messages: Mapping[int, str]) -> dict[int, str]:
        normalized: dict[int, str] = {}
        for raw_chat_id, raw_text in messages.items():
            chat_id = int(raw_chat_id)
            text = _BR_RE.sub("\n", str(raw_text or "")).strip()
            if not text:
                continue
            if len(text) > _MAX_TELEGRAM_TEXT:
                raise ValueError("outbox message exceeds Telegram's 4096-character limit")
            normalized[chat_id] = text
        return normalized

    @staticmethod
    def _validate_identity(topic: str, dedupe_scope: str) -> tuple[str, str]:
        normalized_topic = str(topic or "").strip().lower()
        normalized_scope = str(dedupe_scope or "").strip()
        if not _TOPIC_RE.fullmatch(normalized_topic):
            raise ValueError("invalid outbox topic")
        if not normalized_scope or len(normalized_scope) > 500:
            raise ValueError("invalid outbox dedupe scope")
        return normalized_topic, normalized_scope

    async def enqueue_auction_notification(
        self,
        *,
        auction_id: int,
        event: str,
        recipients: Iterable[int] | None = None,
        text: str | None = None,
        messages: Mapping[int, str] | None = None,
    ) -> EnqueueResult:
        if messages is not None and (recipients is not None or text is not None):
            raise ValueError("use either messages or recipients/text")
        if messages is None:
            if text is None:
                raise ValueError("text is required with recipients")
            messages = {int(chat_id): text for chat_id in (recipients or [])}

        claimed, queued = await self._repository.enqueue_auction_notification(
            auction_id=int(auction_id),
            event=event,
            messages=self._normalize_messages(messages),
        )
        return EnqueueResult(claimed=claimed, queued=queued)

    async def enqueue_messages(
        self,
        *,
        topic: str,
        dedupe_scope: str,
        messages: Mapping[int, str],
    ) -> EnqueueResult:
        topic, dedupe_scope = self._validate_identity(topic, dedupe_scope)
        queued = await self._repository.enqueue_messages(
            topic=topic,
            dedupe_scope=dedupe_scope,
            messages=self._normalize_messages(messages),
        )
        return EnqueueResult(claimed=queued > 0, queued=queued)

    async def enqueue_card_day_notification(
        self,
        *,
        user_id: int,
        card_id: int,
        day: date,
        text: str,
    ) -> EnqueueResult:
        messages = self._normalize_messages({int(user_id): text})
        if not messages:
            return EnqueueResult(claimed=False, queued=0)
        claimed, queued = await self._repository.enqueue_card_day_notification(
            user_id=int(user_id),
            card_id=int(card_id),
            day=day,
            text=messages[int(user_id)],
        )
        return EnqueueResult(claimed=claimed, queued=queued)

    async def enqueue_copy_message_broadcast(
        self,
        *,
        topic: str,
        dedupe_scope: str,
        recipients: Iterable[int],
        from_chat_id: int,
        message_id: int,
    ) -> EnqueueResult:
        topic, dedupe_scope = self._validate_identity(topic, dedupe_scope)
        normalized_recipients = sorted(
            {int(chat_id) for chat_id in recipients if int(chat_id) != 0}
        )
        queued = await self._repository.enqueue_copy_message_broadcast(
            topic=topic,
            dedupe_scope=dedupe_scope,
            recipients=normalized_recipients,
            from_chat_id=int(from_chat_id),
            message_id=int(message_id),
        )
        return EnqueueResult(claimed=queued > 0, queued=queued)

    async def diagnostic_summary(self) -> dict[str, Any]:
        return await self._repository.diagnostic_summary()

    async def list_failed(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return await self._repository.list_failed(limit=limit)

    async def requeue_confirmed_not_sent(
        self,
        outbox_id: int,
        *,
        reviewed_by: int,
        note: str | None = None,
    ) -> bool:
        return await self._repository.requeue_confirmed_not_sent(
            outbox_id,
            reviewed_by=reviewed_by,
            note=note,
        )

    async def confirm_delivered(
        self,
        outbox_id: int,
        *,
        reviewed_by: int,
        note: str | None = None,
    ) -> bool:
        return await self._repository.confirm_delivered(
            outbox_id,
            reviewed_by=reviewed_by,
            note=note,
        )
