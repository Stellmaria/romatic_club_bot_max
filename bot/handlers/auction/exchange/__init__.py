from __future__ import annotations

"""Auction exchange compatibility package."""

from aiogram import Router

from .submission import router as submission_router
from .moderation import router as moderation_router
from .catalog import router as catalog_router
from .editor import router as editor_router
from .diagnostics import router as diagnostics_router
from .common import (  # noqa: F401
    EX_MODE_CARD,
    EX_MODE_CARDLIKE,
    EX_MODE_DECK,
    EX_MODE_DECK_SPLIT,
    EX_STATUS_APPROVED,
    EX_WHOLE_DECK_PRICE,
    _cur_emoji,
    _currency_emoji,
    _currency_label,
    _deck_id_from_row as exchange_deck_id_from_row,
    _exchange_gain_for_card as exchange_gain_for_card,
    _exchange_price_for_card as exchange_price_for_card,
    _get_exchange_deck_ids,
    _get_exchange_deck_ids as get_exchange_deck_ids,
    _get_exchange_deck_ids as get_exchange_deck_ids,
    _get_exchange_decks_for_menu as get_exchange_decks_for_menu,
    _tg_clean as clean_telegram_text,
    currency_to_emoji,
    exchange_deck_keyboard,
)
from .moderation import (  # noqa: F401
    _media_kind_from_error,
    format_pending_exchange_batch_card,
    pending_exchange_kb,
    show_pending_exchange_requests,
    show_pending_exchange_requests_all,
)
from .catalog import (  # noqa: F401
    _format_exchange_approved_lot_caption,
    _kb_exchange_approved_decks,
    _kb_exchange_approved_lot_actions,
    _kb_exchange_approved_root,
    _q_exchange_approved_decks,
    _q_exchange_whole_deck_batches,
    _safe_edit_text_or_caption,
)

router = Router(name="auction_exchange")
router.include_router(submission_router)
router.include_router(moderation_router)
router.include_router(catalog_router)
router.include_router(editor_router)
router.include_router(diagnostics_router)

__all__ = [
    "router",
    "EX_MODE_CARD",
    "EX_MODE_CARDLIKE",
    "EX_MODE_DECK",
    "EX_MODE_DECK_SPLIT",
    "EX_STATUS_APPROVED",
    "_cur_emoji",
    "_currency_emoji",
    "_currency_label",
    "_get_exchange_deck_ids",
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
