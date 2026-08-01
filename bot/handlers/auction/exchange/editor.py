from __future__ import annotations

import html

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.domain.auctions import (
    ExchangeBatchNotFound,
    InvalidExchangeTransition,
    UnsupportedCurrency,
)
from bot.handlers.admin.action_support.compat import send_admin_log
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.exchange_editor import ApprovedExchangeEditorService
from bot.telegram.callback_parser import split_callback_data


router = Router(name="auction_exchange_editor")


class ApprovedExchangeEditFSM(StatesGroup):
    waiting_price = State()
    waiting_comment = State()
    waiting_proof = State()


_MODE_LABELS = {
    "card": "Одна карта",
    "deck_split": "Набор карт",
    "deck": "Колода целиком",
}

_CURRENCY_LABELS = {
    "алмазы": "💎 Алмазы",
    "чашки": "☕️ Чашки",
    "сокровища": "🪙 Сокровища",
}


def _back_to_editor_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к редактору",
                    callback_data=f"ex_edit:{int(batch_id)}",
                )
            ]
        ]
    )


def _continue_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Продолжить редактирование",
                    callback_data=f"ex_edit:{int(batch_id)}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку биржи",
                    callback_data="ex_appr:root",
                )
            ],
        ]
    )


def _mode_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🃏 Одна карта",
        callback_data=f"ex_edit_mode_set:{int(batch_id)}:card",
    )
    kb.button(
        text="🗂 Набор карт",
        callback_data=f"ex_edit_mode_set:{int(batch_id)}:deck_split",
    )
    kb.button(
        text="📚 Колода целиком",
        callback_data=f"ex_edit_mode_set:{int(batch_id)}:deck",
    )
    kb.button(text="⬅️ Назад", callback_data=f"ex_edit:{int(batch_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _currency_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for currency, label in _CURRENCY_LABELS.items():
        kb.button(
            text=label,
            callback_data=f"ex_edit_currency_set:{int(batch_id)}:{currency}",
        )
    kb.button(text="⬅️ Назад", callback_data=f"ex_edit:{int(batch_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _callback_batch_id(data: str | None, prefix: str) -> int:
    raw = str(data or "")
    if not raw.startswith(prefix):
        raise ValueError("Некорректная кнопка редактора.")
    return int(raw[len(prefix):])


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ExchangeBatchNotFound):
        return "Лот биржи не найден."
    if isinstance(exc, InvalidExchangeTransition):
        return (
            "Редактировать можно только принятый лот до начала публикации. "
            f"Текущий статус: {exc.current}."
        )
    if isinstance(exc, UnsupportedCurrency):
        return "Неизвестная валюта."
    return str(exc) or "Не удалось изменить лот."


def _admin_label(user: types.User | None) -> str:
    if user is None:
        return "неизвестный админ"
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


async def _notify_change(
    *,
    bot: types.Bot,
    batch: dict,
    admin_user: types.User | None,
    field_label: str,
    old_value: object,
    new_value: object,
) -> None:
    batch_id = int(batch.get("batch_id") or 0)
    user_id = int(batch.get("user_id") or 0)
    old_text = str(old_value if old_value not in (None, "") else "—")
    new_text = str(new_value if new_value not in (None, "") else "—")

    if user_id:
        try:
            await bot.send_message(
                user_id,
                (
                    "✏️ <b>Изменён ваш принятый лот на бирже</b>\n"
                    f"Batch: <code>{batch_id}</code>\n"
                    f"{html.escape(field_label)}: "
                    f"<b>{html.escape(old_text)}</b> → "
                    f"<b>{html.escape(new_text)}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await send_admin_log(
        bot,
        (
            "✏️ <b>Биржа: изменение принятого лота</b>\n"
            f"Админ: <b>{html.escape(_admin_label(admin_user))}</b>\n"
            f"Batch: <code>{batch_id}</code>\n"
            f"{html.escape(field_label)}: "
            f"<b>{html.escape(old_text)}</b> → "
            f"<b>{html.escape(new_text)}</b>"
        ),
    )


async def _ensure_editable(
    service: ApprovedExchangeEditorService,
    batch_id: int,
) -> dict:
    batch = await service.get(int(batch_id))
    status = str(batch.get("status") or "")
    if status != "approved" or batch.get("deleted_at") is not None:
        raise InvalidExchangeTransition(current=status or "unknown", target="approved_edit")
    return batch


async def _finish_callback(
    call: types.CallbackQuery,
    *,
    batch_id: int,
    text: str,
) -> None:
    await call.message.answer(text, reply_markup=_continue_keyboard(batch_id))
    await call.answer()


async def _finish_message(
    message: types.Message,
    *,
    batch_id: int,
    text: str,
) -> None:
    await message.answer(text, reply_markup=_continue_keyboard(batch_id))


@router.callback_query(F.data.startswith("ex_edit_mode:"))
@admin_only
async def exchange_edit_mode(call: types.CallbackQuery) -> None:
    try:
        batch_id = _callback_batch_id(call.data, "ex_edit_mode:")
        service = await ApprovedExchangeEditorService.create()
        await _ensure_editable(service, batch_id)
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await call.message.answer(
        "Выберите новый режим лота:",
        reply_markup=_mode_keyboard(batch_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_edit_mode_set:"))
@admin_only
async def exchange_edit_mode_set(call: types.CallbackQuery) -> None:
    try:
        _, batch_raw, mode = split_callback_data(str(call.data or ""), ":", 2)
        batch_id = int(batch_raw)
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_mode(batch_id, mode)
        old_label = _MODE_LABELS.get(str(before.get("mode") or ""), before.get("mode") or "—")
        new_label = _MODE_LABELS.get(str(updated.get("mode") or ""), updated.get("mode") or "—")
        await _notify_change(
            bot=call.bot,
            batch=updated,
            admin_user=call.from_user,
            field_label="Режим",
            old_value=old_label,
            new_value=new_label,
        )
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await _finish_callback(
        call,
        batch_id=batch_id,
        text=f"✅ Режим изменён: <b>{html.escape(str(new_label))}</b>.",
    )


@router.callback_query(F.data.startswith("ex_edit_price:"))
@admin_only
async def exchange_edit_price(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        batch_id = _callback_batch_id(call.data, "ex_edit_price:")
        service = await ApprovedExchangeEditorService.create()
        batch = await _ensure_editable(service, batch_id)
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await state.set_state(ApprovedExchangeEditFSM.waiting_price)
    await state.update_data(exchange_edit_batch_id=batch_id)
    await call.message.answer(
        f"Текущая цена: <b>{int(batch.get('price') or 0)}</b>.\n"
        "Введите новую цену целым числом, не меньше нуля:",
        parse_mode="HTML",
        reply_markup=_back_to_editor_keyboard(batch_id),
    )
    await call.answer()


@router.message(ApprovedExchangeEditFSM.waiting_price, F.text)
@admin_only
async def exchange_edit_price_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    batch_id = int(data.get("exchange_edit_batch_id") or 0)
    try:
        price = int(str(message.text or "").strip())
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_price(batch_id, price)
        await _notify_change(
            bot=message.bot,
            batch=updated,
            admin_user=message.from_user,
            field_label="Цена",
            old_value=int(before.get("price") or 0),
            new_value=int(updated.get("price") or 0),
        )
    except (TypeError, ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await message.answer(
            _error_text(exc),
            reply_markup=_back_to_editor_keyboard(batch_id),
        )
        return

    await state.clear()
    await _finish_message(
        message,
        batch_id=batch_id,
        text=f"✅ Цена изменена на <b>{int(updated.get('price') or 0)}</b>.",
    )


@router.callback_query(F.data.startswith("ex_edit_currency:"))
@admin_only
async def exchange_edit_currency(call: types.CallbackQuery) -> None:
    try:
        batch_id = _callback_batch_id(call.data, "ex_edit_currency:")
        service = await ApprovedExchangeEditorService.create()
        await _ensure_editable(service, batch_id)
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await call.message.answer(
        "Выберите новую валюту:",
        reply_markup=_currency_keyboard(batch_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_edit_currency_set:"))
@admin_only
async def exchange_edit_currency_set(call: types.CallbackQuery) -> None:
    try:
        _, batch_raw, currency = split_callback_data(str(call.data or ""), ":", 2)
        batch_id = int(batch_raw)
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_currency(batch_id, currency)
        old_currency = str(before.get("currency") or "—")
        new_currency = str(updated.get("currency") or "—")
        await _notify_change(
            bot=call.bot,
            batch=updated,
            admin_user=call.from_user,
            field_label="Валюта",
            old_value=_CURRENCY_LABELS.get(old_currency, old_currency),
            new_value=_CURRENCY_LABELS.get(new_currency, new_currency),
        )
    except (
        ValueError,
        UnsupportedCurrency,
        ExchangeBatchNotFound,
        InvalidExchangeTransition,
    ) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await _finish_callback(
        call,
        batch_id=batch_id,
        text=(
            "✅ Валюта изменена: "
            f"<b>{html.escape(_CURRENCY_LABELS.get(new_currency, new_currency))}</b>."
        ),
    )


@router.callback_query(F.data.startswith("ex_edit_comment:"))
@admin_only
async def exchange_edit_comment(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        batch_id = _callback_batch_id(call.data, "ex_edit_comment:")
        service = await ApprovedExchangeEditorService.create()
        batch = await _ensure_editable(service, batch_id)
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await state.set_state(ApprovedExchangeEditFSM.waiting_comment)
    await state.update_data(exchange_edit_batch_id=batch_id)
    current = str(batch.get("comment") or "—")
    await call.message.answer(
        f"Текущий комментарий: <b>{html.escape(current)}</b>\n\n"
        "Отправьте новый комментарий. Чтобы очистить его, отправьте <code>-</code>.",
        parse_mode="HTML",
        reply_markup=_back_to_editor_keyboard(batch_id),
    )
    await call.answer()


@router.message(ApprovedExchangeEditFSM.waiting_comment, F.text)
@admin_only
async def exchange_edit_comment_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    batch_id = int(data.get("exchange_edit_batch_id") or 0)
    new_comment = "" if str(message.text or "").strip() == "-" else str(message.text or "").strip()
    try:
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_comment(batch_id, new_comment)
        await _notify_change(
            bot=message.bot,
            batch=updated,
            admin_user=message.from_user,
            field_label="Комментарий",
            old_value=before.get("comment") or "—",
            new_value=updated.get("comment") or "—",
        )
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await message.answer(
            _error_text(exc),
            reply_markup=_back_to_editor_keyboard(batch_id),
        )
        return

    await state.clear()
    await _finish_message(
        message,
        batch_id=batch_id,
        text="✅ Комментарий обновлён.",
    )


@router.callback_query(F.data.startswith("ex_edit_proof:"))
@admin_only
async def exchange_edit_proof(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        batch_id = _callback_batch_id(call.data, "ex_edit_proof:")
        service = await ApprovedExchangeEditorService.create()
        await _ensure_editable(service, batch_id)
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await call.answer(_error_text(exc), show_alert=True)
        return

    await state.set_state(ApprovedExchangeEditFSM.waiting_proof)
    await state.update_data(exchange_edit_batch_id=batch_id)
    await call.message.answer(
        "Отправьте новое фото подтверждения. Чтобы удалить пруф, отправьте <code>-</code>.",
        parse_mode="HTML",
        reply_markup=_back_to_editor_keyboard(batch_id),
    )
    await call.answer()


@router.message(ApprovedExchangeEditFSM.waiting_proof, F.photo)
@admin_only
async def exchange_edit_proof_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    batch_id = int(data.get("exchange_edit_batch_id") or 0)
    proof_id = message.photo[-1].file_id
    try:
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_proof(batch_id, proof_id)
        old_has_proof = bool(
            str(before.get("proof_photo_id") or "").strip()
            and str(before.get("proof_photo_id") or "").upper() != "NO_PROOF"
        )
        await _notify_change(
            bot=message.bot,
            batch=updated,
            admin_user=message.from_user,
            field_label="Пруф",
            old_value="есть" if old_has_proof else "нет",
            new_value="есть",
        )
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await message.answer(
            _error_text(exc),
            reply_markup=_back_to_editor_keyboard(batch_id),
        )
        return

    await state.clear()
    await _finish_message(
        message,
        batch_id=batch_id,
        text="✅ Фото подтверждения обновлено.",
    )


@router.message(ApprovedExchangeEditFSM.waiting_proof, F.text)
@admin_only
async def exchange_edit_proof_text(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    batch_id = int(data.get("exchange_edit_batch_id") or 0)
    text = str(message.text or "").strip().lower()
    if text not in {"-", "нет", "удалить"}:
        await message.answer(
            "Пришлите фото либо отправьте <code>-</code>, чтобы удалить пруф.",
            parse_mode="HTML",
            reply_markup=_back_to_editor_keyboard(batch_id),
        )
        return

    try:
        service = await ApprovedExchangeEditorService.create()
        before = await _ensure_editable(service, batch_id)
        updated = await service.set_proof(batch_id, None)
        old_has_proof = bool(
            str(before.get("proof_photo_id") or "").strip()
            and str(before.get("proof_photo_id") or "").upper() != "NO_PROOF"
        )
        await _notify_change(
            bot=message.bot,
            batch=updated,
            admin_user=message.from_user,
            field_label="Пруф",
            old_value="есть" if old_has_proof else "нет",
            new_value="нет",
        )
    except (ValueError, ExchangeBatchNotFound, InvalidExchangeTransition) as exc:
        await message.answer(
            _error_text(exc),
            reply_markup=_back_to_editor_keyboard(batch_id),
        )
        return

    await state.clear()
    await _finish_message(
        message,
        batch_id=batch_id,
        text="✅ Фото подтверждения удалено.",
    )


__all__ = ["router", "ApprovedExchangeEditFSM"]
