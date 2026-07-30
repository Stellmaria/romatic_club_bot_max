from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from aiogram import Bot

from bot.core.time import moscow_date, utc_now
from bot.handlers.admin.helper.admin_constants import (
    load_full_auction_ctx,
    render_auction_caption,
)
from bot.handlers.admin.helper.user_helpers import get_owner_refs
from bot.handlers.auction.winner import _post_rules_under_lot
from bot.services.auction_workflows import AuctionPublicationService
from bot.telegram.media import bot_send_media_any
from config import AUCTION_CHANNEL_ID, AUCTION_CHANNEL_USERNAME
from db.db import count_sold_by_card_id, count_sold_same_card, list_auctions

logger = logging.getLogger("auction_bot.publication")


def _without_usernames(value: object) -> str:
    return re.sub(r"@\w+", "", str(value or "")).strip()


def _target_channel(configured: int | str | None) -> int | str:
    if isinstance(configured, int) and configured:
        return configured
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    if AUCTION_CHANNEL_USERNAME:
        username = AUCTION_CHANNEL_USERNAME.strip()
        return username if username.startswith("@") else f"@{username}"
    raise RuntimeError("auction publication channel is not configured")


def _media_id(*records: dict[str, Any]) -> str | None:
    invalid = {"", "0", "none", "null", "default_photo_id"}
    for record in records:
        raw = record.get("image_id") or record.get("image") or record.get("photo_id")
        if isinstance(raw, str) and raw.strip().lower() not in invalid:
            return raw.strip()
    return None


async def _publication_context(auction: dict[str, Any]) -> tuple[dict, dict, dict, int]:
    auction_id = int(auction["auction_id"])
    owners_count = 1
    try:
        owners = await get_owner_refs(auction_id)
        refs = {item.strip() for item in str(owners or "").split(",") if item.strip()}
        owners_count = len(refs) or 1
    except Exception:
        logger.exception("Could not load owners for auction %s", auction_id)

    context = await load_full_auction_ctx(auction_id)
    full_auction = dict(context.get("auction") or {})
    card = dict(context.get("card") or {})
    deck = dict(context.get("deck") or {})

    try:
        card_id = card.get("card_id") or full_auction.get("card_id")
        if card_id:
            full_auction["sold_count"] = await count_sold_by_card_id(card_id=int(card_id))
        else:
            hero = str(full_auction.get("hero_name") or card.get("hero_name") or "").strip()
            name = str(full_auction.get("card_name") or card.get("card_name") or "").strip()
            if hero and name:
                full_auction["sold_count"] = await count_sold_same_card(
                    hero_name=hero,
                    card_name=name,
                )
    except Exception:
        logger.exception("Could not calculate sold count for auction %s", auction_id)

    for field in ("end_time", "hero_name", "card_name", "currency", "start_price", "auction_kind"):
        full_auction.setdefault(field, auction.get(field))
    if not full_auction.get("comment"):
        full_auction["comment"] = _without_usernames(auction.get("comment")) or "-"
    else:
        full_auction["comment"] = _without_usernames(full_auction["comment"]) or "-"
    return full_auction, card, deck, owners_count


async def publish_auction_lot(
    bot: Bot,
    auction: dict[str, Any],
    channel_id: int | str | None = AUCTION_CHANNEL_ID,
    lot_number: int | None = None,
    publication_service: AuctionPublicationService | None = None,
) -> int | None:
    """Deliver one claimed auction and atomically record its Telegram message."""
    del lot_number  # retained for compatibility with existing admin calls
    auction_id = int(auction["auction_id"])
    if auction.get("message_id"):
        return int(auction["message_id"])

    service = publication_service or await AuctionPublicationService.create()
    if str(auction.get("status") or "").lower() != "publishing":
        try:
            auction = await service.claim_one(auction_id)
        except Exception as exc:
            logger.warning("Auction %s cannot be claimed: %s", auction_id, exc)
            return None

    try:
        full_auction, card, deck, owners_count = await _publication_context(auction)
        caption = render_auction_caption(
            full_auction,
            card=card,
            deck=deck,
            owners_count=owners_count,
            show_min_bid=True,
        )
        target = _target_channel(channel_id)
        media = _media_id(full_auction, card, auction)

        message = None
        if media and len(caption) <= 1000:
            message = await bot_send_media_any(
                bot,
                chat_id=target,
                file_id=media,
                caption=caption,
                parse_mode="HTML",
            )
        if message is None:
            message = await bot.send_message(
                target,
                caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        message_id = int(message.message_id)
        stored = await service.mark_published(auction_id, message_id=message_id)
        if not stored:
            logger.critical(
                "Auction %s was delivered as message %s but its lease was lost; manual review required",
                auction_id,
                message_id,
            )
            return message_id

        asyncio.create_task(_post_rules_under_lot(bot, auction_id))
        logger.info("Published auction %s as message %s", auction_id, message_id)
        return message_id
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Could not publish auction %s", auction_id)
        try:
            await service.mark_failed(auction_id, error=repr(exc))
        except Exception:
            logger.exception("Could not record publication failure for auction %s", auction_id)
        return None


async def get_lot_number_for_day(auction: dict[str, Any]) -> int:
    start_time = auction.get("start_time")
    if not start_time:
        return 1
    lots = await list_auctions(["active", "scheduled", "publishing", "pending"])
    same_day = sorted(
        (
            lot for lot in lots
            if lot.get("start_time") and moscow_date(lot["start_time"]) == moscow_date(start_time)
        ),
        key=lambda lot: (lot["start_time"], lot["auction_id"]),
    )
    for index, lot in enumerate(same_day, 1):
        if int(lot["auction_id"]) == int(auction["auction_id"]):
            return index
    return 1


async def auction_publisher_loop(bot: Bot) -> None:
    service = await AuctionPublicationService.create()
    while True:
        try:
            stale_ids = await service.recover_stale()
            if stale_ids:
                logger.error("Publication leases require manual review: %s", stale_ids)
            auctions = await service.claim_due(now=utc_now(), limit=20)
            for auction in auctions:
                await publish_auction_lot(
                    bot,
                    auction,
                    channel_id=AUCTION_CHANNEL_ID,
                    lot_number=await get_lot_number_for_day(auction),
                    publication_service=service,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auction publisher iteration failed")
        await asyncio.sleep(30)
