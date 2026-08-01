"""Compatibility facade for split admin card-economy capabilities."""

from aiogram import Router

from bot.handlers.admin.helper.new import (
    card_economy_luxury,
    card_economy_mutation,
    card_economy_shared,
    card_economy_subscriptions,
    card_economy_winner_print,
)

for _module in (
    card_economy_shared,
    card_economy_mutation,
    card_economy_luxury,
    card_economy_subscriptions,
    card_economy_winner_print,
):
    globals().update(
        {
            _name: getattr(_module, _name)
            for _name in dir(_module)
            if callable(getattr(_module, _name))
        }
    )

router = Router(name="admin_card_economy")
router.include_routers(
    card_economy_mutation.router,
    card_economy_luxury.router,
    card_economy_subscriptions.router,
    card_economy_winner_print.router,
)
