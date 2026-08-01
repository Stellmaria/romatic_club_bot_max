"""Seller listing browser, filters, navigation and actions."""

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
from bot.telegram.callback_parser import split_callback_data

my_sales_router = Router(name="market_flow_my_sales")
router = my_sales_router


async def _show_my_sales(
        event: Message | CallbackQuery,
        user_id: int,
        state: Optional[FSMContext] = None,
        tab: Optional[str] = None,
) -> None:
    msg: Message = event.message if isinstance(event, CallbackQuery) else event

    tab_norm = (tab or "active").lower()
    allowed = {"active", "hidden", "sold", "archived", "all"}
    if tab_norm not in allowed:
        tab_norm = "active"

    statuses = ["active", "hidden", "sold", "archived"] if tab_norm == "all" else [tab_norm]

    rows = await market_seller_listing_summaries(user_id, statuses)

    title_map = {
        "active": "Активные", "hidden": "Скрытые",
        "sold": "Проданные", "archived": "Архив", "all": "Все",
    }
    total = len(rows)
    header = f"🧾 <b>Мои объявления</b> · {title_map[tab_norm]} ({total})"

    if total == 0:
        await msg.answer(f"{header}\n\nПусто.")
        return

    lines = [header, ""]
    for r in rows[:20]:
        lid = r["listing_id"]
        title = (r.get("description") or "—").strip().splitlines()[0]
        if len(title) > 64:
            title = title[:61] + "…"
        status = r.get("status") or "unknown"
        count = int(r.get("items_count") or 0)
        lines.append(f"• <code>#{lid}</code> · {title} · {status} · {count} шт.")
    if total > 20:
        lines += ["", f"Показаны первые 20 из {total}."]

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=("▪️ " if tab_norm == "active" else "▫️ ") + "Активные",
                                 callback_data="market:my:active"),
            InlineKeyboardButton(text=("▪️ " if tab_norm == "hidden" else "▫️ ") + "Скрытые",
                                 callback_data="market:my:hidden"),
            InlineKeyboardButton(text=("▪️ " if tab_norm == "sold" else "▫️ ") + "Проданные",
                                 callback_data="market:my:sold"),
        ],
        [
            InlineKeyboardButton(text=("▪️ " if tab_norm == "archived" else "▫️ ") + "Архив",
                                 callback_data="market:my:archived"),
            InlineKeyboardButton(text=("▪️ " if tab_norm == "all" else "▫️ ") + "Все", callback_data="market:my:all"),
        ],
    ])

    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            await msg.answer(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("market:my:"))
async def cb_my_sales_tabs(call: CallbackQuery, state: FSMContext):
    _, _, tab = split_callback_data(call.data, ":")
    await _show_my_sales(call, call.from_user.id, state=state, tab=tab)


def my_sales_tabs_kb(active: str, counts: dict[str, int]) -> InlineKeyboardMarkup:
    tabs = [
        ("all", "Все", "📋"),
        ("active", "Активные", "🟢"),
        ("hidden", "Скрытые", "🙈"),
        ("sold", "Продано", "✅"),
        ("archived", "Архив", "🗄"),
    ]

    def btn(code: str, label: str, emoji: str) -> InlineKeyboardButton:
        mark = "●" if code == active else "○"
        n = counts.get(code, 0)
        text = f"{mark} {emoji} {label} ({n})"
        return InlineKeyboardButton(text=text, callback_data=f"{CB_PREFIX}:mine:tab:{code}")

    rows = [
        [btn(*tabs[0]), btn(*tabs[1])],
        [btn(*tabs[2]), btn(*tabs[3])],
        [btn(*tabs[4])],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


my_sales_continuation_router = Router(name="market_flow_my_sales_continuation")
router = my_sales_continuation_router


@router.callback_query(F.data.in_({"my:nav:prev", "my:nav:next", "my:nav:close"}))
async def my_sales_nav(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    s = data.get(_MY) or {}
    ids: list[int] = list(s.get("ids") or [])
    if not ids:
        try:
            await call.message.edit_reply_markup(None)
        except:
            pass
        return
    idx = int(s.get("idx") or 0)
    if call.data == "my:nav:close":
        await call.message.delete()
        return
    total = len(ids)
    idx = (idx - 1) % total if call.data.endswith("prev") else (idx + 1) % total
    s["idx"] = idx
    await state.update_data({_MY: s})
    await _my_sales_render(call, state, edit=True)


@router.callback_query(F.data.startswith("my:act:"))
async def my_sales_actions(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    s = data.get(_MY) or {}
    ids: list[int] = list(s.get("ids") or [])
    idx = int(s.get("idx") or 0)
    if not ids:
        return
    lid = int(ids[idx])
    action = split_callback_data(call.data, ":")[2]

    if action == "proof":
        fid = await market_get_cover_file_id(lid)
        await call.message.answer_photo(fid) if fid else await call.message.answer("Фото подтверждения отсутствует.")
        return

    if action == "delete":
        await market_hard_delete_listing(lid)
        # удаляем из списка и перелистываем
        ids.pop(idx)
        if not ids:
            await state.update_data({_MY: {"ids": [], "idx": 0, "tab": s.get('tab', 'active')}})
            await call.message.edit_text("Все объявления удалены.")
            return
        idx = 0 if idx >= len(ids) else idx
        await state.update_data({_MY: {"ids": ids, "idx": idx, "tab": s.get('tab', 'active')}})
        await _my_sales_render(call, state, edit=True)
        return

    # стейт-машина статусов
    if action == "toggle_hidden":
        await market_toggle_named_status(lid, "hidden")

    if action == "toggle_archive":
        await market_toggle_named_status(lid, "archived")

    if action == "toggle_sold":
        await market_toggle_named_status(lid, "sold")

    # edit текущей карточки
    await _my_sales_render(call, state, edit=True)


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?(Активные|Скрытые|Проданные|Архив|Все)$"))
async def my_sales_filter_click(message: Message, state: FSMContext):
    mapping = {"Активные": "active", "Скрытые": "hidden", "Проданные": "sold", "Архив": "archived", "Все": "all"}
    name = message.text.replace("▪️ ", "").replace("▫️ ", "")
    await _my_sales_enter(message, state, mapping[name])


# Compatibility: the primary fragment remains the module-level router.
router = my_sales_router

