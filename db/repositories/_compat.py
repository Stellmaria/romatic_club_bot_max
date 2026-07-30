"""Minimal lazy bridge for the remaining cross-domain legacy calls.

These wrappers are deliberately small and temporary. They resolve through the
compatibility facade at call time, preventing repository import cycles while
handlers are migrated to service boundaries.
"""

__all__ = [
    '_has_column',
    'auction_exists',
    'get_user',
    'get_user_by_username',
    'is_user_uid_banned',
    '_normalize_username',
]

async def _has_column(*args, **kwargs):
    from db import db as _facade
    return await _facade._has_column(*args, **kwargs)

async def auction_exists(*args, **kwargs):
    from db import db as _facade
    return await _facade.auction_exists(*args, **kwargs)

async def get_user(*args, **kwargs):
    from db import db as _facade
    return await _facade.get_user(*args, **kwargs)

async def get_user_by_username(*args, **kwargs):
    from db import db as _facade
    return await _facade.get_user_by_username(*args, **kwargs)

async def is_user_uid_banned(*args, **kwargs):
    from db import db as _facade
    return await _facade.is_user_uid_banned(*args, **kwargs)

def _normalize_username(*args, **kwargs):
    from db import db as _facade
    return _facade._normalize_username(*args, **kwargs)

