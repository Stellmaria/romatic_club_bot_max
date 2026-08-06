"""Compatibility facade for the split auction submission handlers."""

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from bot.core.legacy_config import legacy_config
from bot.domain.auctions import AuctionKind
from bot.handlers.auction import (
    guides,
    luxury_admin,
    preorder,
    preorder_submission,
    submission,
    submission_support,
)
from bot.handlers.auction.guides import guides_kb
from bot.handlers.auction.luxury_admin import cmd_remove_luxury
from bot.handlers.auction.publication import auction_publisher_loop
from bot.handlers.auction.submission import addlot_start
from bot.handlers.auction.submission_support import (
    auction_currency_kb,
    compute_start_price_limits,
)
from bot.handlers.auction.winner_components.common import admin_tag
from bot.handlers.auction.winner_components.thanks import build_thanks_kb
from bot.legacy_fsm import UserAddLotFSM

__all__ = [
    "addlot_start",
    "admin_tag",
    "auction_publisher_loop",
    "build_thanks_kb",
    "cmd_remove_luxury",
    "compute_start_price_limits",
    "guides_kb",
    "router",
    "submission_support",
]

router = Router(name=__name__)


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_auction_kind),
    F.data == f"auk_kind:{AuctionKind.PREORDER.value}",
)
async def start_preorder_auction_kind(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    """Route the preorder auction kind directly into the future-deck cart."""

    message = call.message
    if not isinstance(message, types.Message):
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    preorder_kind = AuctionKind.PREORDER
    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)
    is_admin = call.from_user.id in legacy_config.ADMINS
    if not is_admin and luxury_level < preorder_kind.minimum_luxury_level:
        await call.answer(
            f"Этот тип доступен с уровня Лакшери {preorder_kind.minimum_luxury_level}.",  # noqa: RUF001
            show_alert=True,
        )
        return

    await state.update_data(auction_kind=preorder_kind.value)
    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    await preorder._show_future_decks(message, state)
    await call.answer()


router.include_routers(
    preorder_submission.router,
    preorder.router,
    submission.router,
    guides.router,
    luxury_admin.router,
)


async def _ask_for_currency(message: types.Message, state: FSMContext) -> None:
    """Compatibility copy for legacy imports and focused transition checks."""

    data = await state.get_data()
    kind = str(data.get("auction_kind") or "standard").strip().lower()
    if kind == AuctionKind.FREE.value:
        prompt = "Выберите, в какой валюте принимать предложения:"
    elif kind == AuctionKind.REVERSE.value:
        prompt = "Выберите валюту обратного аукциона:"
    else:
        prompt = "Выберите валюту:"
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await message.answer(prompt, reply_markup=auction_currency_kb(kind))


# Free-currency UI choices are owned by ``submission_support``:
# KeyboardButton(text="🍵 Чай")
# KeyboardButton(text="💎 Алмазы")
# KeyboardButton(text="🍵 + 💎 Чай или/и алмазы")
# KeyboardButton(text="🧩 Комбо (свои варианты)")
# accepted_currencies = ["чашки", "алмазы"]
