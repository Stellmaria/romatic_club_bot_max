import html
import re
from typing import Optional

from aiogram import F, Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers.admin.services.market_constants import CB_KIND, CB_PREFIX, _EXTRAS_TAIL_RE, _EXTRAS_HEAD_RE, \
    STAR_DB_CODE, PAGE_CARDS, CB_PAGE, CB_SEL, CB_BACK
from bot.handlers.admin.services.market_db_helpers import fetch_card, _db_exec
from bot.handlers.admin.services.market_diamonds_flow import start_diamonds_currency_flow
from bot.handlers.admin.services.market_fsm import MarketAddFSM, MarketEditFSM, MarketSearchFSM
from bot.handlers.admin.services.market_keyboards import market_kind_kb, market_decks_kb, currency_multi_keyboard, \
    cash_multi_keyboard, kb_deck_mode, kb_custom_qty_choice, confirm_publish_kb, prices_menu_kb, prices_cash_menu_kb, \
    sold_confirm_kb, kb_proof_choice, kb_proof_single_skip, market_cards_kb, proof_skip_kb, my_sales_filters_reply_kb, \
    my_sales_nav_kb
from bot.handlers.admin.services.market_render import build_card_preview_caption, _reload_listing_inplace, \
    _format_extra_for_summary
from bot.handlers.admin.services.market_service import _kb_proof_each_skip, _send_prompt
from bot.handlers.admin.services.market_utils import get_selected_ids, safe_delete, _normalize_pay_type, parse_tiers, \
    _distinct_cards_count, safe_edit_text, remove_selected_id, add_selected_id, currency_emoji, \
    validate_price_by_currency, _card_title
from bot.services.market import (
    market_delete_all_prices,
    market_decrement_all_items_and_total,
    market_get_cover_file_id,
    market_hard_delete_listing,
    market_has_any_proof,
    market_listing_navigation_view,
    market_replace_price,
    market_search,
    market_seller_listing_ids,
    market_seller_listing_summaries,
    market_set_cover,
    market_set_description,
    market_set_item_proof,
    market_toggle_named_status,
)
from db.legacy import get_all_decks, market_create_listing, market_add_listing_item, market_add_rate_tiers, \
    market_add_items, market_get_rate_tiers, market_set_item_qty, market_dec_item_qty, \
    market_set_status, get_cards_by_deck

router = Router(name="market_flow")


_MY = "my_sales"  # ключ в FSM


async def _my_sales_set_filter_and_show(message: Message, state: FSMContext, tab: str):
    user_id = message.from_user.id
    statuses = ["active", "hidden", "sold", "archived"] if tab == "all" else [tab]
    ids = await market_seller_listing_ids(user_id, statuses)
    await state.update_data({_MY: {"ids": ids, "idx": 0, "tab": tab}})
    # снизу — фильтры
    await message.answer("Фильтр применён.", reply_markup=my_sales_filters_reply_kb(tab))
    if not ids:
        await message.answer("Пусто.")
        return
    await _my_sales_render(message, state, edit=False)


from aiogram.types import Message, CallbackQuery, InputMediaPhoto


async def _my_sales_render(target: Message | CallbackQuery, state: FSMContext, edit: bool):
    data = await state.get_data()
    s = data.get(_MY) or {}
    ids: list[int] = list(s.get("ids") or [])
    if not ids:
        # Нечего показывать
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text("Пусто.")
            except Exception:
                await target.message.answer("Пусто.")
        else:
            await target.answer("Пусто.")
        return

    idx = max(0, min(int(s.get("idx") or 0), len(ids) - 1))
    lid = int(ids[idx])

    # Лот + первая карта из него
    lot, card, tiers = await market_listing_navigation_view(lid)

    # Подпись
    title = card.get("title") or (lot.get("description") or "—").splitlines()[0]
    rarity = str(card.get("rarity") or "—")
    deck_name = card.get("deck_name") or (f"Колода {card.get('deck_id')}" if card.get('deck_id') else "—")

    yields = []
    if int(card.get("diamonds") or 0) > 0:
        yields.append(f"💎 {int(card['diamonds'])}")
    if int(card.get("cups") or 0) > 0:
        yields.append(f"☕ {int(card['cups'])}")
    if int(card.get("treasures") or 0) > 0:
        yields.append(f"🏴‍☠️ {int(card['treasures'])}")
    gives_line = " · ".join(yields) if yields else "—"

    price_lines = []
    for t in tiers:
        pt = (t.get("pay_type") or "").lower()
        if pt == "cash":
            code = (t.get("cash_code") or "").upper()
            price_lines.append(f"{code} {t['price']:.2f}")
        elif pt == "diamonds":
            price_lines.append(f"💎 {int(t['price'])}")
        elif pt == "cups":
            price_lines.append(f"☕ {int(t['price'])}")
        elif pt == "treasures":
            price_lines.append(f"🏴‍☠️ {int(t['price'])}")

    cnt = int(lot.get("items_count") or 0)
    st = str(lot.get("status") or "unknown")
    proof = "есть" if lot.get("cover_file_id") else "отсутствует"

    caption = (
            f"<b>{title}</b> — <i>{rarity}</i>\n"
            f"{deck_name}\n\n"
            f"<b>Цены:</b>\n" + ("\n".join(f"• {p}" for p in price_lines) or "—") + "\n\n"
                                                                                    f"Доступно: {cnt}\n"
                                                                                    f"Даёт: {gives_line}\n"
                                                                                    f"Фото подтверждения: {proof}\n\n"
                                                                                    f"<i>Статус: {st}</i>"
    )

    kb = my_sales_nav_kb(idx, len(ids), st)
    cover = lot.get("cover_file_id")

    # Рендер
    if isinstance(target, CallbackQuery):
        if cover:
            media = InputMediaPhoto(media=cover, caption=caption, parse_mode="HTML")
            await target.message.edit_media(media=media, reply_markup=kb)
        else:
            await target.message.edit_text(caption, reply_markup=kb, parse_mode="HTML")
    else:
        if cover:
            await target.answer_photo(cover, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(caption, reply_markup=kb, parse_mode="HTML")


@router.message(Command("sell"), F.chat.type == "private")
async def sell_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await state.set_state(MarketAddFSM.CHOOSE_KIND)
    await message.answer("Что продаём?", reply_markup=market_kind_kb())

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:go:"))
    async def market_panel_go(call: CallbackQuery, state: FSMContext, bot: Bot):
        _, _, action = call.data.split(":")
        await call.answer()  # закрыть "часики"

        if action in ("sell_cards", "sell_deck"):
            # Идём в обычный мастер /sell (там уже выберешь вид продажи)
            await sell_start(call.message, state, bot)
            return

        if action == "sell_currency":
            # Мастер прайсов для алмазов
            await start_diamonds_currency_flow(call.message, state)
            return

        if action == "find":
            await market_find(call.message, state)
            return

        if action == "my_sales":
            await my_sales_open(call.message, state)
            return

        if action == "help":
            await call.message.answer(
                "Помощь по магазину:\n/sell — создать объявление\n/find — поиск\n/my_sales — мои объявления"
            )
            return


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


@router.message(F.chat.type == "private", F.text == "📦 Мои объявления")
async def rk_my_sales(message: Message, state: FSMContext):
    await my_sales_open(message, state)


@router.message(F.chat.type == "private", F.text == "🛒 Продать")
async def rk_sell(message: Message, state: FSMContext, bot: Bot):
    await sell_start(message, state, bot)


@router.message(F.chat.type == "private", F.text == "🔍 Поиск")
async def rk_find(message: Message, state: FSMContext):
    await market_find(message, state)


@router.message(F.chat.type == "private", F.text == "📦 Мои объявления")
async def rk_my_sales(message: Message, state: FSMContext):
    await my_sales_open(message, state)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:go:"))
async def market_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    action = call.data.split(":")[2]

    # «Продать»
    if action in {"sell_cards", "sell_deck", "sell_currency"}:
        await sell_start(call.message, state, bot)
        return

    # «Поиск»
    try:
        from .market_manage_flow import find_start  # если есть мастер поиска
        if action == "find":
            await find_start(call.message, state)
            return
    except Exception:
        pass

    # «Мои объявления»
    if action == "my_sales":
        await _show_my_sales(call.message, call.from_user.id, state=state, tab="active")
        return

    if action == "help":
        await call.message.answer("FAQ: /sell, /find, /my_sales")


@router.message(F.text == "🛒 Продать")
async def _sell_btn(message: Message, state: FSMContext, bot: Bot):
    await sell_start(message, state, bot)


@router.callback_query(MarketAddFSM.CHOOSE_KIND, F.data.startswith(f"{CB_KIND}:"))
async def choose_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[2]
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
    cur = call.data.split(":")[2]
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


@router.callback_query(MarketAddFSM.CURRENCY, F.data == f"{CB_PREFIX}:cur_custom")
async def cb_cur_custom(call: CallbackQuery, state: FSMContext):
    await state.set_state(MarketAddFSM.CUSTOM_VARIANT)
    await call.message.answer(
        "Опиши <b>другой вариант</b> оплаты/обмена (бартер):\n"
        "например, «шоколадка Ritter Sport», «перевод в игре», «подарочная карта» и т.п.",
        parse_mode="HTML",
    )
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

    m = _EXTRAS_HEAD_RE.match(text) or _EXTRAS_TAIL_RE.match(text)
    if m:
        if m.re is _EXTRAS_HEAD_RE:
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
    mode = call.data.rsplit(":", 1)[-1]
    if mode not in ("bulk", "split"):
        mode = "split"
    await state.update_data(deck_mode=mode)

    txt = "Режим продажи: 📦 одним лотом" if mode == "bulk" else "Режим продажи: 🧩 по картам"
    await call.message.edit_text(
        f"{txt}\n\nПроверь параметры и опубликуй объявление.",
        reply_markup=confirm_publish_kb()
    )


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:deckmode:"))
async def cb_deck_mode(call: CallbackQuery, state: FSMContext):
    mode = call.data.rsplit(":", 1)[-1]
    await state.update_data(deck_mode=mode)

    if mode == "bulk":
        await state.set_state(MarketAddFSM.PRICE_BULK)
        await call.message.edit_text(
            "Введи цену за всю колоду (в выбранной валюте).",
            reply_markup=None
        )
    else:
        await state.set_state(MarketAddFSM.PRICE)


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


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:edit:"))
async def edit_action(call: CallbackQuery, state: FSMContext):
    _, _, sub, lid_str = call.data.split(":")
    lid = int(lid_str)
    parts = call.data.split(":")
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
        await _reload_listing_inplace(call, lid)
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
    _, _, _, verdict, lid_str = call.data.split(":")
    lid = int(lid_str)

    if verdict == "no":
        await safe_delete(call.message)
        await call.answer("Отменено")
        return

    await market_hard_delete_listing(lid)

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
    _, _, lid_str = call.data.split(":")
    lid = int(lid_str)
    await call.message.answer("Подтверди продажу. Уменьшить количество в объявлении на 1?",
                              reply_markup=sold_confirm_kb(lid))
    await call.answer()


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:sold_yes:"))
async def cb_mark_sold_yes(call: CallbackQuery):
    _, _, _, lid_str = call.data.split(":")
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
    parts = call.data.split(":")
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
    await _reload_listing_inplace(call, lid)


async def _delete_all_prices(lid: int) -> None:
    await market_delete_all_prices(lid)


async def _upsert_price(lid: int, pay_type: str, price: float | None, cash_code: str | None = None) -> None:
    pay_type, cash_code = _normalize_pay_type(pay_type, cash_code)
    await market_replace_price(
        lid,
        pay_type=pay_type,
        cash_code=cash_code,
        price=price,
    )


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
    _, _, tab = call.data.split(":")
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
        if _distinct_cards_count(await state.get_data()) > 1:
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
    _, _, deck_id_str, page_str = call.data.split(":")
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
    _, _, deck_id_str, page_str = call.data.split(":")
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
    _, _, deck_id_str, page_str = call.data.split(":")
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
    _, _, deck_id_str, card_id_str, page_str = call.data.split(":")
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
    code = call.data.split(":")[2].upper()
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


@router.callback_query(MarketAddFSM.COVER, F.data == f"{CB_PREFIX}:proof:skip")
async def cover_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(proof_file_id=None)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("Ок, без фото.")
    await go_to_confirm(call.message, state)


@router.message(MarketAddFSM.COVER, F.photo)
async def cover_photo(message: Message, state: FSMContext) -> None:
    fid = message.photo[-1].file_id
    await state.update_data(proof_file_id=fid)
    await message.answer("Фото подтверждения сохранено.")
    await go_to_confirm(message, state)


@router.message(MarketAddFSM.COVER)
async def cover_any(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip().lower()
    if txt in {"-", "пропустить", "skip", "нет"}:
        await state.update_data(proof_file_id=None)
        await go_to_confirm(message, state)
        return
    await message.answer("Прикрепи фото подтверждения или нажми кнопку ниже.", reply_markup=proof_skip_kb())


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
            m = _EXTRAS_HEAD_RE.match(x) or _EXTRAS_TAIL_RE.match(x)
            if m:
                if m.re is _EXTRAS_HEAD_RE:
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
    title = _card_title(card)
    total = len(ids)
    kb = _kb_proof_each_skip()

    text = f"Пришли фото подтверждения для карты <b>({i + 1}/{total})</b>:\n<b>{title}</b>"
    await _send_prompt(msg_or_call, text=text, image_id=card.get("image_id"), kb=kb)


async def _render_currency_menu(call_or_msg, state):
    data = await state.get_data()
    selected = set(data.get("cur_multi") or data.get("currencies") or [])
    extras = list(data.get("custom_variants") or data.get("custom_payments") or [])

    parts = []
    if extras:
        preview = "\n".join(_format_extra_for_summary(x)[:96] for x in extras[:10])
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
    action = call.data.split(":")[2]

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

    # Стейт-машина статусов находится в marketplace use case.
    if action == "toggle_hidden":
        await market_toggle_named_status(lid, "hidden")

    if action == "toggle_archive":
        await market_toggle_named_status(lid, "archived")

    if action == "toggle_sold":
        await market_toggle_named_status(lid, "sold")

    # edit текущей карточки
    await _my_sales_render(call, state, edit=True)


async def _my_sales_enter(message: Message, state: FSMContext, tab: str):
    # ставим клавиатуру фильтров и НЕ удаляем это сообщение
    await message.answer("Фильтр объявлений", reply_markup=my_sales_filters_reply_kb(tab), disable_notification=True)

    statuses = ["active", "hidden", "sold", "archived"] if tab == "all" else [tab]
    ids = await market_seller_listing_ids(message.from_user.id, statuses)

    await state.update_data({_MY: {"ids": ids, "idx": 0, "tab": tab}})

    if not ids:
        await message.answer("Пусто.")
        return

    await _my_sales_render(message, state, edit=False)


async def _my_sales_render(target: Message | CallbackQuery, state: FSMContext, edit: bool):
    data = await state.get_data()
    s = data.get(_MY) or {}
    ids: list[int] = list(s.get("ids") or [])
    idx = int(s.get("idx") or 0)
    lid = int(ids[idx])

    lot, card, tiers = await market_listing_navigation_view(lid)

    # подпись
    title = card.get("title") or (lot.get("description") or "—").splitlines()[0]
    rarity = str(card.get("rarity") or "—")
    deck_name = card.get("deck_name") or (f"Колода {card.get('deck_id')}" if card.get('deck_id') else "—")

    yields = []
    if int(card.get("diamonds") or 0) > 0:  yields.append(f"💎 {int(card['diamonds'])}")
    if int(card.get("cups") or 0) > 0:      yields.append(f"☕ {int(card['cups'])}")
    if int(card.get("treasures") or 0) > 0: yields.append(f"🏴‍☠️ {int(card['treasures'])}")
    gives_line = " · ".join(yields) if yields else "—"

    price_lines = []
    for t in tiers:
        if t["pay_type"] == "cash":
            code = (t.get("cash_code") or "").upper()
            price_lines.append(f"{code} {t['price']:.2f}")
        elif t["pay_type"] == "diamonds":
            price_lines.append(f"💎 {int(t['price'])}")
        elif t["pay_type"] == "cups":
            price_lines.append(f"☕ {int(t['price'])}")
        elif t["pay_type"] == "treasures":
            price_lines.append(f"🏴‍☠️ {int(t['price'])}")

    cnt = int(lot.get("items_count") or 0)
    st = str(lot.get("status") or "unknown")
    proof = "есть" if lot.get("cover_file_id") else "отсутствует"

    caption = (
            f"<b>{title}</b> — <i>{rarity}</i>\n"
            f"{deck_name}\n\n"
            f"<b>Цены:</b>\n" + ("\n".join(f"• {p}" for p in price_lines) or "—") + "\n\n"
                                                                                    f"Доступно: {cnt}\n"
                                                                                    f"Даёт: {gives_line}\n"
                                                                                    f"Фото подтверждения: {proof}\n\n"
                                                                                    f"<i>Статус: {st}</i>"
    )

    kb = my_sales_nav_kb(idx, len(ids), st)
    cover = lot.get("cover_file_id")

    if isinstance(target, CallbackQuery):
        if cover:
            media = InputMediaPhoto(media=cover, caption=caption, parse_mode="HTML")
            await target.message.edit_media(media=media, reply_markup=kb)
        else:
            await target.message.edit_text(caption, reply_markup=kb, parse_mode="HTML")
    else:
        if cover:
            await target.answer_photo(cover, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(caption, reply_markup=kb, parse_mode="HTML")


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?(Активные|Скрытые|Проданные|Архив|Все)$"))
async def my_sales_filter_click(message: Message, state: FSMContext):
    mapping = {"Активные": "active", "Скрытые": "hidden", "Проданные": "sold", "Архив": "archived", "Все": "all"}
    name = message.text.replace("▪️ ", "").replace("▫️ ", "")
    await _my_sales_enter(message, state, mapping[name])
