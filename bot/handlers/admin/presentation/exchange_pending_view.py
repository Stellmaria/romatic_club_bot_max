"""Presentation helpers for choosing and browsing pending exchange requests."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.auction.exchange.moderation import (
    format_pending_exchange_batch_card,
    pending_exchange_kb,
)
from bot.services.exchange_media import get_exchange_cover_media
from bot.services.exchange_moderation import ExchangeModerationService
from bot.telegram.media import answer_media_any

_DETAIL_MESSAGE_ID_KEY = "exchange_pending_detail_message_id"
_HEADER_MESSAGE_ID_KEY = "exchange_pending_header_message_id"
_PAGE_KEY = "exchange_pending_page"


def pending_exchange_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📄 По одному",
            callback_data="expend_mode|one",
        ),
        InlineKeyboardButton(
            text="📚 Все сразу",
            callback_data="expend_mode|all",
        ),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admreq_back"))
    return builder.as_markup()


def pending_exchange_navigation_kb(*, page: int, total: int) -> InlineKeyboardMarkup:
    page = max(0, int(page))
    total = max(0, int(total))
    builder = InlineKeyboardBuilder()

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"expend_page|{page - 1}",
            )
        )
    navigation.append(
        InlineKeyboardButton(
            text=f"{min(page + 1, total) if total else 0}/{total}",
            callback_data=f"expend_page|{page}",
        )
    )
    if page + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"expend_page|{page + 1}",
            )
        )
    builder.row(*navigation)
    builder.row(
        InlineKeyboardButton(
            text="📚 Все сразу",
            callback_data="expend_mode|all",
        ),
        InlineKeyboardButton(
            text="↩️ Выбор режима",
            callback_data="admreq|pending|exchange",
        ),
    )
    return builder.as_markup()


def _pending_exchange_header_text(*, page: int, total: int) -> str:
    return (
        "🛒 <b>Заявки на биржу</b>\n\n"
        "Режим: <b>по одному</b>\n"
        f"Заявка: <b>{page + 1}</b> из <b>{total}</b>"
    )


async def _edit_or_answer(
    message: Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> Message:
    try:
        edited = await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return edited if isinstance(edited, Message) else message
    except Exception:
        return await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


async def _edit_header_or_answer(
    message: Message,
    *,
    header_message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> int:
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=header_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return header_message_id
    except Exception:
        header = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return int(header.message_id)


async def clear_pending_exchange_detail(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    detail_message_id = data.get(_DETAIL_MESSAGE_ID_KEY)
    if detail_message_id:
        try:
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=int(detail_message_id),
            )
        except Exception:
            pass
    await state.update_data(
        **{
            _DETAIL_MESSAGE_ID_KEY: None,
            _HEADER_MESSAGE_ID_KEY: None,
            _PAGE_KEY: None,
        }
    )


async def show_pending_exchange_mode_picker(message: Message) -> None:
    moderation = await ExchangeModerationService.create()
    total = await moderation.pending_total()
    await _edit_or_answer(
        message,
        text=(
            "🛒 <b>Заявки на биржу</b>\n\n"
            f"На модерации: <b>{total}</b>\n\n"
            "Как показать заявки?"
        ),
        reply_markup=pending_exchange_mode_kb(),
    )


async def show_pending_exchange_all_header(message: Message) -> None:
    await _edit_or_answer(
        message,
        text=(
            "🛒 <b>Заявки на биржу</b>\n\n"
            "Режим: <b>все сразу</b>.\n"
            "Заявки будут отправлены отдельными сообщениями ниже."
        ),
        reply_markup=pending_exchange_mode_kb(),
    )


async def _send_pending_exchange_detail(
    message: Message,
    batch: dict,
) -> Message:
    batch_id = int(batch.get("batch_id") or 0)
    proof_id = str(batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    items_count = int(batch.get("items_count") or 0)
    status_title = "Лакшери" if bool(batch.get("is_luxury")) else "Обычный"
    text = (
        f"👑 <b>Статус пользователя:</b> {status_title}\n\n"
        + format_pending_exchange_batch_card(batch, items_count=items_count)
    )
    actions = pending_exchange_kb(batch_id, has_proof=has_proof)

    cover_id = None
    try:
        cover_id, _ = await get_exchange_cover_media(batch_id)
    except Exception:
        cover_id = None
    media_id = cover_id or (proof_id if has_proof else None)

    detail: Message | None = None
    if media_id:
        detail = await answer_media_any(
            message,
            str(media_id),
            caption=text,
            reply_markup=actions,
            parse_mode="HTML",
        )
    if detail is None:
        detail = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=actions,
            disable_web_page_preview=True,
        )
    return detail


async def show_pending_exchange_request_one(
    message: Message,
    state: FSMContext,
    *,
    page: int = 0,
) -> None:
    moderation = await ExchangeModerationService.create()
    rows = await moderation.pending_batches(include_luxury=True)

    await clear_pending_exchange_detail(message, state)

    if not rows:
        await _edit_or_answer(
            message,
            text="🛒 <b>Заявки на биржу</b>\n\nНет заявок на модерацию.",
            reply_markup=pending_exchange_mode_kb(),
        )
        return

    total = len(rows)
    page = min(max(0, int(page)), total - 1)
    batch = rows[page]

    header = await _edit_or_answer(
        message,
        text=_pending_exchange_header_text(page=page, total=total),
        reply_markup=pending_exchange_navigation_kb(page=page, total=total),
    )
    detail = await _send_pending_exchange_detail(message, batch)

    await state.update_data(
        **{
            _DETAIL_MESSAGE_ID_KEY: int(detail.message_id),
            _HEADER_MESSAGE_ID_KEY: int(header.message_id),
            _PAGE_KEY: page,
        }
    )


async def continue_pending_exchange_request_one(
    message: Message,
    state: FSMContext,
    *,
    processed_batch_id: int,
) -> None:
    """Show the next pending request after an action in one-by-one mode."""

    data = await state.get_data()
    raw_page = data.get(_PAGE_KEY)
    raw_header_message_id = data.get(_HEADER_MESSAGE_ID_KEY)
    if raw_page is None or raw_header_message_id is None:
        return

    moderation = await ExchangeModerationService.create()
    rows = await moderation.pending_batches(include_luxury=True)

    if any(int(row.get("batch_id") or 0) == int(processed_batch_id) for row in rows):
        return

    if not rows:
        await _edit_header_or_answer(
            message,
            header_message_id=int(raw_header_message_id),
            text=("🛒 <b>Заявки на биржу</b>\n\n" "✅ Все заявки на модерацию обработаны."),
            reply_markup=pending_exchange_mode_kb(),
        )
        await state.update_data(
            **{
                _DETAIL_MESSAGE_ID_KEY: None,
                _HEADER_MESSAGE_ID_KEY: None,
                _PAGE_KEY: None,
            }
        )
        return

    total = len(rows)
    page = min(max(0, int(raw_page)), total - 1)
    header_message_id = await _edit_header_or_answer(
        message,
        header_message_id=int(raw_header_message_id),
        text=_pending_exchange_header_text(page=page, total=total),
        reply_markup=pending_exchange_navigation_kb(page=page, total=total),
    )
    detail = await _send_pending_exchange_detail(message, rows[page])

    await state.update_data(
        **{
            _DETAIL_MESSAGE_ID_KEY: int(detail.message_id),
            _HEADER_MESSAGE_ID_KEY: header_message_id,
            _PAGE_KEY: page,
        }
    )


__all__ = [
    "clear_pending_exchange_detail",
    "continue_pending_exchange_request_one",
    "pending_exchange_mode_kb",
    "pending_exchange_navigation_kb",
    "show_pending_exchange_all_header",
    "show_pending_exchange_mode_picker",
    "show_pending_exchange_request_one",
]
