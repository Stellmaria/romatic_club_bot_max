"""Schedule preview and forced publication.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

from bot.handlers.admin.moderation_shared import *  # noqa: F403
from bot.domain.auctions import currency_choices_label
from bot.handlers.admin.helper.new.utils import auction_kind_label
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)


@router.message(F.text.in_(['/preview_schedule']), F.chat.type == "private")
@admin_only
async def schedule_command(message: types.Message, state: FSMContext):
    await start_preview_schedule(message, state)


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data.startswith("preview_schedule|"))
@admin_only
async def preview_schedule_month(call: types.CallbackQuery, state: FSMContext):
    _, year_month = split_callback_data(call.data, "|")
    try:
        year, month = map(int, year_month.split('-')[:2])
    except Exception as e:
        await call.answer("Ошибка даты! Данные: " + str(year_month), show_alert=True)
        return
    await state.update_data(year=year, month=month)
    await call.message.answer(
        "Выберите день:",
        reply_markup=days_keyboard("preview_schedule", None, year, month)
    )
    await state.set_state(PreviewScheduleFSM.choosing_day)
    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_day, F.data.startswith("preview_schedule|"))
@admin_only
async def preview_schedule_day(call: types.CallbackQuery, state: FSMContext):
    parts = split_callback_data(call.data, "|")
    try:
        date_part = parts[2] if (len(parts) == 3 and parts[2].count("-") == 2) \
            else parts[1] if (len(parts) == 2 and parts[1].count("-") == 2) \
            else (_ for _ in ()).throw(ValueError)
        year, month, day = map(int, date_part.split("-"))
    except ValueError:
        await call.message.answer(f"Ошибка формата даты: {safe_html(call.data)}", parse_mode="HTML")
        await state.clear()
        return
    # Acknowledge the button before the database/rendering work.  Telegram
    # callbacks expire quickly, while the schedule can contain many lots.
    try:
        await call.answer()
    except TelegramBadRequest:
        pass

    selected_date = date(year, month, day)
    # Always read a fresh live snapshot.  The repository excludes historical
    # ``finished`` rows and includes approved/publishing lots that already
    # reserve a slot.
    auctions = await get_auctions_by_date_with_owners(selected_date)
    refreshed_at = to_moscow_wall(utc_now())
    header = (
        f"📅 <b>Актуальное расписание на {selected_date.strftime('%d.%m.%Y')}</b>\n"
        f"🕒 Обновлено: {refreshed_at.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n\n"
    )
    blocks: list[str] = [header]
    grouped: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    occupied: list[tuple] = []
    for lot in auctions:
        hero = lot.get('hero_name')
        card_name = lot.get('card_name') or ''
        deck_id = lot.get('deck_id')
        display_name = safe_html(hero) if hero else safe_html(card_name) if card_name else 'Без имени'
        deck_text = f"Колода №{deck_id}" if deck_id else ""
        start_msk = to_moscow(lot['start_time'])
        end_msk = to_moscow(lot['end_time'])
        start_s = start_msk.strftime('%H:%M')
        end_s = end_msk.strftime('%H:%M')
        key = (start_s, end_s, display_name, deck_text)
        grouped[key].append(lot)
        occupied.append((start_msk.time(), end_msk.time()))
    if not grouped:
        blocks.append("Нет лотов на этот день.\n\n")
    else:
        for (start_s, end_s, display_name, deck_text), lots in sorted(grouped.items(),
                                                                      key=lambda x: (x[0][0], x[0][1], x[0][2])):
            qty = len(lots)
            block = ""
            for lot in lots:
                auction_id = lot.get("auction_id")
                owners_txt = owners_to_links_text(lot.get("owners_json"))
                created_at = lot.get("created_at")
                created_str = to_moscow(created_at).strftime('%d.%m.%Y %H:%M') if created_at else '-'
                comment = (lot.get("comment") or "").strip()
                comment_text = f"💬 Комментарий: {safe_html(comment)}\n" if comment and comment != "-" else ""
                price = lot.get("start_price")
                currency = lot.get("currency", '')
                kind_key = str(lot.get("auction_kind") or "standard").strip().lower()
                kind_text = safe_html(auction_kind_label(kind_key))
                accepted_text = safe_html(
                    currency_choices_label(
                        lot.get("accepted_currencies"),
                        fallback=currency,
                        custom_terms=lot.get("custom_offer_terms"),
                    )
                )
                if kind_key == "reverse":
                    price_text = (
                        f"💱 Валюта ставок: {accepted_text}\n"
                        "📉 Побеждает минимальная ставка\n"
                    )
                elif kind_key == "free":
                    price_text = f"💱 Принимаются предложения: {accepted_text}\n"
                else:
                    price_text = f"💵 Цена: {price} {currency}\n" if price else ""
                deck_id = lot.get("deck_id")
                deck_line = f"Колода №{deck_id}" if deck_id else ""
                block += (
                    f"🎴 <b>{safe_html(lot.get('card_name') or display_name)}</b> {deck_line}\n"
                    f"🔎 Auction ID: <b>{auction_id}</b>\n"
                    f"👤 Герой: {safe_html(lot.get('hero_name') or '-')}\n"
                    f"⚙️ Тип: {kind_text}\n"
                    f"⏰ <b>{start_s}–{end_s}</b>\n"
                    f"🙍‍♂️ <b>Владелец(ы):</b> {owners_txt}\n"
                    f"{comment_text}"
                    f"{price_text}"
                    f"🕑 Дата заявки: {created_str}\n"
                )
            block += f"👥 <b>Количество:</b> {qty}\n"
            block += "──────\n"
            blocks.append(block)
    free_slots = generate_free_slots_for_date(selected_date, occupied)
    if free_slots:
        free_slots_text = "<b>🟢 Свободное время для записи:</b>\n"
        for slot in free_slots:
            free_slots_text += f"▫️ {slot.strftime('%H:%M')}–{(slot + timedelta(minutes=30)).strftime('%H:%M')}\n"
        blocks.append(free_slots_text)
    else:
        blocks.append("<b>🔒 Нет свободных слотов для записи в этот день.</b>\n")
    for chunk in split_message_by_blocks(blocks):
        await call.message.answer(tg_clean(chunk), parse_mode="HTML")
    await state.clear()


@router.message(F.text.in_(['/edit_schedule']), F.chat.type == "private")
@admin_only
async def edit_schedule_command(message: types.Message, state: FSMContext):
    await start_edit_schedule(message, state)


@router.message(F.text.startswith('/force_publish'))
async def force_publish_handler(message: types.Message):
    import logging
    logger = logging.getLogger("auction")
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if message.from_user.id not in ADMINS:
        logger.warning(f"[FORCE_PUBLISH] Нет доступа для user_id={message.from_user.id}")
        await message.answer("Нет доступа.")
        return
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            logger.info(f"[FORCE_PUBLISH] Неверный формат: {message.text}")
            await message.answer("Используй: /force_publish <auction_id>")
            return
        auction_id = int(parts[1])
        logger.info(f"[FORCE_PUBLISH] Запрос публикации для auction_id={auction_id}")
        lot = await get_lot_by_id(auction_id)
        if not lot:
            logger.warning(f"[FORCE_PUBLISH] Лот не найден: auction_id={auction_id}")
            await message.answer(f"Лот с auction_id={auction_id} не найден.")
            return
        bot = message.bot
        logger.info(f"[FORCE_PUBLISH] Публикуем лот {auction_id}: {lot.get('card_name')}")
        msg_id = await publish_auction_lot(bot, lot)
        if msg_id:
            logger.info(f"[FORCE_PUBLISH] Лот {auction_id} опубликован: message_id={msg_id}")
            await message.answer(f"Лот {auction_id} опубликован, message_id={msg_id}.")
        else:
            logger.error(f"[FORCE_PUBLISH] Ошибка публикации лота {auction_id}: msg_id is None!")
            await message.answer(f"Ошибка публикации лота {auction_id}.")
    except Exception as e:
        logger.error(f"[FORCE_PUBLISH] Ошибка: {e}", exc_info=True)
        await message.answer(f"Ошибка: {e}")


__all__ = [
    "router",
    "schedule_command",
    "preview_schedule_month",
    "preview_schedule_day",
    "edit_schedule_command",
    "force_publish_handler",
]
