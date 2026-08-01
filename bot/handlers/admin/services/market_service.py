from __future__ import annotations

from aiogram import Bot, Router
from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery

from bot.handlers.admin.services.market_constants import STAR_DB_CODE, CB_PREFIX
from bot.handlers.admin.services.market_db_helpers import persist_proofs, fetch_card
from bot.handlers.admin.services.market_fsm import MarketAddFSM
from bot.handlers.admin.services.market_keyboards import market_reply_kb, my_listing_actions, \
    listing_public_kb
from bot.handlers.admin.services.market_render import build_accepts_sentence, build_card_preview_caption
from bot.handlers.admin.services.market_utils import can_publish_more, get_selected_ids
from bot.services.market import (
    get_all_decks,
    market_add_items,
    market_add_rate_tiers,
    market_create_listing,
    market_listing_display_tiers,
    market_listing_items,
    market_seller_listings,
    market_set_cover,
    market_set_item_proof,
    market_set_item_quantity,
)

router = Router(name="market")


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _kb_proof_each_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить для этой карты", callback_data=f"{CB_PREFIX}:add:proof:each:skip")]
    ])


async def _send_prompt(msg_or_call, *, text: str, image_id: str | None, kb: InlineKeyboardMarkup) -> None:
    target = msg_or_call.message if hasattr(msg_or_call, "message") else msg_or_call
    if image_id:
        await target.answer_photo(image_id, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


def _is_skip_desc(s: str | None) -> bool:
    if s is None:
        return True
    t = s.strip().lower()
    return t in {"", "-", "—", "–", "−", "skip", "пропустить"}


# async def can_publish_more(_: Bot, user_id: int) -> Tuple[bool, int]:
#     """Проверка лимита активных объявлений."""
#     if await is_luxury_user(user_id):
#         return True, 999_999
#     used = await market_count_active(user_id)
#     left = max(0, MAX_LISTINGS_ORDINARY - used)
#     return left > 0, left

@router.message(Command("market"), F.chat.type == "private")
async def market_root(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await message.answer("Кнопки снизу активированы.", reply_markup=market_reply_kb())
    await message.answer(
        "<b>🛒 Магазин</b>\n\n"
        "Здесь продают:\n"
        "• 🃏 карты поштучно\n"
        "• 📚 целые колоды\n"
        "• 💎 алмазы, ☕ чашки, 🏴‍☠️ сокровища\n\n"
        "Полезное: /sell, /find, /my_sales",
        parse_mode="HTML",
    )


async def get_decks_list(page: int | None = None) -> list[dict]:
    return await get_all_decks()


CB_PREFIX = "market"


@router.message(F.text == "🔍 Поиск")
async def _find_btn(message: Message, state: FSMContext):
    await message.answer("Введи /find и параметры фильтра. Скоро сделаю мастера поиска по кнопкам.")

@router.message(F.text == "🧾 Мои сделки")
async def _my_deals_btn(message: Message):
    await message.answer("История сделок появится позже.")


@router.callback_query(MarketAddFSM.CONFIRM, F.data == f"{CB_PREFIX}:confirm:yes")
async def cb_confirm_yes(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()

    cards = list(map(int, data.get("card_ids") or data.get("cards") or []))
    prices_raw: dict = dict(data.get("prices") or {})
    qty_map: dict = dict(data.get("qty_map") or {})
    default_qty = int(data.get("quantity") or 1)

    desc = (data.get("description") or "").strip()

    custom_variants: list[str] = list(data.get("custom_variants") or [])
    accepts_line = build_accepts_sentence(custom_variants)
    desc_final = desc
    if accepts_line:
        desc_final = (desc_final + f"\n\n{accepts_line}").strip()

    cover_file_id = data.get("proof_file_id") or None
    proof_map: dict[str, str] = dict(data.get("proof_by_card") or {})

    def _as_tiers(obj: dict) -> dict:
        if not obj:
            return {}
        if all(isinstance(v, (int, float, str)) for v in obj.values()):
            out = {}
            for k, v in obj.items():
                try:
                    out[k] = float(v)
                except Exception:
                    pass
            return out
        for key in ("common", "__all__", "_all", "*"):
            sub = obj.get(key)
            if isinstance(sub, dict):
                return _as_tiers(sub)
        return {}

    common = _as_tiers(prices_raw)

    created = 0

    for card_id in cards:
        per_card: dict = dict(prices_raw.get(str(card_id)) or {}) or dict(common)

        if not per_card and not custom_variants:
            continue

        qty = int(qty_map.get(str(card_id)) or default_qty or 1)

        lid = await market_create_listing(
            seller_id=call.from_user.id,
            currency_type="cash",
            price_num=0,
            cash_code=None,
            description=desc_final,
        )

        await market_add_items(lid, [card_id])

        await persist_proofs(lid, state)

        await market_set_item_quantity(lid, card_id, qty)

        if cover_file_id:
            await market_set_cover(lid, cover_file_id)

        per_item_fid = proof_map.get(str(card_id))
        if per_item_fid:
            await market_set_item_proof(lid, card_id, per_item_fid)

        tiers_payload, order = [], 0
        for key, val in per_card.items():
            k = str(key).lower()
            if k.startswith("cash:"):
                tiers_payload.append({
                    "label": None, "qty": None, "pay_type": "cash",
                    "cash_code": k.split(":", 1)[1].upper(),
                    "price": float(val), "sort_order": order
                })
            elif k in ("cups", "diamonds", "treasures"):
                tiers_payload.append({
                    "label": None, "qty": None, "pay_type": k,
                    "cash_code": None, "price": float(val), "sort_order": order
                })
            elif k == "tgstars":  # ← ДОБАВЬ ЭТУ ВЕТКУ
                tiers_payload.append({
                    "label": None, "qty": None, "pay_type": "cash",
                    "cash_code": STAR_DB_CODE, "price": float(val), "sort_order": order
                })
            order += 1

        if tiers_payload:
            await market_add_rate_tiers(lid, tiers_payload)

        created += 1

    await state.clear()
    await call.answer("Готово!")
    await bot.send_message(call.from_user.id, f"✅ Создано объявлений: {created}")


async def show_my_sales(message: Message) -> None:
    user_id = message.from_user.id

    listings = await market_seller_listings(user_id)

    if not listings:
        await message.answer(
            "У тебя пока нет объявлений. Исправлять это будем или продолжаем коллекционировать пустоту?")
        return

    for listing in listings:
        lid = int(listing["listing_id"])

        items = await market_listing_items(lid)
        tiers = await market_listing_display_tiers(lid)

        per_card_prices: dict[str, float | int] = {}
        for t in tiers or []:
            if int(t["qty"]) != 1:
                continue
            pt = str(t["pay_type"]).lower()
            if pt == "cash":
                code = (t["cash_code"] or "").upper() or "BYN"
                per_card_prices[f"cash:{code}"] = float(t["price"])
            elif pt in ("cups", "diamonds", "treasure", "treasures", "tgstars"):
                per_card_prices[pt] = int(float(t["price"]))
            else:
                per_card_prices[pt] = float(t["price"])

        if items:
            qty = sum(int(x["quantity"]) for x in items)
            if len(items) == 1:
                it = items[0]
                card = {
                    "title": it["card_name"],
                    "hero": it["hero_name"],
                    "rarity": it["rarity"],
                }
                photo_id = it["image_id"] or listing["cover_file_id"]
            else:
                card = {"title": f"Лот из {len(items)} карт"}
                photo_id = listing["cover_file_id"] or items[0]["image_id"]
        else:
            qty = 0
            card = {"title": f"Лот #{lid}"}
            photo_id = listing["cover_file_id"]

        has_proof = bool(listing["cover_file_id"])
        description = listing["description"]
        cash_code = None

        cap = build_card_preview_caption(
            card, per_card_prices or None, cash_code, description,
            has_proof=has_proof, qty_available=qty, status=listing["status"],
            created_at=listing.get("created_at")
        )

        kb = my_listing_actions(lid, status=listing["status"])

        if photo_id:
            await message.answer_photo(photo=photo_id, caption=cap, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(cap, parse_mode="HTML", reply_markup=kb)


@router.callback_query(MarketAddFSM.CONFIRM, F.data == f"{CB_PREFIX}:confirm:no")
async def cb_confirm_no(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Отменено.")
    await call.answer()


@router.message(MarketAddFSM.CONFIRM)
async def confirm_listing(message: Message, state: FSMContext, bot: Bot):
    if (message.text or "").strip().lower() != "да":
        await state.clear()
        await message.answer("Отменено.")
        return

    data = await state.get_data()
    can, _ = await can_publish_more(bot, message.from_user.id)
    if not can:
        await state.clear()
        await message.answer("Лимит объявлений исчерпан.")
        return

    cards = sorted(get_selected_ids(data))
    prices: dict = dict(data.get("prices") or {})
    desc = data.get("description")

    created = 0
    for card_id in cards:
        per_card: dict = dict(prices.get(str(card_id)) or {})
        if not per_card:
            continue

        lid = await market_create_listing(
            seller_id=message.from_user.id,
            currency_type="cash",
            price_num=0,
            cash_code=None,
            description=desc,
        )
        await market_add_items(lid, [card_id])

        tiers, order = [], 0
        for key, val in per_card.items():
            if str(key).startswith("cash:"):
                tiers.append({"label": None, "qty": None, "pay_type": "cash",
                              "cash_code": str(key).split(":", 1)[1].upper(),
                              "price": float(val), "sort_order": order})
            else:
                tiers.append({"label": None, "qty": None, "pay_type": key,
                              "cash_code": None, "price": float(val), "sort_order": order})
            order += 1
        await market_add_rate_tiers(lid, tiers)

        card = await fetch_card(card_id)
        caption = build_card_preview_caption(card, per_card, None, desc)
        if card.get("image_id"):
            await message.answer_photo(card["image_id"], caption=caption, parse_mode="HTML",
                                       reply_markup=listing_public_kb(message.from_user.id, lid, True))
        else:
            await message.answer(caption, parse_mode="HTML",
                                 reply_markup=listing_public_kb(message.from_user.id, lid, True))
        created += 1

    await state.clear()
    await message.answer(f"Готово. Создано объявлений: {created}.")

# Public compatibility aliases. Cross-feature imports must use these names.
kb_proof_each_skip = _kb_proof_each_skip
send_prompt = _send_prompt
