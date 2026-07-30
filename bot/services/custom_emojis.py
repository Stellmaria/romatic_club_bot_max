"""Named custom-emoji use cases for Telegram commands."""

from __future__ import annotations

from typing import Any

from bot.repositories.custom_emojis import CustomEmojiRepository
from db.pool import get_db_pool


class CustomEmojiService:
    def __init__(self, repository: CustomEmojiRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "CustomEmojiService":
        return cls(CustomEmojiRepository(await get_db_pool()))

    async def ensure_schema(self) -> None:
        await self._repository.ensure_schema()

    async def save(self, name: str, emoji_id: str) -> None:
        normalized_name = (name or "").strip().lower()
        normalized_id = (emoji_id or "").strip()
        if not normalized_name:
            raise ValueError("emoji name is required")
        if not normalized_id:
            raise ValueError("custom emoji id is required")
        await self._repository.upsert(name=normalized_name, emoji_id=normalized_id)

    async def list_all(self) -> list[dict[str, Any]]:
        return await self._repository.list_all()

    async def delete(self, name: str) -> str | None:
        normalized_name = (name or "").strip().lower()
        if not normalized_name:
            return None
        return await self._repository.delete(normalized_name)


__all__ = ["CustomEmojiService"]
