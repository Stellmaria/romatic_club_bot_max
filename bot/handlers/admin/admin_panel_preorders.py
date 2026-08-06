"""Structured preorder queue for the admin moderation panel."""

from __future__ import annotations

import html
from collections.abc import Mapping

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.domain.preorders import (
    PREORDER_MODE_WHOLE_DECK,
    format_preorder_composition,
    normalize_preorder_items,
    normalize_preorder_mode,
)
from bot.handlers.admin import admin_panel_requests
from bot.handlers.admin.action_support.transport import send_lot_card_safe
from bot.handlers.admin.helper.admin_constants import RARITY_EMOJI
from bot.handlers.admin.helper.new.formatting import format_pending_lot
from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_owners import get_lot_owners_with_levels
from bot.services.preorder_submissions import PreorderSubmissionService

router = Router(name=__name__)


def _register_preorder_kind() -> None:
    admin_panel_requests.ADMIN_AUK_KIND_LABELS["preorder"] = "🗓 Предзаказ"
    order = admin_panel_requests.ADMIN_AUK_KIND_ORDER
    if "preorder" not in order:
        exchange_index = order.index("exchange") if "exchange" in order else len(order)
        order.insert(exchange_index, "preorder")


_register_preorder_kind()


def _items_from_row(row: Mapping[str, object]) -> dict[str, int]:
    raw = row.get("preorder_items")
    return normalize_preorder_items(raw if isinstance(raw, Mapping) else {})


def _preorder_details(row: Mapping[str, object]) -> str:
    mode = normalize_preorder_mode(row.get("preorder_mode"))
    items = _items_from_row(row)
    if mode == PREORDER_MODE_WHOLE_DECK:
        mode_label = "целая колода"
        composition = "вся будущая колода"
    else:
        mode_label = "карты по редкостям"
        composition = format_preorder_composition(items) or "—"

    item_lines = ""
    if items:
        item_lines = "\n" + "\n".join(
            f"{RARITY_EMOJI.get(rarity, '🃏')} {html.escape(rarity)}: {quantity}"
            for rarity, quantity in items.items()
        )

    deck_id = html.escape(str(row.get("preorder_deck_id") or "—"))
    deck_name = html.escape(
        str(row.get("preorder_deck_name") or "Будущая колода")
    )
    return (
        "\n\n🗓 <b>Данные предзаказа</b>\n"
        f"🗂 <b>Будущая колода:</b> №{deck_id} — {deck_name}\n"
        f"⚙️ <b>Режим:</b> {mode_label}\n"
        f"🃏 <b>Состав:</b> {html.escape(composition)}"
        f"{item_lines}"
    )


@router.callback_query(F.data == "admreq|pending|preorder")
@admin_only
async def show_pending_preorders(call: CallbackQuery) -> None:
    await call.answer()
    message = call.message
    if not isinstance(message, types.Message):
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    rows = await (await PreorderSubmissionService.create()).list_pending(limit=50)
    if not rows:
        await message.answer("✅ Нет заявок на предзаказ.")
        return

    for source in rows:
        lot = dict(source)
        lot.setdefault("status", "pending")
        auction_id = int(lot.get("auction_id") or 0)
        owners = await get_lot_owners_with_levels(message.bot, auction_id)
        text = format_pending_lot(lot, owners) + _preorder_details(lot)
        keyboard = build_lot_keyboard(lot, role="admin", show_proof=True)
        await send_lot_card_safe(message, lot, text, keyboard)


__all__ = ["router", "show_pending_preorders"]
