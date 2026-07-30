"""Injected cross-feature entrypoints used by the private-user router.

The user router must not import another handler module.  The composition root
binds these callbacks once all feature implementations have been imported.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


AddLotStarter = Callable[[Any, Any, Any], Awaitable[None]]
CardSubscriptionStarter = Callable[[Any, Any], Awaitable[None]]

_add_lot_starter: AddLotStarter | None = None
_card_subscription_starter: CardSubscriptionStarter | None = None


def configure_user_entrypoints(
    *,
    add_lot: AddLotStarter,
    card_subscription: CardSubscriptionStarter,
) -> None:
    """Bind feature callbacks at the application composition boundary."""

    global _add_lot_starter, _card_subscription_starter
    _add_lot_starter = add_lot
    _card_subscription_starter = card_subscription


async def launch_add_lot(message: Any, state: Any, bot: Any) -> None:
    if _add_lot_starter is None:
        raise RuntimeError("add-lot user entrypoint is not configured")
    await _add_lot_starter(message, state, bot)


async def launch_card_subscription(message: Any, state: Any) -> None:
    if _card_subscription_starter is None:
        raise RuntimeError("card-subscription user entrypoint is not configured")
    await _card_subscription_starter(message, state)


__all__ = [
    "configure_user_entrypoints",
    "launch_add_lot",
    "launch_card_subscription",
]
