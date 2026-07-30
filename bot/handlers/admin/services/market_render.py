"""Compatibility facade for marketplace rendering helpers."""
from bot.services.market_legacy.market_render import *  # noqa: F403
from bot.services.market_legacy import market_render as _impl
__getattr__ = getattr(_impl, "__getattr__", lambda name: getattr(_impl, name))
