from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext

from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, ADD_CARD_FIELDS
from bot.handlers.admin.action_support.compat import start_add_card_fsm, send_admin_log
from bot.handlers.admin.helper.new.formatting import format_card_caption, format_admin_action_log
from bot.handlers.admin.helper.new.keyboards import confirm_keyboard, rarity_keyboard, \
    decks_menu_keyboard, decks_keyboard, back_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.helper.user_helpers import is_card_num_exists
from bot.core.legacy_config import ADMINS_OWNERS, ADMIN_SECRET
from db.legacy import get_all_decks, log_audit_action, add_card
from bot.legacy_fsm import AddCardFSM
from bot.telegram.callback_parser import split_callback_data


def register_cards_admin_handlers(router: Router):
    @router.message(F.text == "/addcard", F.chat.type == "private")
    @admin_only
    async def addcard_start(message: types.Message, state: FSMContext):
        await start_add_card_fsm(message, state)

    @router.message(AddCardFSM.waiting_for_admin_password)
    async def check_admin_password_card(message: types.Message, state: FSMContext):
        if message.from_user.id in ADMINS_OWNERS or message.text.strip() == ADMIN_SECRET:
            decks = await get_all_decks()
            if not decks:
                await message.answer("В базе нет ни одной колоды. Сначала добавьте колоду!")
                return
            kb = decks_keyboard(decks, prefix="admin_deck")
            await message.answer(
                "Владелец, доступ разрешён без пароля.\nВыбери колоду:",
                reply_markup=kb
            )
            await state.set_state(AddCardFSM.waiting_for_deck)
        else:
            await message.answer(
                "Пароль неверный.",
                reply_markup=back_keyboard(text="Отмена", callback="addcard_cancel")
            )

    @router.callback_query(F.data == "addcard_cancel")
    async def addcard_cancel_from_anywhere(call: types.CallbackQuery, state: FSMContext):
        await call.message.answer(ADMIN_MESSAGES["addcard_cancelled"])
        await state.clear()
        await call.answer()

    @router.callback_query(AddCardFSM.waiting_for_deck, F.data.startswith("admin_deck_"))
    async def addcard_choose_deck(call: types.CallbackQuery, state: FSMContext):
        deck_id = int(split_callback_data(call.data, "_")[-1])
        await state.update_data(deck_id=deck_id)
        await call.message.answer("Введи название карты:", reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_card_name)
        await call.answer()

    @router.message(AddCardFSM.waiting_for_card_name, F.text)
    async def addcard_card_name(message: types.Message, state: FSMContext):
        await state.update_data(card_name=message.text.strip())
        await message.answer(ADD_CARD_FIELDS[1][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_num)

    @router.message(AddCardFSM.waiting_for_num, F.text.regexp(r"^\d+$"))
    async def addcard_num(message: types.Message, state: FSMContext):
        num = int(message.text)
        if await is_card_num_exists(num):
            await message.answer(
                ADMIN_MESSAGES["card_num_duplicate"],
                reply_markup=back_keyboard()
            )
            return
        await state.update_data(num=num)
        await message.answer(ADD_CARD_FIELDS[2][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_hero_name)

    @router.message(AddCardFSM.waiting_for_num)
    async def addcard_num_incorrect(message: types.Message):
        await message.answer(
            ADMIN_MESSAGES["card_num_incorrect"],
            reply_markup=back_keyboard()
        )

    @router.message(AddCardFSM.waiting_for_hero_name, F.text)
    async def addcard_hero(message: types.Message, state: FSMContext):
        await state.update_data(hero_name=message.text.strip())
        await message.answer(ADD_CARD_FIELDS[3][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_image)

    @router.message(AddCardFSM.waiting_for_image, F.photo)
    async def addcard_image(message: types.Message, state: FSMContext):
        image_id = message.photo[-1].file_id
        await state.update_data(image_id=image_id)
        await message.answer(ADD_CARD_FIELDS[4][1], reply_markup=rarity_keyboard())
        await state.set_state(AddCardFSM.waiting_for_rarity)

    @router.callback_query(AddCardFSM.waiting_for_rarity, F.data.startswith("rarity|"))
    async def addcard_rarity_call(call: types.CallbackQuery, state: FSMContext):
        rarity_value = split_callback_data(call.data, "|")[1]
        await state.update_data(rarity=rarity_value)
        await call.message.answer(ADD_CARD_FIELDS[5][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_story)
        await call.answer()

    @router.message(AddCardFSM.waiting_for_rarity, F.text)
    async def addcard_rarity_text(message: types.Message, state: FSMContext):
        await state.update_data(rarity=message.text.strip())
        await message.answer(ADD_CARD_FIELDS[5][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_story)

    @router.message(AddCardFSM.waiting_for_story, F.text)
    async def addcard_story(message: types.Message, state: FSMContext):
        await state.update_data(story=message.text.strip())
        await message.answer(ADD_CARD_FIELDS[6][1], reply_markup=back_keyboard())
        await state.set_state(AddCardFSM.waiting_for_quote)

    @router.message(AddCardFSM.waiting_for_quote, F.text)
    async def addcard_quote(message: types.Message, state: FSMContext):
        quote = message.text.strip()
        await state.update_data(quote=None if quote == '-' else quote)
        data = await state.get_data()
        preview = format_card_caption(data)
        reply_markup = confirm_keyboard(
            yes_text=ADMIN_MESSAGES["confirm"],
            no_text=ADMIN_MESSAGES["cancel"],
            yes_callback="addcard_confirm_yes",
            no_callback="addcard_cancel"
        )
        await message.answer_photo(
            photo=data["image_id"],
            caption="<b>Предпросмотр карты:</b>\n\n" + preview,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await state.set_state(AddCardFSM.waiting_for_confirmation)

    @router.callback_query(AddCardFSM.waiting_for_confirmation, F.data == "addcard_confirm_yes")
    async def addcard_confirm(call: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await add_card(
            card_name=data["card_name"],
            num=data["num"],
            hero_name=data["hero_name"],
            image_id=data["image_id"],
            rarity=data["rarity"],
            deck_id=data["deck_id"],
            story=data["story"],
            quote=data.get("quote"),
        )
        await call.message.answer(ADMIN_MESSAGES["card_added"])
        await send_admin_log(
            call.bot,
            format_admin_action_log(
                action="add_card",
                admin={"id": call.from_user.id, "username": call.from_user.username or call.from_user.full_name},
                lot={"card_name": data["card_name"], "deck_id": data["deck_id"], "num": data["num"]}
            )
        )
        await log_audit_action(
            user_id=call.from_user.id,
            action_type="add_card",
            auction_id=None,
            details=f"Добавлена карта: {data.get('card_name')}, номер: {data.get('num')}, колода: {data.get('deck_id')}"
        )
        await state.clear()
        await call.answer()

    @router.message(
        F.state.in_([
            AddCardFSM.waiting_for_deck,
            AddCardFSM.waiting_for_card_name,
            AddCardFSM.waiting_for_num,
            AddCardFSM.waiting_for_hero_name,
            AddCardFSM.waiting_for_image,
            AddCardFSM.waiting_for_rarity,
            AddCardFSM.waiting_for_story,
            AddCardFSM.waiting_for_quote,
            AddCardFSM.waiting_for_confirmation,
            AddCardFSM.waiting_for_admin_password,
        ]),
        F.text.lower().in_(["отмена", "назад", "⬅️ назад"]),
        F.chat.type == "private"
    )
    async def universal_cancel_addcard(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "Добавление карты отменено.",
            reply_markup=decks_menu_keyboard()
        )

    @router.callback_query(F.data.in_(["addcard_cancel", "back"]))
    async def addcard_cancel_universal(call: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await call.message.answer(
            "Добавление карты отменено.",
            reply_markup=decks_menu_keyboard()
        )
        await call.answer()
