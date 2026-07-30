"""Compatibility helpers for marketplace handlers.

Database access is owned by :mod:`bot.services.market`; this historical module
keeps old handler imports stable while the flow modules are being separated.
"""

from __future__ import annotations

from aiogram.fsm.context import FSMContext

from bot.services.market import fetch_card as _fetch_card
from bot.services.market import market_persist_proofs


async def persist_proofs(listing_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    await market_persist_proofs(
        int(listing_id),
        proof_file_id=data.get("proof_file_id") or data.get("cover_file_id"),
        proof_by_card=dict(data.get("proof_by_card") or {}),
    )


async def fetch_card(card_id: int) -> dict:
    return await _fetch_card(int(card_id))


async def _db_exec(*_args, **_kwargs) -> None:
    """Retired generic SQL escape hatch; kept only for compatibility."""
    raise RuntimeError("market handlers must use named MarketService operations")


__all__ = ["_db_exec", "fetch_card", "persist_proofs"]
