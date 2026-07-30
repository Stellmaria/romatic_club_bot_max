"""Exchange cover-media use case."""

from __future__ import annotations

from bot.repositories.exchange_media import ExchangeMediaRepository
from db.core import get_db_pool


async def get_exchange_cover_media(batch_id: int) -> tuple[str | None, str]:
    """Return the first card media, then the proof photo as fallback."""
    repository = ExchangeMediaRepository(await get_db_pool())
    return await repository.cover_media(int(batch_id))
