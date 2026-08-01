import calendar
from datetime import datetime
from typing import Any, List, Optional

from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.handlers.admin.helper.admin_constants import (
    BUTTONS, CALLBACK_SHOW_PROOF, CALLBACK_REJECT_LOT, CALLBACK_CONFIRM_LOT
)
from bot.handlers.admin.services.slots import slot_allowed_for_user


def menu_keyboard(*rows: Any) -> ReplyKeyboardMarkup:
    """Build a reply keyboard without reflowing previously added rows."""

    kb = ReplyKeyboardBuilder()
    for row in rows:
        values = row if isinstance(row, (list, tuple)) else (row,)
        kb.row(*(KeyboardButton(text=str(text)) for text in values))
    return kb.as_markup(resize_keyboard=True)


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def build_exchange_batch_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📸 Фото подтверждения", callback_data=f"ex_show_proof|{batch_id}")
        ],
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ex_approve|{batch_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ex_reject|{batch_id}")
        ],
    ])


def build_lot_keyboard(
        lot: dict | int,
        role: str = "user",
        show_proof: bool = True,
        buttons: Optional[List[Any]] = None,
) -> InlineKeyboardMarkup:
    # защита от "передали int"
    if isinstance(lot, int):
        lot = {"auction_id": int(lot)}

    kb = InlineKeyboardBuilder()
    auction_id = lot.get("auction_id")
    proof_id = lot.get("proof_photo_id")

    if buttons:
        for btn_row in buttons:
            kb.row(*(InlineKeyboardButton(text=btn.text, callback_data=btn.callback_data) for btn in btn_row))

    elif role == "admin":
        status = lot.get("status")
        if status == "pending":
            kb.row(
                InlineKeyboardButton(text=BUTTONS["approve"], callback_data=f"{CALLBACK_CONFIRM_LOT}|{auction_id}"),
                InlineKeyboardButton(text=BUTTONS["reject"], callback_data=f"{CALLBACK_REJECT_LOT}|{auction_id}"),
            )
            kb.row(
                InlineKeyboardButton(text=BUTTONS["edit"], callback_data=f"edit_pending_lot|{auction_id}"),
            )
            if show_proof and proof_id:
                kb.row(
                    InlineKeyboardButton(text="📸 Фото подтверждения",
                                         callback_data=f"{CALLBACK_SHOW_PROOF}|{auction_id}")
                )

    else:
        kb.row(
            InlineKeyboardButton(text=BUTTONS["edit"], callback_data=f"useredit|{auction_id}"),
        )
        kb.row(
            InlineKeyboardButton(text=BUTTONS["delete"], callback_data=f"delete_lot|{auction_id}"),
        )

    return kb.as_markup()


def back_keyboard(text: str = "⬅️ Назад", callback: str = "back") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text=text, callback_data=callback))
    return kb.as_markup()

def inline_back_keyboard(text: str = "⬅️ Назад", callback: str = "givetrusted_cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]])

def build_back_keyboard(auction_id: int) -> InlineKeyboardMarkup:
    from bot.handlers.admin.helper.admin_constants import CALLBACK_BACK_TO_LOT
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(
            text=BUTTONS["back"],
            callback_data=f"{CALLBACK_BACK_TO_LOT}|{auction_id}",
        )
    )
    return kb.as_markup()

def confirm_keyboard(
        yes_text: str = "Подтвердить",
        no_text: str = "Отмена",
        yes_callback: str = "confirm_yes",
        no_callback: str = "confirm_no",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=yes_text, callback_data=yes_callback),
        InlineKeyboardButton(text=no_text, callback_data=no_callback),
    )
    return kb.as_markup()

def period_keyboard(
        period: str,
        prefix: str,
        auction_id: Optional[int] = None,
        base_date: Optional[datetime] = None,
        count: int = 3,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    now = base_date or datetime.now()
    if period == "month":
        for i in range(count):
            month = (now.month + i - 1) % 12 + 1
            year = now.year + ((now.month + i - 1) // 12)
            text = datetime(year, month, 1).strftime("%B %Y")
            cb = f"{prefix}|{auction_id}|{year}-{month:02d}" if auction_id is not None else f"{prefix}|{year}-{month:02d}"
            kb.add(InlineKeyboardButton(text=text, callback_data=cb))
        kb.adjust(1)
    elif period == "day":
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        for day in range(1, days_in_month + 1):
            cb = f"{prefix}|{auction_id}|{now.year}-{now.month:02d}-{day:02d}" if auction_id is not None else f"{prefix}|{now.year}-{now.month:02d}-{day:02d}"
            kb.add(InlineKeyboardButton(text=str(day), callback_data=cb))
        kb.adjust(7)
    return kb.as_markup()

def time_slots_keyboard(
        prefix: str, auction_id: int, slots: List[datetime], is_luxury: bool
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    allowed = [dt for dt in slots if slot_allowed_for_user(dt, is_luxury)]
    for slot in allowed:
        text = slot.strftime("%H:%M")
        kb.add(InlineKeyboardButton(text=text, callback_data=f"{prefix}|{auction_id}|{slot.isoformat()}"))
    kb.adjust(4)
    return kb.as_markup()

def confirm_action_keyboard(
        auction_id: int,
        iso_str: str,
        confirm_text: str = "Да, выбрать это время",
        confirm_callback: str = "confirm_lot",
        back_callback: str = "back",
        back_text: str = "⬅️ Назад",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(
            text=f"{BUTTONS.get('confirm', '✅')} {confirm_text}",
            callback_data=f"{confirm_callback}|{auction_id}|{iso_str}",
        )
    )
    kb.add(InlineKeyboardButton(text=back_text, callback_data=back_callback))
    return kb.as_markup()

def decks_keyboard(decks: List[dict], prefix: str = "admin_deck") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    decks = sorted(decks, key=lambda d: int(d.get("deck_id") or d.get("id") or 0))
    for deck in decks:
        kb.add(
            InlineKeyboardButton(
                text=f"{deck['deck_id']}. {deck['deck_name']}",
                callback_data=f"{prefix}_{deck['deck_id']}"
            )
        )
    kb.adjust(1)
    return kb.as_markup()

def decks_menu_keyboard() -> ReplyKeyboardMarkup:
    return menu_keyboard(
        ["➕ Добавить колоду", "➕ Добавить карту", "🔎 Список карт"],
        ["⬅️ Назад"]
    )


def rarity_keyboard(prefix: str = "rarity") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, value in [("Бронза", "bronze"), ("Серебро", "silver"), ("Золото", "gold"), ("Алмаз", "diamond")]:
        kb.add(InlineKeyboardButton(text=label, callback_data=f"{prefix}|{value}"))
    kb.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back"))
    kb.adjust(1)
    return kb.as_markup()


def build_back_button(back_to: str, auction_id: int, extra: Optional[str] = None) -> list:
    data = f"back_to_{back_to}|{auction_id}"
    if extra:
        data += f"|{extra}"
    return [InlineKeyboardButton(text="⬅️ Назад", callback_data=data)]
