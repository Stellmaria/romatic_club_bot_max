from __future__ import annotations

"""Auction exchange package.

Phase 7 separates the former handler monolith into focused routers while
preserving the historic import surface for the rest of the application.
"""

from aiogram import Router

from .submission import router as submission_router
from .moderation import router as moderation_router
from .catalog import router as catalog_router
from .diagnostics import router as diagnostics_router

router = Router(name="auction_exchange")
router.include_router(submission_router)
router.include_router(moderation_router)
router.include_router(catalog_router)
router.include_router(diagnostics_router)

from .common import (  # noqa: E402,F401
    EX_WHOLE_DECK_PRICE,
    _deck_id_from_row as exchange_deck_id_from_row,
    _exchange_gain_for_card as exchange_gain_for_card,
    _exchange_price_for_card as exchange_price_for_card,
    _get_exchange_deck_ids as get_exchange_deck_ids,
    _get_exchange_decks_for_menu as get_exchange_decks_for_menu,
    _tg_clean as clean_telegram_text,
    currency_to_emoji,
    exchange_deck_keyboard,
)
from .moderation import (  # noqa: E402,F401
    _media_kind_from_error,
    format_pending_exchange_batch_card,
    pending_exchange_kb,
    show_pending_exchange_requests,
    show_pending_exchange_requests_all,
)
from .catalog import (  # noqa: E402,F401
    _format_exchange_approved_lot_caption,
    _kb_exchange_approved_decks,
    _kb_exchange_approved_lot_actions,
    _kb_exchange_approved_root,
    _q_exchange_approved_decks,
    _q_exchange_whole_deck_batches,
    _safe_edit_text_or_caption,
)

__all__ = [
    "router",
    "currency_to_emoji",
    "exchange_deck_keyboard",
    "exchange_deck_id_from_row",
    "exchange_gain_for_card",
    "exchange_price_for_card",
    "get_exchange_deck_ids",
    "get_exchange_decks_for_menu",
    "clean_telegram_text",
    "format_pending_exchange_batch_card",
    "pending_exchange_kb",
    "show_pending_exchange_requests",
    "show_pending_exchange_requests_all",
]
