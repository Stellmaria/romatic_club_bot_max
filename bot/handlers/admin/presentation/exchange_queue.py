"""Presentation of one pending exchange request.

The module owns no router. It is shared by the exchange moderation adapter and
schedule navigation without creating handler-to-handler dependencies.
"""

from aiogram import types
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.exchange_media import get_exchange_cover_media
from bot.services.exchange_moderation import ExchangeModerationQueries
from db.users import is_luxury_user
from bot.telegram.media import media_kind_from_error
from bot.handlers.auction.exchange_moderation import format_pending_exchange_batch_card

EX1_APPROVE = "ex1:approve"
EX1_REJECT = "ex1:reject"
EX1_DELETE = "ex1:delete"
EX1_DEL_YES = "ex1:del_yes"
EX1_DEL_NO = "ex1:del_no"


class ExchangeOneRejectFSM(StatesGroup):
    waiting_for_reason = State()


def build_exchange_one_keyboard(
    batch_id: int,
    *,
    has_proof: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    first_row: list[InlineKeyboardButton] = []
    if has_proof:
        first_row.append(
            InlineKeyboardButton(
                text="📸 Подтверждение",
                callback_data=f"exchange_proof|{batch_id}",
            )
        )
    first_row.append(
        InlineKeyboardButton(
            text="🃏 Состав",
            callback_data=f"exchange_items|{batch_id}",
        )
    )
    builder.row(*first_row)
    builder.row(
        InlineKeyboardButton(
            text="✅ Одобрить",
            callback_data=f"{EX1_APPROVE}|{batch_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"{EX1_REJECT}|{batch_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"{EX1_DELETE}|{batch_id}",
        )
    )
    return builder.as_markup()


def build_exchange_one_delete_confirmation(batch_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить",
        callback_data=f"{EX1_DEL_YES}|{batch_id}",
    )
    builder.button(text="⬅️ Нет", callback_data=f"{EX1_DEL_NO}|{batch_id}")
    builder.adjust(2)
    return builder.as_markup()


async def show_pending_exchange_one(message: types.Message) -> None:
    """Show the oldest pending exchange request as one moderation card."""

    queries = await ExchangeModerationQueries.create()
    total = await queries.pending_total()
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    pending = await queries.pending(limit=1)
    row = pending[0] if pending else None
    if not row:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    batch_id = int(row.get("batch_id") or 0)
    proof_id = str(row.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    items_count = int(row.get("items_count") or 0)

    try:
        luxury = await is_luxury_user(int(row.get("user_id") or 0))
    except Exception:
        luxury = False

    status_line = "👑 <b>Статус пользователя:</b> " + (
        "Лакшери" if luxury else "Обычный"
    )
    text = (
        "🛒 <b>Заявки на биржу</b>\n"
        f"Осталось: <b>{total}</b>\n"
        f"{status_line}\n\n"
        + format_pending_exchange_batch_card(dict(row), items_count=items_count)
    )
    keyboard = build_exchange_one_keyboard(batch_id, has_proof=has_proof)

    cover_id, cover_kind = await get_exchange_cover_media(batch_id)
    media_id = cover_id or (proof_id if has_proof else None)
    kind = cover_kind if cover_id else "photo"

    if not media_id:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    try:
        await _answer_media(message, kind, media_id, text, keyboard)
    except Exception as error:
        fallback_kind = media_kind_from_error(error) or "photo"
        try:
            await _answer_media(
                message,
                fallback_kind,
                media_id,
                text,
                keyboard,
            )
        except Exception:
            await message.answer(
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )


async def _answer_media(
    message: types.Message,
    kind: str,
    media_id: str,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if kind == "video":
        await message.answer_video(
            media_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    elif kind == "animation":
        await message.answer_animation(
            media_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await message.answer_photo(
            media_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


__all__ = (
    "EX1_APPROVE",
    "EX1_DELETE",
    "EX1_DEL_NO",
    "EX1_DEL_YES",
    "EX1_REJECT",
    "ExchangeOneRejectFSM",
    "build_exchange_one_delete_confirmation",
    "build_exchange_one_keyboard",
    "show_pending_exchange_one",
)
