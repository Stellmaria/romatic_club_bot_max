"""Approved lot and schedule editing.

Handlers retain their relative order from the legacy ``admin_panel`` module.
"""

import asyncio


from bot.handlers.admin.admin_panel_shared import *  # noqa: F403
from bot.handlers.admin.schedule_card_view import (
    build_schedule_lot_caption,
    build_schedule_lot_keyboard,
    refresh_schedule_card_origin,
    remember_schedule_card_origin,
)

router = Router(name=__name__)


@router.message(F.text == "📝 Редактировать расписание", F.chat.type == "private")
@admin_only
async def edit_schedule_button(message: Message, state: FSMContext):
    await start_edit_schedule(message, state)


@router.callback_query(F.data.startswith("edit_schedule_lot|"))
@admin_only
async def edit_lot_menu(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await remember_schedule_card_origin(
        state,
        call.message,
        auction_id,
        delete_callback_prefix="admin_delete_lot",
        delete_label="🚫 Отменить",
    )
    await _send_edit_lot_menu(call.message, state, auction_id)
    await call.answer()


@router.callback_query(F.data.startswith("expend_mode|"))
@admin_only
async def exchange_pending_mode_pick(call: CallbackQuery):
    mode = (call.data or "").split("|", 1)[-1]
    await call.answer()

    try:
        await call.message.delete()
    except Exception:
        pass

    if mode == "one":
        await show_pending_exchange_one(call.message)
    else:
        await show_pending_exchange_requests_all(call.message, limit=200)


@router.callback_query(EditScheduleFSM.choosing_field, F.data.startswith("edit_field|"))
@admin_only
async def edit_field_handler(call: CallbackQuery, state: FSMContext):
    _, field, auction_id_raw = call.data.split("|")
    try:
        auction_id = int(auction_id_raw)
    except Exception:
        data = await state.get_data()
        auction_id = int(data.get("auction_id") or 0)

    if not auction_id:
        await call.answer("Не понял какой лот.", show_alert=True)
        return

    await state.update_data(auction_id=auction_id, edit_field=field)

    # Время: выбор месяца/дня/слота (как в модерации при принятии заявки)
    if field == "time":
        await state.set_state(EditScheduleFSM.choosing_month)

        kb = months_keyboard(prefix="edit_schedule", auction_id=auction_id)
        kb = _kb_add_back(kb, "edit_lot_back")

        await call.message.answer(
            "Ок. Выберите новую дату/время (как раньше).\n\n"
            "Сначала выберите месяц:",
            reply_markup=kb,
        )
        await call.answer()
        return

    # Цена
    if field == "price":
        await call.message.answer("Введите новую стартовую цену:", reply_markup=_back_to_lot_kb())
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Валюта (у тебя уже работает, просто добавим “назад”)
    if field == "currency":
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💎 Алмазы", callback_data="set_currency|алмазы")],
                [types.InlineKeyboardButton(text="🍵 Чашки", callback_data="set_currency|чашки")],
                [types.InlineKeyboardButton(text="🍵 + 💎 Чай или/и алмазы", callback_data="set_currency|чашки_алмазы")],
            ]
        )
        kb = _kb_add_back(kb, "edit_lot_back")
        await call.message.answer("Выберите валюту:", reply_markup=kb)
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Коммент
    if field == "comment":
        await call.message.answer(
            "Введите новый комментарий.\n"
            "Чтобы очистить: отправьте <code>-</code>",
            reply_markup=_back_to_lot_kb(),
            parse_mode="HTML",
        )
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Фото
    if field == "photo":
        await call.message.answer("Пришлите новое фото для лота:", reply_markup=_back_to_lot_kb())
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Тип аука
    if field == "auction_kind":
        await call.message.answer("Выберите тип аука:", reply_markup=_auk_kind_kb(auction_id))
        await call.answer()
        return

    # Крафт на UID
    if field == "craft_uid":
        await call.message.answer("Крафт на UID возможен?", reply_markup=_craft_uid_kb(auction_id))
        await call.answer()
        return

    await call.answer("Неизвестное поле.", show_alert=True)


@router.callback_query(F.data.startswith("set_auk_kind|"))
@admin_only
async def set_auction_kind_handler(call: CallbackQuery, state: FSMContext):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Кривой callback.", show_alert=True)
        return

    _, kind, auction_id_raw = parts
    auction_id = int(auction_id_raw)

    lot_before = await get_lot_by_id(auction_id)
    old_kind = (lot_before or {}).get("auction_kind")

    await _update_auction_field(auction_id, "auction_kind", kind)

    await notify_owners_lot_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Тип аука", old_kind, kind)],
    )

    lot_after = dict(lot_before or {})
    lot_after["auction_kind"] = kind

    await send_lot_edit_log(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Тип аука", old_kind, kind)],
        audit_action_type="edit_lot_kind",
        audit_details=f"Тип аука изменён: {old_kind} -> {kind}",
    )

    await call.message.answer("✅ Тип аука обновлён.", reply_markup=_back_to_lot_kb())
    await call.answer()


@router.callback_query(F.data.startswith("set_craft_uid|"))
@admin_only
async def set_craft_uid_handler(call: CallbackQuery, state: FSMContext):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Кривой callback.", show_alert=True)
        return

    _, val, auction_id_raw = parts
    auction_id = int(auction_id_raw)

    if val == "1":
        new_val = True
        val_s = "Да"
    elif val == "0":
        new_val = False
        val_s = "Нет"
    else:
        new_val = None
        val_s = "Не указано"

    lot_before = await get_lot_by_id(auction_id)
    old_val = (lot_before or {}).get("craft_uid_possible")

    await _update_auction_field(auction_id, "craft_uid_possible", new_val)

    await notify_owners_lot_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Крафт на UID", old_val, new_val)],
    )

    lot_after = dict(lot_before or {})
    lot_after["craft_uid_possible"] = new_val

    await send_lot_edit_log(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Крафт на UID", old_val, new_val)],
        audit_action_type="edit_lot_craft_uid",
        audit_details=f"craft_uid_possible: {old_val} -> {new_val}",
    )

    await call.message.answer(
        f"✅ Крафт на UID: <b>{val_s}</b>.",
        parse_mode="HTML",
        reply_markup=_back_to_lot_kb(),
    )
    await call.answer()


@router.message(EditScheduleFSM.entering_value, F.photo | F.video | F.animation | F.document)
@admin_only
async def edit_schedule_value_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    auction_id = data.get("auction_id")

    if field != "photo":
        await message.answer("Сейчас ожидается не медиа. Нажмите ⬅️ Назад и выберите нужное поле.")
        return

    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.animation:
        file_id = message.animation.file_id
    elif message.document and (message.document.mime_type or "").startswith("video/"):
        file_id = message.document.file_id

    if not file_id:
        await message.answer("Пришлите фото или видео.")
        return

    lot_before = await get_lot_by_id(auction_id)
    old_media = (lot_before or {}).get("image_id") or (lot_before or {}).get("card_image_id")

    await _update_auction_field(auction_id, "image_id", file_id)

    await notify_owners_lot_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Медиа", short_media_id(old_media), short_media_id(file_id))],
    )

    lot_after = dict(lot_before or {})
    lot_after["image_id"] = file_id

    await send_lot_edit_log(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Медиа", short_media_id(old_media), short_media_id(file_id))],
        audit_action_type="edit_lot_media",
        audit_details=f"Медиа обновлено: {short_media_id(old_media)} -> {short_media_id(file_id)}",
    )

    await message.answer("✅ Медиа обновлено.")
    await _send_edit_lot_menu(message, state, auction_id)


@router.message(EditScheduleFSM.entering_value, F.text)
@admin_only
async def edit_schedule_value_text(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    auction_id = data.get("auction_id")

    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)
    txt = (message.text or "").strip()

    # Цена
    if field == "price":
        try:
            price = int(txt)
            if price <= 0:
                raise ValueError
        except Exception:
            await message.answer("Введите корректную цену (целое число > 0).", reply_markup=_back_to_lot_kb())
            return

        lot_before = await get_lot_by_id(auction_id)
        old_price = (lot_before or {}).get("start_price")
        cur = (lot_before or {}).get("currency")

        await _update_auction_field(auction_id, "start_price", price)

        await notify_owners_lot_changed(
            message.bot,
            auction_id=auction_id,
            admin_user=message.from_user,
            title="Изменения по вашему лоту",
            stage_label="в расписании",
            changes=[("Стартовая цена", old_price, price)],
        )

        lot_after = dict(lot_before or {})
        lot_after["start_price"] = price

        old_label = f"{old_price} {cur}" if old_price is not None and cur else (
            str(old_price) if old_price is not None else "—"
        )
        new_label = f"{price} {cur}" if cur else str(price)

        await send_lot_edit_log(
            message.bot,
            admin_user=message.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Стартовая цена", old_label, new_label)],
            audit_action_type="edit_lot_price",
            audit_details=f"Стартовая цена: {old_label} -> {new_label}",
        )

        await message.answer("✅ Цена обновлена.")
        await _send_edit_lot_menu(message, state, auction_id)
        return

    # Комментарий
    if field == "comment":
        new_comment = "" if txt == "-" else txt

        lot_before = await get_lot_by_id(auction_id)
        old_comment = (lot_before or {}).get("comment")

        await update_lot_field_with_notify(
            message.bot,
            auction_id=auction_id,
            field="comment",
            value=new_comment,
            admin_user=message.from_user,
            field_label="Комментарий",
        )

        lot_after = dict(lot_before or {})
        lot_after["comment"] = new_comment

        await send_lot_edit_log(
            message.bot,
            admin_user=message.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Комментарий", old_comment, new_comment)],
            audit_action_type="edit_lot_comment",
            audit_details=f"Комментарий: {(old_comment or '(пусто)')} -> {(new_comment or '(пусто)')}",
        )

        await message.answer("✅ Комментарий обновлён.")
        await _send_edit_lot_menu(message, state, auction_id)
        return

    await message.answer(
        "Сейчас ожидается другое действие. Нажмите ⬅️ Назад и выберите поле.",
        reply_markup=_back_to_lot_kb(),
    )


@router.callback_query(F.data == "edit_time_months")
@admin_only
async def edit_time_months(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")

    # если нет auction_id, просто вернёмся в старт редактуры расписания
    if not auction_id:
        await start_edit_schedule(call.message, state)
        await call.answer()
        return

    await state.set_state(EditScheduleFSM.choosing_month)
    kb = months_keyboard(prefix="edit_schedule", auction_id=int(auction_id))
    kb = _kb_add_back(kb, "edit_lot_back")
    await call.message.answer("Выберите месяц для изменения времени лота:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("edit_schedule|"))
@admin_only
async def edit_schedule_router(call: CallbackQuery, state: FSMContext):
    auction_id, date_str = await parse_auction_and_date_from_callback(call.data, state)

    if not date_str:
        await call.answer("Ошибка формата даты.", show_alert=True)
        return

    parts = date_str.split("-")

    # 1) Выбран месяц: YYYY-MM
    if len(parts) == 2:
        try:
            year, month = map(int, parts)
        except Exception:
            await call.answer("Ошибка даты месяца.", show_alert=True)
            return

        await state.update_data(year=year, month=month, auction_id=auction_id)

        kb = days_keyboard("edit_schedule", auction_id, year, month)
        # Назад снизу: возвращаемся к месяцам
        # (для лота это вернёт в months list, а оттуда можно уйти в меню лота)
        kb = _kb_add_back(kb, "edit_time_months")

        await call.message.answer(
            "Выберите день для изменения времени лота:" if auction_id else "Выберите день:",
            reply_markup=kb,
        )
        await state.set_state(EditScheduleFSM.choosing_day)
        await call.answer()
        return

    # 2) Выбран день: YYYY-MM-DD
    if len(parts) == 3:
        try:
            year, month, day = map(int, parts)
            selected_date = date(year, month, day)
        except Exception:
            await call.answer("Ошибка даты дня.", show_alert=True)
            return

        await state.update_data(selected_date=selected_date, auction_id=auction_id)

        # Редактируем конкретный лот (перенос времени)
        if auction_id:
            free_slots, is_luxury, schedule_str, lot, auctions = await get_free_slots_and_schedule_for_lot(
                int(auction_id), selected_date
            )

            if not free_slots:
                await call.message.answer("Нет свободных слотов на выбранную дату.")
                await call.answer()
                return

            kb = time_slots_keyboard("edit_time_slot", int(auction_id), free_slots, is_luxury)

            # Назад снизу: вернуться к дням этого месяца
            kb = _kb_add_back(kb, f"edit_schedule|{int(auction_id)}|{year:04d}-{month:02d}")

            text = (
                f"Расписание на {selected_date.strftime('%d.%m.%Y')}:\n"
                f"{schedule_str}\n\n"
                f"❗️ — слот занят этой же картой этим же владельцем\n"
                f"🟡 — слот занят этой же картой, но у другого владельца (вы можете выбрать этот слот)"
            )
            await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
            await state.set_state(EditScheduleFSM.choosing_time)
            await call.answer()
            return

        # Просмотр/редактура списка лотов на день (без переносов)
        auctions = await get_auctions_by_date_with_owners(selected_date)
        if not auctions:
            await call.message.answer("На выбранный день нет лотов.")
            await call.answer()
            return

        for lot in auctions:
            a_id = int(lot["auction_id"])
            owners_text = await get_lot_owners_text(a_id)
            caption = build_schedule_lot_caption(lot, owners_text)
            kb = build_schedule_lot_keyboard(
                a_id,
                delete_callback_prefix="admin_delete_lot",
                delete_label="🚫 Отменить",
            )

            image_id = lot.get("image_id")
            if image_id:
                await safe_answer_photo(call.message, image_id, caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                await call.message.answer(caption, reply_markup=kb, parse_mode="HTML")

        await call.answer()
        return

    # <- вот тут у тебя и был битый show_alert
    await call.answer("Непонятный формат даты.", show_alert=True)


@router.callback_query(F.data == "edit_lot_back")
@admin_only
async def edit_lot_back(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    if auction_id:
        await _send_edit_lot_menu(call.message, state, int(auction_id))
    else:
        await start_edit_schedule(call.message, state)
    await call.answer()


@router.callback_query(F.data == "edit_schedule_back")
@admin_only
async def edit_schedule_back_any(call: CallbackQuery, state: FSMContext):
    # безопасный “назад” без попыток угадать твою текущую вложенность
    await start_edit_schedule(call.message, state)
    await call.answer()


@router.callback_query(F.data.startswith("admin_delete_lot|"))
@admin_only
async def delete_lot_confirm(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    lot = await get_lot_by_id(auction_id)
    text = (
        f"❗️ <b>Отменить лот?</b>\n\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Герой: {lot['hero_name']}\n"
        f"Стартовая цена: {lot['start_price']} {lot['currency']}\n\n"
        "Запись, владельцы и история сохранятся со статусом <code>cancelled</code>."
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"edit_schedule_lot|{auction_id}")],
        [types.InlineKeyboardButton(text="🚫 Да, отменить лот", callback_data=f"delete_lot_final|{auction_id}")]
    ])
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("delete_lot_final|"))
@admin_only
async def delete_lot_final(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await call.message.answer("Лот не найден.")
        await call.answer()
        return

    moderation_service = await AuctionModerationService.create()
    await moderation_service.cancel(auction_id)
    owners = await get_lot_owners(auction_id)
    for o in owners:
        try:
            await call.bot.send_message(
                o['user_id'],
                f"🚫 Ваш лот <b>{lot['card_name']}</b> был отменён модератором.",
                parse_mode="HTML"
            )
        except Exception:
            logger.exception("Could not notify owner about cancelled auction %s", auction_id)
    owners_text = await get_lot_owners_text(auction_id)
    log_text = (
        f"🚫 <b>Лот отменён админом</b>\n"
        f"🎴 Лот №{lot['auction_id']}: {lot['card_name']}\n"
        f"🙍‍♂️ Владелец(ы): {owners_text or '-'}\n"
        f"💰 Старт: {lot['start_price']} {lot['currency']}\n"
        f"💬 Комментарий: {lot.get('comment', '-')}\n"
        f"📅 Дата выхода: {to_moscow(lot['start_time']).strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {to_moscow(lot['start_time']).strftime('%H:%M')}–{to_moscow(lot['end_time']).strftime('%H:%M')} (МСК)\n"
        f"🛠️ Действие: отмена через панель расписания"
    )
    await send_admin_log(call.bot, log_text)
    await log_audit_action(
        user_id=call.from_user.id,
        action_type="admin_cancel_lot",
        auction_id=auction_id,
        details="Лот отменён через редактор расписания"
    )
    await call.message.answer("✅ Лот отменён; история сохранена.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("edit_time_slot|"))
@admin_only
async def edit_time_slot_confirm(call: CallbackQuery, state: FSMContext):
    _, auction_id, iso_str = call.data.split("|")
    auction_id = int(auction_id)
    start_time = to_moscow(datetime.fromisoformat(iso_str))
    end_time = auction_end_at_59(start_time)

    await state.update_data(new_start_time=start_time, new_end_time=end_time)

    lot = await get_lot_by_id(auction_id)
    text = (
        f"Подтвердите изменение времени для лота <b>{lot['card_name']}</b>:\n\n"
        f"Новое время: {start_time.strftime('%d.%m %H:%M')}–{end_time.strftime('%H:%M')}"
    )

    back_cb = f"edit_schedule|{auction_id}|{start_time.date().isoformat()}"
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"edit_time_save|{auction_id}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)]
    ])

    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("edit_time_save|"))
@admin_only
async def save_edited_time(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "new_start_time" not in data or "new_end_time" not in data:
        await safe_callback_answer(
            call,
            "Не найдено выбранное время. Откройте перенос заново.",
            show_alert=True,
        )
        return

    auction_id = int(call.data.split("|")[1])
    start_time = to_moscow(data["new_start_time"])
    end_time = auction_end_at_59(start_time)

    # Telegram keeps the loading spinner until answerCallbackQuery is called.
    # A reschedule also sends logs, refreshes an old card and notifies owners,
    # so answering only at the very end made a successful move look frozen.
    await safe_callback_answer(call, "⏳ Переношу лот…")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    lot = await get_lot_by_id(auction_id)
    if not lot:
        await call.message.answer("❌ Лот не найден. Обновите расписание.")
        return

    old_start = to_moscow(lot["start_time"])
    old_end = to_moscow(lot["end_time"])

    try:
        moderation_service = await AuctionModerationService.create()
        persisted_lot = await asyncio.wait_for(
            moderation_service.reschedule(
                auction_id,
                start_time=start_time,
                end_time=end_time,
            ),
            timeout=12,
        )
    except asyncio.TimeoutError:
        logger.error("Timed out while rescheduling auction_id=%s", auction_id)
        await call.message.answer(
            "❌ База слишком долго отвечала. Перенос отменён транзакцией. "
            "Откройте расписание заново и повторите попытку."
        )
        return
    except AuctionSlotConflict:
        await call.message.answer("❌ Этот слот уже занят. Выберите другое время.")
        return
    except InvalidAuctionTransition as exc:
        await call.message.answer(
            f"❌ Лот нельзя перенести из статуса <code>{exc.current}</code>. "
            "Обновите расписание.",
            parse_mode="HTML",
        )
        return
    except Exception:
        logger.exception("Could not reschedule auction_id=%s", auction_id)
        await call.message.answer(
            "❌ Не удалось перенести лот из-за внутренней ошибки. "
            "Изменение не подтверждено; проверьте расписание."
        )
        return

    persisted_start = to_moscow(persisted_lot["start_time"])
    persisted_end = to_moscow(persisted_lot["end_time"])
    if (
        persisted_start.replace(second=0, microsecond=0)
        != start_time.replace(second=0, microsecond=0)
        or persisted_end.replace(microsecond=0) != end_time.replace(microsecond=0)
    ):
        logger.error(
            "Reschedule verification mismatch auction_id=%s expected=%s/%s actual=%s/%s",
            auction_id,
            start_time,
            end_time,
            persisted_start,
            persisted_end,
        )
        await call.message.answer(
            "⚠️ База вернула другое время после переноса. "
            "Откройте расписание заново; успешный лог не отправлен."
        )
        return

    # The database move is already committed. Show the result before optional
    # Telegram/logging side effects so a slow log chat cannot hide success.
    await call.message.answer(
        "✅ <b>Лот перенесён</b>\n"
        f"{old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} → "
        f"{persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)",
        parse_mode="HTML",
    )

    owners = await get_lot_owners(auction_id)
    owner_ids = [o["user_id"] for o in owners]
    user_flags = []
    for owner_id in owner_ids:
        try:
            is_lux, user = await asyncio.gather(
                is_luxury_user(owner_id),
                get_user(owner_id),
            )
        except Exception:
            logger.exception("Could not read owner flags user_id=%s", owner_id)
            continue
        is_trusted = user and user.get("is_trusted")
        if is_lux:
            user_flags.append("Лакшери")
        if is_trusted:
            user_flags.append("Доверенный")
    flags_str = ", ".join(sorted(set(user_flags))) if user_flags else "Обычный"

    admin = {
        "id": call.from_user.id,
        "username": call.from_user.username or call.from_user.full_name,
    }
    owners_text = await get_lot_owners_text(auction_id)

    try:
        card_refresh_status = await asyncio.wait_for(
            refresh_schedule_card_origin(
                call.bot,
                state,
                auction_id,
                lot=persisted_lot,
                owners_text=owners_text,
            ),
            timeout=6,
        )
    except asyncio.TimeoutError:
        card_refresh_status = False
        logger.warning("Schedule card refresh timed out auction_id=%s", auction_id)
    except Exception:
        card_refresh_status = False
        logger.exception("Schedule card refresh failed auction_id=%s", auction_id)

    log_text = format_admin_action_log(
        action="move_lot",
        admin=admin,
        lot={
            **persisted_lot,
            "start_time": persisted_start,
            "end_time": persisted_end,
        },
        owners_text=owners_text,
    )
    log_text += (
        f"\n🏷️ <b>Тип владельца:</b> {flags_str}"
        f"\n📅 <b>Старое время:</b> {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} (МСК)"
        f"\n➡️ <b>Новое время:</b> {persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)"
    )
    try:
        await asyncio.wait_for(send_admin_log(call.bot, log_text), timeout=6)
    except Exception:
        logger.exception("Could not send reschedule admin log auction_id=%s", auction_id)

    try:
        await log_audit_action(
            user_id=call.from_user.id,
            action_type="move_lot",
            auction_id=auction_id,
            details=(
                f"Перенос с {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} "
                f"на {persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} | "
                f"Тип владельца: {flags_str} | "
                f"Карточка обновлена: {card_refresh_status}"
            ),
        )
    except Exception:
        logger.exception("Could not write reschedule audit auction_id=%s", auction_id)

    async def _notify_owner(owner: dict) -> None:
        await call.bot.send_message(
            owner["user_id"],
            f"⏳ <b>Ваша карта <u>{lot['card_name']}</u> была перенесена!</b>\n\n"
            f"<b>Новое время аукциона:</b> "
            f"{persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)\n"
            f"Ранее стояло: {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')}\n"
            f"<b>Ваш статус:</b> {flags_str}",
            parse_mode="HTML",
        )

    for owner in owners:
        try:
            await asyncio.wait_for(_notify_owner(owner), timeout=5)
        except Exception:
            logger.exception(
                "Could not notify owner about rescheduled auction_id=%s user_id=%s",
                auction_id,
                owner.get("user_id"),
            )

    if card_refresh_status is False:
        await call.message.answer(
            "⚠️ Время в базе изменено, но старую карточку Telegram не обновил. "
            "После повторного открытия расписания будет показано новое время."
        )

    await state.clear()


@router.callback_query(EditScheduleFSM.entering_value, F.data.startswith("set_currency|"))
async def set_currency_handler(call: CallbackQuery, state: FSMContext):
    _, currency = call.data.split("|")
    data = await state.get_data()
    auction_id = data.get("auction_id")
    await state.update_data(new_currency=currency)
    await call.message.answer(f"Введите новую стартовую цену (<b>{currency}</b>):")
    await state.set_state(EditScheduleFSM.editing_currency_price)
    await call.answer()


@router.message(EditScheduleFSM.editing_currency_price)
@admin_only
async def set_currency_price_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    currency = data.get("new_currency")

    if not auction_id or not currency:
        await message.answer("Потерялись данные (auction_id/валюта). Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)

    try:
        price = int(message.text)
        if price <= 0:
            await message.answer("Цена должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("Введите корректную цену (целое положительное число).")
        return

    lot_before = await get_lot_by_id(auction_id)
    old_currency = (lot_before or {}).get("currency")
    old_price = (lot_before or {}).get("start_price")

    moderation_service = await AuctionModerationService.create()
    await moderation_service.update_fields(
        auction_id,
        changes={"currency": currency, "start_price": price},
    )

    lot_after = dict(lot_before or {})
    lot_after["currency"] = currency
    lot_after["start_price"] = price

    old_label = f"{old_price} {old_currency}" if old_price is not None and old_currency else (
        str(old_price) if old_price is not None else "—")
    new_label = f"{price} {currency}"

    await send_lot_edit_log(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[
            ("Валюта", old_currency, currency),
            ("Стартовая цена", old_label, new_label),
        ],
        audit_action_type="edit_lot_currency_price",
        audit_details=f"Валюта/цена: {old_currency} {old_price} -> {currency} {price}",
    )

    await notify_owners_lot_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[
            ("Валюта", old_currency, currency),
            ("Стартовая цена", old_price, price),
        ],
    )

    await message.answer(
        f"Валюта лота успешно изменена на <b>{currency}</b>!\n"
        f"Стартовая цена теперь <b>{price} {currency}</b>.",
        parse_mode="HTML",
    )
    await state.clear()


@router.message(EditScheduleFSM.entering_value)
async def edit_price_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    try:
        price = int(message.text)
        if price <= 0:
            await message.answer("Цена должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("Введите корректную цену (целое положительное число).")
        return
    await _update_auction_field(int(auction_id), "start_price", price)
    lot = await get_lot_by_id(auction_id)
    owners_text = await get_lot_owners_text(auction_id)
    log_text = format_admin_action_log(
        action="edit_lot",
        admin={"id": message.from_user.id, "username": message.from_user.username or message.from_user.full_name},
        lot={**lot, "start_price": price},
        owners_text=owners_text
    )
    await send_admin_log(message.bot, log_text)
    await log_audit_action(
        user_id=message.from_user.id,
        action_type="edit_lot_price",
        auction_id=auction_id,
        details=f"Изменена цена на {price} {lot['currency']}"
    )
    for o in await get_lot_owners(auction_id):
        try:
            await message.bot.send_message(
                o['user_id'],
                f"💰 <b>У вашей карты <u>{lot['card_name']}</u> изменилась стартовая цена!</b>\n\n"
                f"Теперь: <b>{price} {lot['currency']}</b>.",
                parse_mode="HTML"
            )
        except Exception:
            logger.exception("Could not notify owner about edited auction %s", auction_id)
    await message.answer(
        f"Стартовая цена лота успешно изменена на <b>{price} {lot['currency']}</b>!",
        parse_mode="HTML"
    )
    await state.clear()


__all__ = [
    "router",
    "edit_schedule_button",
    "edit_lot_menu",
    "exchange_pending_mode_pick",
    "edit_field_handler",
    "set_auction_kind_handler",
    "set_craft_uid_handler",
    "edit_schedule_value_photo",
    "edit_schedule_value_text",
    "edit_time_months",
    "edit_schedule_router",
    "edit_lot_back",
    "edit_schedule_back_any",
    "delete_lot_confirm",
    "delete_lot_final",
    "edit_time_slot_confirm",
    "save_edited_time",
    "set_currency_handler",
    "set_currency_price_handler",
    "edit_price_handler",
]
