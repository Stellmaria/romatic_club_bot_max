import html

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

import importlib
from bot.handlers.admin.services.market_constants import CB_BUMP, CB_PREFIX
from bot.handlers.admin.services.market_db_helpers import fetch_card
from bot.handlers.admin.services.market_keyboards import edit_listing_kb, my_listing_actions, listing_public_kb, \
    market_reply_kb
from bot.handlers.admin.services.market_render import _reload_listing_inplace
from bot.handlers.admin.services.market_utils import ensure_owner, can_bump_now, _upsert_price, safe_delete
from bot.services.market import market_get_status, market_quantity_total
from db.legacy import market_get_listing, market_bump, market_set_status, _get_listing_core, market_toggle_actual

router = Router(name="market_manage")

_MY = "my_sales"  # ключ в FSM


@router.callback_query(F.data.startswith(f"{CB_BUMP}:"))
async def cb_bump(call: CallbackQuery):
    lid = int(call.data.split(":")[2])
    if not await ensure_owner(call, lid):
        return
    listing = await market_get_listing(lid)
    ok, left = can_bump_now(listing)
    if not ok:
        mins = (left // 60) + 1
        await call.answer(f"Рано. Можно апнуть через ~{mins} мин.", show_alert=True)
        return
    await market_bump(lid)
    await call.answer("Поднято в списке.")


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:act:"))
async def ask_action(call: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        _, _, action, lid_str = call.data.split(":")
    except ValueError:
        await call.answer()
        return
    lid = int(lid_str)

    if action == "hide":
        await market_set_status(lid, "hidden")
        await call.answer("Скрыто")

    elif action == "activate":
        await market_set_status(lid, "active")
        await call.answer("Сделано активным")

    elif action == "archive":
        await market_set_status(lid, "archived")
        await call.answer("Отправлено в архив")

    elif action == "sold":
        left = await market_quantity_total(lid)

        if left <= 0:
            await call.answer("Остатка нет.")
            return

        if left == 1:
            from db.legacy import market_dec_item_qty, market_set_status
            new_left = await market_dec_item_qty(lid, 1)
            if new_left <= 0:
                await market_set_status(lid, "sold")
            await _reload_listing_inplace(call, lid)
            await call.answer("Продано 1.")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="−2", callback_data=f"{CB_PREFIX}:do:soldqty:{lid}:2"),
            InlineKeyboardButton(text="−1", callback_data=f"{CB_PREFIX}:do:soldqty:{lid}:1"),
        ]])
        await call.message.answer("Сколько продали?", reply_markup=kb)
        await call.answer()
        return

    elif action == "edit":
        await call.message.answer("Что редактировать?", reply_markup=edit_listing_kb(lid))
        await call.answer()
        return


    elif action == "del":
        await state.update_data(_del_msg_id=call.message.message_id, _del_chat_id=call.message.chat.id)
        await call.answer("Удалить объявление? Действие необратимо.", show_alert=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"{CB_PREFIX}:do:del:yes:{lid}")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{CB_PREFIX}:do:del:no:{lid}")],
        ])
        await call.message.answer("Удалить это объявление безвозвратно?", reply_markup=kb)
        return

    else:
        await call.answer()
        return

    status = await market_get_status(lid) or "active"
    try:
        await call.message.edit_reply_markup(reply_markup=my_listing_actions(lid, status))
    except Exception:
        pass


async def set_price_value(message: Message, state: FSMContext):
    data = await state.get_data()
    lid = int(data["edit_lid"])
    pay_type = str(data["pay_type"])
    cash_code = data.get("cash_code")

    raw = (message.text or "").strip()

    if raw in {"-", "—", "–"}:
        await _upsert_price(lid, pay_type, None, cash_code)
        await message.answer("Цена удалена.")
        await state.clear()
        await _reload_listing_inplace(message, lid)
        return

    def bad():
        if pay_type in ("cups", "diamonds", "treasures", "tgstars"):
            return "Нужно целое число ≥ 0. Пример: 25"
        return "Нужно число ≥ 0. Пример: 2.50"

    try:
        txt = raw.replace(",", ".")
        if pay_type in ("cups", "diamonds", "treasures", "tgstars"):
            val = int(float(txt))
            if val < 0:
                raise ValueError
        else:
            val = float(txt)
            if val < 0:
                raise ValueError
    except Exception:
        await message.answer(bad())
        return

    await _upsert_price(lid, pay_type, val, cash_code)
    await message.answer("Цена обновлена.")
    await state.clear()
    await _reload_listing_inplace(message, lid)


@router.callback_query(F.data.startswith("mkt:del:"))
async def del_listing(call: CallbackQuery):
    lid = int(call.data.split(":")[2])
    await market_set_status(lid, "deleted")
    await call.answer("Удалено")
    await safe_delete(call.message)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:do:"))
async def do_action(call: CallbackQuery):
    _, _, action, lid_str, verdict = call.data.split(":")
    lid = int(lid_str)
    if verdict == "no":
        await call.answer("Отменено")
        return
    if not await ensure_owner(call, lid):
        return

    if action == "hide":
        await market_set_status(lid, "hidden")
        await call.answer("Скрыто")
    elif action == "arch":
        await market_set_status(lid, "archived")
        await call.answer("В архиве")
    elif action == "del":
        await market_set_status(lid, "deleted")
        await call.answer("Удалено")

    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:proof:show:"))
async def cb_show_proof(call: CallbackQuery, state: FSMContext, bot: Bot):
    lid = int(call.data.split(":")[-1])
    listing = await _get_listing_core(lid)
    if not listing:
        await call.answer("Лот не найден", show_alert=True);
        return

    proof_one = listing.get("proof_file_id")
    proof_map: dict = listing.get("proof_by_card") or {}

    if not proof_one and not proof_map:
        await call.answer("Фото подтверждения не прикреплено.");
        return

    if proof_one:
        await call.message.answer_photo(proof_one, caption="Фото подтверждения (общее для лота).")

    if proof_map:
        items = []
        for k, v in proof_map.items():
            if not v: continue
            try:
                cid = int(k)
            except:
                continue
            items.append((cid, v))

        from aiogram.types import InputMediaPhoto
        for pack in (items[i:i + 10] for i in range(0, len(items), 10)):
            media = []
            for cid, fid in pack:
                card = await fetch_card(cid)
                hero = html.escape(card.get("hero_name") or "")
                name = html.escape(card.get("card_name") or "")
                rarity = str(card.get("rarity") or "?")
                media.append(InputMediaPhoto(fid, caption=f"Пруф для: {hero} — {name} [{rarity}]", parse_mode="HTML"))
            if len(media) == 1:
                it = media[0]
                await call.message.answer_photo(it.media, caption=it.caption, parse_mode="HTML")
            else:
                await bot.send_media_group(call.message.chat.id, media)

    await call.answer("Показываю фото")


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:toggle:"))
async def cb_toggle_actual(call: CallbackQuery):
    _, _, lid_str = call.data.split(":")
    lid = int(lid_str)

    st = await market_toggle_actual(lid)

    try:
        await call.message.edit_reply_markup(
            reply_markup=listing_public_kb(call.from_user.id, lid, st == "active")
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await call.answer("Статус обновлён")


# --- Смена фильтра нижними кнопками ------------------------------------------
@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?Активные$"))
async def ms_f_active(message: Message, state: FSMContext):
    await importlib.import_module("bot.handlers.admin.services.market_add_flow")._my_sales_set_filter_and_show(message, state, "active")


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?Скрытые$"))
async def ms_f_hidden(message: Message, state: FSMContext):
    await importlib.import_module("bot.handlers.admin.services.market_add_flow")._my_sales_set_filter_and_show(message, state, "hidden")


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?Проданные$"))
async def ms_f_sold(message: Message, state: FSMContext):
    await importlib.import_module("bot.handlers.admin.services.market_add_flow")._my_sales_set_filter_and_show(message, state, "sold")


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?Архив$"))
async def ms_f_archived(message: Message, state: FSMContext):
    await importlib.import_module("bot.handlers.admin.services.market_add_flow")._my_sales_set_filter_and_show(message, state, "archived")


@router.message(F.chat.type == "private", F.text.regexp(r"^(?:[▪▫]\s)?Все$"))
async def ms_f_all(message: Message, state: FSMContext):
    await importlib.import_module("bot.handlers.admin.services.market_add_flow")._my_sales_set_filter_and_show(message, state, "all")


@router.message(F.chat.type == "private", F.text == "⬅️ Назад")
async def ms_back(message: Message, state: FSMContext):
    # возвращаем обычную клавиатуру магазина
    await message.answer("\u200B", reply_markup=market_reply_kb())
    try:
        await message.delete()
    except:
        pass
    await state.update_data({_MY: None})
