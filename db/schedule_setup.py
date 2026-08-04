"""Persistence for Premium schedule setup, previews and approvals."""

from __future__ import annotations

from datetime import date
from typing import Any

from bot.domain.schedule_lots import SPECIAL_SCHEDULE_ASSETS
from db.core import execute, fetch, fetchrow


async def get_emoji_assets() -> dict[str, dict[str, Any]]:
    rows = await fetch("""
        SELECT asset_key, custom_emoji_id, fallback, updated_by, updated_at
        FROM public.schedule_emoji_assets
        ORDER BY asset_key
        """)
    return {str(row["asset_key"]): dict(row) for row in rows}


async def upsert_emoji_asset(
    asset_key: str,
    custom_emoji_id: int,
    *,
    fallback: str,
    updated_by: int,
) -> None:
    await execute(
        """
        INSERT INTO public.schedule_emoji_assets(
            asset_key, custom_emoji_id, fallback, updated_by, updated_at
        ) VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (asset_key) DO UPDATE
        SET custom_emoji_id = EXCLUDED.custom_emoji_id,
            fallback = EXCLUDED.fallback,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        asset_key,
        int(custom_emoji_id),
        fallback,
        int(updated_by),
    )


async def get_deck_emoji(deck_id: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT deck_id, custom_emoji_id, updated_by, updated_at
        FROM public.schedule_deck_emojis
        WHERE deck_id = $1
        """,
        int(deck_id),
    )
    return dict(row) if row else None


async def upsert_deck_emoji(deck_id: int, custom_emoji_id: int, *, updated_by: int) -> None:
    await execute(
        """
        INSERT INTO public.schedule_deck_emojis(deck_id, custom_emoji_id, updated_by, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (deck_id) DO UPDATE
        SET custom_emoji_id = EXCLUDED.custom_emoji_id,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        int(deck_id),
        int(custom_emoji_id),
        int(updated_by),
    )


async def get_card_emoji(card_id: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT card_id, custom_emoji_id, verified, verified_by, verified_at,
               updated_by, updated_at
        FROM public.schedule_card_emojis
        WHERE card_id = $1
        """,
        int(card_id),
    )
    return dict(row) if row else None


async def upsert_card_emoji(card_id: int, custom_emoji_id: int, *, updated_by: int) -> None:
    await execute(
        """
        INSERT INTO public.schedule_card_emojis(
            card_id, custom_emoji_id, verified, updated_by, updated_at
        ) VALUES ($1, $2, false, $3, now())
        ON CONFLICT (card_id) DO UPDATE
        SET custom_emoji_id = EXCLUDED.custom_emoji_id,
            verified = false,
            verified_by = NULL,
            verified_at = NULL,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        int(card_id),
        int(custom_emoji_id),
        int(updated_by),
    )


async def mark_card_emoji_verified(card_id: int, *, verified_by: int) -> None:
    await execute(
        """
        UPDATE public.schedule_card_emojis
        SET verified = true,
            verified_by = $2,
            verified_at = now(),
            updated_at = now()
        WHERE card_id = $1
        """,
        int(card_id),
        int(verified_by),
    )


async def get_setup_session(user_id: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT user_id, stage, asset_key, deck_id, card_id, updated_at
        FROM public.schedule_setup_sessions
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(row) if row else None


async def set_setup_session(
    user_id: int,
    *,
    stage: str,
    asset_key: str | None = None,
    deck_id: int | None = None,
    card_id: int | None = None,
) -> None:
    await execute(
        """
        INSERT INTO public.schedule_setup_sessions(
            user_id, stage, asset_key, deck_id, card_id, updated_at
        ) VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (user_id) DO UPDATE
        SET stage = EXCLUDED.stage,
            asset_key = EXCLUDED.asset_key,
            deck_id = EXCLUDED.deck_id,
            card_id = EXCLUDED.card_id,
            updated_at = now()
        """,
        int(user_id),
        stage,
        asset_key,
        int(deck_id) if deck_id is not None else None,
        int(card_id) if card_id is not None else None,
    )


async def clear_setup_session(user_id: int) -> None:
    await execute(
        "DELETE FROM public.schedule_setup_sessions WHERE user_id = $1",
        int(user_id),
    )


async def get_all_decks_for_setup() -> list[dict[str, Any]]:
    rows = await fetch("""
        SELECT d.id AS deck_id, d.name AS deck_name, d.deck_type,
               e.custom_emoji_id AS deck_emoji_id
        FROM public.decks d
        LEFT JOIN public.schedule_deck_emojis e ON e.deck_id = d.id
        ORDER BY d.id
        """)
    return [dict(row) for row in rows]


async def get_cards_for_setup(deck_id: int) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT c.card_id, c.deck_id, c.num, c.card_name, c.hero_name,
               c.image_id, c.rarity, c.obtain_type, c.obtain_amount,
               c.story, c.quote, d.name AS deck_name,
               e.custom_emoji_id AS card_emoji_id,
               COALESCE(e.verified, false) AS emoji_verified
        FROM public.cards c
        JOIN public.decks d ON d.id = c.deck_id
        LEFT JOIN public.schedule_card_emojis e ON e.card_id = c.card_id
        WHERE c.deck_id = $1
        ORDER BY c.num NULLS LAST, c.card_id
        """,
        int(deck_id),
    )
    return [dict(row) for row in rows]


async def get_card_for_setup(card_id: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT c.card_id, c.deck_id, c.num, c.card_name, c.hero_name,
               c.image_id, c.rarity, c.obtain_type, c.obtain_amount,
               c.story, c.quote, d.name AS deck_name,
               e.custom_emoji_id AS card_emoji_id,
               COALESCE(e.verified, false) AS emoji_verified
        FROM public.cards c
        JOIN public.decks d ON d.id = c.deck_id
        LEFT JOIN public.schedule_card_emojis e ON e.card_id = c.card_id
        WHERE c.card_id = $1
        """,
        int(card_id),
    )
    return dict(row) if row else None


async def set_preview_target(
    *,
    chat_id: int,
    thread_id: int | None,
    set_by: int,
) -> None:
    await execute(
        """
        INSERT INTO public.schedule_preview_target(
            singleton_id, chat_id, thread_id, set_by, updated_at
        ) VALUES (1, $1, $2, $3, now())
        ON CONFLICT (singleton_id) DO UPDATE
        SET chat_id = EXCLUDED.chat_id,
            thread_id = EXCLUDED.thread_id,
            set_by = EXCLUDED.set_by,
            updated_at = now()
        """,
        int(chat_id),
        int(thread_id) if thread_id else None,
        int(set_by),
    )


async def get_preview_target() -> dict[str, Any] | None:
    row = await fetchrow("""
        SELECT chat_id, thread_id, set_by, updated_at
        FROM public.schedule_preview_target
        WHERE singleton_id = 1
        """)
    return dict(row) if row else None


async def get_publication_review(target_date: date) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT target_date, status, preview_chat_id, preview_thread_id,
               preview_message_id, reviewed_by, reviewed_at,
               channel_message_id, created_at, updated_at
        FROM public.schedule_publication_reviews
        WHERE target_date = $1
        """,
        target_date,
    )
    return dict(row) if row else None


async def record_pending_preview(
    target_date: date,
    *,
    chat_id: int,
    thread_id: int | None,
    message_id: int,
) -> None:
    await execute(
        """
        INSERT INTO public.schedule_publication_reviews(
            target_date, status, preview_chat_id, preview_thread_id,
            preview_message_id, created_at, updated_at
        ) VALUES ($1, 'pending', $2, $3, $4, now(), now())
        ON CONFLICT (target_date) DO UPDATE
        SET status = CASE
                WHEN schedule_publication_reviews.status = 'published'
                    THEN schedule_publication_reviews.status
                ELSE 'pending'
            END,
            preview_chat_id = EXCLUDED.preview_chat_id,
            preview_thread_id = EXCLUDED.preview_thread_id,
            preview_message_id = EXCLUDED.preview_message_id,
            reviewed_by = NULL,
            reviewed_at = NULL,
            updated_at = now()
        """,
        target_date,
        int(chat_id),
        int(thread_id) if thread_id else None,
        int(message_id),
    )


async def set_publication_review_status(
    target_date: date,
    *,
    status: str,
    reviewed_by: int,
) -> None:
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    await execute(
        """
        UPDATE public.schedule_publication_reviews
        SET status = $2,
            reviewed_by = $3,
            reviewed_at = now(),
            updated_at = now()
        WHERE target_date = $1
          AND status <> 'published'
        """,
        target_date,
        status,
        int(reviewed_by),
    )


async def mark_publication_published(target_date: date, *, channel_message_id: int) -> None:
    await execute(
        """
        UPDATE public.schedule_publication_reviews
        SET status = 'published',
            channel_message_id = $2,
            updated_at = now()
        WHERE target_date = $1
        """,
        target_date,
        int(channel_message_id),
    )


async def get_setup_audit() -> dict[str, Any]:
    common = await fetchrow(
        """
        SELECT COUNT(*) FILTER (
                   WHERE asset_key = ANY($1::text[])
               ) AS configured
        FROM public.schedule_emoji_assets
        """,
        [
            "rarity:bronze",
            "rarity:silver",
            "rarity:gold",
            "rarity:epic",
            "currency:diamonds",
            "currency:tea",
            "whole_deck",
            *(spec.key for spec in SPECIAL_SCHEDULE_ASSETS),
        ],
    )
    totals = await fetchrow("""
        SELECT (SELECT COUNT(*) FROM public.decks) AS decks_total,
               (SELECT COUNT(*) FROM public.schedule_deck_emojis) AS decks_configured,
               (SELECT COUNT(*) FROM public.cards) AS cards_total,
               (SELECT COUNT(*) FROM public.schedule_card_emojis WHERE verified) AS cards_verified
        """)
    return {
        "common_configured": int(common["configured"] or 0) if common else 0,
        "common_total": 7 + len(SPECIAL_SCHEDULE_ASSETS),
        "decks_total": int(totals["decks_total"] or 0) if totals else 0,
        "decks_configured": int(totals["decks_configured"] or 0) if totals else 0,
        "cards_total": int(totals["cards_total"] or 0) if totals else 0,
        "cards_verified": int(totals["cards_verified"] or 0) if totals else 0,
    }


async def get_schedule_lots_for_day(day: date) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        WITH matched AS (
            SELECT a.*,
                   COALESCE(a.card_id, c.card_id) AS resolved_card_id,
                   c.num AS card_num,
                   COALESCE(
                       c.deck_id,
                       CASE
                           WHEN lower(a.card_name) ~ 'вся\\s+[0-9]+\\s+колода'
                           THEN substring(
                               lower(a.card_name)
                               FROM 'вся\\s+([0-9]+)\\s+колода'
                           )::integer
                       END
                   ) AS resolved_deck_id,
                   c.rarity,
                   c.obtain_type,
                   c.obtain_amount,
                   c.image_id AS card_image_id,
                   (c.card_id IS NULL AND lower(a.card_name) ~ 'вся\\s+[0-9]+\\s+колода')
                       AS whole_deck
            FROM public.auctions a
            LEFT JOIN LATERAL (
                SELECT candidate.*
                FROM public.cards candidate
                WHERE candidate.card_id = a.card_id
                   OR (
                        a.card_id IS NULL
                        AND lower(trim(candidate.card_name)) = lower(trim(a.card_name))
                        AND lower(trim(COALESCE(candidate.hero_name, ''))) =
                            lower(trim(COALESCE(a.hero_name, '')))
                   )
                ORDER BY (candidate.card_id = a.card_id) DESC, candidate.card_id
                LIMIT 1
            ) c ON true
            WHERE CASE
                    WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                        THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                    ELSE a.start_time::date
                  END = $1
              AND a.status IN ('approved', 'scheduled', 'publishing', 'active')
        )
        SELECT m.*,
               d.name AS deck_name,
               ce.custom_emoji_id AS card_emoji_id,
               COALESCE(ce.verified, false) AS card_emoji_verified,
               de.custom_emoji_id AS deck_emoji_id,
               COALESCE(totals.diamonds, 0) AS deck_diamonds,
               COALESCE(totals.tea, 0) AS deck_tea
        FROM matched m
        LEFT JOIN public.decks d ON d.id = m.resolved_deck_id
        LEFT JOIN public.schedule_card_emojis ce ON ce.card_id = m.resolved_card_id
        LEFT JOIN public.schedule_deck_emojis de ON de.deck_id = m.resolved_deck_id
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(
                       CASE WHEN lower(c.obtain_type::text) IN ('diamonds', 'diamond')
                            THEN c.obtain_amount ELSE 0 END
                   ), 0)::integer AS diamonds,
                   COALESCE(SUM(
                       CASE WHEN lower(c.obtain_type::text) IN ('tea', 'cups', 'cup')
                            THEN c.obtain_amount ELSE 0 END
                   ), 0)::integer AS tea
            FROM public.cards c
            WHERE c.deck_id = m.resolved_deck_id
        ) totals ON true
        ORDER BY m.start_time, m.auction_id
        """,
        day,
    )
    return [dict(row) for row in rows]


__all__ = [
    "clear_setup_session",
    "get_all_decks_for_setup",
    "get_card_emoji",
    "get_card_for_setup",
    "get_cards_for_setup",
    "get_deck_emoji",
    "get_emoji_assets",
    "get_preview_target",
    "get_publication_review",
    "get_schedule_lots_for_day",
    "get_setup_audit",
    "get_setup_session",
    "mark_card_emoji_verified",
    "mark_publication_published",
    "record_pending_preview",
    "set_preview_target",
    "set_publication_review_status",
    "set_setup_session",
    "upsert_card_emoji",
    "upsert_deck_emoji",
    "upsert_emoji_asset",
]
