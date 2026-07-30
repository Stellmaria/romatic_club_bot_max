"""Entry points for administrative FSM forms and compatibility formatting."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Union

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.action_support.transport import _safe_strip
from bot.handlers.admin.helper.new.keyboards import back_keyboard, decks_keyboard, period_keyboard
from bot.presentation.admin import format_owner_html
from db.auctions import get_lot_by_message_id

async def start_preview_schedule(
        message_or_call: Union[Message, CallbackQuery], state: FSMContext
) -> None:
    await state.clear()
    await message_or_call.answer(
        "Выберите месяц для просмотра расписания:",
        reply_markup=period_keyboard(period="month", prefix="preview_schedule"),
    )
    from bot.telegram.states import PreviewScheduleFSM

    await state.set_state(PreviewScheduleFSM.choosing_month)


async def start_edit_schedule(
        message_or_call: Union[Message, CallbackQuery],
        state: FSMContext,
        auction_id: Optional[int] = None,
) -> None:
    from bot.telegram.states import EditScheduleFSM

    await state.clear()
    await state.set_state(EditScheduleFSM.choosing_month)
    reply_markup = period_keyboard(
        period="month", prefix="edit_schedule", auction_id=auction_id
    )

    if isinstance(message_or_call, Message):
        await message_or_call.answer(
            "Выберите месяц для просмотра и редактирования расписания:",
            reply_markup=reply_markup,
        )
    else:
        msg = getattr(message_or_call, "message", None)
        if isinstance(msg, Message):
            await msg.answer(
                "Выберите месяц для просмотра и редактирования расписания:",
                reply_markup=reply_markup,
            )
        await message_or_call.answer()


async def add_deck_fsm_entry(message: Message, state: FSMContext) -> None:
    from db.cards import add_deck
    from bot.telegram.states import AddDeckFSM

    fu = getattr(message, "from_user", None)
    is_owner = isinstance(fu, User) and (fu.id in ADMINS_OWNERS)

    text = _safe_strip(getattr(message, "text", None))
    parts = text.split(maxsplit=1)

    if is_owner and text.startswith("/add_deck") and len(parts) == 2:
        deck_name = parts[1]
        await add_deck(deck_name)
        await message.answer(
            f"Колода <b>{deck_name}</b> успешно добавлена!", parse_mode="HTML"
        )
    elif is_owner:
        await message.answer(
            "Введите название новой колоды:", reply_markup=back_keyboard()
        )
        await state.set_state(AddDeckFSM.waiting_for_deck_name)
    else:
        await message.answer(
            "Введите пароль администратора для добавления колоды:",
            reply_markup=back_keyboard(),
        )
        await state.set_state(AddDeckFSM.waiting_for_admin_password)


async def start_add_card_fsm(message: Message, state: FSMContext) -> None:
    from db.cards import get_all_decks
    from bot.telegram.states import AddCardFSM

    await state.clear()

    fu = getattr(message, "from_user", None)
    if not isinstance(fu, User):
        await message.answer("Не могу определить отправителя команды.")
        return

    if fu.id in ADMINS_OWNERS:
        decks = await get_all_decks()
        await message.answer(
            "Владелец, доступ разрешён без пароля.\nВыбери колоду:",
            reply_markup=decks_keyboard(decks, prefix="admin_deck"),
        )
        await state.set_state(AddCardFSM.waiting_for_deck)
    else:
        await message.answer(
            "Введите пароль администратора для добавления карты:",
            reply_markup=back_keyboard(text="Отмена", callback="addcard_cancel"),
        )
        await state.set_state(AddCardFSM.waiting_for_admin_password)


def owners_to_links_text(owners: Any) -> str:
    if owners is None:
        return "—"
    data: Any = owners
    if isinstance(owners, str):
        try:
            data = json.loads(owners)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = []
    if not isinstance(data, list) or not data:
        return "—"
    return "\n".join(format_owner_html(o) for o in data if isinstance(o, Mapping))


async def get_lot_by_channel_message_id(msg_id: int) -> Optional[dict]:
    try:
        return await get_lot_by_message_id(msg_id)
    except (TypeError, ValueError) as exc:
        print(f"[WARN] get_lot_by_message_id failed: {exc}")
        return None
    except Exception as exc:
        print(f"[WARN] get_lot_by_message_id raised {type(exc).__name__}: {exc}")
        return None


__all__ = (
    'start_preview_schedule',
    'start_edit_schedule',
    'add_deck_fsm_entry',
    'start_add_card_fsm',
    'owners_to_links_text',
    'get_lot_by_channel_message_id',
)

