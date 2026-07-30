from __future__ import annotations

from typing import NotRequired, TypedDict


class OwnerRecord(TypedDict):
    user_id: int
    username: NotRequired[str | None]
    full_name: NotRequired[str | None]


# Historical public name retained for annotations in the legacy database API.
Owner = OwnerRecord
