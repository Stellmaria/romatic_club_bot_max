"""Application service for auction guide reactions and access checks."""

from __future__ import annotations

from bot.repositories.guides import GuideThanksRepository
from db.pool import get_db_pool


class GuideThanksService:
    def __init__(self, repository: GuideThanksRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "GuideThanksService":
        return cls(GuideThanksRepository(await get_db_pool()))

    async def ensure_schema(self) -> None:
        await self._repository.ensure_schema()

    async def totals(self) -> tuple[int, int]:
        return await self._repository.totals()

    async def increment(
        self,
        *,
        user_id: int,
        author: str | None = None,
    ) -> tuple[int, int]:
        return await self._repository.increment(user_id=int(user_id), author=author)

    async def increment_admin(self, *, author: str, user_id: int) -> None:
        await self._repository.increment_admin(author=author, user_id=int(user_id))

    async def reset(self) -> None:
        await self._repository.reset()

    async def admin_page(
        self,
        page: int,
        *,
        page_size: int,
    ) -> tuple[list[tuple[str, int, int]], int]:
        return await self._repository.admin_page(page=int(page), page_size=int(page_size))

    async def is_luxury_user(self, user_id: int) -> bool:
        return await self._repository.is_luxury_user(int(user_id))
