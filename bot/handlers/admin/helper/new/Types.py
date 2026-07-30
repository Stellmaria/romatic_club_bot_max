from typing import TypedDict, Optional


class Owner(TypedDict, total=False):
    user_id: int
    username: Optional[str]
    full_name: Optional[str]
