"""Administrative auction context assembly."""

from __future__ import annotations

from typing import Any

from bot.repositories.admin_auctions import AdminAuctionRepository
from db.pool import get_db_pool


class AdminAuctionContextService:
    def __init__(self, repository: AdminAuctionRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AdminAuctionContextService":
        return cls(AdminAuctionRepository(await get_db_pool()))

    async def load_full_context(self, auction_id: int) -> dict[str, dict[str, Any]]:
        row = await self._repository.full_context_row(int(auction_id))
        if not row:
            return {"auction": {}, "card": {}, "deck": {}}

        sellers_total = int(row.get("sellers_total") or 0)
        sellers_verified = int(row.get("sellers_verified") or 0)
        seller_verified = bool(sellers_total and sellers_verified == sellers_total)

        return {
            "auction": {
                "auction_id": row["auction_id"],
                "hero_name": row["hero_name"],
                "card_name": row["card_name"],
                "start_price": row["start_price"],
                "currency": row["currency"],
                "end_time": row["end_time"],
                "comment": row["comment"],
                "status": row["status"],
                "owners_count": row["owners_count"],
                "auction_kind": row.get("auction_kind"),
                "craft_uid_possible": row.get("craft_uid_possible"),
                "sellers_total": sellers_total,
                "sellers_verified": sellers_verified,
                "seller_verified": seller_verified,
            },
            "card": {
                "card_id": row.get("card_id"),
                "hero_name": row.get("c_hero"),
                "card_name": row.get("c_name"),
                "num": row.get("num"),
                "rarity": row.get("rarity"),
                "obtain_type": row.get("obtain_type"),
                "obtain_amount": row.get("obtain_amount"),
                "story": row.get("story"),
                "quote": row.get("quote"),
                "image_id": row.get("card_image_id"),
            },
            "deck": {
                "deck_id": row.get("deck_id"),
                "name": row.get("deck_name"),
            },
        }
