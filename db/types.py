"""Database-facing structural types shared without handler dependencies."""

from typing import Optional, TypedDict


class Owner(TypedDict, total=False):
    user_id: int
    username: Optional[str]
    full_name: Optional[str]
