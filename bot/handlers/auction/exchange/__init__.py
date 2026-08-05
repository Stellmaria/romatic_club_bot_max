"""Public composition package for the auction exchange feature."""

# Public compatibility exports are intentionally grouped by feature module.
# ruff: noqa: E402, I001, SLF001

from __future__ import annotations

from aiogram import Router

from . import common as _common

_EXCHANGE_DECK_IDS = (22, 24, 26, 28)


async def _fixed_exchange_deck_ids(_decks: list[dict] | None = None) -> list[int]:
    """Return the exact deck set supported by the MAX exchange."""
    return list(_EXCHANGE_DECK_IDS)


async def _fixed_exchange_decks_for_menu() -> list[dict]:
    """Build exchange deck rows with labels matching their real IDs."""
    try:
        decks_all = await _common.get_all_decks()
    except Exception:
        decks_all = []

    by_id = {
        _common._deck_id_from_row(deck): dict(deck)
        for deck in decks_all or []
        if _common._deck_id_from_row(deck)
    }

    result: list[dict] = []
    for deck_id in _EXCHANGE_DECK_IDS:
        row = by_id.get(deck_id, {})
        row["deck_id"] = deck_id
        row["name"] = f"{deck_id} колода"
        result.append(row)
    return result


# Keep every exchange entry point on the same fixed deck policy. This also
# prevents a newer or incorrectly named DB row from replacing deck 22 or
# displaying deck 29 under callback ID 28.
_common.EXCHANGE_RESOURCE_DECK_LIMIT = len(_EXCHANGE_DECK_IDS)
_common.EX_DECKS = list(_EXCHANGE_DECK_IDS)
_common._get_exchange_deck_ids = _fixed_exchange_deck_ids
_common.get_exchange_deck_ids = _fixed_exchange_deck_ids
_common._get_exchange_decks_for_menu = _fixed_exchange_decks_for_menu
_common.get_exchange_decks_for_menu = _fixed_exchange_decks_for_menu

from .catalog import (
    format_exchange_approved_lot_caption,
    kb_exchange_approved_decks,
    kb_exchange_approved_lot_actions,
    kb_exchange_approved_root,
    q_exchange_approved_decks,
    q_exchange_whole_deck_batches,
    router as catalog_router,
    safe_edit_text_or_caption,
)
from .common import (
    EX_MODE_CARD,
    EX_MODE_CARDLIKE,
    EX_MODE_DECK,
    EX_MODE_DECK_SPLIT,
    EX_STATUS_APPROVED,
    EX_WHOLE_DECK_PRICE,
    clean_telegram_text,
    cur_emoji,
    currency_emoji,
    currency_label,
    currency_to_emoji,
    deck_id_from_row as exchange_deck_id_from_row,
    exchange_deck_keyboard,
    exchange_gain_for_card,
    exchange_price_for_card,
    get_exchange_deck_ids,
    get_exchange_decks_for_menu,
)
from .diagnostics import router as diagnostics_router
from .editor import router as editor_router
from .moderation import (
    format_pending_exchange_batch_card,
    media_kind_from_error,
    pending_exchange_kb,
    router as moderation_router,
    show_pending_exchange_requests,
    show_pending_exchange_requests_all,
)
from .moderation_queue import ContinuePendingExchangeQueueMiddleware
from .submission import router as submission_router

moderation_router.callback_query.middleware(ContinuePendingExchangeQueueMiddleware())

router = Router(name="auction_exchange")
router.include_router(submission_router)
router.include_router(moderation_router)
router.include_router(catalog_router)
router.include_router(editor_router)
router.include_router(diagnostics_router)

# Compatibility aliases for code outside handlers that has not migrated yet.
_cur_emoji = cur_emoji
_currency_emoji = currency_emoji
_currency_label = currency_label
_get_exchange_deck_ids = get_exchange_deck_ids

__all__ = [  # noqa: RUF022
    "router",
    "EX_MODE_CARD",
    "EX_MODE_CARDLIKE",
    "EX_MODE_DECK",
    "EX_MODE_DECK_SPLIT",
    "EX_STATUS_APPROVED",
    "EX_WHOLE_DECK_PRICE",
    "clean_telegram_text",
    "cur_emoji",
    "currency_emoji",
    "currency_label",
    "currency_to_emoji",
    "exchange_deck_keyboard",
    "exchange_deck_id_from_row",
    "exchange_gain_for_card",
    "exchange_price_for_card",
    "format_exchange_approved_lot_caption",
    "format_pending_exchange_batch_card",
    "get_exchange_deck_ids",
    "get_exchange_decks_for_menu",
    "kb_exchange_approved_decks",
    "kb_exchange_approved_lot_actions",
    "kb_exchange_approved_root",
    "media_kind_from_error",
    "pending_exchange_kb",
    "q_exchange_approved_decks",
    "q_exchange_whole_deck_batches",
    "safe_edit_text_or_caption",
    "show_pending_exchange_requests",
    "show_pending_exchange_requests_all",
    # Deprecated compatibility exports.
    "_cur_emoji",
    "_currency_emoji",
    "_currency_label",
    "_get_exchange_deck_ids",
]
