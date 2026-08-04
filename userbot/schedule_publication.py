# ruff: noqa: RUF001
"""Approved schedule rendering, publication timing and channel pin rotation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from telethon import TelegramClient

from bot.core.settings import UserbotSettings
from bot.core.time import MOSCOW, to_moscow
from bot.domain.auctions.enums import AuctionKind, Currency, normalize_currency_choices
from bot.domain.schedule_lots import schedule_lot_display_name, special_schedule_asset
from db.schedule_publication import (
    get_last_auction_close_for_day,
    get_previous_published_schedule_message,
)
from db.schedule_setup import (
    get_emoji_assets,
    get_publication_review,
    get_schedule_lots_for_day,
    mark_publication_published,
)
from userbot import schedule_announcements as base

logger = logging.getLogger("userbot.schedule_publication")

RenderedScheduleAnnouncement = base.RenderedScheduleAnnouncement
ScheduleEmojiConfigurationError = base.ScheduleEmojiConfigurationError
extract_custom_emoji_assignments = base.extract_custom_emoji_assignments
missing_required_emoji_keys = base.missing_required_emoji_keys
store_emoji_assignments = base.store_emoji_assignments

_KIND_LABELS = {
    AuctionKind.FAST: "быстрый",
    AuctionKind.FREE: "свободный",
    AuctionKind.REVERSE: "обратный",
}


def _auction_kind_label(lot: Mapping[str, Any]) -> str:
    try:
        kind = AuctionKind.from_raw(lot.get("auction_kind"))
    except ValueError:
        return ""
    return _KIND_LABELS.get(kind, "")


def _auction_currency_label(lot: Mapping[str, Any]) -> str:
    """Render only the currency qualifiers required by the public template."""

    choices = set(
        normalize_currency_choices(
            lot.get("accepted_currencies"),
            fallback=lot.get("currency"),
        )
    )
    if {Currency.CUPS, Currency.DIAMONDS}.issubset(choices):
        return "за чай и алмазы"
    if {Currency.CUPS, Currency.TREASURES}.issubset(choices):
        return "за чай и сокровища"
    if {Currency.DIAMONDS, Currency.TREASURES}.issubset(choices):
        return "за алмазы и сокровища"
    if Currency.TREASURES in choices:
        return "за сокровища"
    if Currency.CUPS in choices:
        return "за чай"
    # Diamond-only auctions intentionally have no qualifier in the announcement.
    return ""


def render_schedule_announcement(
    target_date: date,
    lots: Sequence[Mapping[str, Any]],
    emoji_ids: Mapping[str, object],
) -> RenderedScheduleAnnouncement:
    """Render the channel template with concise type and currency markers."""

    builder = base._RichTextBuilder(emoji_ids)
    title = f"АНОНС НА {target_date.day} {base._RU_MONTHS[target_date.month]}"
    builder.emoji("🦋", "header")
    builder.text(f" {title} ")
    builder.emoji("🦋", "header")
    builder.text("\n\n")

    sorted_lots = sorted(
        lots,
        key=lambda lot: (
            lot.get("start_time") or datetime.max,
            int(lot.get("auction_id") or 0),
        ),
    )
    for index, lot in enumerate(sorted_lots):
        start_time = lot.get("start_time")
        start_label = (
            to_moscow(start_time).strftime("%H:%M") if isinstance(start_time, datetime) else "--:--"
        )
        whole_deck = bool(lot.get("whole_deck"))
        special_asset = special_schedule_asset(lot)

        if special_asset:
            builder.emoji(special_asset.fallback, special_asset.key)
        elif whole_deck:
            builder.emoji("🃏", "whole_deck")
        else:
            card_emoji_id = lot.get("card_emoji_id")
            if card_emoji_id:
                builder.emoji_id("🎴", card_emoji_id)
            else:
                hero_key = f"hero:{str(lot.get('hero_name') or '').strip()}"
                card_key = f"card:{str(lot.get('card_name') or '').strip()}"
                builder.emoji("🎴", hero_key, card_key, "card")

        builder.text(f" {start_label} ")

        rarity = base._normalize_rarity(lot.get("rarity"))
        if rarity and not whole_deck and not special_asset:
            builder.emoji("🔹", f"rarity:{rarity}")
            builder.text(" ")

        builder.text(schedule_lot_display_name(lot))

        deck_emoji_id = lot.get("deck_emoji_id")
        if deck_emoji_id and not special_asset:
            builder.text(" ")
            builder.emoji_id("🗂", deck_emoji_id)

        if whole_deck:
            base._append_reward(
                builder,
                amount=lot.get("deck_diamonds"),
                reward_type="diamonds",
            )
            base._append_reward(
                builder,
                amount=lot.get("deck_tea"),
                reward_type="tea",
            )
        else:
            reward_amount = lot.get("obtain_amount")
            reward_type = lot.get("obtain_type")
            if reward_amount in (None, ""):
                reward_amount = lot.get("start_price")
                reward_type = lot.get("currency")
            base._append_reward(
                builder,
                amount=reward_amount,
                reward_type=reward_type,
            )

        currency_label = _auction_currency_label(lot)
        if currency_label:
            builder.text(f" ({currency_label})")
        kind_label = _auction_kind_label(lot)
        if kind_label:
            builder.text(f" · {kind_label}")

        if index != len(sorted_lots) - 1:
            builder.text("\n")

    return builder.build()


# Existing preview and approval code resolves this global at call time.
base.render_schedule_announcement = render_schedule_announcement


async def preview_schedule_announcement(
    target_date: date,
    *,
    config: UserbotSettings,
) -> RenderedScheduleAnnouncement | None:
    return await base.preview_schedule_announcement(target_date, config=config)


def _as_moscow(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW)
    return value.astimezone(MOSCOW)


def schedule_publication_ready_at(
    target_date: date,
    *,
    last_auction_close: datetime | None,
    config: UserbotSettings,
) -> datetime:
    """Return the earliest safe publication time for the approved announcement."""

    if last_auction_close is not None:
        return _as_moscow(last_auction_close)
    previous_day = target_date - timedelta(days=1)
    return datetime.combine(
        previous_day,
        time(
            hour=int(config.schedule_announcements_hour),
            minute=int(config.schedule_announcements_minute),
        ),
        tzinfo=MOSCOW,
    )


async def schedule_publication_is_ready(
    target_date: date,
    *,
    now: datetime,
    config: UserbotSettings,
) -> bool:
    last_close = await get_last_auction_close_for_day(target_date - timedelta(days=1))
    ready_at = schedule_publication_ready_at(
        target_date,
        last_auction_close=last_close,
        config=config,
    )
    return _as_moscow(now) >= ready_at


async def ensure_schedule_pin(
    telegram_client: TelegramClient,
    target_date: date,
    message_id: int,
    *,
    config: UserbotSettings,
) -> None:
    """Pin the new schedule first, then unpin only the preceding schedule post."""

    channel_id = int(config.auction_channel_id)
    await telegram_client.pin_message(channel_id, int(message_id), notify=False)
    previous_message_id = await get_previous_published_schedule_message(target_date)
    if previous_message_id and previous_message_id != int(message_id):
        await telegram_client.unpin_message(
            channel_id,
            int(previous_message_id),
            notify=False,
        )


async def publish_schedule_announcement(
    telegram_client: TelegramClient,
    target_date: date,
    *,
    config: UserbotSettings,
) -> int | None:
    review = await get_publication_review(target_date)
    if review and review.get("status") == "published" and review.get("channel_message_id"):
        message_id = int(review["channel_message_id"])
        await ensure_schedule_pin(
            telegram_client,
            target_date,
            message_id,
            config=config,
        )
        return message_id

    approved_preview = None
    if review and review.get("status") == "approved":
        approved_preview = await base._approved_preview_message(telegram_client, review)
        if approved_preview is None:
            raise ScheduleEmojiConfigurationError(
                "подтверждённое превью не найдено; публикация остановлена"
            )

    if approved_preview is not None:
        publication_text = str(approved_preview.message)
        publication_entities = list(getattr(approved_preview, "entities", None) or ())
    else:
        lots = await get_schedule_lots_for_day(target_date)
        if not lots:
            logger.info(
                "No live auctions for %s; schedule announcement not published",
                target_date,
            )
            return None
        assets = await get_emoji_assets()
        issues = base.schedule_configuration_issues(lots, assets)
        if config.schedule_announcements_require_custom_emoji and issues:
            raise ScheduleEmojiConfigurationError("; ".join(issues))
        rendered = render_schedule_announcement(target_date, lots, assets)
        publication_text = rendered.text
        publication_entities = list(rendered.entities)

    message = await telegram_client.send_message(
        int(config.auction_channel_id),
        publication_text,
        formatting_entities=publication_entities,
        link_preview=False,
        send_as=int(config.auction_channel_id),
    )
    message_id = int(message.id)
    # Persist before pinning. A pin failure then retries against this same post
    # instead of creating duplicate channel announcements.
    await mark_publication_published(target_date, channel_message_id=message_id)
    await ensure_schedule_pin(
        telegram_client,
        target_date,
        message_id,
        config=config,
    )
    logger.info(
        "Published and pinned approved Premium schedule for %s as message %s",
        target_date,
        message_id,
    )
    return message_id


async def schedule_announcement_watchdog(  # noqa: C901
    telegram_client: TelegramClient,
    *,
    config: UserbotSettings,
) -> None:
    warned: dict[date, str] = {}
    pinned_in_process: set[tuple[date, int]] = set()
    while True:
        try:
            if config.schedule_announcements_enabled:
                now = datetime.now(MOSCOW)
                preview_date = base.announcement_target_date(
                    now,
                    hour=base._PREVIEW_HOUR,
                    minute=base._PREVIEW_MINUTE,
                )
                if preview_date is not None:
                    try:
                        preview_message_id = await base.send_schedule_review_preview(
                            telegram_client,
                            preview_date,
                        )
                        if preview_message_id:
                            warned.pop(preview_date, None)
                    except ScheduleEmojiConfigurationError as exc:
                        error_text = str(exc)
                        if warned.get(preview_date) != error_text:
                            logger.warning(
                                "Schedule preview for %s is blocked: %s",
                                preview_date,
                                error_text,
                            )
                            await base._send_blocked_preview_notice(
                                telegram_client,
                                preview_date,
                                error_text,
                            )
                            warned[preview_date] = error_text

                # At midnight an approved schedule for the current date may still
                # be waiting for an auction that ended after 00:00. Check both the
                # current and next calendar date instead of deriving one fixed slot.
                for target_date in (now.date(), now.date() + timedelta(days=1)):
                    review = await get_publication_review(target_date)
                    if not review:
                        continue
                    status = str(review.get("status") or "")
                    channel_message_id = review.get("channel_message_id")
                    if status == "published" and channel_message_id:
                        pin_key = (target_date, int(channel_message_id))
                        if pin_key not in pinned_in_process:
                            await ensure_schedule_pin(
                                telegram_client,
                                target_date,
                                int(channel_message_id),
                                config=config,
                            )
                            pinned_in_process.add(pin_key)
                        continue
                    if status != "approved":
                        continue
                    if await schedule_publication_is_ready(
                        target_date,
                        now=now,
                        config=config,
                    ):
                        message_id = await publish_schedule_announcement(
                            telegram_client,
                            target_date,
                            config=config,
                        )
                        if message_id:
                            pinned_in_process.add((target_date, int(message_id)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Schedule announcement watchdog failed")
        await asyncio.sleep(30)


__all__ = [
    "RenderedScheduleAnnouncement",
    "ScheduleEmojiConfigurationError",
    "ensure_schedule_pin",
    "extract_custom_emoji_assignments",
    "missing_required_emoji_keys",
    "preview_schedule_announcement",
    "publish_schedule_announcement",
    "render_schedule_announcement",
    "schedule_announcement_watchdog",
    "schedule_publication_is_ready",
    "schedule_publication_ready_at",
    "store_emoji_assignments",
]
