"""Compatibility facade for marketplace listing management."""
from bot.services.market_legacy.market_manage_flow import *  # noqa: F403
from bot.services.market_legacy import market_manage_flow as _impl
__getattr__ = getattr(_impl, "__getattr__", lambda name: getattr(_impl, name))
