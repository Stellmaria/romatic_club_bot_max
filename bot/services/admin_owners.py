"""Owner lookup operations used by the admin interface and audit service."""

from __future__ import annotations

import logging

from aiogram import Bot

from bot.presentation.admin import format_owners_block, format_pretty_owners_for_log
from bot.repositories.admin_logs import AdminLogsRepository
from bot.services.luxury import get_user_luxury_level
from db.pool import get_db_pool
from db.users import get_users_by_ids

logger = logging.getLogger(__name__)


async def _get_lot_owners(auction_id: int) -> list[dict]:
    try:
        repository = AdminLogsRepository(await get_db_pool())
        return await repository.get_lot_owners(int(auction_id))
    except Exception:  # noqa: BLE001 - legacy callers expect an empty owner list
        logger.exception("Failed to load owners for lot %s", auction_id)
        return []


async def get_lot_owners_text(auction_id: int) -> str:
    """Load and render the owners of a lot."""
    owners = await _get_lot_owners(int(auction_id))
    return format_owners_block(owners)


async def get_pretty_owners_for_log(auction_id: int) -> str:
    """Load and render owner identities for user-requested lot audit logs."""

    owners = await _get_lot_owners(int(auction_id))
    user_ids = [int(owner["user_id"]) for owner in owners if owner.get("user_id")]
    if not user_ids:
        return "-"
    users = await get_users_by_ids(user_ids)
    return format_pretty_owners_for_log(users)


async def get_lot_owners_with_levels(bot: Bot, auction_id: int) -> list[dict]:
    """Load lot owners and enrich them with their effective Luxury level."""
    owners = await _get_lot_owners(int(auction_id))
    for owner in owners:
        user_id = int(owner.get("user_id") or 0)
        if not user_id:
            continue
        try:
            owner["luxury_level"] = await get_user_luxury_level(bot, user_id)
        except Exception:
            owner["luxury_level"] = 1 if owner.get("is_luxury") else 0
    return owners


__all__ = [
    "get_lot_owners_text",
    "get_lot_owners_with_levels",
    "get_pretty_owners_for_log",
]
