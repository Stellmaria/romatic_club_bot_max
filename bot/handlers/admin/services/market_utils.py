"""Compatibility facade for marketplace workflow utilities."""
from bot.services.market_legacy.market_utils import *  # noqa: F403
from bot.services.market_legacy import market_utils as _impl
__getattr__ = getattr(_impl, "__getattr__", lambda name: getattr(_impl, name))
