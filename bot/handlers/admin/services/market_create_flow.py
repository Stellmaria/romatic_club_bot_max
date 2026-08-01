"""Listing-creation FSM handlers, split into ordered router fragments."""

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
from bot.telegram.callback_parser import rsplit_callback_data, split_callback_data

create_router = Router(name="market_flow_create")
router = create_router


@router.callback_query(MarketAddFSM.CHOOSE_KIND, F.data.startswith(f"{CB_KIND}:"))
async def choose_kind(call: CallbackQuery, state: FSMContext):
    kind = split_callback_data(call.data, ":")[2]
    await state.update_data(offer_kind=kind, card_ids=[], page=0, deck_id=None)
    if kind in ("cards", "whole_deck"):
        decks = await get_all_decks()
        await state.set_state(MarketAddFSM.CHOOSE_DECK)
        await call.message.edit_text("Выбери колоду:", reply_markup=market_decks_kb(decks))
    else:
        await state.set_state(MarketAddFSM.COVER)
        await call.message.edit_text("Пришли обложку (фото) или «-», чтобы пропустить.")
    await call.answer()


@router.message(MarketAddFSM.COVER)
async def cover_step(message: Message, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif (message.text or "").strip() == "-":
        pass
    else:
        await message.answer("Пришли фото или «-».")
        return

    await state.update_data(cover_file_id=file_id)

    data = await state.get_data()
    offer_kind = (data.get("offer_kind") or "").lower()
    if offer_kind == "diamonds":
        await start_diamonds_currency_flow(message, state)
        return

    await state.set_state(MarketAddFSM.TIERS)
    await message.answer(
        "Введи прайс строками. Примеры:\n"
        "<code>150 40 BYN\n300 90 BYN\n600 170 BYN</code>\n"
        "или для чашек/сокровищ:\n"
        "<code>100 250 cups\n160 400 cups</code>\n"
        "Можно подпись вместо количества: <code>Пакет+ 600 BYN</code>",
        parse_mode="HTML",
    )


@router.callback_query(MarketAddFSM.CURRENCY, F.data.startswith(f"{CB_PREFIX}:cur_toggle:"))
async def cb_currency_toggle(call: CallbackQuery, state: FSMContext):
    cur = split_callback_data(call.data, ":")[2]
    data = await state.get_data()

    chosen: list[str] = list(data.get("cur_multi") or [])
    s = set(chosen)
    if cur in s:
        s.remove(cur)
    else:
        s.add(cur)

    chosen = [c for c in ["cups", "diamonds", "treasures", "tgstars", "cash"] if c in s]
    await state.update_data(cur_multi=chosen)

    extras_cnt = len(list((await state.get_data()).get("custom_variants") or []))

    await call.message.edit_reply_markup(
        reply_markup=currency_multi_keyboard(set(s), extras_count=extras_cnt)
    )
    await call.answer()


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cur_done")
async def cb_currency_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chosen: list[str] = list(data.get("cur_multi") or [])
    custom_variants: list[str] = list(data.get("custom_variants") or [])

    if not chosen and custom_variants:
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await call.message.edit_text("Добавь описание/условия или напиши «-».")
        await call.answer()
        return

    if not chosen:
        await call.answer("Выбери хотя бы одну валюту или добавь «Свой вариант».", show_alert=True)
        return

    if "cash" in chosen:
        await state.update_data(cash_multi=[])
        await call.message.edit_text("Выбери фиатные валюты:", reply_markup=cash_multi_keyboard(set()))
        await call.answer()
        return

    if (data.get("offer_kind") or "").lower() in {"deck", "whole_deck"} and not data.get("deck_mode"):
        await state.set_state(MarketAddFSM.DECK_MODE)
        await call.message.edit_text("Как продаём колоду?", reply_markup=kb_deck_mode())
        await call.answer()
        return

    await state.update_data(price_cursor={"ci": 0, "pi": 0}, prices={})
    await state.set_state(MarketAddFSM.PRICE)
    await ask_next_price(call.message, state)
    await call.answer()


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cur_custom")
async def cb_cur_custom(call: CallbackQuery, state: FSMContext):
    await state.set_state(MarketAddFSM.CUSTOM_VARIANT)
    await call.message.answer(
        "Опиши <b>другой вариант</b> оплаты/обмена (бартер):\n"
        "например, «шоколадка Ritter Sport», «перевод в игре», «подарочная карта» и т.п.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(MarketAddFSM.CUSTOM_VARIANT, F.text)
async def msg_custom_variant(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой вариант не принимаю. Введи текст или нажми «Назад».")
        return

    m = EXTRAS_HEAD_RE.match(text) or EXTRAS_TAIL_RE.match(text)
    if m:
        if m.re is EXTRAS_HEAD_RE:
            qty, label = int(m.group(1)), m.group(2).strip()
        else:
            label, qty = m.group(1).strip(), int(m.group(2))
        normalized = f"{label} ×{qty}"
        data = await state.get_data()
        custom: list[str] = list(data.get("custom_variants") or [])
        custom.append(normalized[:200])
        await state.update_data(custom_variants=custom)
        await message.answer(f"Добавлено: <b>{html.escape(normalized)}</b>.", parse_mode="HTML")
        await state.set_state(MarketAddFSM.CURRENCY)
        await _render_currency_menu(message, state)
        return

    await state.update_data(pending_custom=text[:200])
    await state.set_state(MarketAddFSM.CUSTOM_VARIANT_QTY)
    await message.answer(
        f"Указать количество для «{html.escape(text[:200])}»?",
        reply_markup=kb_custom_qty_choice()
    )


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cash_add")
async def cb_cash_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(MarketAddFSM.CASH_CODE)
    await call.message.answer(
        "Пришли <b>3-буквенный код</b> валюты (ISO 4217), можно с флагом.\n"
        "Примеры: <code>EUR 🇪🇺</code>, <code>GEL 🇬🇪</code>, <code>PLN</code>.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(MarketAddFSM.CASH_CODE)
async def cash_code_entered(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    m = re.search(r"\b([A-Za-z]{3})\b", raw)
    if not m:
        await message.answer("Нужен трёхбуквенный код вроде EUR, USD, PLN.")
        return
    code = m.group(1).upper()
    flag_match = re.findall(r"[\U0001F1E6-\U0001F1FF]{2}|\N{REGIONAL INDICATOR SYMBOL LETTER A}", raw)
    flag = flag_match[-1] if flag_match else ""
    extra_flags = globals().setdefault("EXTRA_FLAGS", {})
    if flag:
        extra_flags[code] = flag

    data = await state.get_data()
    cash_multi: list[str] = list(data.get("cash_multi") or [])
    if code not in cash_multi:
        cash_multi.append(code)
    cash_extra: list[str] = list(data.get("cash_extra") or [])
    if code not in cash_extra:
        cash_extra.append(code)

    await state.update_data(cash_multi=cash_multi, cash_extra=cash_extra)
    await state.set_state(MarketAddFSM.CURRENCY)

    kb = cash_multi_keyboard(set(cash_multi), extras=cash_extra)
    await message.answer("Добавлено. Выбери фиатные валюты:", reply_markup=kb)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:deckmode:"))
async def cb_set_deck_mode(call: CallbackQuery, state: FSMContext):
    await call.answer()
    mode = rsplit_callback_data(call.data, ":", 1)[-1]
    if mode not in ("bulk", "split"):
        mode = "split"
    await state.update_data(deck_mode=mode)

    txt = "Режим продажи: 📦 одним лотом" if mode == "bulk" else "Режим продажи: 🧩 по картам"
    await call.message.edit_text(
        f"{txt}\n\nПроверь параметры и опубликуй объявление.",
        reply_markup=confirm_publish_kb()
    )


async def _ask_deck_mode(msg_or_call, state):
    await state.set_state(MarketAddFSM.DECK_MODE)
    text = "Как продаём колоду?"
    kb = kb_deck_mode()
    try:
        obj = msg_or_call.message if hasattr(msg_or_call, "message") else msg_or_call
        await obj.edit_text(text, reply_markup=kb)
    except Exception:
        await (msg_or_call.answer if hasattr(msg_or_call, "answer") else msg_or_call.reply)(text, reply_markup=kb)


@router.callback_query(F.data == f"{CB_PREFIX}:confirm:yes")
async def cb_confirm_yes(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    user_id = call.from_user.id

    offer_kind = (data.get("offer_kind") or "cards").lower()
    deck_mode = (data.get("deck_mode") or "split").lower()

    tiers_payload: list[dict] = list(data.get("tiers_payload") or [])

    ALLOWED = {"cash", "diamonds", "cups", "treasures"}

    def _norm_tier(t: dict) -> dict | None:
        pay = str(t.get("pay_type") or "").lower().strip()
        price = t.get("price")
        if price is None:
            return None
        if pay in ("tgstars", "tg_stars", "stars"):
            return {
                "pay_type": "cash",
                "cash_code": STAR_DB_CODE,  # "TGS"
                "price": price,
                "label": t.get("label"),
                "qty": t.get("qty"),
                "sort_order": t.get("sort_order", 0),
            }
        if pay not in ALLOWED:
            return None
        return {
            "pay_type": pay,
            "cash_code": t.get("cash_code"),
            "price": price,
            "label": t.get("label"),
            "qty": t.get("qty"),
            "sort_order": t.get("sort_order", 0),
        }

    tiers_norm = [x for x in (_norm_tier(t) for t in tiers_payload) if x]

    # --- «Вся колода одним лотом» --------------------------------------------
    if offer_kind == "deck" and deck_mode == "bulk":
        lid = await market_create_listing(
            seller_id=user_id,
            currency_type="cash",
            price_num=0,
            cash_code=None,
            description=(data.get("description") or None),
        )

        qty_map: dict[str, int] = dict(data.get("qty_map") or {})
        for cid in get_selected_ids(data):
            q = max(1, int(qty_map.get(str(cid), 1)))
            await market_add_listing_item(lid, int(cid), q)

        if tiers_norm:
            await market_add_rate_tiers(lid, tiers_norm)

        proof_one = data.get("proof_file_id") or data.get("cover_file_id")
        if proof_one:
            await market_set_cover(lid, proof_one, touch_updated_at=True)

        await state.clear()
        await call.message.answer("✅ Объявление по всей колоде создано (1 шт).")
        return

    # --- По картам (каждая карта — своё объявление) --------------------------
    created = 0
    selected_ids = get_selected_ids(data)
    cash_code = (data.get("cash_code") or "").upper() or None
    description = (data.get("description") or None)
    proof_one = data.get("proof_file_id") or data.get("cover_file_id")
    qty_map: dict[str, int] = dict(data.get("qty_map") or {})

    for cid in selected_ids:
        lid = await market_create_listing(
            seller_id=user_id,
            currency_type="cash",
            price_num=0,
            cash_code=cash_code,
            description=description,
        )

        q = max(1, int(qty_map.get(str(cid), 1)))
        await market_add_listing_item(lid, int(cid), q)

        if tiers_norm:
            await market_add_rate_tiers(lid, tiers_norm)

        if proof_one:
            await market_set_cover(lid, proof_one, touch_updated_at=True)

        created += 1

    await state.clear()
    await call.message.answer(f"✅ Создано объявлений: {created}")


async def _finalize_publish_listing(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

    seller_id = message.from_user.id
    offer_kind = data.get("offer_kind") or "cards"
    description = (data.get("description") or "").strip() or None
    cover_file_id = data.get("cover_file_id")  # может быть None
    card_ids: list[int] = list(map(int, data.get("card_ids") or []))
    tiers: list[dict] = list(data.get("tiers") or [])

    lid = await market_create_listing(
        seller_id=seller_id,
        status="active",
        description=description,
        currency_type="cash",
        cash_code=None,
        price_num=0,
        offer_kind=offer_kind,
        cover_file_id=cover_file_id,
        deck_id=None,
    )

    items_to_add = [{"card_id": cid, "quantity": 1} for cid in card_ids] or [{"card_id": card_ids[0], "quantity": 1}]
    await market_add_items(lid, items_to_add)

    if tiers:
        await market_add_rate_tiers(lid, tiers)

    proof_map: dict[str, str] = dict(data.get("proof_by_card") or {})
    for card_id_str, fid in proof_map.items():
        try:
            cid = int(card_id_str)
            await market_set_item_proof(lid, cid, fid)
        except Exception:
            pass

    has_any_proof = await market_has_any_proof(lid)

    first_cid = card_ids[0]
    tiers_now = await market_get_rate_tiers(lid) or []
    price_map: dict[str, float] = {}
    for t in tiers_now:
        ptype = (t.get("pay_type") or "").lower()
        if ptype == "cash":
            code = (t.get("cash_code") or "").upper()
            price_map[f"cash:{code}"] = float(t.get("price") or 0)
        else:
            price_map[ptype] = float(t.get("price") or 0)

    card = await fetch_card(first_cid)
    caption = build_card_preview_caption(
        card,
        price_map,
        None,
        description,
        has_proof=has_any_proof,
        qty_available=len(card_ids),
        status="active",
    )

    if card.get("image_id"):
        await message.answer_photo(card["image_id"], caption=caption, parse_mode="HTML")
    else:
        await message.answer(caption, parse_mode="HTML")

    await state.clear()


create_continuation_router = Router(name="market_flow_create_continuation")
router = create_continuation_router


@router.message(MarketAddFSM.TIERS)
async def tiers_step(message: Message, state: FSMContext):
    tiers = parse_tiers((message.text or "").strip())
    if not tiers:
        await message.answer("Не смог разобрать прайс. Дай строки вида «150 40 BYN».")
        return
    await state.update_data(tiers=tiers)
    await state.set_state(MarketAddFSM.DESCRIPTION)
    await message.answer("Добавь описание/условия или напиши «-».")


@router.message(MarketAddFSM.QUANTITY)
async def quantity_step(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    qty = 1 if txt == "-" else max(1, int(txt)) if txt.isdigit() else None
    if qty is None:
        await message.answer("Нужно число или «-».")
        return

    await state.update_data(quantity=qty)

    data = await state.get_data()
    cards = sorted(get_selected_ids(data))
    prices: dict = dict(data.get("prices") or {})
    cash_code = data.get("cash_code")
    desc = data.get("description")
    proof = bool(data.get("proof_file_id"))
    for card_id in cards:
        card = await fetch_card(card_id)
        per_card = dict(prices.get(str(card_id)) or {})
        cap = build_card_preview_caption(card, per_card, cash_code, desc, has_proof=proof)
        cap += f"\n<b>Доступно:</b> {qty} шт."
        if card.get("image_id"):
            await message.answer_photo(card["image_id"], caption=cap, parse_mode="HTML")
        else:
            await message.answer(cap, parse_mode="HTML")

    await state.set_state(MarketAddFSM.CONFIRM)
    await message.answer("Опубликовать эти объявления?", reply_markup=confirm_publish_kb())


@router.message(MarketAddFSM.PROOF_CHOICE)
async def ask_proof_choice(msg: Message, state: FSMContext):
    await msg.answer(
        "Прикрепи фото подтверждения наличия.",
        reply_markup=kb_proof_choice()
    )


@router.callback_query(F.data == f"{CB_PREFIX}:add:proof:skip")
async def add_proof_skip(call: CallbackQuery, state: FSMContext):
    await state.update_data(proof_file_id=None, proof_map={})
    await state.set_state(MarketAddFSM.DESCRIPTION)
    await call.message.answer("Опиши лот (или оставь пустым):")
    await call.answer()


@router.callback_query(F.data == f"{CB_PREFIX}:add:proof:single")
async def add_proof_single(call: CallbackQuery, state: FSMContext):
    await state.update_data(expect_single_proof=True)
    await state.set_state(MarketAddFSM.PHOTO)
    await call.message.answer("Пришли одно фото для всего лота или нажми «⏭ Пропустить».")
    await call.answer()


@router.callback_query(F.data == f"{CB_PREFIX}:add:proof:each")
async def add_proof_each_start(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cards: list[int] = list(map(int, data.get("card_ids") or data.get("cards") or []))
    if not cards:
        await call.answer("Нет выбранных карт.", show_alert=True);
        return
    await state.update_data(proof_by_card={}, proof_idx=0, cards=cards)
    await state.set_state(MarketAddFSM.PROOF_EACH)
    await _ask_next_card_proof(call.message, state)
    await call.answer()


@router.message(MarketAddFSM.PROOF_EACH, F.photo)
async def proof_each_photo(msg: Message, state: FSMContext):
    data = await state.get_data()
    ids = sorted(get_selected_ids(data))
    i = int(data.get("proof_each_index") or 0)
    if i >= len(ids):
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await msg.answer("Фото получено. Опиши лот.")
        return

    file_id = msg.photo[-1].file_id
    cid = ids[i]

    proof_map: dict = dict(data.get("proof_by_card") or {})
    proof_map[str(cid)] = file_id

    await state.update_data(proof_by_card=proof_map, proof_each_index=i + 1)
    await _ask_proof_each(msg, state)


@router.callback_query(MarketAddFSM.PROOF_EACH, F.data == f"{CB_PREFIX}:add:proof:skip_one")
async def add_proof_each_skip_one(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = int(data.get("proof_idx") or 0)
    await state.update_data(proof_idx=idx + 1)
    await _ask_next_card_proof(call.message, state)
    await call.answer()


@router.message(MarketAddFSM.DESCRIPTION, F.text)
async def description_step(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()

    if data.get("qty_pending"):
        selected_ids = sorted(get_selected_ids(data))
        idx = int(data.get("qty_index") or 0)
        qty_map: dict[str, int] = dict(data.get("qty_map") or {})

        raw = (message.text or "").strip()
        try:
            qty_val = int(raw)
            if qty_val < 1:
                raise ValueError
        except ValueError:
            await message.answer("Нужно ввести целое число ≥ 1. Попробуй ещё раз.")
            return

        if 0 <= idx < len(selected_ids):
            qty_map[str(selected_ids[idx])] = qty_val
            idx += 1
            await state.update_data(qty_index=idx, qty_map=qty_map)

        if idx < len(selected_ids):
            next_card = await fetch_card(selected_ids[idx])
            hero = html.escape(next_card.get("hero_name") or "")
            name = html.escape(next_card.get("card_name") or "")
            rarity = next_card.get("rarity") or "?"
            await message.answer(
                f"Сколько штук доступно для:\n<b>{hero}</b> — {name} [{rarity}]?",
                parse_mode="HTML",
            )
            return

        await state.update_data(qty_pending=False)
        if distinct_cards_count(await state.get_data()) > 1:
            await state.set_state(MarketAddFSM.PROOF_CHOICE)
            await message.answer("Прикрепи фото подтверждения наличия.", reply_markup=kb_proof_choice())
        else:
            await state.set_state(MarketAddFSM.PHOTO)
            await message.answer(
                "Пришли одно фото для всего лота или нажми «⏭ Пропустить».",
                reply_markup=kb_proof_single_skip(),
            )
        return

    desc_text = message.text or ""

    def _is_skip_desc(s: str) -> bool:
        t = s.strip().lower()
        return t in {"", "-", "—", "–", "−", "skip", "пропустить"}

    desc = None if _is_skip_desc(desc_text) else desc_text.strip()
    await state.update_data(description=desc)

    try:
        await go_to_confirm(message, state)
    except Exception as e:
        await message.answer(f"Не смог собрать превью: {e!r}")


@router.message(MarketAddFSM.PHOTO, F.photo)
async def proof_single_photo(msg: Message, state: FSMContext):
    file_id = msg.photo[-1].file_id
    await state.update_data(proof_file_id=file_id, expect_single_proof=False)
    await state.set_state(MarketAddFSM.DESCRIPTION)
    await msg.answer("Фото сохранено. Теперь отправь описание лота (или «-» чтобы пропустить).")


@router.message(MarketAddFSM.PHOTO, F.text)
async def proof_single_text(msg: Message, state: FSMContext):
    txt = (msg.text or "").strip()
    if txt == "-":
        await state.update_data(proof_file_id=None, expect_single_proof=False)
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await msg.answer("Ок, без фото. Теперь отправь описание лота (или оставь пустым).")
        return
    await msg.answer("Пришли фото или нажми «⏭ Пропустить».")


@router.callback_query(StateFilter(MarketAddFSM.CHOOSE_DECK), F.data.startswith(f"{CB_PREFIX}:deck:"))
async def cb_choose_deck(call: CallbackQuery, state: FSMContext):
    _, _, deck_id_str, page_str = split_callback_data(call.data, ":")
    deck_id = int(deck_id_str)
    page = int(page_str)

    data = await state.get_data()
    offer_kind = (data.get("offer_kind") or "").lower()

    if offer_kind == "whole_deck":
        cards = await get_cards_by_deck(deck_id)
        ids = [int(c["card_id"]) for c in (cards or [])]
        if not ids:
            await call.answer("В этой колоде нет карт.", show_alert=True)
            return

        await state.update_data(deck_id=deck_id, card_ids=ids)
        await state.set_state(MarketAddFSM.CURRENCY)

        extras_cnt = len(list((await state.get_data()).get("custom_variants") or []))
        await call.message.edit_text(
            "Выбери валюту(ы):",
            reply_markup=currency_multi_keyboard(set(), extras_count=extras_cnt),
        )
        await call.answer()
        return

    cards = await get_cards_by_deck(deck_id)
    selected = get_selected_ids(await state.get_data())
    kb = market_cards_kb(
        deck_id=deck_id,
        cards=cards,
        selected=selected,
        page=page,
        page_size=PAGE_CARDS,
        limit=None,
    )

    await state.update_data(deck_id=deck_id, page=page)
    await state.set_state(MarketAddFSM.PICK_CARDS)

    await safe_edit_text(
        call.message,
        f"Колода #{deck_id}. Отметь карты:",
        reply_markup=kb
    )
    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.PICK_CARDS), F.data.startswith(f"{CB_PAGE}:"))
async def cb_cards_page(call: CallbackQuery, state: FSMContext):
    _, _, deck_id_str, page_str = split_callback_data(call.data, ":")
    deck_id = int(deck_id_str)
    page = int(page_str)

    cards = await get_cards_by_deck(deck_id)
    selected = get_selected_ids(await state.get_data())

    kb = market_cards_kb(
        deck_id=deck_id,
        cards=cards,
        selected=selected,
        page=page,
        page_size=PAGE_CARDS,
        limit=None,
    )
    await state.update_data(page=page)
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.PICK_CARDS), F.data.startswith(f"{CB_PREFIX}:reset:"))
async def cb_cards_reset(call: CallbackQuery, state: FSMContext):
    _, _, deck_id_str, page_str = split_callback_data(call.data, ":")
    deck_id = int(deck_id_str)
    page = int(page_str)

    await state.update_data(card_ids=[], page=page)
    cards = await get_cards_by_deck(deck_id)

    kb = market_cards_kb(
        deck_id=deck_id,
        cards=cards,
        selected=set(),
        page=page,
        page_size=PAGE_CARDS,
        limit=None,
    )
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass

    await call.answer("Выбор очищен")


@router.callback_query(StateFilter(MarketAddFSM.PICK_CARDS), F.data.startswith(f"{CB_SEL}:"))
async def cb_toggle_card(call: CallbackQuery, state: FSMContext):
    _, _, deck_id_str, card_id_str, page_str = split_callback_data(call.data, ":")
    deck_id = int(deck_id_str)
    card_id = int(card_id_str)
    page = int(page_str)

    selected = get_selected_ids(await state.get_data())
    if card_id in selected:
        await remove_selected_id(state, card_id)
    else:
        await add_selected_id(state, card_id, None)

    selected = get_selected_ids(await state.get_data())
    cards = await get_cards_by_deck(deck_id)

    kb = market_cards_kb(
        deck_id=deck_id,
        cards=cards,
        selected=selected,
        page=page,
        page_size=PAGE_CARDS,
        limit=None,
    )
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass

    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.PICK_CARDS), F.data == CB_BACK)
async def cb_back_to_decks(call: CallbackQuery, state: FSMContext):
    decks = await get_all_decks()
    await state.set_state(MarketAddFSM.CHOOSE_DECK)
    await safe_edit_text(
        call.message,
        "Выбери колоду:",
        reply_markup=market_decks_kb(decks)
    )
    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.PICK_CARDS), F.data == f"{CB_PREFIX}:done")
async def cb_cards_done(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = sorted(get_selected_ids(data))
    if not selected:
        await call.answer("Не выбрано ни одной карты.", show_alert=True)
        return

    await state.update_data(
        card_ids=selected,
        price_cursor={"ci": 0, "pi": 0},
        prices={},
        cash_multi=[],
        cash_extra=[],
        cur_multi=set(),
    )

    await state.set_state(MarketAddFSM.CURRENCY)

    extras_cnt = len(list((await state.get_data()).get("custom_variants") or []))

    await call.message.edit_text(
        "Выбери валюту(ы):",
        reply_markup=currency_multi_keyboard(set(), extras_count=extras_cnt),
    )
    await call.answer()


@router.message(MarketAddFSM.PRICE_BULK, F.text)
async def msg_price_bulk(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно положительное число. Попробуй ещё раз.")
        return

    await state.update_data(bulk_price=price)
    await state.set_state(MarketAddFSM.DESCRIPTION)
    await message.answer("Опиши лот (или оставь пустым):")


@router.callback_query(MarketAddFSM.CURRENCY, F.data.startswith(f"{CB_PREFIX}:cash_toggle:"))
async def cb_cash_toggle(call: CallbackQuery, state: FSMContext):
    code = split_callback_data(call.data, ":")[2].upper()
    data = await state.get_data()
    s = set(data.get("cash_multi") or [])
    if code in s:
        s.remove(code)
    else:
        s.add(code)

    order = ["BYN", "RUB", "UAH", "KZT", "USD"]
    extras = list(data.get("cash_extra") or [])
    chosen = [c for c in order if c in s] + [c for c in extras if c in s and c not in order]

    await state.update_data(cash_multi=chosen)
    await call.message.edit_reply_markup(reply_markup=cash_multi_keyboard(set(chosen), extras=extras))
    await call.answer()


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cash_done")
async def cb_cash_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cash_codes = list(data.get("cash_multi") or [])
    if not cash_codes:
        await call.answer("Выбери хотя бы одну валюту.", show_alert=True)
        return
    await state.update_data(price_cursor={"ci": 0, "pi": 0}, prices={})
    await state.set_state(MarketAddFSM.PRICE)
    await ask_next_price(call.message, state)
    await call.answer()


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cur_add_cashcustom")
async def cb_cur_add_cashcustom(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cur_multi = list(data.get("cur_multi") or [])
    if "cash" not in cur_multi:
        cur_multi.append("cash")
    await state.update_data(cur_multi=cur_multi)
    await state.set_state(MarketAddFSM.CASH_CODE)
    await call.message.answer(
        "Пришли <b>3-буквенный код валюты</b> (можно с флагом).\n"
        "Примеры: <code>EUR 🇪🇺</code>, <code>GEL 🇬🇪</code>, <code>PLN</code>.",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.CUSTOM_VARIANT_QTY), F.data == f"{CB_PREFIX}:cur_custom_qty:skip")
async def cb_custom_qty_skip(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pending = (data.get("pending_custom") or "").strip()
    if not pending:
        await call.answer("Нечего добавлять.", show_alert=True)
        return

    custom: list[str] = list(data.get("custom_variants") or [])
    custom.append(pending)
    await state.update_data(custom_variants=custom, pending_custom=None)

    await call.message.answer(f"Добавлено: <b>{html.escape(pending)}</b>.", parse_mode="HTML")
    await state.set_state(MarketAddFSM.CURRENCY)
    await _render_currency_menu(call, state)
    await call.answer()


@router.callback_query(StateFilter(MarketAddFSM.CUSTOM_VARIANT_QTY), F.data == f"{CB_PREFIX}:cur_custom_qty:ask")
async def cb_custom_qty_ask(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pending = (data.get("pending_custom") or "").strip()
    if not pending:
        await call.answer("Нечего считать.", show_alert=True)
        return
    await state.set_state(MarketAddFSM.CUSTOM_VARIANT_QTY_INPUT)
    await call.message.answer(
        f"Введи количество для «{html.escape(pending)}» (целое число ≥ 1)\n"
        f"или «-», чтобы оставить без количества.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(MarketAddFSM.CUSTOM_VARIANT_QTY_INPUT, F.text)
async def msg_custom_qty_input(message: Message, state: FSMContext):
    data = await state.get_data()
    pending = (data.get("pending_custom") or "").strip()
    raw = (message.text or "").strip()

    if raw == "-":
        custom: list[str] = list(data.get("custom_variants") or [])
        custom.append(pending)
        await state.update_data(custom_variants=custom, pending_custom=None)
        await message.answer(f"Добавлено: <b>{html.escape(pending)}</b>.", parse_mode="HTML")
        await state.set_state(MarketAddFSM.CURRENCY)
        await _render_currency_menu(message, state)
        return

    try:
        qty = int(raw)
        if qty < 1:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое число ≥ 1 или «-». Попробуй ещё раз.")
        return

    normalized = f"{pending} ×{qty}"
    custom: list[str] = list(data.get("custom_variants") or [])
    custom.append(normalized[:200])
    await state.update_data(custom_variants=custom, pending_custom=None)

    await message.answer(f"Добавлено: <b>{html.escape(normalized)}</b>.", parse_mode="HTML")
    await state.set_state(MarketAddFSM.CURRENCY)
    await _render_currency_menu(message, state)


async def go_to_confirm(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()

    cards = sorted(get_selected_ids(data))
    if not cards:
        await msg.answer("Нет выбранных карт.")
        return

    prices_raw: dict = dict(data.get("prices") or {})
    custom_variants: list[str] = list(data.get("custom_variants") or [])
    _add = [x.strip() for x in custom_variants if x and str(x).strip()]

    desc = data.get("description") or ""
    if _add:
        pretty = []
        for x in _add:
            m = EXTRAS_HEAD_RE.match(x) or EXTRAS_TAIL_RE.match(x)
            if m:
                if m.re is EXTRAS_HEAD_RE:
                    qty, label = int(m.group(1)), m.group(2).strip()
                else:
                    label, qty = m.group(1).strip(), int(m.group(2))
                pretty.append(f"{label} ×{qty}")
            else:
                pretty.append(x)
        desc = (desc + f"\n\n<b>Дополнительно принимаю:</b> {', '.join(pretty)}").strip()

    proof_id = data.get("proof_file_id")
    proof_map: dict = dict(data.get("proof_by_card") or {})
    has_any_proof = bool(proof_id) or bool(proof_map)

    qty_map: dict = dict(data.get("qty_map") or {})
    default_qty = int(data.get("quantity") or 1)

    for cid in cards:
        per_card: dict = dict(prices_raw.get(str(cid)) or {})
        qty = int(qty_map.get(str(cid)) or default_qty or 1)

        card = await fetch_card(cid)
        caption = build_card_preview_caption(
            card,
            per_card,
            None,
            desc,
            has_proof=has_any_proof,
            qty_available=qty,
            status="active",
        )

        if card.get("image_id"):
            await msg.answer_photo(card["image_id"], caption=caption, parse_mode="HTML")
        else:
            await msg.answer(caption, parse_mode="HTML")

    await state.set_state(MarketAddFSM.CONFIRM)
    await msg.answer("Опубликовать эти объявления?", reply_markup=confirm_publish_kb())


async def ask_next_price(target: Message | CallbackQuery | Message, state: FSMContext):
    data = await state.get_data()
    cards = sorted(get_selected_ids(data))
    curs: list[str] = list(data.get("cur_multi") or [])
    cash_codes: list[str] = list(data.get("cash_multi") or [])

    seq: list[tuple[str, str | None]] = []
    for cur in curs:
        if cur == "cash":
            for code in cash_codes:
                seq.append(("cash", code))
        else:
            seq.append((cur, None))

    cur_i = data.get("price_cursor", {}).get("ci", 0)
    card_i = data.get("price_cursor", {}).get("pi", 0)

    if card_i >= len(cards):
        await state.update_data(qty_pending=True, qty_index=0, qty_map={})
        await state.set_state(MarketAddFSM.DESCRIPTION)

        first_card_id = cards[0]
        card = await fetch_card(first_card_id)
        hero = html.escape(card.get("hero_name") or "")
        name = html.escape(card.get("card_name") or "")
        rarity = card.get("rarity") or "?"
        send = target.answer if isinstance(target, CallbackQuery) else target.reply
        await send(
            f"Сколько штук доступно для карты:\n<b>{hero}</b> — {name} [{rarity}]?\n"
            "Введи целое число ≥ 1.",
            parse_mode="HTML",
        )
        return

    if not seq:
        await state.set_state(MarketAddFSM.CURRENCY)
        send = target.answer if isinstance(target, CallbackQuery) else target.reply
        await send("Сначала выбери валюту(ы).")
        return

    card_id = cards[card_i]
    cur, code = seq[cur_i]
    card = await fetch_card(card_id)

    if cur == "cups":
        rules = "(минимум 2, кратность 2)"
    elif cur == "diamonds":
        rules = "(минимум 30, кратность 10)"
    elif cur == "treasures":
        rules = "(минимум 10, кратность 10)"
    else:
        rules = "(любой положительный, можно с точкой)"

    hero = html.escape(card.get("hero_name") or "")
    name = html.escape(card.get("card_name") or "")
    rarity = card.get("rarity") or "?"
    cur_txt = f"{currency_emoji(cur)} {code or ''}".strip()

    text = (
        f"Карта: <b>{hero}</b> — {name} [{rarity}]\n"
        f"Введи цену в {cur_txt} {rules}"
    )

    if isinstance(target, CallbackQuery):
        await target.message.answer(text, parse_mode="HTML")
    else:
        await target.answer(text, parse_mode="HTML")


@router.message(MarketAddFSM.PRICE)
async def price_entered(message: Message, state: FSMContext):
    raw = (message.text or "").replace(",", ".").strip()
    try:
        price = float(raw)
    except ValueError:
        await message.answer("Нужно число.")
        return

    data = await state.get_data()
    cards = sorted(get_selected_ids(data))
    curs: list[str] = list(data.get("cur_multi") or [])
    cash_codes: list[str] = list(data.get("cash_multi") or [])
    seq: list[tuple[str, str | None]] = []
    for cur in curs:
        if cur == "cash":
            for code in cash_codes:
                seq.append(("cash", code))
        else:
            seq.append((cur, None))

    cur_i = data.get("price_cursor", {}).get("ci", 0)
    card_i = data.get("price_cursor", {}).get("pi", 0)
    cur, code = seq[cur_i]

    ok, err = validate_price_by_currency(cur, price)
    if not ok:
        await message.answer(err);
        return

    prices: dict = dict(data.get("prices") or {})
    card_key = str(cards[card_i])
    per_card: dict = dict(prices.get(card_key) or {})
    store_key = cur if cur != "cash" else f"cash:{code}"
    per_card[store_key] = round(price if cur == "cash" else int(price), 2)
    prices[card_key] = per_card
    await state.update_data(prices=prices)

    cur_i += 1
    if cur_i >= len(seq):
        cur_i = 0
        card_i += 1
    await state.update_data(price_cursor={"ci": cur_i, "pi": card_i})

    await ask_next_price(message, state)


async def _ask_next_card_proof(target: Message, state: FSMContext):
    data = await state.get_data()
    cards: list[int] = list(data.get("cards") or [])
    idx = int(data.get("proof_idx") or 0)
    if idx >= len(cards):
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await target.answer("Фото собраны. Теперь отправь описание лота (необязательно).")
        return

    card = await fetch_card(int(cards[idx]))
    title = card.get("title") or card.get("name") or "карта"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить для этой карты", callback_data=f"{CB_PREFIX}:add:proof:skip_one")]
    ])
    await target.answer(f"Пришли фото подтверждения для: <b>{title}</b>", parse_mode="HTML", reply_markup=kb)


async def _ask_proof_each(msg_or_call, state: FSMContext) -> None:
    data = await state.get_data()
    ids = sorted(get_selected_ids(data))
    i = int(data.get("proof_each_index") or 0)

    if i >= len(ids):
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await (msg_or_call.message if hasattr(msg_or_call, "message") else msg_or_call).answer(
            "Опиши лот (или оставь пустым)."
        )
        return

    cid = ids[i]
    card = await fetch_card(cid)
    title = card_title(card)
    total = len(ids)
    kb = kb_proof_each_skip()

    text = f"Пришли фото подтверждения для карты <b>({i + 1}/{total})</b>:\n<b>{title}</b>"
    await send_prompt(msg_or_call, text=text, image_id=card.get("image_id"), kb=kb)


async def _render_currency_menu(call_or_msg, state):
    data = await state.get_data()
    selected = set(data.get("cur_multi") or data.get("currencies") or [])
    extras = list(data.get("custom_variants") or data.get("custom_payments") or [])

    parts = []
    if extras:
        preview = "\n".join(format_extra_for_summary(x)[:96] for x in extras[:10])
        more = "" if len(extras) <= 10 else f"\n…и ещё {len(extras) - 10}"
        parts.append(f"Дополнительно будет добавлено:\n{preview}{more}")
    parts.append("Выбери валюту(ы) или нажми «Готово».")
    txt = "\n\n".join(parts)

    kb = currency_multi_keyboard(selected, extras_count=len(extras))

    try:
        obj = call_or_msg.message if hasattr(call_or_msg, "message") else call_or_msg
        await obj.edit_text(txt, reply_markup=kb)
    except Exception:
        await (call_or_msg.answer if hasattr(call_or_msg, "answer") else call_or_msg.reply)(
            txt, reply_markup=kb
        )


@router.callback_query(MarketAddFSM.PROOF_EACH, F.data == f"{CB_PREFIX}:add:proof:each:skip")
async def proof_each_skip(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ids = sorted(get_selected_ids(data))
    i = int(data.get("proof_each_index") or 0)
    if i >= len(ids):
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await call.message.answer("Окей, двигаемся дальше. Опиши лот.")
        await call.answer()
        return

    cid = ids[i]
    proof_map: dict = dict(data.get("proof_by_card") or {})
    proof_map[str(cid)] = None

    await state.update_data(proof_by_card=proof_map, proof_each_index=i + 1)
    await _ask_proof_each(call, state)
    await call.answer("Пропущено")


@router.message(MarketAddFSM.DESCRIPTION, F.photo)
async def description_photo_step(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("qty_pending"):
        await message.answer("Сначала укажи количество. Потом сможешь прислать фото подтверждения.")
        return

    file_id = message.photo[-1].file_id
    await state.update_data(proof_file_id=file_id)
    await message.answer("Фото подтверждения сохранено. Теперь отправь описание или «-».")


@router.callback_query(MarketAddFSM.DESCRIPTION, F.data == f"{CB_PREFIX}:proof_skip")
async def cb_proof_skip(call: CallbackQuery, state: FSMContext):
    await state.update_data(proof_file_id=None)
    await call.message.answer("Окей, без фото. Теперь отправь описание условий или «-».")
    await call.answer()


@router.callback_query(F.data.in_({f"{CB_PREFIX}:cancel", f"{CB_PREFIX}:confirm:no"}))
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отменено", show_alert=False)
    try:
        await call.message.edit_reply_markup(None)
    except Exception:
        pass
    await call.message.answer("❎ Отменено.")


@router.callback_query(F.data == f"{CB_PREFIX}:deckmode:bulk")
async def cb_deckmode_bulk(call: CallbackQuery, state: FSMContext):
    await state.update_data(deck_mode="bulk")
    await call.answer("Режим: одним лотом")
    await state.set_state(MarketAddFSM.CURRENCY)
    await _render_currency_menu(call, state)


@router.callback_query(F.data == f"{CB_PREFIX}:deckmode:split")
async def cb_deckmode_split(call: CallbackQuery, state: FSMContext):
    await state.update_data(deck_mode="split")
    await call.answer("Режим: по картам")
    await state.set_state(MarketAddFSM.CURRENCY)
    await _render_currency_menu(call, state)


# Compatibility: the primary fragment remains the module-level router.
router = create_router
