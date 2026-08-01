"""Lot approval, rejection, and deletion moderation.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

from bot.handlers.admin.moderation_shared import *  # noqa: F403
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)


@router.callback_query(F.data.startswith("back_to_"))
async def fsm_back_handler(call: types.CallbackQuery, state: FSMContext):
    data = split_callback_data(call.data, "|")
    if len(data) < 2:
        await call.answer("Некорректный возврат!", show_alert=True)
        return
    back_to = data[0][8:]
    try:
        auction_id = int(data[1])
    except (ValueError, TypeError):
        await call.answer("Некорректный ID лота!", show_alert=True)
        return
    extra = data[2] if len(data) > 2 else None
    if back_to == "lot":
        lot = await get_lot_by_id(auction_id)
        owners = await get_lot_owners_with_levels(call.bot, auction_id)
        text = format_pending_lot(lot, owners)
        kb = build_lot_keyboard(lot, role="admin")
        media_id = lot.get("image_id") or lot.get("card_image_id")
        if media_id:
            await safe_answer_photo(
                call.message,
                media_id,
                caption=text,
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
        try:
            await call.message.delete()
        except TelegramAPIError as e:
            print(f"Can't delete message: {e}")
        await call.answer()
        return
    elif back_to == "month":
        lot = await get_lot_by_id(auction_id)
        owners_txt = await get_lot_owners_text(auction_id)
        await safe_edit_message(
            call,
            f"Лот:\n<b>{lot['card_name']}</b>\nВладельцы: {owners_txt or '-'}\n\n{ADMIN_MESSAGES['choose_month']}",
            reply_markup=months_keyboard(prefix="choose_month", auction_id=auction_id)
        )
        await state.set_state(ApproveLotFSM.choosing_month)
        await call.answer()
        return
    elif back_to == "day":
        data_state = await state.get_data()
        year = data_state.get("year")
        month = data_state.get("month")
        await safe_edit_message(
            call,
            ADMIN_MESSAGES["choose_day"],
            reply_markup=days_keyboard("choose_day", auction_id, year, month)
        )
        await state.set_state(ApproveLotFSM.choosing_day)
        await call.answer()
        return
    elif back_to == "time":
        selected_time = datetime.fromisoformat(extra)
        selected_date = selected_time.date()
        data_state = await state.get_data()
        lot = data_state.get('lot') or await get_lot_by_id(auction_id)
        is_luxury = data_state.get("is_luxury", False)
        auctions = await get_auctions_by_date_with_owners(selected_date)
        schedule_lines = await build_schedule_lines(auctions, lot)
        schedule_str = "\n".join(schedule_lines) if schedule_lines else "Нет запланированных лотов на этот день."
        free_slots = await find_free_slots(auctions, lot, auction_id, selected_date)
        free_slots = filter_slots_by_user_type(free_slots, is_luxury)
        if not free_slots:
            await call.answer("Нет свободных слотов на эту дату!", show_alert=True)
            return
        await safe_edit_message(
            call,
            f"<b>Расписание на {selected_date.strftime('%d.%m.%Y')}</b>:\n"
            f"{schedule_str}\n\n"
            f"<i>Выберите свободное время для публикации лота:</i>",
            reply_markup=time_slots_keyboard("choose_time", int(auction_id), free_slots, is_luxury)
        )
        await state.set_state(ApproveLotFSM.choosing_time)
        await call.answer()
        return


@router.message(ModActionFSM.waiting_for_reject_pending_reason, F.chat.type == "private")
@admin_only
async def handle_reject_pending_reason(message: types.Message, state: FSMContext):
    async def get_row(auction_id): return await get_lot_by_id(auction_id)

    async def get_lot(row): return row

    async def update_status(auction_id, status):
        if status != "rejected":
            raise ValueError(f"unsupported moderation status: {status}")
        service = await AuctionModerationService.create()
        await service.reject(int(auction_id))

    def admin_log_text(lot, owners_text, row, reason, msg):
        return REJECT_LOT_ADMIN_LOG.format(
            datetime=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            admin_name=msg.from_user.full_name,
            admin_id=msg.from_user.id,
            auction_id=lot["auction_id"],
            card_name=html.escape(lot.get("card_name", "-")),
            owners_text=owners_text,
            reason=reason
        )

    def user_notify(lot, row, reason):
        rsn = (reason or "").strip() or "—"

        # краткие поля (как в pending)
        auction_id = lot.get("auction_id")
        card_name = html.escape(str(lot.get("card_name") or "—"))
        hero_name = html.escape(str(lot.get("hero_name") or "—"))
        deck = html.escape(str(lot.get("deck_name") or lot.get("deck_id") or "—"))
        rarity = html.escape(str(lot.get("rarity") or "—"))
        currency = str(lot.get("currency") or "алмазы")
        cur_emoji = "🍵" if "чай" in currency.lower() else ("🪙" if "сокров" in currency.lower() else "💎")
        start_price = lot.get("start_price")
        comment = html.escape(str(lot.get("comment") or "-"))
        craft_uid = lot.get("craft_uid_possible")
        craft_line = "✅ Да" if craft_uid else "❌ Нет"

        return (
            "❌ <b>Ваша заявка на добавление лота отклонена</b>\n"
            f"🆔 ID лота: <code>{auction_id}</code>\n"
            f"🎴 Лот: <b>{card_name}</b>\n"
            f"👤 Герой: <b>{hero_name}</b>\n"
            f"📚 Колода: <b>{deck}</b>\n"
            f"✨ Редкость: <b>{rarity}</b>\n"
            f"💰 Старт: <b>{start_price} {cur_emoji} ({html.escape(currency)})</b>\n"
            f"🆔 Крафт на UID: <b>{craft_line}</b>\n"
            f"💬 Комментарий: <i>{comment}</i>\n"
            f"🔒 Причина: <i>{html.escape(rsn)}</i>\n\n"
            "Если есть вопросы — обратитесь к администрации."
        )


    await process_reject_action(
        message, state,
        obj_id_key="auction_id",
        get_row_fn=get_row,
        get_lot_fn=get_lot,
        update_status_fn=update_status,
        admin_log_text_builder=admin_log_text,
        user_notify_builder=user_notify,
        admin_action_type="reject_lot_pending"
    )


@router.message(ModActionFSM.waiting_for_reject_delete_reason, F.chat.type == "private")
@admin_only
async def handle_reject_delete_reason(message: types.Message, state: FSMContext):
    async def get_row(request_id): return await get_delete_request(request_id)

    async def get_lot(row): return await get_lot_by_id(row["lot_id"])

    async def update_status(request_id, status): await update_delete_request_status(request_id, status)

    def admin_log_text(lot, owners_text, row, reason, msg):
        return REJECT_DELETE_ADMIN_LOG.format(
            auction_id=lot["auction_id"],
            owners_text=owners_text,
            delete_reason=row["reason"],
            reason=reason,
            admin_name=msg.from_user.full_name,
            admin_id=msg.from_user.id
        )

    def user_notify(lot, row, reason):
        rsn = (reason or "").strip() or "—"

        # краткие поля (как в pending)
        auction_id = lot.get("auction_id")
        card_name = html.escape(str(lot.get("card_name") or "—"))
        hero_name = html.escape(str(lot.get("hero_name") or "—"))
        deck = html.escape(str(lot.get("deck_name") or lot.get("deck_id") or "—"))
        rarity = html.escape(str(lot.get("rarity") or "—"))
        currency = str(lot.get("currency") or "алмазы")
        cur_emoji = "🍵" if "чай" in currency.lower() else ("🪙" if "сокров" in currency.lower() else "💎")
        start_price = lot.get("start_price")
        comment = html.escape(str(lot.get("comment") or "-"))
        craft_uid = lot.get("craft_uid_possible")
        craft_line = "✅ Да" if craft_uid else "❌ Нет"

        return (
            "❌ <b>Ваша заявка на добавление лота отклонена</b>\n"
            f"🆔 ID лота: <code>{auction_id}</code>\n"
            f"🎴 Лот: <b>{card_name}</b>\n"
            f"👤 Герой: <b>{hero_name}</b>\n"
            f"📚 Колода: <b>{deck}</b>\n"
            f"✨ Редкость: <b>{rarity}</b>\n"
            f"💰 Старт: <b>{start_price} {cur_emoji} ({html.escape(currency)})</b>\n"
            f"🆔 Крафт на UID: <b>{craft_line}</b>\n"
            f"💬 Комментарий: <i>{comment}</i>\n"
            f"🔒 Причина: <i>{html.escape(rsn)}</i>\n\n"
            "Если есть вопросы — обратитесь к администрации."
        )


    await process_reject_action(
        message, state,
        obj_id_key="request_id",
        get_row_fn=get_row,
        get_lot_fn=get_lot,
        update_status_fn=update_status,
        admin_log_text_builder=admin_log_text,
        user_notify_builder=user_notify,
        admin_action_type="reject_delete_request"
    )


@router.message(F.text.startswith("/add_admin"), F.chat.type == "private")
@admin_only
@owner_or_secret_required
async def add_admin_cmd(message: types.Message, state: FSMContext = None, *args, **kwargs):
    await admin_add_remove(message, state, is_remove=False)


@router.message(F.text.startswith("/remove_admin"), F.chat.type == "private")
@admin_only
@owner_or_secret_required
async def remove_admin_cmd(message: types.Message, state: FSMContext = None, *args, **kwargs):
    await admin_add_remove(message, state, is_remove=True)


@router.callback_query(ApproveLotFSM.choosing_month, F.data.startswith("choose_month|"))
async def choose_month(call: types.CallbackQuery, state: FSMContext):
    _, auction_id, year_month = split_callback_data(call.data, "|")
    try:
        year, month = map(int, year_month.split('-')[:2])
    except Exception as e:
        await call.answer("Ошибка даты! Данные: " + str(year_month), show_alert=True)
        return
    await state.update_data(year=year, month=month)
    kb = days_keyboard("choose_day", int(auction_id), year, month)
    kb.inline_keyboard.append(build_back_button("lot", int(auction_id)))
    await safe_edit_message(
        call,
        ADMIN_MESSAGES["choose_day"],
        reply_markup=kb
    )
    await state.set_state(ApproveLotFSM.choosing_day)


@router.callback_query(ApproveLotFSM.choosing_day, F.data.startswith("choose_day|"))
async def choose_day(call: types.CallbackQuery, state: FSMContext):
    _, auction_id, year_month_day = split_callback_data(call.data, "|")
    year, month, day = map(int, year_month_day.split('-'))
    selected_date = date(year, month, day)
    free_slots, is_luxury, schedule_str, lot, auctions = await get_free_slots_and_schedule_for_lot(int(auction_id),
                                                                                                   selected_date)
    kb = time_slots_keyboard("choose_time", int(auction_id), free_slots, is_luxury)
    kb.inline_keyboard.append(build_back_button("month", int(auction_id)))
    text = (
        f"Расписание на {selected_date.strftime('%d.%m.%Y')}:\n"
        f"{schedule_str}\n\n"
        f"❗️ — слот занят этой же картой этим же владельцем\n"
        f"🟡 — слот занят этой же картой, но у другого владельца (вы можете выбрать этот слот)"
    )
    await call.message.answer(
        text,
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.update_data(
        selected_date=selected_date,
        all_auctions_today=auctions,
        is_luxury=is_luxury,
        lot=lot
    )
    await state.set_state(ApproveLotFSM.choosing_time)


@router.callback_query(ApproveLotFSM.choosing_time, F.data.startswith("choose_time|"))
async def choose_time(call: types.CallbackQuery, state: FSMContext):
    _, auction_id, iso_str = split_callback_data(call.data, "|")
    auction_id = int(auction_id)
    selected_time = to_moscow(datetime.fromisoformat(iso_str))
    end_time = auction_end_at_59(selected_time)
    data = await state.get_data()
    lot = data.get('lot') or await get_lot_by_id(auction_id)
    auctions = data.get('all_auctions_today')
    if auctions is None:
        auctions = await get_auctions_by_date(selected_time.date())
    conflict_lots = [
        a for a in auctions
        if a['card_name'] == lot['card_name']
           and to_moscow(a['start_time']) <= selected_time < to_moscow(a['end_time'])
           and a['auction_id'] != auction_id
    ]
    if conflict_lots:
        info_lines = []
        for a in conflict_lots:
            start = to_moscow(a['start_time']).strftime('%H:%M')
            end = to_moscow(a['end_time']).strftime('%H:%M')
            owner_text = await get_owner_refs(a['auction_id'])
            info_lines.append(f"⏰ {start}–{end} | <b>{lot['card_name']}</b> | {owner_text}")
        info_str = "\n".join(info_lines)
        await call.answer(
            "⚠️ ВНИМАНИЕ: На это время уже запланирована эта карта у другого владельца! Подробности ниже.",
            show_alert=True)
        for part in split_message(info_str, 4000):
            await call.message.answer(part, parse_mode="HTML")
    owners_txt = await get_lot_owners_text(auction_id)
    preview = MSG_CONFIRM_PUBLICATION.format(
        card_name=lot['card_name'],
        owners=owners_txt,
        start=selected_time.strftime('%d.%m %H:%M'),
        end=end_time.strftime('%H:%M')
    )
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text=BUTTONS["confirm"],
            callback_data=f"{CALLBACK_CONFIRM_LOT}|{auction_id}|{iso_str}"
        )],
        [types.InlineKeyboardButton(
            text=BUTTONS["back"],
            callback_data=f"back_to_time|{auction_id}|{iso_str}"
        )]
    ])

    await state.update_data(selected_time=selected_time, end_time=end_time)
    await safe_edit_message(call, preview, reply_markup=markup)
    await state.set_state(ApproveLotFSM.confirming)


@router.callback_query(ApproveLotFSM.confirming, F.data.startswith("choose_time_back|"))
async def legacy_choose_time_back(call: types.CallbackQuery, state: FSMContext):
    # поддержка старых сообщений, где назад было choose_time_back|{auction_id}
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    try:
        auction_id = int(parts[1])
    except Exception:
        await call.answer("Некорректный ID лота.", show_alert=True)
        return

    data = await state.get_data()
    selected_time = data.get("selected_time")

    # selected_time может быть datetime или строкой
    if isinstance(selected_time, str):
        try:
            selected_time = datetime.fromisoformat(selected_time)
        except Exception:
            selected_time = None

    if not selected_time:
        # фоллбек: вернём к выбору месяца
        lot = await get_lot_by_id(auction_id)
        owners_txt = await get_lot_owners_text(auction_id)
        await safe_edit_message(
            call,
            f"Лот:\n<b>{lot['card_name']}</b>\nВладельцы: {owners_txt or '-'}\n\n{ADMIN_MESSAGES['choose_month']}",
            reply_markup=months_keyboard(prefix="choose_month", auction_id=auction_id),
        )
        await state.set_state(ApproveLotFSM.choosing_month)
        await call.answer()
        return

    # просто переиспользуем уже существующий механизм back_to_time
    await fsm_back_handler(
        call.__class__(**{**call.model_dump(), "data": f"back_to_time|{auction_id}|{selected_time.isoformat()}"}),
        state)


@router.callback_query(F.data.startswith("confirm_lot|"))
@admin_only
async def handle_confirm_lot(call: types.CallbackQuery, state: FSMContext):
    parts = split_callback_data(call.data, "|")
    if len(parts) == 2:
        auction_id = int(parts[1])
        lot = await get_lot_by_id(auction_id)
        if not lot:
            await call.message.answer(ADMIN_MESSAGES["lot_not_found"])
            return
        owners_txt = await get_lot_owners_text(auction_id)
        await state.clear()
        await state.update_data(auction_id=auction_id, lot=lot)
        await safe_edit_message(
            call,
            f"Лот:\n<b>{lot['card_name']}</b>\nВладельцы: {owners_txt or '-'}\n\n{ADMIN_MESSAGES['choose_month']}",
            reply_markup=months_keyboard(prefix="choose_month", auction_id=auction_id)
        )
        await state.set_state(ApproveLotFSM.choosing_month)
        await call.answer()
    elif len(parts) == 3:
        auction_id = int(parts[1])
        moderator = admin_tag(call.from_user)
        kb = await build_thanks_kb(int(auction_id), moderator)

        iso_str = parts[2]
        start_time = to_moscow(datetime.fromisoformat(iso_str))
        end_time = auction_end_at_59(start_time)

        try:
            moderation_service = await AuctionModerationService.create()
            await moderation_service.schedule(
                auction_id,
                start_time=start_time,
                end_time=end_time,
            )
        except AuctionSlotConflict:
            await call.answer(
                "Этот слот уже заняли. Выберите другое время.",
                show_alert=True,
            )
            return
        except InvalidAuctionTransition as exc:
            await call.answer(
                f"Заявка уже обработана (статус: {exc.current}).",
                show_alert=True,
            )
            return
        lot = await get_lot_by_id(auction_id)

        owners = await get_lot_owners(int(auction_id))
        owner_users = []
        for o in owners:
            user = await get_user(int(o["user_id"]))
            if user:
                user = dict(user)
                user["is_luxury"] = await is_luxury_user(int(user["user_id"]))
                owner_users.append(user)

        owners_text = ", ".join(
            "👑 @" + u["username"] if u.get("is_luxury") and u.get("username") else
            ("@" + u["username"] if u.get("username") else f"id:{u['user_id']}")
            for u in owner_users
        ) or "-"

        await send_admin_log(
            call.bot,
            format_admin_action_log(
                action="approve_lot",
                admin={"id": call.from_user.id, "username": call.from_user.username or call.from_user.full_name},
                lot=lot,
                owners_text=owners_text
            )
        )
        await log_audit_action(
            user_id=call.from_user.id,
            action_type="approve_lot",
            auction_id=auction_id,
            details=f"Лот {lot['card_name']} одобрен на {start_time:%d.%m %H:%M}–{end_time.strftime('%H:%M')}"
        )

        # --- расширенная инфа для владельца ---
        def _cur_emoji_local(cur: str) -> str:
            s = (cur or "").lower()
            if "чай" in s or "чаш" in s:
                return "🍵"
            if "сокров" in s:
                return "🪙"
            return "💎"

        card_name = _html.escape(str(lot.get("card_name") or "-"))
        hero_name = _html.escape(str(lot.get("hero_name") or "-"))

        deck_id = lot.get("deck_id")
        deck_name = str(lot.get("deck_name") or "").strip()
        if deck_id and deck_name:
            deck_line = f"{int(deck_id)} колода — {_html.escape(deck_name)}"
        elif deck_id:
            deck_line = f"{int(deck_id)} колода"
        else:
            deck_line = "—"

        rarity = str(lot.get("rarity") or "").strip()
        rarity_line = _html.escape(rarity) if rarity else "—"

        craft_val = lot.get("craft_uid_possible")
        if craft_val is True:
            craft_line = "✅ Да"
        elif craft_val is False:
            craft_line = "❌ Нет"
        else:
            craft_line = "—"

        currency = str(lot.get("currency") or "алмазы").strip()
        emoji = _cur_emoji_local(currency)
        start_price = lot.get("start_price")
        price_line = f"{int(start_price)} {emoji}" if start_price is not None else f"— {emoji}"

        sold_before = lot.get("sold_before") or lot.get("sold_cnt") or lot.get("sold_count")
        sold_line = str(int(sold_before)) if sold_before is not None else "—"

        # “подарок/профит” если поля есть
        obtain_amount = lot.get("obtain_amount") or lot.get("gift_amount") or lot.get("gain_amount")
        obtain_type = str(lot.get("obtain_type") or lot.get("gift_type") or lot.get("gain_type") or "").strip().lower()
        if obtain_amount is not None:
            if "чай" in obtain_type or "чаш" in obtain_type:
                g_emoji = "🍵"
            elif "сокров" in obtain_type:
                g_emoji = "🪙"
            else:
                g_emoji = "💎"
            gift_line = f"+{int(obtain_amount)} {g_emoji}"
        else:
            gift_line = "—"

        story = _html.escape(str(lot.get("story") or "—"))
        quote = _html.escape(str(lot.get("quote") or "—"))

        comment = str(lot.get("comment") or "").strip()
        comment_line = _html.escape(comment) if comment else "-"

        image_id = lot.get("image_id")

        for u in owner_users:
            notify_text = (
                f"✅ Ваш лот <b>{card_name}</b> одобрен и добавлен в расписание!\n"
                f"📅 Дата: {start_time.strftime('%d.%m.%Y')}\n"
                f"⏰ Время: {start_time.strftime('%H:%M')} (МСК)\n\n"
                f"{hero_name} — {card_name}\n"
                f"Колода: 🃏 {deck_line}\n"
                f"Редкость: 🏷️ {rarity_line}\n"
                f"Крафт на UID возможен: 🆔 {craft_line}\n"
                f"Продано ранее: 📊 {sold_line}\n"
                f"При получении в подарок даёт: 🎁 {gift_line}\n"
                f"История: 📜 {story}\n"
                f"Цитата: 💬 {quote}\n"
                f"Комментарий: 🗨️ {comment_line}\n\n"
                "Ожидайте публикации в канале аукциона!"
                f"\n\n<b>Модератор:</b> {_html.escape(moderator)}"
            )
            try:
                if image_id and image_id != "DEFAULT_PHOTO_ID":
                    await _bot_send_media_any(
                        call.bot,
                        chat_id=int(u["user_id"]),
                        file_id=str(image_id),
                        caption=notify_text,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                else:
                    await call.bot.send_message(
                        int(u["user_id"]),
                        notify_text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
            except Exception as e:
                print(f"Ошибка уведомления владельца {u['user_id']}: {e}")

        await safe_edit_message(call, ADMIN_MESSAGES["lot_scheduled"])
        await state.clear()
        await call.answer()
    else:
        await call.answer("Некорректный callback!", show_alert=True)


@router.callback_query(F.data.startswith("reject_lot|"))
@admin_only
async def start_reject_lot(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(split_callback_data(call.data, "|")[1])
    await state.update_data(auction_id=auction_id)
    await call.message.answer(MSG_REASON_REJECT_ADD)
    await state.set_state(ModActionFSM.waiting_for_reject_pending_reason)
    await call.answer()


@router.callback_query(F.data.startswith("show_proof|"))
async def show_proof_photo(call: types.CallbackQuery):
    auction_id = int(split_callback_data(call.data, "|")[1])
    lot = await get_lot_by_id(auction_id)
    proof_photo_id = lot.get("proof_photo_id")
    if proof_photo_id:
        kb = build_back_keyboard(auction_id)
        await call.message.answer_photo(
            proof_photo_id,
            caption=MSG_PHOTO_CONFIRM,
            reply_markup=kb
        )
        await call.answer()
    else:
        await call.answer(MSG_PHOTO_NOT_FOUND, show_alert=True)


@router.callback_query(F.data.startswith("back_to_lot|"))
async def back_to_lot(call: types.CallbackQuery):
    auction_id = int(split_callback_data(call.data, "|")[1])
    lot = await get_lot_by_id(auction_id)
    owners = await get_lot_owners_with_levels(call.bot, auction_id)
    text = format_pending_lot(lot, owners)
    kb = build_lot_keyboard(lot)
    media_id = lot.get("image_id") or lot.get("card_image_id")
    if media_id:
        await safe_answer_photo(call.message, media_id, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.message(F.text == "/delete_requests", F.chat.type == "private")
@admin_only
async def show_delete_requests_cmd_command(message: types.Message):
    await show_delete_requests_for_moderation(message)


@router.callback_query(F.data.startswith("approve_delete|"))
@admin_only
async def approve_delete_request(call: types.CallbackQuery, state: FSMContext):
    req_id = int(split_callback_data(call.data, "|")[1])
    row = await get_delete_request(req_id)
    if not row:
        await call.answer("Заявка не найдена или уже обработана.", show_alert=True)
        return
    lot = await get_lot_by_id(row["lot_id"])
    if not lot:
        await call.answer("Лот уже удалён.", show_alert=True)
        return
    owners = await get_lot_owners(lot["auction_id"])
    moderator_tag = admin_tag(call.from_user)
    if call.from_user.username:
        uname = html.escape(call.from_user.username)
        moderator_html = f'<a href="https://t.me/{uname}">@{uname}</a>'
    else:
        moderator_html = f'<a href="tg://user?id={call.from_user.id}">{html.escape(call.from_user.full_name)}</a>'
    notification_text = (
        f"🗑️ Ваша заявка на удаление лота <b>{html.escape(lot['card_name'])}</b> одобрена!\n"
        f"Лот был удалён модератором: {moderator_html}\n\n"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n"
    )
    thanks_kb = await build_thanks_kb(lot["auction_id"], moderator_tag)
    image_id = lot.get("image_id")
    for owner in owners:
        try:
            if image_id and image_id != "DEFAULT_PHOTO_ID":
                await call.bot.send_photo(
                    owner["user_id"],
                    photo=image_id,
                    caption=notification_text,
                    parse_mode="HTML",
                    reply_markup=thanks_kb,
                )
            else:
                await call.bot.send_message(
                    owner["user_id"],
                    notification_text,
                    parse_mode="HTML",
                    reply_markup=thanks_kb,
                )
        except TelegramAPIError:
            pass
    moderation_service = await AuctionModerationService.create()
    await moderation_service.cancel(int(lot["auction_id"]))
    await update_delete_request_status(req_id, "approved")
    owners_text = await get_pretty_owners_for_log(lot["auction_id"])
    log_text = format_admin_action_log(
        action="delete_lot",
        admin={"id": call.from_user.id, "username": call.from_user.username or call.from_user.full_name},
        lot=lot,
        owners_text=owners_text
    )
    await send_admin_log(call.bot, log_text)
    await log_audit_action(
        user_id=call.from_user.id,
        action_type="approve_delete_request",
        auction_id=lot["auction_id"],
        details=f"Лот удалён модератором через заявку на удаление"
    )
    try:
        if call.message.photo:
            await call.message.edit_caption("✅ Лот успешно удалён и заявка закрыта.", parse_mode="HTML")
        else:
            await call.message.edit_text("✅ Лот успешно удалён и заявка закрыта.", parse_mode="HTML")
    except TelegramAPIError:
        pass
    await call.answer("Лот удалён.")


@router.callback_query(F.data.startswith("reject_delete|"))
@admin_only
async def reject_delete_request(call: types.CallbackQuery, state: FSMContext):
    request_id = int(split_callback_data(call.data, "|")[1])
    await state.update_data(request_id=request_id)
    await call.message.answer(MSG_REASON_REJECT_DELETE)
    await state.set_state(ModActionFSM.waiting_for_reject_delete_reason)
    await call.answer()


@router.message(RejectDeleteFSM.waiting_for_reject_reason)
@admin_only
async def process_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    req_id = data.get("req_id")
    reason = (message.text or "").strip()
    row = await get_delete_request(req_id)
    if not row:
        await message.answer("Заявка не найдена или уже обработана.")
        await state.clear()
        return
    lot = await get_lot_by_id(row["lot_id"])
    await update_delete_request_status(req_id, "rejected")
    owners = await get_lot_owners(row["lot_id"])
    notification_text = (
        f"🚫 Ваша заявка на удаление лота <b>{lot.get('card_name', '-')}</b> отклонена.\n"
        f"Причина отказа: <i>{reason or 'Не указана'}</i>"
    )
    for owner in owners:
        await notify_lot_owner(message.bot, owner["user_id"], lot, notification_text)
    owners_text = await get_pretty_owners_for_log(row["lot_id"])
    log_text = (
        "<b>❌ Заявка на удаление лота ОТКЛОНЕНА</b>\n"
        f"Лот: <b>{lot.get('card_name', '-')}</b>\n"
        f"Владелец(ы): {owners_text}\n"
        f"Причина отклонения: <i>{reason or 'Не указана'}</i>"
    )
    await send_admin_log(message.bot, log_text)
    await message.answer("Заявка отклонена.")
    await state.clear()


@router.message(F.text.startswith("/adddeck"), F.chat.type == "private")
async def add_deck_command(message: types.Message, state: FSMContext):
    await add_deck_fsm_entry(message, state)


__all__ = [
    "router",
    "fsm_back_handler",
    "handle_reject_pending_reason",
    "handle_reject_delete_reason",
    "add_admin_cmd",
    "remove_admin_cmd",
    "choose_month",
    "choose_day",
    "choose_time",
    "legacy_choose_time_back",
    "handle_confirm_lot",
    "start_reject_lot",
    "show_proof_photo",
    "back_to_lot",
    "show_delete_requests_cmd_command",
    "approve_delete_request",
    "reject_delete_request",
    "process_reject_reason",
    "add_deck_command",
]
