"""Compatibility facade for the modular winner feature.

New code should import from ``bot.features.winner`` owner modules. Existing
routers and workers keep stable symbol identities through this facade.
"""

from bot.features.winner import *  # noqa: F403
from bot.features.winner import __all__ as __all__
