from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.core.time import to_moscow
from bot.handlers.admin.helper.admin_constants import CURRENCY_EMOJI
from bot.handlers.admin.helper.new.utils import auction_kind_label
from bot.domain.auctions import currency_choices_label

logger = logging.getLogger(__name__)

_STATE_PREFIX = "schedule_card_origin_"


def build_schedule_lot_keyboard(
    auction_id: int,
    *,
    delete_callback_prefix: str = "delete_lot",
    delete_label: str = "🗑️ Удалить",
) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_schedule_lot|{int(auction_id)}",
                ),
                types.InlineKeyboardButton(
                    text=delete_label,
                    callback_data=f"{delete_callback_prefix}|{int(auction_id)}",
                ),
            ]
        ]
    )


def build_schedule_lot_caption(lot: Mapping[str, Any], owners_text: str | None) -> str:
    auction_id = int(lot["auction_id"])
    currency_raw = str(lot.get("currency") or "")
    currency_fancy = CURRENCY_EMOJI.get(currency_raw.lower(), currency_raw)
    hero_name = lot.get("hero_name") or "-"
    deck_id = lot.get("deck_id") if lot.get("deck_id") is not None else "—"
    kind_key = str(lot.get("auction_kind") or "standard").strip().lower()
    kind_text = auction_kind_label(kind_key)
    accepted_text = currency_choices_label(
        lot.get("accepted_currencies"),
        fallback=currency_raw,
        custom_terms=lot.get("custom_offer_terms"),
    )

    start_time = to_moscow(lot["start_time"])
    end_time = to_moscow(lot["end_time"])
    created_at = lot.get("created_at")
    created_str = to_moscow(created_at).strftime("%d.%m.%Y %H:%M") if created_at else "-"

    if kind_key == "reverse":
        price_line = (
            f"💱 <b>{accepted_text}</b>\n"
            "📉 Побеждает минимальная ставка\n"
        )
    elif kind_key == "free":
        price_line = f"💱 Предложения: <b>{accepted_text}</b>\n"
    else:
        price_line = f"💵 <b>{lot['start_price']} {currency_fancy}</b>\n"

    return (
        f"🎴 <b>{lot['card_name']}</b>\n"
        f"🔎 Auction ID: <b>{auction_id}</b>\n"
        f"👤 Герой: <b>{hero_name}</b>\n"
        f"Колода: <b>{deck_id}</b>\n"
        f"⚙️ Тип: <b>{kind_text}</b>\n"
        f"⏰ <b>{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}</b>\n"
        f"{price_line}"
        f"💬 {lot.get('comment', '-') or '-'}\n"
        f"👑 Владелец(ы): {owners_text or '-'}\n"
        f"🕑 Дата заявки: {created_str}\n"
    )


def _looks_like_schedule_card(message: Message, auction_id: int) -> bool:
    body = message.caption or message.text or ""
    return "Auction ID:" in body and str(int(auction_id)) in body


async def remember_schedule_card_origin(
    state: FSMContext,
    message: Message,
    auction_id: int,
    *,
    delete_callback_prefix: str,
    delete_label: str,
) -> bool:
    """Remember the lot card that opened the edit flow.

    Subsequent callbacks are sent from month/day/confirmation messages. Those
    must not replace the original card reference, otherwise the stale card can
    never be refreshed after a successful move.
    """

    if not _looks_like_schedule_card(message, auction_id):
        return False

    await state.update_data(
        **{
            f"{_STATE_PREFIX}auction_id": int(auction_id),
            f"{_STATE_PREFIX}chat_id": int(message.chat.id),
            f"{_STATE_PREFIX}message_id": int(message.message_id),
            f"{_STATE_PREFIX}has_caption": message.caption is not None,
            f"{_STATE_PREFIX}delete_callback_prefix": delete_callback_prefix,
            f"{_STATE_PREFIX}delete_label": delete_label,
        }
    )
    return True


async def refresh_schedule_card_origin(
    bot: Bot,
    state: FSMContext,
    auction_id: int,
    *,
    lot: Mapping[str, Any],
    owners_text: str | None,
) -> bool | None:
    """Refresh the old admin card after the database move is committed.

    Returns ``True`` when refreshed, ``False`` when Telegram rejected the edit,
    and ``None`` when the flow did not originate from a remembered lot card.
    """

    data = await state.get_data()
    remembered_auction_id = data.get(f"{_STATE_PREFIX}auction_id")
    if remembered_auction_id is None or int(remembered_auction_id) != int(auction_id):
        return None

    chat_id = data.get(f"{_STATE_PREFIX}chat_id")
    message_id = data.get(f"{_STATE_PREFIX}message_id")
    if chat_id is None or message_id is None:
        return None

    caption = build_schedule_lot_caption(lot, owners_text)
    keyboard = build_schedule_lot_keyboard(
        int(auction_id),
        delete_callback_prefix=str(
            data.get(f"{_STATE_PREFIX}delete_callback_prefix") or "delete_lot"
        ),
        delete_label=str(data.get(f"{_STATE_PREFIX}delete_label") or "🗑️ Удалить"),
    )

    try:
        if bool(data.get(f"{_STATE_PREFIX}has_caption")):
            await bot.edit_message_caption(
                chat_id=int(chat_id),
                message_id=int(message_id),
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        logger.warning(
            "Could not refresh schedule card auction_id=%s chat_id=%s message_id=%s: %s",
            auction_id,
            chat_id,
            message_id,
            exc,
        )
        return False
    except TelegramAPIError:
        logger.exception(
            "Could not refresh schedule card auction_id=%s chat_id=%s message_id=%s",
            auction_id,
            chat_id,
            message_id,
        )
        return False

    return True
