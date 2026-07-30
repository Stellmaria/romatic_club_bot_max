from __future__ import annotations

from typing import Any

from bot.domain.media_assets import (
    infer_media_type,
    normalize_target_key,
    normalize_target_kind,
)
from db.repositories.media_assets import (
    delete_media_asset,
    get_media_asset,
    list_media_assets,
    upsert_media_asset,
)


async def resolve_media_file_id(
    target_kind: str,
    target_key: str | int,
    *,
    fallback: str | None = None,
) -> str | None:
    try:
        asset = await get_media_asset(target_kind, target_key)
    except Exception:
        # Migrations may not yet be applied during a rolling deployment.
        asset = None
    if asset:
        file_id = str(asset.get("file_id") or "").strip()
        if file_id:
            return file_id
    fallback_value = str(fallback or "").strip()
    return fallback_value or None


async def resolve_media_asset(
    target_kind: str,
    target_key: str | int,
) -> dict[str, Any] | None:
    return await get_media_asset(target_kind, target_key)


async def configure_media_asset(
    *,
    target_kind: str,
    target_key: str | int,
    file_id: str,
    media_type: str | None = None,
    file_unique_id: str | None = None,
    thumb_file_id: str | None = None,
    updated_by: int | None = None,
) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind)
    key = normalize_target_key(kind, target_key)
    normalized_type = infer_media_type(file_id, media_type)
    return await upsert_media_asset(
        target_kind=kind,
        target_key=key,
        file_id=file_id,
        media_type=normalized_type,
        file_unique_id=file_unique_id,
        thumb_file_id=thumb_file_id,
        updated_by=updated_by,
    )


async def remove_media_asset(target_kind: str, target_key: str | int) -> bool:
    return await delete_media_asset(target_kind, target_key)


async def get_configured_media(target_kind: str | None = None) -> list[dict[str, Any]]:
    return await list_media_assets(target_kind)
