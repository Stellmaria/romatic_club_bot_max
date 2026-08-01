"""Listing edit, deletion and sold-state actions."""

import html
import re
from typing import Optional

from aiogram import F, Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers.admin.services.market_constants import CB_KIND, CB_PREFIX, EXTRAS_TAIL_RE, EXTRAS_HEAD_RE, \
    STAR_DB_CODE, PAGE_CARDS, CB_PAGE, CB_SEL, CB_BACK
from bot.handlers.admin.services.market_db_helpers import fetch_card
from bot.handlers.admin.services.market_diamonds_flow import start_diamonds_currency_flow
from bot.handlers.admin.services.market_fsm import MarketAddFSM, MarketEditFSM, MarketSearchFSM
from bot.handlers.admin.services.market_keyboards import market_kind_kb, market_decks_kb, currency_multi_keyboard, \
    cash_multi_keyboard, kb_deck_mode, kb_custom_qty_choice, confirm_publish_kb, prices_menu_kb, prices_cash_menu_kb, \
    sold_confirm_kb, kb_proof_choice, kb_proof_single_skip, market_cards_kb
from bot.handlers.admin.services.market_render import build_card_preview_caption, reload_listing_inplace, \
    format_extra_for_summary
from bot.handlers.admin.services.market_sales import (
    MY,
    my_sales_enter,
    my_sales_render,
    my_sales_set_filter_and_show,
)
from bot.handlers.admin.services.market_service import kb_proof_each_skip, send_prompt
from bot.handlers.admin.services.market_utils import get_selected_ids, safe_delete, normalize_pay_type, parse_tiers, \
    distinct_cards_count, safe_edit_text, remove_selected_id, add_selected_id, currency_emoji, \
    validate_price_by_currency, card_title
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

router = Router(name="market_flow_edit")


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:edit:"))
async def edit_action(call: CallbackQuery, state: FSMContext):
    _, _, sub, lid_str = split_callback_data(call.data, ":")
    lid = int(lid_str)
    parts = split_callback_data(call.data, ":")
    action = parts[2]
    lid = int(parts[-1])

    if len(parts) < 4 or parts[1] != "act":
        await call.answer()
        return
    _, _, action, *rest = parts
    lid_str = rest[0] if rest else None
    if not lid_str or not lid_str.isdigit():
        await call.answer("Некорректный идентификатор", show_alert=True)
        return
    lid = int(lid_str)

    if sub == "back":
        await call.answer("Возврат")
        try:
            await call.message.delete()
        except Exception:
            pass
        return

    if sub == "qty":
        await state.update_data(edit_lid=lid)
        await state.set_state(MarketEditFSM.QTY)
        await call.message.answer("Введи новое количество (целое число ≥ 0).")
        await call.answer();
        return

    if sub == "photo":
        await state.update_data(edit_lid=lid)
        await state.set_state(MarketEditFSM.PHOTO)
        await call.message.answer("Пришли новое фото подтверждения или нажми «Отмена».")
        await call.answer();
        return

    if sub == "desc":
        await state.update_data(edit_lid=lid)
        await state.set_state(MarketEditFSM.DESC)
        await call.message.answer("Отправь новый текст описания или «-» чтобы очистить.")
        await call.answer();
        return

    if action == "prices":
        await call.message.answer("Какую цену правим?", reply_markup=prices_menu_kb(lid))
        await call.answer();
        return

    if action == "pricecash":
        await call.message.answer("Выбери валюту:", reply_markup=prices_cash_menu_kb(lid))
        await call.answer();
        return

    if action == "clearprices":
        await _delete_all_prices(lid)
        await call.answer("Цены сброшены")
        await reload_listing_inplace(call, lid)
        return

    if action == "price":
        ptype = parts[3]
        cash_code = None
        pay_type = ptype
        if ptype.startswith("cash-"):
            pay_type = "cash"
            cash_code = ptype.split("-", 1)[1].upper()

        await state.update_data(edit_lid=lid, pay_type=pay_type, cash_code=cash_code)
        await state.set_state(MarketEditFSM.PRICE)

        hint = "целое число" if pay_type in ("cups", "diamonds", "treasures") else "число (2.50)"
        unit = {"cups": "чашек", "diamonds": "алмазов", "treasures": "сокровищ", "cash": "в выбранной валюте"}.get(
            pay_type, "")
        text = f"Введи новую цену ({hint}) для {unit}. Или пришли «-» чтобы удалить."
        await call.message.answer(text)
        await call.answer();
        return
    await call.answer()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:do:del:"))
async def do_delete_listing(call: CallbackQuery, state: FSMContext, bot: Bot):
    _, _, _, verdict, lid_str = split_callback_data(call.data, ":")
    lid = int(lid_str)

    if verdict == "no":
        await safe_delete(call.message)
        await call.answer("Отменено")
        return

    try:
        await market_hard_delete_listing(lid)
    except Exception:
        await market_set_status(lid, "archived")

    data = await state.get_data()
    await safe_delete(bot=bot,
                      chat_id=(data.get("_del_chat_id") or call.message.chat.id),
                      message_id=data.get("_del_msg_id"))

    await safe_delete(call.message)

    await call.answer("Объявление удалено")


@router.message(MarketEditFSM.PHOTO, F.photo)
async def set_photo_message(message: Message, state: FSMContext):
    data = await state.get_data()
    lid = int(data.get("edit_lid"))
    fid = message.photo[-1].file_id
    await market_set_cover(lid, fid)
    await message.answer("Фото подтверждения обновлено.")
    await state.clear()


@router.message(MarketEditFSM.DESC, F.text)
async def set_desc_message(message: Message, state: FSMContext):
    data = await state.get_data()
    lid = int(data.get("edit_lid"))
    desc = (message.text or "").strip() or None

    await market_set_description(lid, desc)
    await message.answer("Описание обновлено.")
    await state.clear()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:sold:"))
async def cb_mark_sold(call: CallbackQuery):
    _, _, lid_str = split_callback_data(call.data, ":")
    lid = int(lid_str)
    await call.message.answer("Подтверди продажу. Уменьшить количество в объявлении на 1?",
                              reply_markup=sold_confirm_kb(lid))
    await call.answer()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:sold_yes:"))
async def cb_mark_sold_yes(call: CallbackQuery):
    _, _, _, lid_str = split_callback_data(call.data, ":")
    lid = int(lid_str)

    left = await market_decrement_all_items_and_total(lid)

    if left <= 0:
        await market_set_status(lid, "hidden")
        await market_set_status(lid, "archived")

    await call.answer("Обновлено")

    try:
        await call.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == f"{CB_PREFIX}:sold_no")
async def cb_mark_sold_no(call: CallbackQuery):
    await call.answer("Отменено", show_alert=False)
    try:
        await call.message.delete()
    except Exception:
        pass


@router.message(MarketEditFSM.QTY)
async def set_qty_message(message: Message, state: FSMContext):
    data = await state.get_data()
    lid = int(data.get("edit_lid"))
    try:
        qty = max(0, int((message.text or "1").strip()))
    except ValueError:
        await message.answer("Нужно целое число.")
        return
    await market_set_item_qty(lid, qty)
    await message.answer(f"Количество обновлено: {qty}.")
    await state.clear()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:do:soldqty:"))
async def do_soldqty(call: CallbackQuery, state: FSMContext):
    parts = split_callback_data(call.data, ":")
    lid = int(parts[3])
    which = parts[4]

    if which not in {"1", "2"}:
        await call.answer()
        return

    dec = int(which)
    left = await market_dec_item_qty(lid, dec)
    if left <= 0:
        await market_set_status(lid, "sold")
        await call.answer(f"Продано {dec}. Остаток 0 — помечено как «продано».")
    else:
        await call.answer(f"Продано {dec}. Осталось {left}.")

    try:
        await call.message.delete()
    except Exception:
        pass
    await reload_listing_inplace(call, lid)


async def _delete_all_prices(lid: int) -> None:
    await market_delete_all_prices(lid)


async def _upsert_price(lid: int, pay_type: str, price: float | None, cash_code: str | None = None) -> None:
    pay_type, cash_code = normalize_pay_type(pay_type, cash_code)
    await market_replace_price(
        lid,
        pay_type=pay_type,
        cash_code=cash_code,
        price=price,
    )
