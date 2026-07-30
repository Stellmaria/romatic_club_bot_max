"""Pending lot field editing.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

from bot.handlers.admin.moderation_shared import *  # noqa: F403

router = Router(name=__name__)


@router.callback_query(F.data.startswith("edit_pending_lot|"))
@admin_only
async def edit_pending_lot_menu(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⚙️ Тип аука", callback_data=f"edit_pending_kind|{auction_id}")],
        [types.InlineKeyboardButton(text="🆔 Крафт на UID", callback_data=f"edit_pending_craft|{auction_id}")],
        [types.InlineKeyboardButton(text="💵 Изменить цену", callback_data=f"edit_pending_price|{auction_id}")],
        [types.InlineKeyboardButton(text="💱 Изменить валюту", callback_data=f"edit_pending_currency|{auction_id}")],
        [types.InlineKeyboardButton(text="💬 Комментарий", callback_data=f"edit_pending_comment|{auction_id}")],
        [types.InlineKeyboardButton(text="🖼/🎞 Задать/сменить медиа", callback_data=f"set_lot_photo|{auction_id}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_to_lot|{auction_id}")]
    ])
    await call.message.answer("Что хотите изменить?", reply_markup=kb)
    await state.set_state(ApproveLotFSM.editing_pending_lot)
    await call.answer()


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("edit_pending_kind|"))
@admin_only
async def edit_pending_kind(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⭐ Стандартный", callback_data=f"pending_set_kind|standard|{auction_id}")],
        [types.InlineKeyboardButton(text="✨ Обратный", callback_data=f"pending_set_kind|reverse|{auction_id}")],
        [types.InlineKeyboardButton(text="⚡ Быстрый", callback_data=f"pending_set_kind|fast|{auction_id}")],
        [types.InlineKeyboardButton(text="🪶 Свободный", callback_data=f"pending_set_kind|free|{auction_id}")],
        [types.InlineKeyboardButton(text="👑 Чёрный", callback_data=f"pending_set_kind|black|{auction_id}")],
        [types.InlineKeyboardButton(text="🛍 Биржа", callback_data=f"pending_set_kind|exchange|{auction_id}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"edit_pending_lot|{auction_id}")],
    ])

    await call.message.answer("Выберите вид аукциона:", reply_markup=kb)
    await call.answer()


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("edit_pending_craft|"))
@admin_only
async def edit_pending_craft(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Да", callback_data=f"pending_set_craft|1|{auction_id}"),
            types.InlineKeyboardButton(text="❌ Нет", callback_data=f"pending_set_craft|0|{auction_id}"),
        ],
        [types.InlineKeyboardButton(text="♻️ Сброс", callback_data=f"pending_set_craft|none|{auction_id}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"edit_pending_lot|{auction_id}")],
    ])

    await call.message.answer("Крафт на UID возможен?", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("pending_set_craft|"))
@admin_only
async def pending_set_craft(call: types.CallbackQuery, state: FSMContext):
    _, raw, auction_id_raw = (call.data or "").split("|", 2)
    auction_id = int(auction_id_raw)

    raw = raw.strip().lower()
    if raw in {"1", "yes", "true"}:
        val = True
    elif raw in {"0", "no", "false"}:
        val = False
    else:
        val = None

    old_lot = await get_lot_by_id(auction_id)
    old_val = (old_lot or {}).get("craft_uid_possible")

    await _update_auction_field(auction_id, "craft_uid_possible", val)

    await _log_pending_change(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        action_type="edit_pending_craft_uid",
        field_title="Крафт на UID",
        old_value=old_val,
        new_value=val,
    )

    await notify_owners_pending_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        changes=[("Крафт на UID", old_val, val)],
    )


    await call.message.answer("✅ Крафт на UID обновлён.")
    await _send_pending_lot_card(call.message, call.bot, auction_id)
    await state.clear()
    await call.answer()


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("edit_pending_comment|"))
@admin_only
async def edit_pending_comment(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    await call.message.answer("Введите комментарий (или '-' чтобы очистить):")
    await state.set_state(ApproveLotFSM.editing_pending_comment)
    await call.answer()


@router.message(ApproveLotFSM.editing_pending_comment, F.text)
@admin_only
async def save_pending_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    auction_id = int(data.get("auction_id") or 0)
    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    old_lot = await get_lot_by_id(auction_id)
    old_comment = (old_lot or {}).get("comment")

    raw = (message.text or "").strip()
    new_comment = "" if raw == "-" else raw

    await _update_auction_field(auction_id, "comment", new_comment)

    await notify_owners_lot_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Комментарий", old_comment, new_comment)],
    )


    await _log_pending_change(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        action_type="edit_pending_comment",
        field_title="Комментарий",
        old_value=old_comment,
        new_value=new_comment,
    )

    await message.answer("✅ Комментарий обновлён.")
    await _send_pending_lot_card(message, message.bot, auction_id)
    await state.clear()


@router.callback_query(F.data.startswith("pending_set_kind|"))
@admin_only
async def pending_set_kind(call: types.CallbackQuery, state: FSMContext):
    _, kind, auction_id_raw = (call.data or "").split("|", 2)
    auction_id = int(auction_id_raw)

    old_lot = await get_lot_by_id(auction_id)
    old_kind = (old_lot or {}).get("auction_kind")

    await _update_auction_field(auction_id, "auction_kind", kind)

    await _log_pending_change(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        action_type="edit_pending_kind",
        field_title="Тип аука",
        old_value=old_kind,
        new_value=kind,
    )

    await notify_owners_pending_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        changes=[("Тип аука", old_kind, kind)],
    )


    await call.message.answer("✅ Тип аука обновлён.")
    await _send_pending_lot_card(call.message, call.bot, auction_id)
    await state.clear()
    await call.answer()


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("edit_pending_price|"))
@admin_only
async def edit_pending_price(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    await call.message.answer("Введите новую стартовую цену (число):")
    await state.set_state(ApproveLotFSM.editing_pending_price)
    await call.answer()


@router.message(ApproveLotFSM.editing_pending_price)
@admin_only
async def save_pending_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    auction_id = int(data.get("auction_id") or 0)
    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    old_lot = await get_lot_by_id(auction_id)
    old_price = (old_lot or {}).get("start_price")

    try:
        new_price = int((message.text or "").strip())
    except Exception:
        await message.answer("Ошибка формата! Введите целое число.")
        return

    await _update_auction_field(auction_id, "start_price", new_price)

    await _log_pending_change(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        action_type="edit_pending_price",
        field_title="Стартовая цена",
        old_value=old_price,
        new_value=new_price,
    )

    await notify_owners_pending_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        changes=[("Стартовая цена", old_price, new_price)],
    )


    await message.answer(f"✅ Цена обновлена: {new_price}")
    await _send_pending_lot_card(message, message.bot, auction_id)
    await state.clear()


@router.callback_query(F.data.startswith("set_lot_photo|"))
@admin_only
async def set_lot_photo_from_lot(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)
    await call.message.answer(
        "Пришли фото для лота. Предыдущее фото (если было) будет заменено.",
        reply_markup=back_keyboard(text="Назад", callback=f"back_to_lot|{auction_id}")
    )
    await state.set_state(ApproveLotFSM.uploading_image)
    await call.answer()


@router.message(ApproveLotFSM.uploading_image, F.photo | F.video | F.animation | F.document)
@admin_only
async def handle_uploaded_lot_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    auction_id = int(data.get("auction_id") or 0)
    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return
    old_lot = await get_lot_by_id(auction_id)
    old_media = (old_lot or {}).get("image_id")
    media_id = _extract_media_file_id(message)
    if not media_id:
        await message.answer("Пожалуйста, пришли фото/видео для лота (или нажми 'Назад').")
        return
    await _update_auction_field(auction_id, "image_id", media_id)
    await _log_pending_field_change(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        field_title="Медиа (фото/видео)",
        old_value=old_media,
        new_value=media_id,
        action_type="pending_edit_media",
        lot_override={"image_id": media_id},
    )
    await notify_owners_pending_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        changes=[("Медиа", short_media_id(old_media), short_media_id(media_id))],
    )

    await message.answer("✅ Медиа успешно сохранено для лота.")
    await _send_pending_lot_card(message, message.bot, auction_id)
    await state.clear()


@router.message(ApproveLotFSM.uploading_image)
@admin_only
async def handle_uploaded_lot_not_photo(message: types.Message, state: FSMContext):
    await message.answer("Пожалуйста, пришли изображение для лота или нажми 'Назад'.")


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("edit_pending_currency|"))
@admin_only
async def edit_pending_currency(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💎 Алмазы", callback_data="currency_diamonds")],
        [types.InlineKeyboardButton(text="🍵 Чашки", callback_data="currency_cups")],
        [types.InlineKeyboardButton(text="🍵 + 💎 Чай или/и алмазы", callback_data="currency_both")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"edit_pending_lot|{auction_id}")],
    ])

    await call.message.answer("Выберите валюту:", reply_markup=kb)
    await state.set_state(ApproveLotFSM.editing_pending_currency)
    await call.answer()


@router.callback_query(
    ApproveLotFSM.editing_pending_currency,
    F.data.in_(["currency_diamonds", "currency_cups", "currency_both"]),
)
@admin_only
async def save_pending_currency(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auction_id = int(data.get("auction_id") or 0)
    if not auction_id:
        await call.answer("Потерялся auction_id.", show_alert=True)
        await state.clear()
        return

    old_lot = await get_lot_by_id(auction_id)
    old_currency = (old_lot or {}).get("currency")

    mapping = {
        "currency_diamonds": "алмазы",
        "currency_cups": "чашки",
        "currency_both": "чашки",
    }
    new_currency = mapping.get(call.data)

    accepted = ["чашки", "алмазы"] if call.data == "currency_both" else [new_currency]

    await _update_auction_field(auction_id, "currency", new_currency)
    await _update_auction_field(auction_id, "accepted_currencies", accepted)
    await _update_auction_field(auction_id, "custom_offer_terms", None)

    await _log_pending_change(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        action_type="edit_pending_currency",
        field_title="Валюта",
        old_value=old_currency,
        new_value=new_currency,
    )

    await notify_owners_pending_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        changes=[("Валюта", old_currency, new_currency)],
    )


    await call.message.answer(f"✅ Валюта обновлена: {new_currency}")
    await _send_pending_lot_card(call.message, call.bot, auction_id)
    await state.clear()
    await call.answer()


@router.message(F.text.lower().in_(["отмена", "назад", "⬅️ назад"]), F.chat.type == "private")
@admin_only
async def universal_cancel_text(message: types.Message, state: FSMContext):
    await process_universal_cancel_text(message, state)


@router.callback_query(F.data.in_(["givetrusted_cancel", "removetrusted_cancel"]))
@admin_only
async def universal_trusted_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    txt = CANCEL_TEXTS[call.data][0]
    await call.message.edit_text(
        f"{txt}\n\n{ADMIN_MESSAGES['admin_panel_greeting']}",
        reply_markup=menu_keyboard(
            ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
            ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
        )
    )
    await call.answer()


__all__ = [
    "router",
    "edit_pending_lot_menu",
    "edit_pending_kind",
    "edit_pending_craft",
    "pending_set_craft",
    "edit_pending_comment",
    "save_pending_comment",
    "pending_set_kind",
    "edit_pending_price",
    "save_pending_price",
    "set_lot_photo_from_lot",
    "handle_uploaded_lot_photo",
    "handle_uploaded_lot_not_photo",
    "edit_pending_currency",
    "save_pending_currency",
    "universal_cancel_text",
    "universal_trusted_cancel",
]
