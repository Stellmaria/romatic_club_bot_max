"""Cards, users, logs, broadcasts, and statistics menus.

Handlers retain their relative order from the legacy ``admin_panel`` module.
"""

from bot.handlers.admin.admin_panel_shared import *  # noqa: F403
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)


@router.message(F.text.in_(["🔎 Список карт", "/cards"]), F.chat.type == "private")
@admin_only
async def show_decks_for_cards(message: Message):
    decks = await get_all_decks()
    if not decks:
        await message.answer("В базе нет ни одной колоды!")
        return
    await message.answer(
        "Выберите колоду для просмотра списка карт:",
        reply_markup=decks_keyboard(decks, prefix="show_deck")
    )


@router.callback_query(F.data.startswith("show_deck_"))
@admin_only
async def show_cards_in_deck(call: CallbackQuery):
    deck_id = int(split_callback_data(call.data, "_")[-1])
    cards = await get_cards_by_deck_id(deck_id)
    if not cards:
        await call.message.answer("В этой колоде пока нет ни одной карты.")
        await call.answer()
        return
    decks = await get_all_decks()
    deck_name = next((d['deck_name'] for d in decks if d['deck_id'] == deck_id), "-")
    await call.message.answer(f"<b>Карты в колоде <u>{deck_name}</u>:</b>", parse_mode="HTML")
    for card in cards:
        rarity = (card.get('rarity') or '').strip().lower()
        emoji = RARITY_EMOJI.get(rarity, '')
        treasure = RARITY_TREASURE.get(rarity)
        rarity_ru = RARITY_RU.get(rarity, rarity)
        caption = (
            f"{emoji} Название: {card['card_name']}\n"
            f"Герой: {card.get('hero_name', '-')}\n"
            f"Номер: {card.get('num', '-')}\n"
            f"Редкость: {rarity_ru}"
        )
        if treasure:
            caption += f"  —  За разбив: {treasure} 🪙 сокровищ"
        caption += f"\nИстория: {card.get('story', '-')}\n"
        if card.get("quote"):
            caption += f"Цитата: {card['quote']}\n"
        image_id = card.get("image_id")
        try:
            if image_id and image_id != "DEFAULT_PHOTO_ID":
                await _answer_media_any(call.message, image_id, caption=caption)
            else:
                await call.message.answer(caption)
        except Exception as e:
            await call.message.answer(f"{emoji} {card['card_name']}\n[Ошибка отправки медиа: {e}]")
    await call.answer()


@router.message(F.text == "👥 Пользователи", F.chat.type == "private")
@admin_only
async def users_menu(message: Message):
    await message.answer(
        "Действия с пользователями:",
        reply_markup=menu_keyboard(
            ["👤 Список админов", "👥 Список пользователей", "🤝 Список доверенных"],
            ["⬅️ Назад"]
        )
    )


@router.message(F.text == "🚫 Логи", F.chat.type == "private")
@admin_only
async def logs_menu(message: Message):
    await message.answer(
        "Действия с логами и аудитом:",
        reply_markup=menu_keyboard(
            ["📋 Аудит-логи"],
            ["⬅️ Назад"]
        )
    )


@router.message(F.text == "📋 Аудит-логи", F.chat.type == "private")
@admin_only
async def audit_logs_cmd(message: Message):
    logs = await get_audit_logs()
    if not logs:
        await message.answer("Логи не найдены.")
        return
    msg = "<b>Последние действия админов:</b>\n"
    msg += "".join(format_log_entry(log) for log in logs)
    await message.answer(msg, parse_mode="HTML")


@router.message(F.text == "📣 Рассылка", F.chat.type == "private")
@admin_only
async def broadcast_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Массовая рассылка всем пользователям:",
        reply_markup=menu_keyboard(["✉️ Создать рассылку"], ["⬅️ Назад"])
    )


@router.message(F.text == "✉️ Создать рассылку", F.chat.type == "private")
@admin_only
async def start_broadcast_from_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        ADMIN_MESSAGES["broadcast_enter_text"],
        reply_markup=menu_keyboard([BUTTONS["cancel"]])
    )
    await state.set_state(BroadcastFSM.waiting_for_text)


@router.message(F.text == "📊 Статистика", F.chat.type == "private")
@admin_only
async def stats_menu(message: Message):
    await message.answer(
        "Раздел: Статистика",
        reply_markup=menu_keyboard(
            ["📈 Показать статистику"],
            ["📅 Полное расписание"],
            ["⬅️ Назад"],
        ),
    )


@router.message(F.text == "📅 Полное расписание", F.chat.type == "private")
@admin_only
async def stats_full_schedule(message: Message, state: FSMContext):
    await state.clear()

    today = date.today()
    year, month = today.year, today.month

    await message.answer(
        "Выберите месяц для просмотра расписания:",
        reply_markup=_kb_stats_schedule_navigator(year, month),
    )
    await state.set_state(PreviewScheduleFSM.choosing_month)


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data.startswith("stats_schedule_set|"))
@admin_only
async def stats_schedule_set_month(call: CallbackQuery):
    try:
        _, ym = split_callback_data(call.data or "", "|", 1)
        year_s, month_s = ym.split("-", 1)
        year = int(year_s)
        month = int(month_s)
        if month < 1 or month > 12:
            raise ValueError("month out of range")
    except Exception:
        await call.answer("Кривая кнопка.", show_alert=True)
        return

    msg = getattr(call, "message", None)
    if isinstance(msg, Message):
        try:
            await msg.edit_reply_markup(reply_markup=_kb_stats_schedule_navigator(year, month))
        except Exception:
            pass

    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data == "stats_schedule_today")
@admin_only
async def stats_schedule_today(call: CallbackQuery):
    today = date.today()
    year, month = today.year, today.month

    msg = getattr(call, "message", None)
    if isinstance(msg, Message):
        try:
            await msg.edit_reply_markup(reply_markup=_kb_stats_schedule_navigator(year, month))
        except Exception:
            pass

    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data == "stats_schedule_noop")
@admin_only
async def stats_schedule_noop(call: CallbackQuery):
    await call.answer()


@router.message(F.text == "🎴 Карты", F.chat.type == "private")
async def cards_menu(message: Message):
    await message.answer(
        "Раздел: Карты",
        reply_markup=decks_menu_keyboard()
    )


@router.message(F.text == "➕ Добавить колоду", F.chat.type == "private")
async def add_deck_button(message: Message, state: FSMContext):
    await add_deck_fsm_entry(message, state)


@router.message(AddDeckFSM.waiting_for_admin_password)
async def check_admin_password(message: Message, state: FSMContext):
    if is_owner_or_valid_secret(message.from_user.id, message.text):
        await message.answer("Пароль принят. Введите название новой колоды:", reply_markup=back_keyboard())
        await state.set_state(AddDeckFSM.waiting_for_deck_name)
    else:
        await message.answer("❌ Неверный пароль!", reply_markup=back_keyboard())


@router.message(AddDeckFSM.waiting_for_deck_name)
async def deck_name_received(message: Message, state: FSMContext):
    deck_name = message.text.strip()
    await state.update_data(deck_name=deck_name)
    await message.answer(
        f"Добавить новую колоду с названием:\n<b>{deck_name}</b>?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add_deck")],
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_deck")],
            ]
        ),
        parse_mode="HTML"
    )
    await state.set_state(AddDeckFSM.waiting_for_confirmation)


@router.callback_query(AddDeckFSM.waiting_for_confirmation, F.data == "confirm_add_deck")
async def confirm_add_deck(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await add_deck(data["deck_name"])
    await call.message.answer(f"Колода <b>{data['deck_name']}</b> успешно добавлена!", parse_mode="HTML")
    await log_audit_action(
        user_id=call.from_user.id,
        action_type="add_deck",
        auction_id=None,
        details=f"Добавлена колода: {data['deck_name']}"
    )
    await send_admin_log(
        call.bot,
        format_admin_action_log(
            action="add_deck",
            admin={"id": call.from_user.id, "username": call.from_user.username or call.from_user.full_name},
            lot={"deck_name": data["deck_name"]}
        )
    )
    await state.clear()
    await call.answer()


@router.callback_query(AddDeckFSM.waiting_for_confirmation, F.data == "cancel_add_deck")
async def cancel_add_deck(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Добавление колоды отменено.")
    await state.clear()
    await call.answer()


@router.message(F.text == "➕ Добавить карту", F.chat.type == "private")
@admin_only
async def add_card_button(message: Message, state: FSMContext):
    await start_add_card_fsm(message, state)


@router.callback_query(F.data == "universal_cancel")
@admin_only
async def universal_cancel_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await send_admin_main_menu(call.message)
    await call.answer()


@router.callback_query(F.data.in_(CANCEL_TEXTS.keys()))
@admin_only
async def universal_cancel(call: CallbackQuery, state: FSMContext):
    await process_universal_cancel_callback(call, state)


@router.message(
    F.text.lower().in_(["назад", "⬅️ назад", "отмена"]),
    F.chat.type == "private"
)
@admin_only
async def universal_back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await send_admin_main_menu(message)


@router.callback_query(F.data.in_([
    "admin_back",
    "addadmin_cancel", "removeadmin_cancel",
    "givetrusted_cancel", "removetrusted_cancel",
    "universal_cancel"
]))
@admin_only
async def admin_inline_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.bot.send_message(
        call.message.chat.id,
        ADMIN_MESSAGES.get("admin_panel_greeting", "Добро пожаловать в админ-панель! Выберите раздел:"),
        reply_markup=menu_keyboard(
            ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
            ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
        )
    )
    await call.answer()


@router.callback_query(F.data == "pending_menu:auctions")
@admin_only
async def pending_menu_auctions(call: CallbackQuery):
    lots = await get_pending_auctions()
    if not lots:
        await call.message.answer("Нет pending-аукционов.")
        await call.answer()
        return

    for lot in lots:
        owners_list: list[Owner] = cast(list[Owner], await get_lot_owners(lot["auction_id"]))
        text = format_pending_lot(lot, owners_list)
        kb = build_lot_keyboard(lot, role="admin")
        await send_lot_card_safe(call.message, lot, text, kb)

    await call.answer()


@router.message(F.text.in_(['/adminhelp', '/admin_help']), F.chat.type == "private")
@admin_only
async def admin_help(message: Message):
    await message.answer(ADMIN_COMMANDS_INFO, parse_mode="HTML")


__all__ = [
    "router",
    "show_decks_for_cards",
    "show_cards_in_deck",
    "users_menu",
    "logs_menu",
    "audit_logs_cmd",
    "broadcast_menu",
    "start_broadcast_from_menu",
    "stats_menu",
    "stats_full_schedule",
    "stats_schedule_set_month",
    "stats_schedule_today",
    "stats_schedule_noop",
    "cards_menu",
    "add_deck_button",
    "check_admin_password",
    "deck_name_received",
    "confirm_add_deck",
    "cancel_add_deck",
    "add_card_button",
    "universal_cancel_callback",
    "universal_cancel",
    "universal_back_to_main",
    "admin_inline_back",
    "pending_menu_auctions",
    "admin_help",
]
