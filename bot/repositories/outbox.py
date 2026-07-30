from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

import asyncpg


class TelegramOutboxRepository:
    """Transactional persistence for Telegram commands.

    Auction notification flags are claimed in the same transaction as their
    outbox rows.  A second bot instance therefore observes the flag and cannot
    enqueue the same logical notification again.
    """

    _AUCTION_EVENT_FIELDS = {
        "start": "notified_start",
        "one_minute": "notified_1min",
        "end": "notified_end",
    }

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    def _payload(text: str) -> str:
        return json.dumps(
            {
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": False,
                "disable_web_page_preview": True,
                "protect_content": False,
            },
            ensure_ascii=False,
        )

    @classmethod
    async def _insert_messages(
        cls,
        conn: asyncpg.Connection,
        *,
        topic: str,
        dedupe_scope: str,
        messages: Mapping[int, str],
    ) -> int:
        ordered = sorted(
            (int(chat_id), str(text))
            for chat_id, text in messages.items()
            if int(chat_id) != 0 and str(text)
        )
        if not ordered:
            return 0

        rows = await conn.fetch(
            """
            WITH input AS (
                SELECT *
                FROM unnest($1::text[], $2::bigint[], $3::text[])
                    AS item(dedupe_key, chat_id, payload_text)
            )
            INSERT INTO public.telegram_outbox (
                dedupe_key, topic, method, chat_id, payload
            )
            SELECT dedupe_key, $4, 'send_message', chat_id, payload_text::jsonb
            FROM input
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING outbox_id
            """,
            [f"{topic}:{dedupe_scope}:{chat_id}" for chat_id, _ in ordered],
            [chat_id for chat_id, _ in ordered],
            [cls._payload(text) for _, text in ordered],
            topic,
        )
        return len(rows)

    async def enqueue_auction_notification(
        self,
        *,
        auction_id: int,
        event: str,
        messages: Mapping[int, str],
    ) -> tuple[bool, int]:
        flag = self._AUCTION_EVENT_FIELDS.get(event)
        if flag is None:
            raise ValueError(f"unsupported auction notification event: {event}")

        normalized = {
            int(chat_id): str(text)
            for chat_id, text in messages.items()
            if int(chat_id) != 0 and str(text)
        }
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchrow(
                    f"""
                    UPDATE public.auctions
                    SET {flag} = TRUE
                    WHERE auction_id = $1
                      AND COALESCE({flag}, FALSE) = FALSE
                    RETURNING auction_id
                    """,
                    int(auction_id),
                )
                if not claimed:
                    return False, 0

                queued = await self._insert_messages(
                    conn,
                    topic="auction",
                    dedupe_scope=f"{int(auction_id)}:{event}",
                    messages=normalized,
                )
        return True, queued

    async def enqueue_messages(
        self,
        *,
        topic: str,
        dedupe_scope: str,
        messages: Mapping[int, str],
    ) -> int:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                return await self._insert_messages(
                    conn,
                    topic=topic,
                    dedupe_scope=dedupe_scope,
                    messages=messages,
                )

    async def enqueue_copy_message_broadcast(
        self,
        *,
        topic: str,
        dedupe_scope: str,
        recipients: list[int],
        from_chat_id: int,
        message_id: int,
    ) -> int:
        chat_ids = sorted({int(chat_id) for chat_id in recipients if int(chat_id) != 0})
        if not chat_ids:
            return 0
        payload = json.dumps(
            {
                "from_chat_id": int(from_chat_id),
                "message_id": int(message_id),
                "protect_content": False,
            }
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    WITH input AS (
                        SELECT *
                        FROM unnest($1::text[], $2::bigint[])
                            AS item(dedupe_key, chat_id)
                    )
                    INSERT INTO public.telegram_outbox (
                        dedupe_key, topic, method, chat_id, payload
                    )
                    SELECT dedupe_key, $3, 'copy_message', chat_id, $4::jsonb
                    FROM input
                    ON CONFLICT (dedupe_key) DO NOTHING
                    RETURNING outbox_id
                    """,
                    [f"{topic}:{dedupe_scope}:{chat_id}" for chat_id in chat_ids],
                    chat_ids,
                    topic,
                    payload,
                )
        return len(rows)

    async def enqueue_card_day_notification(
        self,
        *,
        user_id: int,
        card_id: int,
        day: date,
        text: str,
    ) -> tuple[bool, int]:
        """Claim the daily card marker and enqueue its message atomically."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchrow(
                    """
                    INSERT INTO public.card_day_notifications (user_id, card_id, day)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, card_id, day) DO NOTHING
                    RETURNING id
                    """,
                    int(user_id),
                    int(card_id),
                    day,
                )
                if not claimed:
                    return False, 0
                queued = await self._insert_messages(
                    conn,
                    topic="card-day",
                    dedupe_scope=f"{day.isoformat()}:{int(card_id)}",
                    messages={int(user_id): text},
                )
        return True, queued

    async def claim_batch(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    WITH due AS (
                        SELECT outbox_id
                        FROM public.telegram_outbox
                        WHERE status = 'pending'
                          AND available_at <= now()
                          AND attempts < max_attempts
                        ORDER BY available_at, outbox_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT $1
                    )
                    UPDATE public.telegram_outbox o
                    SET status = 'processing',
                        attempts = o.attempts + 1,
                        locked_at = now(),
                        last_error = NULL,
                        updated_at = now()
                    FROM due
                    WHERE o.outbox_id = due.outbox_id
                    RETURNING o.*
                    """,
                    max(1, int(limit)),
                )
        return [dict(row) for row in rows]

    async def mark_sent(self, outbox_id: int, *, message_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.telegram_outbox
                SET status = 'sent',
                    delivery_state = 'confirmed_sent',
                    sent_at = now(),
                    telegram_message_id = $2,
                    locked_at = NULL,
                    last_error = NULL,
                    updated_at = now()
                WHERE outbox_id = $1 AND status = 'processing'
                RETURNING outbox_id
                """,
                int(outbox_id),
                int(message_id),
            )
        return bool(row)

    async def retry_after(
        self,
        outbox_id: int,
        *,
        delay_seconds: int,
        error: str,
    ) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.telegram_outbox
                SET status = CASE
                        WHEN attempts < max_attempts THEN 'pending'
                        ELSE 'failed'
                    END,
                    delivery_state = CASE
                        WHEN attempts < max_attempts THEN 'not_attempted'
                        ELSE 'confirmed_not_sent'
                    END,
                    available_at = now() + make_interval(secs => $2::double precision),
                    locked_at = NULL,
                    last_error = $3,
                    updated_at = now()
                WHERE outbox_id = $1 AND status = 'processing'
                RETURNING outbox_id
                """,
                int(outbox_id),
                max(1, int(delay_seconds)),
                (error or "Telegram retry requested")[:2000],
            )
        return bool(row)

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        delivery_state: str = "unknown",
    ) -> bool:
        if delivery_state not in {"unknown", "confirmed_not_sent"}:
            raise ValueError("invalid failed delivery state")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.telegram_outbox
                SET status = 'failed',
                    delivery_state = $3,
                    locked_at = NULL,
                    last_error = $2,
                    updated_at = now()
                WHERE outbox_id = $1 AND status = 'processing'
                RETURNING outbox_id
                """,
                int(outbox_id),
                (error or "unknown delivery error")[:2000],
                delivery_state,
            )
        return bool(row)

    async def fail_stale(self, *, older_than_minutes: int = 15) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE public.telegram_outbox
                SET status = 'failed',
                    delivery_state = 'unknown',
                    last_error = 'worker lease expired; delivery outcome unknown; manual review required',
                    updated_at = now()
                WHERE status = 'processing'
                  AND locked_at <= now() - make_interval(mins => $1::int)
                RETURNING outbox_id
                """,
                max(1, int(older_than_minutes)),
            )
        return [int(row["outbox_id"]) for row in rows]

    async def diagnostic_summary(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, delivery_state, COUNT(*) AS count
                FROM public.telegram_outbox
                GROUP BY status, delivery_state
                ORDER BY status, delivery_state
                """
            )
            oldest = await conn.fetchrow(
                """
                SELECT outbox_id, created_at, available_at
                FROM public.telegram_outbox
                WHERE status = 'pending'
                ORDER BY created_at, outbox_id
                LIMIT 1
                """
            )
        return {
            "counts": [dict(row) for row in rows],
            "oldest_pending": dict(oldest) if oldest else None,
        }

    async def list_failed(self, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT outbox_id, topic, chat_id, attempts, max_attempts,
                       delivery_state, last_error, created_at, updated_at
                FROM public.telegram_outbox
                WHERE status = 'failed'
                ORDER BY updated_at DESC, outbox_id DESC
                LIMIT $1
                """,
                min(100, max(1, int(limit))),
            )
        return [dict(row) for row in rows]

    async def requeue_confirmed_not_sent(
        self,
        outbox_id: int,
        *,
        reviewed_by: int,
        note: str | None = None,
    ) -> bool:
        """Replay only when Telegram definitely rejected the original request."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.telegram_outbox
                SET status = 'pending',
                    delivery_state = 'not_attempted',
                    attempts = 0,
                    available_at = now(),
                    locked_at = NULL,
                    last_error = NULL,
                    reviewed_at = now(),
                    reviewed_by = $2,
                    review_note = $3,
                    updated_at = now()
                WHERE outbox_id = $1
                  AND status = 'failed'
                  AND delivery_state = 'confirmed_not_sent'
                RETURNING outbox_id
                """,
                int(outbox_id),
                int(reviewed_by),
                (note or "manual safe replay")[:500],
            )
        return bool(row)

    async def confirm_delivered(
        self,
        outbox_id: int,
        *,
        reviewed_by: int,
        note: str | None = None,
    ) -> bool:
        """Record an administrator's external confirmation without resending."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.telegram_outbox
                SET status = 'sent',
                    delivery_state = 'confirmed_sent',
                    sent_at = COALESCE(sent_at, now()),
                    locked_at = NULL,
                    reviewed_at = now(),
                    reviewed_by = $2,
                    review_note = $3,
                    updated_at = now()
                WHERE outbox_id = $1
                  AND status = 'failed'
                  AND delivery_state = 'unknown'
                RETURNING outbox_id
                """,
                int(outbox_id),
                int(reviewed_by),
                (note or "delivery confirmed manually")[:500],
            )
        return bool(row)
