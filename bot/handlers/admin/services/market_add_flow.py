"""Compatibility facade for the marketplace add workflow."""
from bot.services.market_legacy.market_add_flow import *  # noqa: F403
from bot.services.market_legacy import market_add_flow as _impl
__getattr__ = getattr(_impl, "__getattr__", lambda name: getattr(_impl, name))
