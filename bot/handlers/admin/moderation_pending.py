"""Pending lot field editing.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

import html
from bot.handlers.admin.helper.admin_constants import (
    ADMIN_MESSAGES,
    CANCEL_TEXTS,
)
from typing import Any
from bot.telegram.states import ApproveLotFSM
from bot.services.auction_workflows import AuctionModerationService
from aiogram import (
    F,
    Router,
    types,
)
from aiogram.fsm.context import FSMContext
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_thanks import (
    admin_tag,
    build_thanks_kb,
)
from bot.handlers.admin.helper.new.keyboards import (
    back_keyboard,
    build_lot_keyboard,
    menu_keyboard,
)
from bot.handlers.admin.helper.new.formatting import (
    format_admin_action_log,
    format_pending_lot,
    get_lot_owners_with_levels,
)
from db.auctions import (
    get_lot_by_id,
    get_lot_owners,
)
from bot.services.admin_owners import get_lot_owners_text
from db.admin import log_audit_action
from bot.services.admin_auction_notifications import notify_owners_lot_changed
from bot.handlers.admin.presentation.media import extract_media_file_id
from bot.handlers.admin.action_support.transport import process_universal_cancel_text
from bot.handlers.admin.action_support.exchange import (
    safe_answer_photo,
    tg_clean,
)
from bot.services.admin_logging import send_admin_log
from bot.handlers.admin.logs_admin import short_media_id


from bot.telegram.callback_parser import split_callback_data

async def _update_auction_field(auction_id: int, field: str, value: Any) -> dict[str, Any]:
    service = await AuctionModerationService.create()
    return await service.update_field(auction_id, field=field, value=value)

def _pretty(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"

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

router = Router(name=__name__)


@router.callback_query(F.data.startswith("edit_pending_lot|"))
@admin_only
async def edit_pending_lot_menu(call: types.CallbackQuery, state: FSMContext):
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    _, raw, auction_id_raw = split_callback_data(call.data or "", "|", 2)
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    _, kind, auction_id_raw = split_callback_data(call.data or "", "|", 2)
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
    media_id = extract_media_file_id(message)
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
    auction_id = int(split_callback_data(call.data, "|")[1])
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
