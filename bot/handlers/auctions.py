"""Compatibility facade for the split auction submission handlers."""

from aiogram import Router

from bot.handlers.auction import guides, luxury_admin, submission, submission_support
from bot.handlers.auction.exchange.common import compute_start_price_limits
from bot.handlers.auction.winner_components.common import admin_tag
from bot.handlers.auction.winner_components.thanks import build_thanks_kb
from bot.handlers.auction.publication import auction_publisher_loop
from bot.domain.auctions import AuctionKind
from bot.legacy_fsm import UserAddLotFSM
from bot.handlers.auction.submission_support import auction_currency_kb
from bot.handlers.auction.submission import addlot_start

router = Router(name=__name__)
router.include_routers(submission.router, guides.router, luxury_admin.router)


async def _ask_for_currency(message, state):
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
# KeyboardButton(text="🧩 Комби (свои варианты)")
# accepted_currencies = ["чашки", "алмазы"]