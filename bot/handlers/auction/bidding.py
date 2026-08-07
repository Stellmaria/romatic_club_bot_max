from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler

from bot.core.legacy_config import legacy_config
from bot.core.time import utc_now
from bot.domain.auctions import (
    AuctionEnded,
    AuctionKindNotBiddable,
    AuctionNotActive,
    AuctionNotFound,
    BidAlreadyRecorded,
    BidFormatError,
    BidNotFound,
    BidOwnershipError,
    BidStepError,
    BidTooHigh,
    BidTooLow,
    BidderBanned,
    BidderNotEligible,
    UnsupportedCurrency,
)
from bot.presentation.audit import format_bid_log
from bot.services.admin_logging import send_admin_log
from bot.services.auction_bids import AuctionBidService
from db.legacy import add_warning, get_warnings_count

logger = logging.getLogger("auction_bot.bidding")
router = Router(name="auction-bidding")


def _bot_bid_validation_enabled() -> bool:
    return (
        not bool(legacy_config.USERBOT_BID_MODERATION)
        and legacy_config.BID_VALIDATION_MODE == "bot"
    )


async def _mute_for_invalid_bid(message: types.Message) -> None:
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            message.from_user.id,
            permissions=types.ChatPermissions(can_send_messages=False),
            until_date=utc_now() + timedelta(minutes=1),
        )
    except Exception:
        logger.exception("Could not temporarily mute invalid bidder %s", message.from_user.id)


async def _delete_message_safely(message: types.Message) -> None:
    try:
        await message.delete()
    except Exception:
        logger.exception("Could not delete invalid bid message %s", message.message_id)


def _step_error_text(exc: BidStepError, currency_emoji: str) -> str:
    return (
        "Ставка не засчитана: нужен шаг "
        f"<b>{exc.step}</b> от стартовой цены <b>{exc.start_price}</b> {currency_emoji}."
    )


@router.message(F.chat.id < 0, ~F.text.startswith("/"))
async def accept_bid_message(message: types.Message) -> None:
    """Bot API adapter used only when userbot moderation is explicitly disabled."""
    if not _bot_bid_validation_enabled():
        raise SkipHandler
    if not message.reply_to_message or not message.from_user or message.from_user.is_bot:
        raise SkipHandler

    service = await AuctionBidService.create()
    username = message.from_user.username or f"id{message.from_user.id}"
    full_name = " ".join(
        part for part in (message.from_user.first_name, message.from_user.last_name) if part
    ).strip()

    try:
        placement = await service.place_for_discussion(
            discussion_root_message_id=message.reply_to_message.message_id,
            bid_message_id=message.message_id,
            bidder_id=message.from_user.id,
            bid_text=message.text or "",
            username=message.from_user.username,
            full_name=full_name,
        )
    except AuctionNotFound:
        # A reply to another comment is not a bid on the root auction post.
        raise SkipHandler
    except BidAlreadyRecorded:
        logger.info("Ignoring duplicate delivery of bid message %s", message.message_id)
        return
    except BidderBanned:
        await _delete_message_safely(message)
        await message.answer(
            f"🚫 @{html.escape(username)}: ставки недоступны из-за действующей блокировки.",
            reply_to_message_id=message.reply_to_message.message_id,
            parse_mode="HTML",
        )
        return
    except BidderNotEligible:
        await _delete_message_safely(message)
        await message.answer(
            "👑 В чёрном аукционе ставки доступны только пользователям Лакшери.",
            reply_to_message_id=message.reply_to_message.message_id,
        )
        return
    except AuctionKindNotBiddable:
        # Свободные аукционы разбираются вручную и не должны поглощаться
        # автоматическим валидатором числовых ставок.
        raise SkipHandler
    except AuctionEnded:
        await message.answer(
            "⏰ Аукцион завершён, ставки больше не принимаются.",
            reply_to_message_id=message.message_id,
        )
        return
    except AuctionNotActive:
        return
    except BidTooLow as exc:
        await message.answer(
            f"⚠️ Минимальная допустимая ставка сейчас: <b>{exc.minimum}</b>.",
            reply_to_message_id=message.message_id,
            parse_mode="HTML",
        )
        return
    except BidTooHigh as exc:
        await message.answer(
            "⚠️ В обратном аукционе новая ставка должна быть ниже текущей. "
            f"Следующая ставка должна быть не выше <b>{exc.maximum}</b>.",
            reply_to_message_id=message.message_id,
            parse_mode="HTML",
        )
        return
    except BidStepError as exc:
        await message.answer(
            _step_error_text(exc, "💰"),
            reply_to_message_id=message.message_id,
            parse_mode="HTML",
        )
        await _mute_for_invalid_bid(message)
        await asyncio.sleep(1)
        await _delete_message_safely(message)
        return
    except (BidFormatError, UnsupportedCurrency) as exc:
        reason = getattr(exc, "user_message", "Валюта аукциона не поддерживается.")
        await message.answer(
            f"⏳ @{html.escape(username)}\nСтавка не засчитана: {html.escape(str(reason))}",
            reply_to_message_id=message.message_id,
        )
        await _mute_for_invalid_bid(message)
        await asyncio.sleep(1)
        await _delete_message_safely(message)
        return
    except Exception:
        logger.exception("Unexpected error while recording bid message %s", message.message_id)
        await message.answer(
            "❌ Не удалось записать ставку. Попробуйте ещё раз или обратитесь к администратору.",
            reply_to_message_id=message.message_id,
        )
        return

    try:
        await send_admin_log(
            message.bot,
            format_bid_log(
                auction_id=placement.auction.auction_id,
                bidder_id=message.from_user.id,
                bidder_username=message.from_user.username,
                amount=placement.bid.amount,
                currency=placement.auction.currency.value,
                message_id=message.message_id,
            ),
        )
    except Exception:
        logger.exception("Could not write bid %s to audit chat", placement.bid.bid_id)


@router.edited_message(F.chat.id < 0)
async def reject_edited_bid(message: types.Message) -> None:
    if not _bot_bid_validation_enabled():
        raise SkipHandler
    if not message.from_user or message.from_user.is_bot:
        raise SkipHandler

    service = await AuctionBidService.create()
    try:
        await service.remove_edited_bid(
            bid_message_id=message.message_id,
            actor_user_id=message.from_user.id,
        )
    except (BidNotFound, AuctionNotFound, AuctionEnded, AuctionNotActive):
        raise SkipHandler
    except BidOwnershipError:
        logger.warning(
            "User %s edited a bid message owned by another user: %s",
            message.from_user.id,
            message.message_id,
        )
        return
    except Exception:
        logger.exception("Could not remove edited bid %s", message.message_id)
        return

    username = message.from_user.username or f"id{message.from_user.id}"
    await add_warning(message.from_user.id, "edit_bid", message_id=message.message_id)
    warnings = await get_warnings_count(message.from_user.id)
    await message.answer(
        "⛔ Редактирование ставок запрещено. Ставка удалена из учёта.\n"
        f"@{html.escape(username)}, предупреждений: <b>{warnings}/4</b>.",
        reply_to_message_id=message.message_id,
        parse_mode="HTML",
    )
    await _delete_message_safely(message)
