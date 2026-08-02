"""Persistence helpers for extended schedule setup controls."""

from __future__ import annotations

from db.core import execute, fetchrow

_TEMP_SCOPES = frozenset({"asset", "deck", "card"})
_CARD_FIELDS = frozenset(
    {"card_name", "num", "hero_name", "image_id", "rarity", "story", "quote"}
)
_DECK_FIELDS = frozenset({"name", "deck_type"})


async def _placeholder_emoji_id() -> int:
    row = await fetchrow(
        """
        SELECT custom_emoji_id
        FROM (
            SELECT custom_emoji_id, 1 AS priority FROM public.schedule_emoji_assets
            UNION ALL
            SELECT custom_emoji_id, 2 AS priority FROM public.schedule_deck_emojis
            UNION ALL
            SELECT custom_emoji_id, 3 AS priority FROM public.schedule_card_emojis
        ) candidates
        WHERE custom_emoji_id > 0
        ORDER BY priority, custom_emoji_id
        LIMIT 1
        """
    )
    if not row:
        raise ValueError(
            "Сначала сохраните хотя бы один настоящий Premium-эмодзи: "
            "он будет использован как временная заглушка."
        )
    return int(row["custom_emoji_id"])


async def mark_temporary_emoji(
    scope: str,
    entity_key: object,
    *,
    placeholder_emoji_id: int,
    fallback: str,
    updated_by: int,
) -> None:
    if scope not in _TEMP_SCOPES:
        raise ValueError("unsupported temporary emoji scope")
    await execute(
        """
        INSERT INTO public.schedule_temporary_emoji_marks(
            scope, entity_key, placeholder_emoji_id, fallback, updated_by, updated_at
        ) VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (scope, entity_key) DO UPDATE
        SET placeholder_emoji_id = EXCLUDED.placeholder_emoji_id,
            fallback = EXCLUDED.fallback,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        scope,
        str(entity_key),
        int(placeholder_emoji_id),
        fallback,
        int(updated_by),
    )


async def clear_temporary_emoji(scope: str, entity_key: object) -> None:
    await execute(
        """
        DELETE FROM public.schedule_temporary_emoji_marks
        WHERE scope = $1 AND entity_key = $2
        """,
        scope,
        str(entity_key),
    )


async def is_temporary_emoji(scope: str, entity_key: object) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM public.schedule_temporary_emoji_marks
        WHERE scope = $1 AND entity_key = $2
        """,
        scope,
        str(entity_key),
    )
    return bool(row)


async def get_temporary_emoji_marks() -> list[dict[str, object]]:
    from db.core import fetch

    rows = await fetch(
        """
        SELECT scope, entity_key, placeholder_emoji_id, fallback, updated_by, updated_at
        FROM public.schedule_temporary_emoji_marks
        ORDER BY scope, entity_key
        """
    )
    return [dict(row) for row in rows]


async def temporary_emoji_counts() -> dict[str, int]:
    row = await fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE scope = 'asset') AS assets,
               COUNT(*) FILTER (WHERE scope = 'deck') AS decks,
               COUNT(*) FILTER (WHERE scope = 'card') AS cards
        FROM public.schedule_temporary_emoji_marks
        """
    )
    return {
        "assets": int(row["assets"] or 0) if row else 0,
        "decks": int(row["decks"] or 0) if row else 0,
        "cards": int(row["cards"] or 0) if row else 0,
    }


async def create_temporary_emoji(
    scope: str,
    entity_key: object,
    *,
    fallback: str,
    updated_by: int,
    upsert_asset,
    upsert_deck,
    upsert_card,
) -> int:
    placeholder = await _placeholder_emoji_id()
    if scope == "asset":
        await upsert_asset(
            str(entity_key),
            placeholder,
            fallback=fallback,
            updated_by=int(updated_by),
        )
    elif scope == "deck":
        await upsert_deck(int(entity_key), placeholder, updated_by=int(updated_by))
    elif scope == "card":
        await upsert_card(int(entity_key), placeholder, updated_by=int(updated_by))
    else:
        raise ValueError("unsupported temporary emoji scope")
    await mark_temporary_emoji(
        scope,
        entity_key,
        placeholder_emoji_id=placeholder,
        fallback=fallback,
        updated_by=updated_by,
    )
    return placeholder


async def restart_schedule_card_reviews() -> None:
    await execute(
        """
        UPDATE public.schedule_card_emojis
        SET verified = false,
            verified_by = NULL,
            verified_at = NULL,
            updated_at = now()
        """
    )


async def update_schedule_card_field(card_id: int, field: str, value: object) -> None:
    if field not in _CARD_FIELDS:
        raise ValueError(f"field is not editable: {field}")
    await execute(
        f"UPDATE public.cards SET {field} = $2 WHERE card_id = $1",
        int(card_id),
        value,
    )


async def update_schedule_deck_field(deck_id: int, field: str, value: object) -> None:
    if field not in _DECK_FIELDS:
        raise ValueError(f"field is not editable: {field}")
    await execute(
        f"UPDATE public.decks SET {field} = $2 WHERE id = $1",
        int(deck_id),
        value,
    )


__all__ = [
    "clear_temporary_emoji",
    "create_temporary_emoji",
    "get_temporary_emoji_marks",
    "is_temporary_emoji",
    "restart_schedule_card_reviews",
    "temporary_emoji_counts",
    "update_schedule_card_field",
    "update_schedule_deck_field",
]
