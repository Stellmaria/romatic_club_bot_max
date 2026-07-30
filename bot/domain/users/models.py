"""Typed read models for users and auction ownership."""

from __future__ import annotations

from typing import TypedDict


class Owner(TypedDict, total=False):
    user_id: int
    username: str | None
    full_name: str | None
