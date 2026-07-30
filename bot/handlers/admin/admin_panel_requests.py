"""Admin menus, access control, and request queues.

Handlers retain their relative order from the legacy ``admin_panel`` module.
"""

from bot.handlers.admin.admin_panel_shared import *  # noqa: F403

router = Router(name=__name__)


@router.callback_query(F.data == "admreq_back")
@admin_only
async def admreq_back(call: CallbackQuery):
    # возвращаемся в меню модерации
    try:
        await call.message.delete()
    except Exception:
        pass

    await call.message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"]
        )
    )
    await call.answer()


@router.message(F.text.regexp(r"^/ex_owners\s+\d+$"))
@admin_only
async def cmd_ex_owners(message: Message):
    parts = (message.text or "").split()
    card_id = int(parts[1])

    owners_map = await get_exchange_owners_for_cards([card_id], status="approved")
    owners = owners_map.get(card_id) or []

    if not owners:
        await message.answer(f"🛒 По карте <code>{card_id}</code> в бирже владельцев не найдено.", parse_mode="HTML")
        return

    lines = [f"🛒 <b>Владельцы по карте</b> <code>{card_id}</code>:\n"]
    for o in owners:
        uname = o["username"]
        utext = f"@{uname}" if uname else "—"
        lines.append(f"• {utext} (id:{o['user_id']}) × {o['qty']} | batch_id: <code>{o['batch_id']}</code>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("admreq|"))
@admin_only
async def admreq_select(call: CallbackQuery):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Некорректная команда.", show_alert=True)
        return

    _, req_type, kind = parts
    kind = _norm_auk_kind(kind)

    # сразу отвечаем на callback, чтобы не висело "часики"
    await call.answer()

    # можно убрать меню выбора, чтобы не засорять чат
    try:
        await call.message.delete()
    except Exception:
        pass

    if req_type == "pending":
        if kind == "exchange":
            await call.message.answer(
                "🛒 <b>Заявки на биржу</b>\n\nКак показать?",
                parse_mode="HTML",
                reply_markup=_kb_exchange_pending_mode(),
            )
        else:
            await show_pendinglots(call.message, kind=kind)
    elif req_type == "delete":
        await show_delete_requests_for_moderation(call.message, kind=kind)
    else:
        await call.message.answer("Некорректный тип заявок.")


@router.message(F.text.in_(['/admin', '/admin_panel']), F.chat.type == "private")
@admin_only
async def show_admin_menu(message: Message, state: FSMContext):
    await state.clear()
    await send_admin_main_menu(message)


@router.message(F.text == "⚙️ Модерация", F.chat.type == "private")
@admin_only
async def moderation_menu(message: Message):
    await message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["🧾 Верификация", "⛔ UID-бан"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"]
        )
    )


@router.message(F.text == "🤝 Доверить пользователя", F.chat.type == "private")
@admin_only
async def start_give_trusted(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_trusted_user)
    await message.answer(
        "Введите username (@username) или user_id для выдачи статуса 'доверенный':",
        reply_markup=inline_back_keyboard()
    )


@router.message(ModActionFSM.waiting_for_trusted_user, F.chat.type == "private")
@admin_only
async def give_trusted_user(message: Message, state: FSMContext):
    await _do_trusted_action(
        message=message,
        state=state,
        who=message.text,
        bot=message.bot,
        grant=True,
    )


@router.message(F.text == "❌ Снять доверие", F.chat.type == "private")
@admin_only
async def start_remove_trusted(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_untrusted_user)
    await message.answer(
        "Введите username (@username) или user_id для снятия статуса 'доверенный':",
        reply_markup=inline_back_keyboard()
    )


@router.message(ModActionFSM.waiting_for_untrusted_user, F.chat.type == "private")
@admin_only
async def remove_trusted_user(message: Message, state: FSMContext):
    await _do_trusted_action(
        message=message,
        state=state,
        who=message.text,
        bot=message.bot,
        grant=False,
    )


@router.message(F.text == "➕ Добавить админа", F.chat.type == "private")
@admin_only
async def start_add_admin(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_admin_user)
    await message.answer(
        "Введите @username или user_id и пароль через пробел (пример: @user password):",
        reply_markup=back_keyboard(text="Назад", callback="addadmin_cancel")
    )


@router.message(ModActionFSM.waiting_for_admin_user, F.chat.type == "private")
@admin_only
async def add_admin_user(message: Message, state: FSMContext):
    await admin_add_remove(message, state, is_remove=False)


@router.message(F.text == "➖ Удалить админа", F.chat.type == "private")
@admin_only
async def start_remove_admin(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_admin_remove_user)
    await message.answer(
        "Введите @username или user_id и пароль через пробел (пример: @user password):",
        reply_markup=back_keyboard(text="Назад", callback="removeadmin_cancel")
    )


@router.message(ModActionFSM.waiting_for_admin_remove_user, F.chat.type == "private")
@admin_only
@owner_or_secret_required
async def remove_admin_user(message: Message, state: FSMContext):
    await admin_add_remove(message, state, is_remove=True)


@router.message(F.text.in_(['/pendinglots', '📝 Заявки на модерацию']), F.chat.type == "private")
@admin_only
async def pendinglots_cmd(message: Message):
    await show_requests_kind_menu(message, req_type="pending")


@router.message(F.text.in_(['/delete_requests', '🗂️ Заявки на удаление']), F.chat.type == "private")
@admin_only
async def show_delete_requests_cmd(message: Message):
    await show_requests_kind_menu(message, req_type="delete")


@router.message(F.text == "📅 Расписание", F.chat.type == "private")
@admin_only
async def schedule_button(message: Message, state: FSMContext):
    await start_preview_schedule(message, state)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message):
    kb = InlineKeyboardBuilder()
    # ведём в корень “принятых лотов”, там уже есть “по колодам/списком”
    kb.button(text="✅ Принятые лоты", callback_data="ex_appr:root")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)

    await message.answer(
        "🛒 <b>Биржа</b>\n\nОткрываю принятые лоты:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "ex_appr:decks")
@admin_only
async def ex_appr_decks(call: types.CallbackQuery):
    decks = await _q_exchange_approved_decks()
    if not decks:
        await _safe_edit_text_or_caption(
            call.message,
            text="🛒 <b>Биржа</b>\n\nПока нет принятых лотов.",
            reply_markup=_kb_exchange_approved_root(),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите колоду:",
        reply_markup=_kb_exchange_approved_decks(decks),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:whole:"))
@admin_only
async def ex_appr_whole(call: types.CallbackQuery):
    # ex_appr:whole:<deck_id>:<page>
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    page = max(0, page)

    per_page = 12
    rows = await _q_exchange_whole_deck_batches(deck_id, limit=500)
    batch_ids = [int(r.get("batch_id") or 0) for r in (rows or []) if int(r.get("batch_id") or 0) > 0]

    total = len(batch_ids)
    if total <= 0:
        await _safe_edit_text_or_caption(
            call.message,
            text=(
                "📚 <b>Биржа → Колода целиком</b>\n\n"
                f"Колода: <b>{deck_id}</b>\n\n"
                "Лотов нет."
            ),
            reply_markup=_kb_ex_appr_back_to_deck(deck_id),
        )
        await call.answer()
        return

    last = max(0, (total - 1) // per_page)
    page = min(page, last)
    chunk = batch_ids[page * per_page: page * per_page + per_page]

    lines = [
        "📚 <b>Биржа → Колода целиком</b>",
        f"Колода: <b>{deck_id}</b>",
        f"Страница: <b>{page + 1}/{last + 1}</b> • Всего: <b>{total}</b>",
        "",
        "Выбери лот:",
    ]

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lotdeck:{deck_id}:{page}:{bid}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:whole:{deck_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:whole:{deck_id}:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}")
    kb.adjust(3, 3, 3, 3, 1, 1)

    await _safe_edit_text_or_caption(call.message, text="\n".join(lines), reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:lotdeck:"))
@admin_only
async def ex_appr_lotdeck_show(call: types.CallbackQuery):
    # ex_appr:lotdeck:<deck_id>:<page>:<batch_id>
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    page = int(parts[3])
    batch_id = int(parts[4])

    caption = await _format_exchange_approved_lot_caption(batch_id)
    back_cb = f"ex_appr:whole:{deck_id}:{page}"
    kb = _kb_exchange_approved_lot_actions(batch_id=batch_id, back_cb=back_cb)

    media_id = None
    kind = "photo"
    try:
        cover_id, cover_kind = await _get_exchange_cover_media(batch_id)
        if cover_id:
            media_id = cover_id
            kind = cover_kind
    except Exception:
        media_id = None

    # показываем лот (как в ex_appr_lot_show), но “назад” ведёт в whole list
    if media_id:
        try:
            await safe_send_media(
                call.bot,
                chat_id=call.message.chat.id,
                file_id=str(media_id),
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
                protect_content=False,
            )
        except Exception:
            # fallback: просто текст
            await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def cb_exchange_approved_root(call: CallbackQuery):
    decks = await get_exchange_deck_overview(status="approved")
    if not decks:
        await call.answer("🛒 На бирже нет активных лотов.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for d in decks:
        deck_id = int(d["deck_id"])
        name = d["deck_name"]
        cnt = int(d["items_count"])
        kb.button(text=f"{name} ({cnt})", callback_data=f"exinv|{deck_id}|0")

    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)

    await call.message.edit_text(
        "🛒 <b>Биржа</b>\nВыберите колоду:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


__all__ = [
    "router",
    "admreq_back",
    "cmd_ex_owners",
    "admreq_select",
    "show_admin_menu",
    "moderation_menu",
    "start_give_trusted",
    "give_trusted_user",
    "start_remove_trusted",
    "remove_trusted_user",
    "start_add_admin",
    "add_admin_user",
    "start_remove_admin",
    "remove_admin_user",
    "pendinglots_cmd",
    "show_delete_requests_cmd",
    "schedule_button",
    "exchange_menu_button",
    "ex_appr_decks",
    "ex_appr_whole",
    "ex_appr_lotdeck_show",
    "cb_exchange_approved_root",
]
