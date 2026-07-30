"""Entry points and panel navigation for market workflows."""

import html
import re
from typing import Optional

from aiogram import F, Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers.admin.services.market_constants import CB_KIND, CB_PREFIX, _EXTRAS_TAIL_RE, _EXTRAS_HEAD_RE, \
    STAR_DB_CODE, PAGE_CARDS, CB_PAGE, CB_SEL, CB_BACK
from bot.handlers.admin.services.market_db_helpers import fetch_card
from bot.handlers.admin.services.market_diamonds_flow import start_diamonds_currency_flow
from bot.handlers.admin.services.market_fsm import MarketAddFSM, MarketEditFSM, MarketSearchFSM
from bot.handlers.admin.services.market_keyboards import market_kind_kb, market_decks_kb, currency_multi_keyboard, \
    cash_multi_keyboard, kb_deck_mode, kb_custom_qty_choice, confirm_publish_kb, prices_menu_kb, prices_cash_menu_kb, \
    sold_confirm_kb, kb_proof_choice, kb_proof_single_skip, market_cards_kb
from bot.handlers.admin.services.market_render import build_card_preview_caption, _reload_listing_inplace, \
    _format_extra_for_summary
from bot.handlers.admin.services.market_sales import (
    _MY,
    _my_sales_enter,
    _my_sales_render,
    _my_sales_set_filter_and_show,
)
from bot.handlers.admin.services.market_service import _kb_proof_each_skip, _send_prompt
from bot.handlers.admin.services.market_utils import get_selected_ids, safe_delete, _normalize_pay_type, parse_tiers, \
    _distinct_cards_count, safe_edit_text, remove_selected_id, add_selected_id, currency_emoji, \
    validate_price_by_currency, _card_title
from bot.services.market import (
    get_all_decks,
    get_cards_by_deck,
    market_create_listing,
    market_add_listing_item,
    market_add_rate_tiers,
    market_add_items,
    market_get_rate_tiers,
    market_set_item_qty,
    market_dec_item_qty,
    market_decrement_all_items_and_total,
    market_delete_all_prices,
    market_get_cover_file_id,
    market_hard_delete_listing,
    market_has_any_proof,
    market_replace_price,
    market_search as search_market,
    market_seller_listing_summaries,
    market_set_cover,
    market_set_description,
    market_set_item_proof,
    market_set_status,
    market_toggle_named_status,
)

from bot.handlers.admin.services.market_search_flow import market_find

router = Router(name="market_flow_entry")


@router.message(Command("sell"), F.chat.type == "private")
async def sell_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await state.set_state(MarketAddFSM.CHOOSE_KIND)
    await message.answer("Что продаём?", reply_markup=market_kind_kb())


@router.message(F.chat.type == "private", F.text == "📦 Мои объявления")
@router.message(F.chat.type == "private", F.text.regexp(r"^/my_sales\b"))
async def my_sales_open(message: Message, state: FSMContext):
    await _my_sales_enter(message, state, "active")


@router.callback_query(F.data.startswith("mkt:go:"))
async def market_panel_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    _, _, action = call.data.split(":")
    await call.answer()

    if action in ("sell_cards", "sell_deck"):
        await sell_start(call.message, state, bot)
        return
    if action == "sell_currency":
        await start_diamonds_currency_flow(call.message, state)
        return
    if action == "find":
        await market_find(call.message, state)
        return
    if action == "my_sales":
        await my_sales_open(call.message, state)
        return
    if action == "help":
        await call.message.answer("Подсказка: /sell — создать, /find — поиск, /my_sales — мои объявления.")


@router.message(F.chat.type == "private", F.text == "🛒 Продать")
async def rk_sell(message: Message, state: FSMContext, bot: Bot):
    await sell_start(message, state, bot)


@router.message(F.chat.type == "private", F.text == "🔍 Поиск")
async def rk_find(message: Message, state: FSMContext):
    await market_find(message, state)


@router.message(F.text == "🛒 Продать")
async def _sell_btn(message: Message, state: FSMContext, bot: Bot):
    await sell_start(message, state, bot)

