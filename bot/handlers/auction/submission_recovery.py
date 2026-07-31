"""Owner recovery controls for unpublished auction submissions."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

from db.auctions import cancel_owner_unpublished_lots, release_stale_unpublished_lots


router = Router(name="auction-submission-recovery")


def _cancel_pending_lot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отозвать неопубликованную заявку",
                    callback_data="user_cancel_pending_lots",
                )
            ]
        ]
    )


async def _cancel_pending_for_user(user_id: int) -> list[int]:
    """Release stale publication slots, then cancel owner-visible drafts."""

    await release_stale_unpublished_lots(int(user_id))
    return await cancel_owner_unpublished_lots(int(user_id))


async def _send_result(message: types.Message, cancelled: list[int]) -> None:
    if cancelled:
        ids = ", ".join(str(item) for item in cancelled)
        await message.answer(
            "✅ Неопубликованная заявка отозвана.\n"
            f"Лоты: <code>{ids}</code>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await message.answer(
        "Не нашлось неопубликованных заявок, которые можно безопасно отозвать.\n"
        "Уже опубликованный или будущий запланированный лот удаляется через администратора.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel_pending"), F.chat.type == "private")
async def cancel_pending_lot_command(message: types.Message, state: FSMContext) -> None:
    cancelled = await _cancel_pending_for_user(message.from_user.id)
    await state.clear()
    await _send_result(message, cancelled)


@router.callback_query(F.data == "user_cancel_pending_lots")
async def cancel_pending_lot_callback(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await call.answer()
    cancelled = await _cancel_pending_for_user(call.from_user.id)
    await state.clear()
    if isinstance(call.message, types.Message):
        await _send_result(call.message, cancelled)


__all__ = [
    "router",
    "_cancel_pending_for_user",
    "_cancel_pending_lot_keyboard",
    "cancel_pending_lot_callback",
    "cancel_pending_lot_command",
]
