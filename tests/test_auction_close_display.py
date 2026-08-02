from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from bot.services.admin_auctions import AdminAuctionContextService


ROOT = Path(__file__).resolve().parents[1]


class _Repository:
    async def full_context_row(self, auction_id: int):
        assert auction_id == 7
        return {
            "auction_id": 7,
            "hero_name": "Героиня",
            "card_name": "Карта",
            "start_price": 100,
            "currency": "алмазы",
            "end_time": datetime(2026, 8, 2, 18, 30, 59),
            "comment": "-",
            "status": "active",
            "owners_count": 1,
            "auction_kind": "standard",
            "craft_uid_possible": False,
            "sellers_total": 1,
            "sellers_verified": 1,
        }


def test_publication_context_displays_next_minute_as_close_time() -> None:
    context = asyncio.run(AdminAuctionContextService(_Repository()).load_full_context(7))

    assert context["auction"]["end_time_str"] == "18:31"


def test_auction_caption_prefers_explicit_close_label() -> None:
    source = (
        ROOT / "bot" / "handlers" / "admin" / "helper" / "admin_constants.py"
    ).read_text(encoding="utf-8")

    assert 'auction.get("end_time_str") or _fmt_time_hhmm_msk' in source
