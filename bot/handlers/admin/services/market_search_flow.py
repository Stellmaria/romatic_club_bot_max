"""Market search conversation and pure result formatting."""

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

router = Router(name="market_flow_search")


@router.message(Command("find"), F.chat.type == "private")
async def market_find(message: Message, state: FSMContext):
    await state.set_state(MarketSearchFSM.ASK_QUERY)
    await message.answer("Введи запрос (имя героя/карты) или «-» чтобы пропустить.")


@router.message(MarketSearchFSM.ASK_QUERY)
async def market_find_query(message: Message, state: FSMContext):
    q = None if (message.text or "").strip() == "-" else (message.text or "").strip()
    await state.update_data(q=q)
    await state.set_state(MarketSearchFSM.ASK_FILTERS)
    await message.answer(
        "Фильтры одной строкой. Пример:\n"
        "<code>kind=cards deck=14 rarity=gold cur=diamonds cash=BYN min=10 max=100</code>",
        parse_mode="HTML",
    )


@router.message(MarketSearchFSM.ASK_FILTERS)
async def market_find_filters(message: Message, state: FSMContext):
    s = (message.text or "").lower()

    def _kv(key: str) -> Optional[str]:
        for part in s.split():
            if part.startswith(key + "="):
                return part.split("=", 1)[1]
        return None

    deck_id = _kv("deck")
    rarity = _kv("rarity")
    cur = _kv("cur")
    cash_code = _kv("cash")
    kind = _kv("kind")
    mn = _kv("min")
    mx = _kv("max")

    try:
        deck_id_i = int(deck_id) if deck_id and deck_id.isdigit() else None
    except Exception:
        deck_id_i = None
    price_min = float(mn.replace(",", ".")) if mn else None
    price_max = float(mx.replace(",", ".")) if mx else None

    q = (await state.get_data()).get("q")

    rows = await market_search(
        deck_id=deck_id_i, rarity=rarity, q=q, currency=cur, cash_code=cash_code,
        offer_kind=kind, price_min=price_min, price_max=price_max, limit=30, offset=0,
    )
    await state.clear()
    if not rows:
        await message.answer("Ничего не найдено.")
        return

    shown = 0
    printed_lids = set()
    for r in rows:
        lid = r["listing_id"]
        if lid not in printed_lids:
            printed_lids.add(lid)
            await message.answer(f"<b>Объявление #{lid}</b>", parse_mode="HTML")
        await message.answer(fmt_search_row(r), parse_mode="HTML")
        shown += 1
        if shown >= 50:
            break


async def market_search(
        *,
        deck_id: Optional[int] = None,
        rarity: Optional[str] = None,
        q: Optional[str] = None,
        currency: Optional[str] = None,
        cash_code: Optional[str] = None,
        offer_kind: Optional[str] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        limit: int = 20,
        offset: int = 0,
) -> list[dict]:
    return await search_market(
        deck_id=deck_id,
        rarity=rarity,
        q=q,
        currency=currency,
        cash_code=cash_code,
        offer_kind=offer_kind,
        price_min=price_min,
        price_max=price_max,
        limit=limit,
        offset=offset,
    )


def fmt_search_row(row: dict) -> str:
    cur = row["currency_type"];
    price = row["price_num"]
    if cur == "cash":
        price_str = f"{price:.2f} {(row.get('cash_code') or 'CUR')}";
        emoji = "💵"
    elif cur == "cups":
        price_str = f"{int(price)}";
        emoji = "☕"
    elif cur == "diamonds":
        price_str = f"{int(price)}";
        emoji = "💎"
    else:
        price_str = f"{int(price)}";
        emoji = "🏴‍☠️"
    title = f"{row.get('hero_name') or ''} — {row.get('card_name') or ''}".strip(" —")
    deck = row.get("deck_id") or "?"
    rru = row.get("rarity") or "?"
    return f"{emoji} <b>{html.escape(title)}</b> · колода #{deck} · {rru} · <i>{price_str}</i>"

