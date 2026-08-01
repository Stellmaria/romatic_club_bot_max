"""Thin compatibility facade for modular database query implementations."""

from db import (
    admin,
    auction_lifecycle_queries,
    auctions,
    cards,
    core,
    exchange,
    market,
    posts,
    schedule_queries,
    subscriptions,
    uid,
    users,
)
from db import reliable_mutations

# Keep one public owner for every compatibility symbol. The historical auctions
# module still owns ``add_auction`` in the facade contract, but its implementation
# is replaced with the strict transactional version until all callers migrate to
# the workflow repository.
auctions.add_auction = reliable_mutations.add_auction

_MODULES = (
    core,
    users,
    auctions,
    auction_lifecycle_queries,
    admin,
    cards,
    schedule_queries,
    subscriptions,
    market,
    exchange,
    posts,
    uid,
)
for _module in _MODULES:
    globals().update({name: getattr(_module, name) for name in _module.__all__})

# Deprecated non-owning view. Assigning to this name no longer mutates hidden
# state; tests and applications install a DatabaseRuntime through db.core.
db_pool = core.db_pool
__all__ = [name for _module in _MODULES for name in _module.__all__] + ["db_pool"]

# Legacy scheduler contract lives in ``db.auctions``:
# date_trunc('minute', a.start_time)
# current_owner.user_id = existing_owner.user_id
# card_id, card_name, hero_name
# Winner ordering is implemented in ``db.auctions``: THEN b.amount END ASC
