from __future__ import annotations

from typing import Any

from db.core import get_db_pool

__all__ = [
    "normalize_target_kind",
    "normalize_media_type",
    "normalize_target_key",
    "get_media_asset",
    "upsert_media_asset",
    "delete_media_asset",
    "list_media_assets",
]

from bot.domain.media_assets import (
    VALID_MEDIA_TYPES,
    VALID_RARITIES,
    VALID_TARGET_KINDS,
    normalize_media_type,
    normalize_target_key,
    normalize_target_kind,
)

async def _target_description(conn: Any, kind: str, key: str) -> str:
    if kind == "deck":
        row = await conn.fetchrow("SELECT id, name FROM public.decks WHERE id=$1", int(key))
        if not row:
            raise LookupError("deck_not_found")
        return f"Колода #{row['id']} — {row['name']}"

    if kind == "card":
        row = await conn.fetchrow(
            "SELECT card_id, hero_name, card_name FROM public.cards WHERE card_id=$1",
            int(key),
        )
        if not row:
            raise LookupError("card_not_found")
        title = " — ".join(x for x in (row["hero_name"], row["card_name"]) if x)
        return f"Карта #{row['card_id']} — {title or 'без названия'}"

    if kind == "auction":
        row = await conn.fetchrow(
            "SELECT auction_id, card_name FROM public.auctions WHERE auction_id=$1",
            int(key),
        )
        if not row:
            raise LookupError("auction_not_found")
        return f"Аукцион #{row['auction_id']} — {row['card_name']}"

    if kind == "rarity":
        return f"Редкость: {key}"
    if kind == "service":
        return f"Услуга: {key}"
    if kind == "spins":
        return f"Кручения: {key}"
    return f"Медиа по умолчанию: {key}"


async def get_media_asset(target_kind: str, target_key: str | int) -> dict[str, Any] | None:
    kind = normalize_target_kind(target_kind)
    key = normalize_target_key(kind, target_key)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT asset_id,
                   target_kind,
                   target_key,
                   media_type,
                   file_id,
                   file_unique_id,
                   thumb_file_id,
                   updated_by,
                   created_at,
                   updated_at
            FROM public.auction_media_assets
            WHERE target_kind=$1 AND target_key=$2
            """,
            kind,
            key,
        )
    return dict(row) if row else None


async def upsert_media_asset(
    *,
    target_kind: str,
    target_key: str | int,
    file_id: str,
    media_type: str = "photo",
    file_unique_id: str | None = None,
    thumb_file_id: str | None = None,
    updated_by: int | None = None,
) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind)
    key = normalize_target_key(kind, target_key)
    media_type = normalize_media_type(media_type)
    file_id = (file_id or "").strip()
    if not file_id:
        raise ValueError("empty_file_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        description = await _target_description(conn, kind, key)
        row = await conn.fetchrow(
            """
            INSERT INTO public.auction_media_assets (
                target_kind,
                target_key,
                media_type,
                file_id,
                file_unique_id,
                thumb_file_id,
                updated_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (target_kind, target_key)
            DO UPDATE SET media_type=EXCLUDED.media_type,
                          file_id=EXCLUDED.file_id,
                          file_unique_id=EXCLUDED.file_unique_id,
                          thumb_file_id=EXCLUDED.thumb_file_id,
                          updated_by=EXCLUDED.updated_by,
                          updated_at=now()
            RETURNING asset_id,
                      target_kind,
                      target_key,
                      media_type,
                      file_id,
                      file_unique_id,
                      thumb_file_id,
                      updated_by,
                      created_at,
                      updated_at
            """,
            kind,
            key,
            media_type,
            file_id,
            (file_unique_id or None),
            (thumb_file_id or None),
            int(updated_by) if updated_by else None,
        )

        synced_rows = 0
        sync_warning: str | None = None
        try:
            if kind == "card":
                status = await conn.execute(
                    """
                    UPDATE public.cards
                    SET image_id=$2,
                        media_type=$3,
                        media_file_id=$2,
                        media_unique_id=$4,
                        thumb_file_id=$5
                    WHERE card_id=$1
                    """,
                    int(key),
                    file_id,
                    media_type,
                    (file_unique_id or None),
                    (thumb_file_id or None),
                )
                synced_rows = int(status.rsplit(" ", 1)[-1])
            elif kind == "auction":
                status = await conn.execute(
                    "UPDATE public.auctions SET image_id=$2 WHERE auction_id=$1",
                    int(key),
                    file_id,
                )
                synced_rows = int(status.rsplit(" ", 1)[-1])
        except Exception as exc:  # registry remains authoritative even for dirty legacy rows
            sync_warning = str(exc).splitlines()[0][:500]

    result = dict(row)
    result.update(
        description=description,
        synced_rows=synced_rows,
        sync_warning=sync_warning,
    )
    return result


async def delete_media_asset(target_kind: str, target_key: str | int) -> bool:
    kind = normalize_target_kind(target_kind)
    key = normalize_target_key(kind, target_key)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM public.auction_media_assets WHERE target_kind=$1 AND target_key=$2",
            kind,
            key,
        )
    return status.endswith(" 1")


async def list_media_assets(target_kind: str | None = None) -> list[dict[str, Any]]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if target_kind:
            kind = normalize_target_kind(target_kind)
            rows = await conn.fetch(
                """
                SELECT target_kind, target_key, media_type, file_id, updated_by, updated_at
                FROM public.auction_media_assets
                WHERE target_kind=$1
                ORDER BY target_key
                """,
                kind,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT target_kind, target_key, media_type, file_id, updated_by, updated_at
                FROM public.auction_media_assets
                ORDER BY target_kind, target_key
                """
            )
    return [dict(row) for row in rows]
