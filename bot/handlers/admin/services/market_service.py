"""Compatibility facade for marketplace conversation helpers."""
from bot.services.market_legacy.market_service import *  # noqa: F403
from bot.services.market_legacy import market_service as _impl
__getattr__ = getattr(_impl, "__getattr__", lambda name: getattr(_impl, name))
