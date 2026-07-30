import html as _html
import html as _html
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Any

from aiogram import types, Router, F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dateutil import tz

from bot.core.time import schedule_slot_key, to_moscow, to_moscow_wall, utc_now
from bot.domain.auctions import AuctionKind, currency_choices_label
from bot.handlers.admin.helper.new.utils import auction_kind_label

from bot.handlers.admin.admin_panel import notify_owners_lot_changed
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, REJECT_LOT_ADMIN_LOG, REJECT_LOT_USER_NOTIFY, \
    MSG_REASON_REJECT_ADD, MSG_REASON_REJECT_DELETE, MSG_PHOTO_CONFIRM, MSG_PHOTO_NOT_FOUND, MSG_CONFIRM_PUBLICATION, \
    REJECT_DELETE_USER_NOTIFY, \
    REJECT_DELETE_ADMIN_LOG, CANCEL_TEXTS, BUTTONS, CALLBACK_CONFIRM_LOT
from bot.handlers.admin.helper.admin_keyboards import days_keyboard
from bot.handlers.admin.helper.admin_keyboards import months_keyboard
from bot.handlers.admin.helper.admin_service import get_free_slots_and_schedule_for_lot
from bot.handlers.admin.action_support.compat import owner_or_secret_required, get_lot_owners_text, \
    safe_edit_message, process_reject_action, admin_add_remove, send_admin_log, show_delete_requests_for_moderation, \
    add_deck_fsm_entry, start_preview_schedule, \
    start_edit_schedule, process_universal_cancel_text, owners_to_links_text, tg_clean, \
    show_pendinglots, safe_answer_photo
from bot.handlers.admin.helper.new.formatting import format_pending_lot, format_admin_action_log, \
    get_lot_owners_with_levels
from bot.handlers.admin.helper.new.helper import split_message
from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard, time_slots_keyboard, \
    build_back_keyboard, back_keyboard, menu_keyboard, build_back_button
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.helper.user_helpers import get_owner_refs, build_schedule_lines, find_free_slots, \
    filter_slots_by_user_type, get_pretty_owners_for_log
from bot.handlers.admin.logs_admin import short_media_id
from bot.handlers.auctions import publish_auction_lot, show_pending_exchange_requests, admin_tag, build_thanks_kb, \
    _bot_send_media_any
from bot.handlers.helper.helpers_users import notify_lot_owner
from bot.utils import generate_free_slots_for_date
from config import ADMINS, AUCTION_CHANNEL_ID, DISCUSSION_CHAT_ID
from db.db import get_auctions_by_date_with_owners as get_auctions_by_date, get_auctions_by_date_with_owners, \
    fetch, fetchrow, get_exchange_batch, set_exchange_batch_status
from db.db import get_lot_by_id, update_auction_status, get_delete_request, update_delete_request_status, \
    get_lot_owners, is_luxury_user, \
    update_auction_time_status, schedule_auction_time_if_available, get_user, log_audit_action, delete_lot, get_auctions_by_date, update_lot_field
from fsm_states import ModActionFSM, ApproveLotFSM, PreviewScheduleFSM, RejectDeleteFSM

router = Router()

import html


def _pretty(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _field_log_block(field_title: str, old_value: Any, new_value: Any) -> str:
    return (
        "\n\n🧩 <b>Изменение поля</b>"
        f"\n📝 <b>Поле:</b> {tg_clean(_pretty(field_title))}"
        f"\n📎 <b>Было:</b> {tg_clean(_pretty(old_value))}"
        f"\n✅ <b>Стало:</b> {tg_clean(_pretty(new_value))}"
    )
async def notify_owners_pending_changed(
    bot,
    *,
    auction_id: int,
    admin_user: types.User,
    changes: list[tuple[str, object, object]],
) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners(int(auction_id))
    if not lot or not owners:
        return

    moderator_tag = admin_tag(admin_user)
    kb = await build_thanks_kb(int(auction_id), moderator_tag)

    def _v(x: object) -> str:
        if x is None:
            return "—"
        s = str(x).strip()
        return s if s else "—"

    ch = "\n".join([f"• <b>{t}:</b> <code>{_v(o)}</code> → <code>{_v(n)}</code>" for t, o, n in changes])

    caption = (
        "🧩 <b>Изменения в вашей заявке (модерация)</b>\n\n"
        f"Лот: <b>{lot.get('card_name') or '—'}</b> — <i>{lot.get('hero_name') or '—'}</i>\n"
        f"ID: <code>{auction_id}</code>\n\n"
        f"<b>Что изменили:</b>\n{ch}\n\n"
        f"👤 <b>Кто изменил:</b> {moderator_tag}\n"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n"
    )

    media_id = lot.get("image_id") or lot.get("photo_id")
    sent: set[int] = set()
    for o in owners:
        try:
            uid = int(o["user_id"])
        except Exception:
            continue
        if uid in sent:
            continue
        sent.add(uid)
        try:
            # pending тоже отправим с текущим медиа
            try:
                await bot.send_photo(uid, media_id, caption=caption, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await bot.send_message(uid, caption, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


async def _log_pending_change(
        bot,
        *,
        admin_user: types.User,
        auction_id: int,
        action_type: str,
        field_title: str,
        old_value: Any,
        new_value: Any,
) -> None:
    new_lot = await get_lot_by_id(int(auction_id))
    owners_text = await get_lot_owners_text(int(auction_id))

    log_text = format_admin_action_log(
        action="edit_pending",
        admin={
            "id": admin_user.id,
            "user_id": admin_user.id,  # на всякий случай под твою структуру
            "username": admin_user.username or "",
            "full_name": admin_user.full_name or "",
        },
        lot=new_lot,
        owners_text=owners_text,
    )
    log_text += _field_log_block(field_title, old_value, new_value)

    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=int(auction_id),
        details=f"{field_title}: {_pretty(old_value)} -> {_pretty(new_value)}",
    )


def _pretty_bool(v: Any) -> str:
    if v is None:
        return "—"
    return "✅ Да" if bool(v) else "❌ Нет"


def _pretty_value(field: str, v: Any) -> str:
    if v is None or v == "":
        return "—"
    if field in ("craft_uid_possible",):
        return _pretty_bool(v)
    return str(v)


def _field_log_block(field_title: str, old_value: Any, new_value: Any) -> str:
    return (
        "\n\n🧩 <b>Изменение поля</b>"
        f"\n📝 <b>Поле:</b> {html.escape(field_title)}"
        f"\n📎 <b>Было:</b> {html.escape(_pretty_value(field_title, old_value))}"
        f"\n✅ <b>Стало:</b> {html.escape(_pretty_value(field_title, new_value))}"
    )


def _extract_media_file_id(msg: types.Message) -> str | None:
    if getattr(msg, "photo", None):
        return msg.photo[-1].file_id
    if getattr(msg, "video", None):
        return msg.video.file_id
    if getattr(msg, "animation", None):
        return msg.animation.file_id
    doc = getattr(msg, "document", None)
    if doc and (doc.mime_type or "").startswith("video/"):
        return doc.file_id
    return None


async def _send_pending_lot_card(message: types.Message, bot, auction_id: int) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners_with_levels(bot, int(auction_id))
    text = format_pending_lot(lot, owners)
    kb = build_lot_keyboard(lot, role="admin")

    media_id = (lot or {}).get("image_id") or (lot or {}).get("card_image_id")
    if media_id:
        # safe_answer_photo(msg, image_id, ...) — никаких photo_id=
        await safe_answer_photo(message, media_id, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("back_to_"))
async def fsm_back_handler(call: types.CallbackQuery, state: FSMContext):
    data = call.data.split("|")
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

    async def update_status(auction_id, status): await update_auction_status(auction_id, status)

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


# @router.message(F.text.startswith("/set_trusted"), F.chat.type == "private")
# @admin_only
# async def set_trusted_cmd(message: types.Message, bot, *args, **kwargs):
#     who = message.text.strip().replace("/set_trusted", "").strip()
#     await process_give_trusted(message, who=who, bot=bot)
#
#
# @router.message(F.text.startswith("/unset_trusted"), F.chat.type == "private")
# @admin_only
# async def unset_trusted_cmd(message: types.Message, bot, *args, **kwargs):
#     who = message.text.strip().replace("/unset_trusted", "").strip()
#     await process_remove_trusted(message, who=who, bot=bot)


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


# @owner_or_secret_required
# @admin_only
# async def show_admins(message):
#     await show_list(message, "👑 Админы", list_admins, user_display)
#
#
# @admin_only
# async def show_users(message):
#     await show_list(message, "👥 Пользователи", get_all_users, user_display)
#
#
# @admin_only
# async def show_trusted(message):
#     await show_list(message, "🤝 Доверенные пользователи", get_all_trusted_users, user_display)


@router.callback_query(ApproveLotFSM.choosing_month, F.data.startswith("choose_month|"))
async def choose_month(call: types.CallbackQuery, state: FSMContext):
    _, auction_id, year_month = call.data.split("|")
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
    _, auction_id, year_month_day = call.data.split("|")
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
    _, auction_id, iso_str = call.data.split("|")
    auction_id = int(auction_id)
    selected_time = datetime.fromisoformat(iso_str)
    end_time = selected_time + timedelta(minutes=30)
    data = await state.get_data()
    lot = data.get('lot') or await get_lot_by_id(auction_id)
    auctions = data.get('all_auctions_today')
    if auctions is None:
        auctions = await get_auctions_by_date(selected_time.date())
    selected_grid_time = schedule_slot_key(selected_time)
    conflict_lots = [
        a for a in auctions
        if str(a.get('card_name') or '').strip().casefold()
           == str(lot.get('card_name') or '').strip().casefold()
           and a.get('start_time') is not None
           and schedule_slot_key(a['start_time']) == selected_grid_time
           and int(a.get('auction_id') or 0) != auction_id
    ]
    if conflict_lots:
        info_lines = []
        for a in conflict_lots:
            start = to_moscow_wall(a['start_time']).strftime('%H:%M')
            end = to_moscow_wall(a['end_time']).strftime('%H:%M')
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
    parts = (call.data or "").split("|")
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
    parts = call.data.split("|")
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
        start_time = datetime.fromisoformat(iso_str)
        end_time = start_time + timedelta(minutes=30)
        end_time = end_time.replace(second=59)

        scheduled, conflict_auction_id = await schedule_auction_time_if_available(
            auction_id,
            start_time,
            end_time,
            "scheduled",
        )
        if not scheduled:
            if conflict_auction_id is not None:
                await call.answer(
                    "Этот получасовой слот уже занят этой же картой у этого владельца. Выберите другое время.",
                    show_alert=True,
                )
            else:
                await call.answer(
                    "Не удалось закрепить время за лотом. Обновите карточку и попробуйте ещё раз.",
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
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)
    await call.message.answer(MSG_REASON_REJECT_ADD)
    await state.set_state(ModActionFSM.waiting_for_reject_pending_reason)
    await call.answer()


@router.callback_query(F.data.startswith("show_proof|"))
async def show_proof_photo(call: types.CallbackQuery):
    auction_id = int(call.data.split("|")[1])
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
    auction_id = int(call.data.split("|")[1])
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
    req_id = int(call.data.split("|")[1])
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
    await delete_lot(lot["auction_id"])
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
    request_id = int(call.data.split("|")[1])
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


# @router.callback_query(F.data.startswith("reject_delete|"))
# @admin_only
# async def reject_delete_request(call: types.CallbackQuery, state: FSMContext):
#     request_id = int(call.data.split("|")[1])
#     await state.update_data(request_id=request_id)
#     await call.message.answer(MSG_REASON_REJECT_DELETE)
#     await state.set_state("waiting_reject_delete_reason")
#     await call.answer()


@router.message(F.text.in_(['/preview_schedule']), F.chat.type == "private")
@admin_only
async def schedule_command(message: types.Message, state: FSMContext):
    await start_preview_schedule(message, state)


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data.startswith("preview_schedule|"))
@admin_only
async def preview_schedule_month(call: types.CallbackQuery, state: FSMContext):
    _, year_month = call.data.split("|")
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
    parts = call.data.split("|")
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
        start_msk = to_moscow_wall(lot['start_time'])
        end_msk = to_moscow_wall(lot['end_time'])
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
                created_str = to_moscow_wall(created_at).strftime('%d.%m.%Y %H:%M') if created_at else '-'
                comment = (lot.get("comment") or "").strip()
                comment_text = f"💬 Комментарий: {safe_html(comment)}\n" if comment and comment != "-" else ""
                price = lot.get("start_price")
                currency = lot.get("currency", '')
                kind_key = str(lot.get("auction_kind") or "standard").strip().lower()
                kind_text = safe_html(auction_kind_label(kind_key))
                accepted_label = safe_html(
                    currency_choices_label(
                        lot.get("accepted_currencies"),
                        fallback=currency,
                        custom_terms=lot.get("custom_offer_terms"),
                    )
                )
                if kind_key == AuctionKind.REVERSE.value:
                    price_text = (
                        f"💱 Валюта ставок: {accepted_label}\n"
                        "📉 Побеждает минимальная ставка\n"
                    )
                elif kind_key == AuctionKind.FREE.value:
                    price_text = f"💱 Принимаются предложения: {accepted_label}\n"
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


#
def split_message_by_blocks(blocks, chunk_size=4096):
    chunks = []
    current = ""
    for block in blocks:
        if len(current) + len(block) > chunk_size:
            chunks.append(current)
            current = ""
        current += block
    if current:
        chunks.append(current)
    return chunks


def safe_html(text):
    return html.escape(str(text)) if text else ""


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


def _pretty(v) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"


async def _log_pending_field_change(
        bot,
        *,
        admin_user: types.User,
        auction_id: int,
        field_title: str,
        old_value,
        new_value,
        action_type: str,
        lot_override: dict | None = None,
) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners_text = await get_lot_owners_text(int(auction_id))

    merged_lot = dict(lot or {})
    if lot_override:
        merged_lot.update(lot_override)

    log_text = format_admin_action_log(
        action="edit_lot",
        admin={"id": admin_user.id, "username": admin_user.username or admin_user.full_name},
        lot=merged_lot,
        owners_text=owners_text,
    )
    log_text += (
        "\n\n🧩 <b>Изменение в модерации (редактор заявки)</b>"
        f"\n✏️ <b>Поле:</b> {tg_clean(field_title)}"
        f"\n🔁 <b>Было:</b> {tg_clean(_pretty(old_value))}"
        f"\n✅ <b>Стало:</b> {tg_clean(_pretty(new_value))}"
    )
    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=int(auction_id),
        details=f"{field_title}: {_pretty(old_value)} -> {_pretty(new_value)}",
    )


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

    await update_lot_field(auction_id, "craft_uid_possible", val)

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

    await update_lot_field(auction_id, "comment", new_comment)

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


@router.callback_query(ApproveLotFSM.editing_pending_lot, F.data.startswith("set_lot_photo|"))
@admin_only
async def set_lot_photo(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await state.update_data(auction_id=auction_id)

    await call.message.answer("Пришли фото/видео/гиф для лота (заменит текущее медиа).")
    await state.set_state(ApproveLotFSM.uploading_image)
    await call.answer()


@router.message(ApproveLotFSM.uploading_image, F.photo | F.video | F.animation | F.document)
@admin_only
async def handle_uploaded_lot_media(message: types.Message, state: FSMContext):
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
        await message.answer("Пришли фото/видео (или нажми Назад).")
        return

    await update_lot_field(auction_id, "image_id", media_id)

    await _log_pending_change(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        action_type="edit_pending_media",
        field_title="Медиа (фото/видео)",
        old_value=old_media,
        new_value=media_id,
    )

    await message.answer("✅ Медиа сохранено.")
    await _send_pending_lot_card(message, message.bot, auction_id)
    await state.clear()


@router.message(ApproveLotFSM.uploading_image)
@admin_only
async def handle_uploaded_lot_media_wrong(message: types.Message, state: FSMContext):
    await message.answer("Пришли фото или видео, или нажми «Назад».")


@router.callback_query(F.data.startswith("pending_set_kind|"))
@admin_only
async def pending_set_kind(call: types.CallbackQuery, state: FSMContext):
    _, kind, auction_id_raw = (call.data or "").split("|", 2)
    auction_id = int(auction_id_raw)

    old_lot = await get_lot_by_id(auction_id)
    old_kind = (old_lot or {}).get("auction_kind")

    await update_lot_field(auction_id, "auction_kind", kind)

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

    await update_lot_field(auction_id, "start_price", new_price)

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
async def set_lot_photo(call: types.CallbackQuery, state: FSMContext):
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
    await update_lot_field(auction_id, "image_id", media_id)
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

    await update_lot_field(auction_id, "currency", new_currency)
    await update_lot_field(auction_id, "accepted_currencies", accepted)
    await update_lot_field(auction_id, "custom_offer_terms", None)

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


def owners_to_compact_text(owners) -> str:
    import json
    if owners is None:
        return "—"
    if isinstance(owners, str):
        try:
            owners = json.loads(owners)
        except Exception:
            owners = []
    if not owners:
        return "—"
    parts = []
    for o in owners:
        uid = o.get("user_id")
        uname = (o.get("username") or "").strip()
        parts.append(f"@{uname}" if uname else (f"id:{uid}" if uid else "—"))
    return ", ".join([p for p in parts if p and p != "—"]) or "—"


log = logging.getLogger("auction_bot")
MSK = tz.gettz("Europe/Moscow")


def _to_msk(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


@router.message(Command("lux_wait"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_lux_wait(message: types.Message):
    sql = """
          SELECT a.auction_id,
                 a.card_name,
                 a.hero_name,
                 a.start_time,
                 u.user_id,
                 u.username,
                 u.full_name
          FROM public.auctions a
                   JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                   JOIN public.users u ON u.user_id = ao.user_id
          WHERE u.is_luxury = TRUE
            AND a.status = 'scheduled'
            AND a.start_time > ((CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') + INTERVAL '3 days')
          ORDER BY a.start_time
          LIMIT 400 \
          """
    rows = await fetch(sql)
    logging.getLogger("auction_bot").info("/lux_wait rows=%s", len(rows or []))
    if not rows:
        await message.answer("Нет назначенных лотов у Лакшери с ожиданием больше 3 дней.")
        return
    MSK = tz.gettz("Europe/Moscow")
    now = datetime.now(MSK)

    def _to_msk(dt):
        return (dt.replace(tzinfo=MSK) if dt.tzinfo is None else dt.astimezone(MSK))

    out = ["<b>Назначенные лоты у Лакшери (> 3 дней ожидания):</b>"]
    for r in rows[:60]:
        st = _to_msk(r["start_time"])
        diff = st - now
        days, hours = diff.days, diff.seconds // 3600
        owner = ("@" + r["username"]) if r.get("username") else (r.get("full_name") or str(r["user_id"]))
        title = r.get("card_name") or "-"
        if r.get("hero_name"):
            title += f" ({r['hero_name']})"
        out.append(
            f"🃏 <b>{_html.escape(title)}</b>\n"
            f"👑 Владелец: {_html.escape(owner)}\n"
            f"⏰ {st.strftime('%d.%m.%Y %H:%M')} МСК • через {days} д {hours} ч"
        )
    await message.answer("\n\n".join(out), parse_mode="HTML")


@router.message(Command("lux_wait_dbg"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_lux_wait_dbg(message: types.Message):
    meta = await fetchrow("""
        SELECT current_database() AS db,
               current_user AS usr,
               inet_server_addr()::text AS host,
               inet_server_port() AS port,
               current_setting('TimeZone') AS tz
    """)
    cnt = await fetchrow("""
                         SELECT count(*) AS c
                         FROM public.auctions a
                                  JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                                  JOIN public.users u ON u.user_id = ao.user_id
                         WHERE u.is_luxury = TRUE
                           AND a.status = 'scheduled'
                           AND a.start_time > (timezone('Europe/Moscow', now()) + interval '3 days')
                         """)
    await message.answer(
        "<b>DB-диагностика</b>\n"
        f"База: <code>{meta['db']}</code>\n"
        f"Роль: <code>{meta['usr']}</code>\n"
        f"Хост: <code>{meta['host']}:{meta['port']}</code>\n"
        f"server_timezone: <code>{meta['tz']}</code>\n\n"
        f"Совпадений по фильтру: <b>{cnt['c']}</b>",
        parse_mode="HTML"
    )


@router.message(Command("multi_auctions"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_multi_auctions(message: types.Message):
    user_id = message.from_user.id
    try:
        admin = await is_admin(user_id)
    except Exception:
        admin = False
    if not admin:
        try:
            if await is_luxury_user(user_id):
                await message.answer("Команда только для обычных пользователей. Лакшери — мимо. 👋")
                return
        except Exception:
            pass
    now_msk_naive = datetime.now(MSK).replace(tzinfo=None)
    rows = await fetch(
        """
        WITH future AS (SELECT a.auction_id,
                               a.card_name,
                               a.hero_name,
                               a.start_time,
                               u.user_id,
                               u.username,
                               u.full_name
                        FROM public.auctions a
                                 JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                                 JOIN public.users u ON u.user_id = ao.user_id
                        WHERE a.status = 'scheduled'
                          AND a.start_time > $1),
             owners AS (SELECT user_id, COUNT(*) AS cnt
                        FROM future
                        GROUP BY user_id
                        HAVING COUNT(*) > 1)
        SELECT f.*, o.cnt
        FROM future f
                 JOIN owners o USING (user_id)
        ORDER BY o.cnt DESC, f.start_time
        LIMIT 400
        """,
        now_msk_naive,
    )

    if not rows:
        await message.answer("Сейчас нет владельцев с более чем одной будущей заявкой.")
        return
    by_owner = {}
    for r in rows:
        oid = r["user_id"]
        owner = by_owner.setdefault(oid, {
            "cnt": r["cnt"],
            "username": r["username"],
            "full_name": r["full_name"],
            "items": []
        })
        owner["items"].append(r)
    owners_sorted = sorted(
        by_owner.values(),
        key=lambda x: (-x["cnt"], min(i["start_time"] for i in x["items"]))
    )[:10]
    out = ["<b>Владельцы с > 1 будущей заявкой:</b>"]
    now_msk = datetime.now(MSK)
    for owner in owners_sorted:
        name = ("@" + owner["username"]) if owner.get("username") else (owner.get("full_name") or "безымянный")
        out.append(f"👤 <b>{_html.escape(name)}</b> • заявок: <b>{owner['cnt']}</b>")
        for r in sorted(owner["items"], key=lambda x: x["start_time"])[:5]:
            st = _to_msk(r["start_time"])
            diff = st - now_msk
            days, hours = diff.days, diff.seconds // 3600
            title = r["card_name"] or "-"
            if r.get("hero_name"):
                title += f" ({r['hero_name']})"
            out.append(
                f" • 🃏 <b>{_html.escape(title)}</b>\n"
                f"   ⏰ {st.strftime('%d.%m.%Y %H:%M')} МСК • через {days} д {hours} ч"
            )
        out.append("")
    await message.answer("\n".join(out).strip(), parse_mode="HTML")


@router.message(Command("proof"), F.chat.type == "private")
@admin_only
async def proof_cmd(message: types.Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Используй: /proof <auction_id>\nПример: /proof 4331")
        return
    auction_id = int(arg)
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await message.answer(f"Лот с auction_id={auction_id} не найден.")
        return
    proof_photo_id = lot.get("proof_photo_id")
    if not proof_photo_id:
        await message.answer(MSG_PHOTO_NOT_FOUND)
        return
    kb = build_back_keyboard(auction_id)
    caption = (
        f"{MSG_PHOTO_CONFIRM}\n\n"
        f"🎴 Лот №{auction_id}: <b>{(lot.get('card_name') or '-')}</b>\n"
        f"🧾 proof_photo_id:\n<code>{proof_photo_id}</code>"
    )
    try:
        await message.answer_photo(
            proof_photo_id,
            caption=caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramAPIError as e:
        await message.answer(f"Не смог отправить proof-фото (возможно, file_id протух): {e}")
        return
    try:
        admin_tag = message.from_user.username or message.from_user.full_name
        await send_admin_log(
            message.bot,
            f"📸 <b>Просмотр подтверждения</b>\n"
            f"👮 Админ: @{admin_tag}\n"
            f"🎴 Лот №{auction_id}: {lot.get('card_name')}\n"
            f"🧾 proof_photo_id: <code>{proof_photo_id}</code>"
        )
        await log_audit_action(
            user_id=message.from_user.id,
            action_type="show_proof",
            auction_id=auction_id,
            details=f"Запрошено proof-фото для лота {auction_id}",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("pending_menu|"))
@admin_only
async def pending_menu_router(call: types.CallbackQuery):
    kind = call.data.split("|", 1)[1]
    if kind == "exchange":
        await show_pending_exchange_requests(call.message)
    else:
        await show_pendinglots(call.message)
    await call.answer()


@router.callback_query(F.data.startswith("ex_show_proof|"))
@admin_only
async def ex_show_proof(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    proof = batch.get("proof_photo_id")
    if not proof:
        await call.answer("Фото подтверждения не найдено.", show_alert=True)
        return
    await call.message.answer_photo(
        proof,
        caption=f"📸 Фото подтверждения для заявки <code>{batch_id}</code>",
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_approve|"))
@admin_only
async def ex_approve(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    await set_exchange_batch_status(batch_id, "approved")
    try:
        await call.bot.send_message(
            int(batch["user_id"]),
            f"✅ Ваша заявка на биржу <code>{batch_id}</code> одобрена.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.message.answer(f"✅ Заявка <code>{batch_id}</code> одобрена.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("ex_reject|"))
@admin_only
async def ex_reject_start(call: types.CallbackQuery, state: FSMContext):
    batch_id = int(call.data.split("|")[1])
    await state.update_data(ex_batch_id=batch_id)
    await call.message.answer(f"Напиши причину отклонения заявки биржи <code>{batch_id}</code>:", parse_mode="HTML")
    await state.set_state(ModActionFSM.waiting_for_reject_exchange_reason)
    await call.answer()





from aiogram.filters import Command
from aiogram import types
from db.db import is_admin
from db.db import is_user_banned  # если у тебя так называется
from db.db import get_user_by_username


@router.message(Command("user_dbg"))
async def cmd_user_dbg(message: types.Message, bot):
    if not await is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /user_dbg @username")
        return

    username = parts[1].strip().lstrip("@")
    u = await get_user_by_username(username)
    if not u:
        await message.answer(f"Не нашёл пользователя @{username} в базе.")
        return

    uid = int(u["user_id"])

    # 1) бан?
    banned = False
    try:
        banned = await is_user_banned(uid)
    except Exception:
        pass

    # 2) подписки/чаты
    def _member_ok(m) -> bool:
        st = getattr(m, "status", None)
        return st in {"member", "administrator", "creator"}

    in_channel = False
    in_discussion = False

    try:
        m1 = await bot.get_chat_member(AUCTION_CHANNEL_ID, uid)
        in_channel = _member_ok(m1)
    except Exception:
        in_channel = False

    try:
        m2 = await bot.get_chat_member(DISCUSSION_CHAT_ID, uid)
        in_discussion = _member_ok(m2)
    except Exception:
        in_discussion = False

    # 3) сводка причин
    reasons = []
    if banned:
        reasons.append("⛔️ В БАНЕ (addlot запрещён)")
    if not in_channel:
        reasons.append("📢 НЕ подписан на канал")
    if not in_discussion:
        reasons.append("💬 НЕ состоит в чате обсуждения")

    lux = "да" if u.get("is_luxury") else "нет"
    trusted = "да" if u.get("is_trusted") else "нет"

    text = (
            f"👤 Проверка пользователя: <b>@{u.get('username') or username}</b>\n"
            f"id: <code>{uid}</code>\n"
            f"лакшери: <b>{lux}</b>\n"
            f"trusted: <b>{trusted}</b>\n\n"
            + (
                "✅ Блокеров для /addlot не вижу." if not reasons else "⚠️ Причины, почему /addlot может не пускать:\n- " + "\n- ".join(
                    reasons))
    )
    await message.answer(text, parse_mode="HTML")


# =========================
# /clik — мини-меню “Жабий помощник”
# =========================

CLIK_ROOT_TEXT = (
    "🐸 <b>Жабий помощник</b>\n\n"
    "Привет! Я бот — твой помощник здесь.\n"
    "Смотри прайс-лист, изучай памятки и смело оформляй заказ.\n"
    "Если что-то не ясно — задай вопрос, я помогу."
)

CLIK_PRICE_TEXT = (
    "💸 <b>Прайс-лист</b>\n"
    "#price_list\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)

CLIK_INSTRUCTION_TEXT = (
    "📌 <b>Памятки / Советы</b>\n"
    "#instruction\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)

CLIK_ASK_TEXT = (
    "❓ <b>Задать вопрос</b>\n"
    "#ask_me\n\n"
    "Напиши вопрос одним сообщением.\n"
    "Я отправлю его админам."
)

CLIK_ORDER_PICK_PAY_TEXT = (
    "🛒 <b>Оформить заказ</b>\n"
    "#make_an_order\n\n"
    "Выбери вариант оплаты:"
)

CLIK_CB = "clik"

# ---- КАРТИНКИ / FILE_ID ----
CLIK_STORY_COVER_PVT = "AgACAgQAAxkBAAELwn9ppGTmTAABJV5-fL_avtoXg1oHo-IAAioOaxtHzCBRAut-JCPq_mIBAAMCAANtAAM6BA"

# Любовные линии ПВТ
CLIK_PVT_LI = [
    ("seb", "Себастьян", "AgACAgQAAxkBAAELwoFppGUelLt-CdGhoOkrqOTM490lGAACFQ5rG-beIFFkrLymWF1WEQEAAwIAA20AAzoE"),
    ("wil", "Вильям", "AgACAgQAAxkBAAELwo1ppGVI6pJ2q5wuNzNXhAka1GakSAACKw5rG0fMIFHW7G34DQqgUAEAAwIAA20AAzoE"),
    ("kri", "Кристина", "AgACAgQAAxkBAAELwo9ppGV02pUgY_2Sy9-ZTO0d4Eo2SAACLA5rG0fMIFFwCeb8kSdCQwEAAwIAA20AAzoE"),
    ("jac", "Джеки", "AgACAgQAAxkBAAELwpZppGWD_2ppkKYejBEyWzx9R-oUFAACLQ5rG0fMIFGP0i2Y6VUTcgEAAwIAA20AAzoE"),
    ("jor", "Хорхе", "AgACAgQAAxkBAAELwrRppGYH8dpcZunjNaNLtz3oH2CyuQACMA5rG0fMIFGcwl05-liqJQEAAwIAA20AAzoE"),
    ("cli", "Клайв", "AgACAgQAAxkBAAELwrhppGYVzWcSl7SYIN-xEDlGsvUeMwACMQ5rG0fMIFGo_o6Kjxw0vgEAAwIAA20AAzoE"),
    ("die", "Диего", "AgACAgQAAxkBAAELwr9ppGYt2gue1lPtrVO_FoQ1PJ-VuQACMg5rG0fMIFE0SHwnZRXkSwEAAwIAA20AAzoE"),
    ("kai", "Кай", "AgACAgQAAxkBAAELwqRppGXQJF3BXJ7b7GFjg9gJU1HwtAACLw5rG0fMIFFzVcOPF9D3BgEAAwIAA20AAzoE"),
    ("lor", "Лоренза", "AgACAgQAAxkBAAELwqJppGWrDdb6ipQYfmnIvChEkF0-1AACLg5rG0fMIFGoyJNIma67CQEAAwIAA20AAzoE"),
]
CLIK_PVT_LI_MAP = {k: {"name": n, "photo": p} for k, n, p in CLIK_PVT_LI}

CLIK_STORIES = [
    "Паруса в Тумане",
    "Рожденная Луной",
    "Моя Голивудская История",
    "Королева за 30 дней",
    "Тени Сентфора",
    "Высокий Прибой",
    "В Ритме Страсти",
    "Я Охочусь на Тебя",
    "Секрет Небес",
    "Легенда Ивы",
    "Дракула. История любви",
    "Путь валькирии",
    "Ярость Титанов",
    "Десять Желаний Софи",
    "Грешный Лондон",
    "По Тонкому Льду",
    "Арканум",
    "Хроники Гладиаторов",
    "Сердце Треспии",
    "Кали: Зов Тьмы",
    "Цветок из Огня Тиамат",
    "Теодора",
    "Сквозь Бурю и Пламя",
    "Идеал",
    "Пси",
    "Покоряя Версаль",
    "Роза Пустыни",
    "Секрет Небес 2",
    "W: Ловчая Времени",
    "Эдемов Сад",
    "Идеал. Том 2",
    "Разбитое Сердце Астреи",
    "Секрет Небес Реквием",
    "Семь Братьев",
    "И Поглотит Нас Морок",
    "Бюро Паралельных Миров. Том 1",
    "Te amo: Том 1. Залив Надежды",
]
CLIK_STORIES_PER_PAGE = 10


class ClikFSM(StatesGroup):
    waiting_question = State()

    order_story = State()
    order_tasks = State()
    order_ach_mode = State()
    order_love_mode = State()          # для НЕ-ПВТ (1/2/3/все)
    order_love_select = State()        # для ПВТ (выбор персонажей)
    waiting_other_text = State()
    order_cups_source = State()

    waiting_order = State()            # логин/пароль


def _clik_mark(v: bool) -> str:
    return "✅" if v else "⬜️"


def _clik_story_key(title: str) -> str:
    return "pvt" if title.strip() == "Паруса в Тумане" else "other"


def _clik_pages(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)


def _kb_clik_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Прайс-лист", callback_data=f"{CLIK_CB}:price"),
            InlineKeyboardButton(text="📌 Памятки", callback_data=f"{CLIK_CB}:instruction"),
        ],
        [
            InlineKeyboardButton(text="🛒 Оформить заказ", callback_data=f"{CLIK_CB}:order"),
            InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"{CLIK_CB}:ask"),
        ],
    ])


def _kb_clik_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")]
    ])


def _kb_clik_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍵 Чашки", callback_data=f"{CLIK_CB}:pay:cups"),
            InlineKeyboardButton(text="💎 Алмазы", callback_data=f"{CLIK_CB}:pay:diamonds"),
            InlineKeyboardButton(text="₽ Рубли", callback_data=f"{CLIK_CB}:pay:rub"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")],
    ])


def _kb_clik_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")]
    ])


def _kb_clik_stories(page: int) -> InlineKeyboardMarkup:
    total = len(CLIK_STORIES)
    pages = _clik_pages(total, CLIK_STORIES_PER_PAGE)
    page = max(0, min(int(page), pages - 1))

    start = page * CLIK_STORIES_PER_PAGE
    chunk = CLIK_STORIES[start:start + CLIK_STORIES_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for i, title in enumerate(chunk, start=start):
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CLIK_CB}:s:pick:{i}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CLIK_CB}:s:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"{CLIK_CB}:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CLIK_CB}:s:page:{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="⬅️ Назад к оплате", callback_data=f"{CLIK_CB}:order")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clik_task_flags(data: dict) -> dict:
    return {
        "play": bool(data.get("clik_t_play")),
        "ach": bool(data.get("clik_t_ach")),
        "love": bool(data.get("clik_t_love")),
        "wardrobe": bool(data.get("clik_t_wardrobe")),
        "other": bool(data.get("clik_t_other")),
    }


def _clik_order_intro(data: dict, step_text: str) -> str:
    pay = html.escape(str(data.get("clik_pay") or "—"))
    story = html.escape(str(data.get("clik_story") or "—"))
    return (
        "✅ <b>Сообщение для заказа</b>\n\n"
        f"💳 <b>Оплата:</b> {pay}\n"
        f"📚 <b>История:</b> {story}\n\n"
        f"{step_text}"
    )


def _clik_preview_summary(data: dict) -> str:
    flags = _clik_task_flags(data)

    # ачивки (заглушка)
    ach_mode = str(data.get("clik_ach_mode") or "").strip()  # all | story

    # любовные линии
    story_key = str(data.get("clik_story_key") or "other")
    love_mode = str(data.get("clik_love_mode") or "").strip()  # all | 1 | 2 | 3
    love_selected = data.get("clik_love_selected") or []

    other_text = (data.get("clik_other_text") or "").strip()

    lines = ["<b>Что нужно сделать:</b>"]
    any_task = False

    if flags["play"]:
        lines.append("• пройти историю;")
        any_task = True

    if flags["ach"]:
        any_task = True
        if ach_mode == "all":
            lines.append("• собрать ачивки (все) — <i>список позже</i>;")
        elif ach_mode == "story":
            lines.append("• собрать ачивки (по истории) — <i>список позже</i>;")
        else:
            lines.append("• собрать ачивки — <i>режим не выбран</i>;")

    if flags["wardrobe"]:
        lines.append("• собрать гардероб (зеркало);")
        any_task = True

    if flags["love"]:
        any_task = True
        if story_key == "pvt":
            if love_selected:
                names = [CLIK_PVT_LI_MAP.get(k, {}).get("name", k) for k in love_selected]
                lines.append("• любовные линии: " + ", ".join(html.escape(n) for n in names) + ";")
            else:
                lines.append("• любовные линии — <i>не выбраны</i>;")
        else:
            if love_mode == "all":
                lines.append("• достичь 100% по любовным линиям (все);")
            elif love_mode in {"1", "2", "3"}:
                lines.append(f"• достичь 100% по любовным линиям ({love_mode});")
            else:
                lines.append("• достичь 100% по любовным линиям — <i>кол-во не выбрано</i>;")

    if flags["other"]:
        any_task = True
        if other_text:
            lines.append(f"• другое: <code>{html.escape(other_text)}</code>;")
        else:
            lines.append("• другое: <i>описание не задано</i>;")

    if not any_task:
        lines.append("• —")

    # чашки (если выбран pay=cups)
    if str(data.get("clik_pay_key") or "") == "cups":
        src = str(data.get("clik_cups_source") or "").strip()
        src_txt = "—"
        if src == "account":
            src_txt = "есть чашки на аккаунте"
        elif src == "daily":
            src_txt = "проходим на ежедневных"
        lines.append("")
        lines.append(f"🍵 <b>Чашки:</b> {html.escape(src_txt)}")

    return "\n".join(lines)


def _kb_clik_tasks(data: dict) -> InlineKeyboardMarkup:
    flags = _clik_task_flags(data)

    ach_mode = str(data.get("clik_ach_mode") or "").strip()
    ach_suffix = ""
    if flags["ach"]:
        ach_suffix = " (все)" if ach_mode == "all" else (" (по истории)" if ach_mode == "story" else "")

    story_key = str(data.get("clik_story_key") or "other")
    love_suffix = ""
    if flags["love"]:
        if story_key == "pvt":
            selected = data.get("clik_love_selected") or []
            love_suffix = f" ({len(selected)})" if selected else ""
        else:
            lm = str(data.get("clik_love_mode") or "").strip()
            love_suffix = " (все)" if lm == "all" else (f" ({lm})" if lm in {"1", "2", "3"} else "")

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📖 Пройти историю {_clik_mark(flags['play'])}", callback_data=f"{CLIK_CB}:t:toggle:play")],
        [InlineKeyboardButton(text=f"🏆 Ачивки{ach_suffix} {_clik_mark(flags['ach'])}", callback_data=f"{CLIK_CB}:t:ach")],
        [InlineKeyboardButton(text=f"💞 Любовные линии{love_suffix} {_clik_mark(flags['love'])}", callback_data=f"{CLIK_CB}:t:love")],
        [InlineKeyboardButton(text=f"🪞 Гардероб (Зеркало) {_clik_mark(flags['wardrobe'])}", callback_data=f"{CLIK_CB}:t:toggle:wardrobe")],
        [InlineKeyboardButton(text=f"✍️ Другое {_clik_mark(flags['other'])}", callback_data=f"{CLIK_CB}:t:other")],
        [InlineKeyboardButton(text="➡️ Далее", callback_data=f"{CLIK_CB}:t:next")],
        [InlineKeyboardButton(text="⬅️ Назад к историям", callback_data=f"{CLIK_CB}:t:back_stories")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_clik_ach_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все ачивки", callback_data=f"{CLIK_CB}:ach:set:all")],
        [InlineKeyboardButton(text="✅ Только по выбранной истории", callback_data=f"{CLIK_CB}:ach:set:story")],
        [InlineKeyboardButton(text="❌ Выключить ачивки", callback_data=f"{CLIK_CB}:ach:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:ach:back")],
    ])


def _kb_clik_love_mode_generic() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все", callback_data=f"{CLIK_CB}:love:set:all")],
        [
            InlineKeyboardButton(text="1", callback_data=f"{CLIK_CB}:love:set:1"),
            InlineKeyboardButton(text="2", callback_data=f"{CLIK_CB}:love:set:2"),
            InlineKeyboardButton(text="3", callback_data=f"{CLIK_CB}:love:set:3"),
        ],
        [InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:love:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:love:back")],
    ])


def _kb_clik_love_pvt(data: dict) -> InlineKeyboardMarkup:
    selected = set(data.get("clik_love_selected") or [])
    rows: list[list[InlineKeyboardButton]] = []

    # кнопки персонажей (в 2 колонки)
    btns: list[InlineKeyboardButton] = []
    for k, n, _p in CLIK_PVT_LI:
        mark = "✅" if k in selected else "⬜️"
        btns.append(InlineKeyboardButton(text=f"{mark} {n}", callback_data=f"{CLIK_CB}:lpvt:toggle:{k}"))

    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"{CLIK_CB}:lpvt:done")])
    rows.append([InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:lpvt:off")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:lpvt:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_clik_cups_source() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍵 Есть чашки на аккаунте", callback_data=f"{CLIK_CB}:cups:account")],
        [InlineKeyboardButton(text="📅 Проходим на ежедневных", callback_data=f"{CLIK_CB}:cups:daily")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:cups:back")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])


def _kb_clik_final_back(data: dict) -> InlineKeyboardMarkup:
    if str(data.get("clik_pay_key") or "") == "cups":
        back_cb = f"{CLIK_CB}:final:back_cups"
    else:
        back_cb = f"{CLIK_CB}:final:back_tasks"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])


async def _ui_edit(call: types.CallbackQuery, *, text: str, kb: InlineKeyboardMarkup, photo_id: str | None = None):
    """
    Универсально обновляет UI.
    - Если photo_id указан: стараемся показать фото + caption (с заменой текст->фото при необходимости).
    - Если photo_id не указан: показываем текст (с заменой фото->текст при необходимости).
    """
    msg = call.message
    if not msg:
        return

    chat_id = msg.chat.id
    try:
        if photo_id:
            if msg.photo:
                media = types.InputMediaPhoto(media=photo_id, caption=text, parse_mode="HTML")
                await msg.edit_media(media=media, reply_markup=kb)
            else:
                new_msg = await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
        else:
            if msg.photo:
                new_msg = await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
            else:
                await msg.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest:
        # Фоллбек: просто отправляем новое
        if photo_id:
            await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def _user_tag(u: types.User) -> str:
    uname = (u.username or "").strip()
    if uname:
        esc = html.escape(uname.lstrip("@"))
        return f'<a href="https://t.me/{esc}">@{esc}</a>'
    return f'<a href="tg://user?id={u.id}">id{u.id}</a>'


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
    parts = (call.data or "").split(":")
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
        page = int((call.data or "").split(":")[-1])
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
        idx = int((call.data or "").split(":")[-1])
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
    key = (call.data or "").split(":")[-1].strip()

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
    mode = (call.data or "").split(":")[-1].strip()  # all | story
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
    mode = (call.data or "").split(":")[-1].strip()  # all | 1 | 2 | 3
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
    key = (call.data or "").split(":")[-1].strip()
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
    tail = (call.data or "").split(":")[-1].strip()
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
