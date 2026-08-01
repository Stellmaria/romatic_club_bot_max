"""CLIK order workflow.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

from bot.handlers.admin.moderation_shared import *  # noqa: F403
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)


@router.message(Command("clik"), F.chat.type == "private")
async def clik_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(CLIK_ROOT_TEXT, reply_markup=_kb_clik_root(), parse_mode="HTML")


@router.callback_query(F.data == f"{CLIK_CB}:noop")
async def clik_noop(call: types.CallbackQuery):
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:root")
async def clik_root(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _ui_edit(call, text=CLIK_ROOT_TEXT, kb=_kb_clik_root(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:price")
async def clik_price(call: types.CallbackQuery):
    await _ui_edit(call, text=CLIK_PRICE_TEXT, kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:instruction")
async def clik_instruction(call: types.CallbackQuery):
    await _ui_edit(call, text=CLIK_INSTRUCTION_TEXT, kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:ask")
async def clik_ask(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ClikFSM.waiting_question)
    await _ui_edit(call, text=CLIK_ASK_TEXT + "\n\n<b>Отмена:</b> напиши «Отмена».", kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.message(ClikFSM.waiting_question, F.chat.type == "private")
async def clik_got_question(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    user = message.from_user
    body = html.escape(txt)
    log_text = (
        "❓ <b>Вопрос от пользователя</b>\n"
        f"👤 {(_user_tag(user) if user else '—')} (id: <code>{user.id if user else 0}</code>)\n\n"
        f"{body}"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer("✅ Вопрос отправлен админам.", reply_markup=_kb_clik_root(), parse_mode="HTML")


@router.callback_query(F.data == f"{CLIK_CB}:order")
async def clik_order(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _ui_edit(call, text=CLIK_ORDER_PICK_PAY_TEXT, kb=_kb_clik_pay(), photo_id=None)
    await call.answer()


@router.callback_query(F.data.startswith(f"{CLIK_CB}:pay:"))
async def clik_pay(call: types.CallbackQuery, state: FSMContext):
    parts = split_callback_data(call.data or "", ":")
    if len(parts) < 3:
        await call.answer("Кривая кнопка.", show_alert=True)
        return

    pay_key = parts[2].strip()
    pay_label = {"cups": "Чашки", "diamonds": "Алмазы", "rub": "Рубли (₽)"}.get(pay_key, pay_key)

    await state.clear()
    await state.update_data(
        clik_pay=pay_label,
        clik_pay_key=pay_key,
        clik_story=None,
        clik_story_key=None,
        clik_story_page=0,

        clik_t_play=False,
        clik_t_ach=False,
        clik_ach_mode=None,
        clik_t_love=False,
        clik_love_mode=None,
        clik_love_selected=[],

        clik_t_wardrobe=False,
        clik_t_other=False,
        clik_other_text=None,

        clik_cups_source=None,
    )
    await state.set_state(ClikFSM.order_story)

    data = await state.get_data()
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(0), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_story, F.data.startswith(f"{CLIK_CB}:s:page:"))
async def clik_story_page(call: types.CallbackQuery, state: FSMContext):
    try:
        page = int(split_callback_data(call.data or "", ":")[-1])
    except Exception:
        page = 0

    await state.update_data(clik_story_page=page)
    data = await state.get_data()
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(page), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_story, F.data.startswith(f"{CLIK_CB}:s:pick:"))
async def clik_story_pick(call: types.CallbackQuery, state: FSMContext):
    try:
        idx = int(split_callback_data(call.data or "", ":")[-1])
    except Exception:
        await call.answer("Не понял историю.", show_alert=True)
        return

    if idx < 0 or idx >= len(CLIK_STORIES):
        await call.answer("История вне списка.", show_alert=True)
        return

    story = CLIK_STORIES[idx]
    story_key = _clik_story_key(story)

    await state.update_data(clik_story=story, clik_story_key=story_key)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    # ПВТ: показываем обложку истории
    if story_key == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:back_stories")
async def clik_back_to_stories(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("clik_story_page") or 0)

    await state.set_state(ClikFSM.order_story)
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(page), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data.startswith(f"{CLIK_CB}:t:toggle:"))
async def clik_task_toggle(call: types.CallbackQuery, state: FSMContext):
    key = split_callback_data(call.data or "", ":")[-1].strip()

    data = await state.get_data()
    if key == "play":
        await state.update_data(clik_t_play=not bool(data.get("clik_t_play")))
    elif key == "wardrobe":
        await state.update_data(clik_t_wardrobe=not bool(data.get("clik_t_wardrobe")))
    else:
        await call.answer()
        return

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:ach")
async def clik_ach_open(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_ach=True)
    await state.set_state(ClikFSM.order_ach_mode)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Ачивки</b>\nВыбери режим (пока без списка достижений, это заглушка):"
    )
    # не обязательно менять фото, оставим что было
    await _ui_edit(call, text=text, kb=_kb_clik_ach_mode(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data.startswith(f"{CLIK_CB}:ach:set:"))
async def clik_ach_set(call: types.CallbackQuery, state: FSMContext):
    mode = split_callback_data(call.data or "", ":")[-1].strip()  # all | story
    if mode not in {"all", "story"}:
        await call.answer("Кривой режим.", show_alert=True)
        return

    await state.update_data(clik_t_ach=True, clik_ach_mode=mode)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data == f"{CLIK_CB}:ach:off")
async def clik_ach_off(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_ach=False, clik_ach_mode=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data == f"{CLIK_CB}:ach:back")
async def clik_ach_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:love")
async def clik_love_open(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    story_key = str(data.get("clik_story_key") or "other")

    await state.update_data(clik_t_love=True)

    if story_key == "pvt":
        # ПВТ: выбор персонажей с картинками
        await state.set_state(ClikFSM.order_love_select)

        # дефолт: показываем первого (Себастьян)
        default_key = "seb"
        await state.update_data(clik_love_last=default_key)

        data = await state.get_data()
        text = _clik_order_intro(
            data,
            "2) <b>Любовные линии (ПВТ)</b>\n"
            "Нажимай на персонажей ниже. Сверху будет меняться картинка выбранной линии.\n"
            "Можно выбрать несколько."
        )
        await _ui_edit(call, text=text, kb=_kb_clik_love_pvt(data), photo_id=CLIK_PVT_LI_MAP[default_key]["photo"])
        await call.answer()
        return

    # НЕ-ПВТ: пока заглушка по количеству
    await state.set_state(ClikFSM.order_love_mode)
    text = _clik_order_intro(
        data,
        "2) <b>Любовные линии</b>\nВыбери сколько линий нужно закрыть (пока без имён героев):"
    )
    await _ui_edit(call, text=text, kb=_kb_clik_love_mode_generic(), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data.startswith(f"{CLIK_CB}:love:set:"))
async def clik_love_set_generic(call: types.CallbackQuery, state: FSMContext):
    mode = split_callback_data(call.data or "", ":")[-1].strip()  # all | 1 | 2 | 3
    if mode not in {"all", "1", "2", "3"}:
        await call.answer("Кривой режим.", show_alert=True)
        return

    await state.update_data(clik_t_love=True, clik_love_mode=mode)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data == f"{CLIK_CB}:love:off")
async def clik_love_off_generic(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_love=False, clik_love_mode=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data == f"{CLIK_CB}:love:back")
async def clik_love_back_generic(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data.startswith(f"{CLIK_CB}:lpvt:toggle:"))
async def clik_love_pvt_toggle(call: types.CallbackQuery, state: FSMContext):
    key = split_callback_data(call.data or "", ":")[-1].strip()
    if key not in CLIK_PVT_LI_MAP:
        await call.answer()
        return

    data = await state.get_data()
    selected = set(data.get("clik_love_selected") or [])

    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)

    await state.update_data(clik_love_selected=list(selected), clik_love_last=key, clik_t_love=True)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Любовные линии (ПВТ)</b>\n"
        "Нажимай на персонажей ниже. Сверху будет меняться картинка выбранной линии.\n"
        "Можно выбрать несколько."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_love_pvt(data), photo_id=CLIK_PVT_LI_MAP[key]["photo"])
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:done")
async def clik_love_pvt_done(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:off")
async def clik_love_pvt_off(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_love=False, clik_love_selected=[], clik_love_last=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:back")
async def clik_love_pvt_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:other")
async def clik_other_open(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_other=True)
    await state.set_state(ClikFSM.waiting_other_text)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Другое</b>\n"
        "Напиши одним сообщением, что именно нужно.\n"
        "Если хочешь выключить «Другое» — пришли <code>-</code>.\n\n"
        "<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_cancel(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.message(ClikFSM.waiting_other_text, F.chat.type == "private")
async def clik_other_text(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    if txt == "-":
        await state.update_data(clik_t_other=False, clik_other_text=None)
    else:
        await state.update_data(clik_t_other=True, clik_other_text=txt)

    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await message.answer_photo(CLIK_STORY_COVER_PVT, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:next")
async def clik_tasks_next(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pay_key = str(data.get("clik_pay_key") or "").strip()

    # если чашки — спрашиваем источник чашек
    if pay_key == "cups":
        await state.set_state(ClikFSM.order_cups_source)
        text = _clik_order_intro(
            data,
            "3) <b>Чашки</b>\nВыбери, есть ли чашки на аккаунте или проходим на ежедневных:"
        )
        await _ui_edit(call, text=text, kb=_kb_clik_cups_source(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
        await call.answer()
        return

    # иначе сразу просим логин/пароль
    await state.set_state(ClikFSM.waiting_order)
    data = await state.get_data()
    text = _clik_order_intro(
        data,
        _clik_preview_summary(data)
        + "\n\n4) <b>Пришли логин и пароль</b> одним сообщением.\n\n<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_final_back(data), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.order_cups_source, F.data == f"{CLIK_CB}:cups:back")
async def clik_cups_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_cups_source, F.data.startswith(f"{CLIK_CB}:cups:"))
async def clik_cups_set(call: types.CallbackQuery, state: FSMContext):
    tail = split_callback_data(call.data or "", ":")[-1].strip()
    if tail not in {"account", "daily"}:
        await call.answer()
        return

    await state.update_data(clik_cups_source=tail)
    await state.set_state(ClikFSM.waiting_order)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        _clik_preview_summary(data)
        + "\n\n4) <b>Пришли логин и пароль</b> одним сообщением.\n\n<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_final_back(data), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.waiting_order, F.data == f"{CLIK_CB}:final:back_cups")
async def clik_final_back_cups(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_cups_source)
    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "3) <b>Чашки</b>\nВыбери, есть ли чашки на аккаунте или проходим на ежедневных:"
    )
    await _ui_edit(call, text=text, kb=_kb_clik_cups_source(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.waiting_order, F.data == f"{CLIK_CB}:final:back_tasks")
async def clik_final_back_tasks(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.message(ClikFSM.waiting_order, F.chat.type == "private")
async def clik_got_order(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    data = await state.get_data()
    pay_label = str(data.get("clik_pay") or "—")

    user = message.from_user
    creds = html.escape(txt)

    order_text = (
        "🛒 <b>Новый заказ</b>\n"
        f"💳 Оплата: <b>{html.escape(pay_label)}</b>\n"
        f"👤 {(_user_tag(user) if user else '—')} (id: <code>{user.id if user else 0}</code>)\n\n"
        f"{_clik_order_intro(data, _clik_preview_summary(data))}\n\n"
        "🔐 <b>Логин и пароль:</b>\n"
        f"<code>{creds}</code>"
    )
    await send_admin_log(message.bot, order_text)

    await state.clear()
    await message.answer("✅ Заказ отправлен админам. Ожидай ответа.", reply_markup=_kb_clik_root(), parse_mode="HTML")


__all__ = [
    "router",
    "clik_cmd",
    "clik_noop",
    "clik_root",
    "clik_price",
    "clik_instruction",
    "clik_ask",
    "clik_got_question",
    "clik_order",
    "clik_pay",
    "clik_story_page",
    "clik_story_pick",
    "clik_back_to_stories",
    "clik_task_toggle",
    "clik_ach_open",
    "clik_ach_set",
    "clik_ach_off",
    "clik_ach_back",
    "clik_love_open",
    "clik_love_set_generic",
    "clik_love_off_generic",
    "clik_love_back_generic",
    "clik_love_pvt_toggle",
    "clik_love_pvt_done",
    "clik_love_pvt_off",
    "clik_love_pvt_back",
    "clik_other_open",
    "clik_other_text",
    "clik_tasks_next",
    "clik_cups_back",
    "clik_cups_set",
    "clik_final_back_cups",
    "clik_final_back_tasks",
    "clik_got_order",
]
