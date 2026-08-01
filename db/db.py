"""Thin compatibility facade for modular database query implementations."""

import sys
import types

from db import (
    admin,
    auctions,
    cards,
    core,
    exchange,
    market,
    posts,
    reliable_mutations,
    subscriptions,
    uid,
    users,
)

# Strict compatibility mutations are loaded after historical modules so their
# atomic implementations replace unsafe legacy exports with the same names.
_MODULES = (
    core,
    users,
    auctions,
    admin,
    cards,
    subscriptions,
    market,
    exchange,
    posts,
    uid,
    reliable_mutations,
)
for _module in _MODULES:
    globals().update({name: getattr(_module, name) for name in _module.__all__})

db_pool = core.db_pool
__all__ = list(
    dict.fromkeys(
        [name for _module in _MODULES for name in _module.__all__] + ["db_pool"]
    )
)

# Legacy scheduler contract lives in ``db.auctions``:
# date_trunc('minute', a.start_time)
# current_owner.user_id = existing_owner.user_id
# card_id, card_name, hero_name
# Winner ordering is implemented in ``db.auctions``: THEN b.amount END ASC


class _FacadeModule(types.ModuleType):
    def __setattr__(self, name, value):
        if name == "db_pool":
            if value is core.db_pool:
                core.db_pool.clear()
            else:
                core.db_pool.bind(value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _FacadeModule
