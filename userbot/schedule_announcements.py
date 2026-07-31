"""Daily auction schedule announcements published by the Premium userbot."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from telethon import TelegramClient
from telethon.tl.types import MessageEntityCustomEmoji

from bot.core.settings import Settings, settings
from bot.core.time import MOSCOW, to_moscow
from db.auctions import get_auctions_by_date_with_owners

logger = logging.getLogger("userbot.schedule_announcements")

_RU_MONTHS = (
    "",
    "ЯНВАРЯ",
    "ФЕВРАЛЯ",
    "МАРТА",
    "АПРЕЛЯ",
    "МАЯ",
    "ИЮНЯ",
    "ИЮЛЯ",
    "АВГУСТА",
    "СЕНТЯБРЯ",
    "ОКТЯБРЯ",
    "НОЯБРЯ",
    "ДЕКАБРЯ",
)
_REQUIRED_EMOJI_KEYS = frozenset({"header", "card", "diamond", "tea"})
_STATE_LOCK = asyncio.Lock()
_USERNAME_RE = re.compile(r"@\w+")


class ScheduleEmojiConfigurationError(RuntimeError):
    """Raised when Premium emoji publication is enabled but not configured."""


@dataclass(frozen=True, slots=True)
class RenderedScheduleAnnouncement:
    text: str
    entities: tuple[MessageEntityCustomEmoji, ...]


def normalize_emoji_key(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_to_python_index(value: str, offset: int) -> int:
    if offset <= 0:
        return 0
    encoded = value.encode("utf-16-le")
    return len(encoded[: offset * 2].decode("utf-16-le", errors="ignore"))


def _state_template() -> dict[str, Any]:
    return {"emoji_ids": {}, "published": {}}


def load_announcement_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _state_template()
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read schedule announcement state from %s", path)
        return _state_template()

    emoji_ids = raw.get("emoji_ids") if isinstance(raw, dict) else None
    published = raw.get("published") if isinstance(raw, dict) else None
    return {
        "emoji_ids": dict(emoji_ids) if isinstance(emoji_ids, dict) else {},
        "published": dict(published) if isinstance(published, dict) else {},
    }


def save_announcement_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def extract_custom_emoji_assignments(message: Any) -> dict[str, int]:
    """Extract ``key = custom emoji`` assignments from a Telegram message."""

    text = str(getattr(message, "message", None) or "")
    result: dict[str, int] = {}
    for entity in getattr(message, "entities", None) or ():
        if not isinstance(entity, MessageEntityCustomEmoji):
            continue
        start = _utf16_to_python_index(text, int(entity.offset))
        end = _utf16_to_python_index(text, int(entity.offset + entity.length))
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end < 0:
            line_end = len(text)
        line = text[line_start:line_end]
        relative_start = start - line_start
        prefix = line[:relative_start].strip()
        fallback = text[start:end].strip()
        key = prefix.rsplit("=", 1)[0].strip() if "=" in prefix else fallback
        normalized = normalize_emoji_key(key)
        if normalized:
            result[normalized] = int(entity.document_id)
    return result


def announcement_target_date(
    now: datetime,
    *,
    hour: int = 23,
    minute: int = 0,
) -> date | None:
    local_now = now.astimezone(MOSCOW) if now.tzinfo else now.replace(tzinfo=MOSCOW)
    if (local_now.hour, local_now.minute) < (hour, minute):
        return None
    return local_now.date() + timedelta(days=1)


def _emoji_id(emoji_ids: Mapping[str, int], *keys: str) -> int | None:
    for key in keys:
        value = emoji_ids.get(normalize_emoji_key(key))
        try:
            parsed = int(value) if value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed:
            return parsed
    return None


class _RichTextBuilder:
    def __init__(self, emoji_ids: Mapping[str, int]) -> None:
        self._emoji_ids = emoji_ids
        self._parts: list[str] = []
        self._entities: list[MessageEntityCustomEmoji] = []
        self._offset = 0

    def text(self, value: object) -> None:
        rendered = str(value)
        self._parts.append(rendered)
        self._offset += utf16_length(rendered)

    def emoji(self, fallback: str, *keys: str) -> None:
        document_id = _emoji_id(self._emoji_ids, *keys)
        self._parts.append(fallback)
        length = utf16_length(fallback)
        if document_id:
            self._entities.append(
                MessageEntityCustomEmoji(
                    offset=self._offset,
                    length=length,
                    document_id=document_id,
                )
            )
        self._offset += length

    def build(self) -> RenderedScheduleAnnouncement:
        return RenderedScheduleAnnouncement(
            text="".join(self._parts),
            entities=tuple(self._entities),
        )


def _display_name(lot: Mapping[str, Any]) -> str:
    hero = str(lot.get("hero_name") or "").strip()
    card = str(lot.get("card_name") or "").strip()
    return hero or card or "Без имени"


def _public_comment(lot: Mapping[str, Any]) -> str:
    value = str(lot.get("comment") or "").replace("\n", " ").strip()
    value = _USERNAME_RE.sub("", value)
    value = " ".join(value.split()).strip(" -")
    if not value or value == "-":
        return ""
    return value[:70] + ("…" if len(value) > 70 else "")


def _currency_style(raw_currency: object) -> tuple[str, str]:
    value = normalize_emoji_key(raw_currency)
    if any(token in value for token in ("diamond", "diamonds", "алмаз", "кристалл", "gem")):
        return "💎", "diamond"
    if any(token in value for token in ("tea", "чай", "cup")):
        return "☕", "tea"
    if any(token in value for token in ("fire", "огонь", "плам")):
        return "🔥", "fire"
    if any(token in value for token in ("card", "карт")):
        return "🃏", "cards"
    return "💰", f"currency:{value}" if value else "money"


def render_schedule_announcement(
    target_date: date,
    lots: Sequence[Mapping[str, Any]],
    emoji_ids: Mapping[str, int],
) -> RenderedScheduleAnnouncement:
    builder = _RichTextBuilder(emoji_ids)
    title = f"АНОНС НА {target_date.day} {_RU_MONTHS[target_date.month]}"
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
        start_label = to_moscow(start_time).strftime("%H:%M") if isinstance(start_time, datetime) else "--:--"
        display_name = _display_name(lot)
        hero_key = f"hero:{str(lot.get('hero_name') or '').strip()}"
        card_key = f"card:{str(lot.get('card_name') or '').strip()}"
        builder.emoji("🎴", hero_key, card_key, "card")
        builder.text(f" {start_label} {display_name}")

        price = lot.get("start_price")
        try:
            amount = int(price) if price not in (None, "") else 0
        except (TypeError, ValueError):
            amount = 0
        if amount:
            fallback, currency_key = _currency_style(lot.get("currency"))
            builder.text(f" +{amount}")
            builder.emoji(fallback, currency_key)

        comment = _public_comment(lot)
        if comment:
            builder.text(f" ({comment})")
        if index != len(sorted_lots) - 1:
            builder.text("\n\n")

    return builder.build()


def missing_required_emoji_keys(emoji_ids: Mapping[str, int]) -> tuple[str, ...]:
    normalized = {normalize_emoji_key(key) for key, value in emoji_ids.items() if value}
    return tuple(sorted(_REQUIRED_EMOJI_KEYS - normalized))


async def store_emoji_assignments(
    assignments: Mapping[str, int],
    *,
    config: Settings = settings,
) -> tuple[str, ...]:
    async with _STATE_LOCK:
        state = load_announcement_state(config.schedule_announcement_state_file)
        current = dict(state.get("emoji_ids") or {})
        current.update(
            {
                normalize_emoji_key(key): int(value)
                for key, value in assignments.items()
                if normalize_emoji_key(key) and int(value)
            }
        )
        state["emoji_ids"] = current
        save_announcement_state(config.schedule_announcement_state_file, state)
    return tuple(sorted(current))


async def preview_schedule_announcement(
    target_date: date,
    *,
    config: Settings = settings,
) -> RenderedScheduleAnnouncement | None:
    lots = await get_auctions_by_date_with_owners(target_date)
    if not lots:
        return None
    async with _STATE_LOCK:
        state = load_announcement_state(config.schedule_announcement_state_file)
    return render_schedule_announcement(target_date, lots, state.get("emoji_ids") or {})


async def publish_schedule_announcement(
    telegram_client: TelegramClient,
    target_date: date,
    *,
    config: Settings = settings,
) -> int | None:
    date_key = target_date.isoformat()
    async with _STATE_LOCK:
        state = load_announcement_state(config.schedule_announcement_state_file)
        existing = (state.get("published") or {}).get(date_key)
        if isinstance(existing, dict) and existing.get("message_id"):
            return int(existing["message_id"])
        emoji_ids = dict(state.get("emoji_ids") or {})

    if config.schedule_announcements_require_custom_emoji:
        missing = missing_required_emoji_keys(emoji_ids)
        if missing:
            raise ScheduleEmojiConfigurationError(
                "missing Premium schedule emoji keys: " + ", ".join(missing)
            )

    lots = await get_auctions_by_date_with_owners(target_date)
    if not lots:
        logger.info("No live auctions for %s; schedule announcement not published", target_date)
        return None

    rendered = render_schedule_announcement(target_date, lots, emoji_ids)
    message = await telegram_client.send_message(
        config.auction_channel_id,
        rendered.text,
        formatting_entities=list(rendered.entities),
        link_preview=False,
        send_as=config.auction_channel_id,
    )
    message_id = int(message.id)

    async with _STATE_LOCK:
        state = load_announcement_state(config.schedule_announcement_state_file)
        published = dict(state.get("published") or {})
        published[date_key] = {
            "message_id": message_id,
            "published_at": datetime.now(MOSCOW).isoformat(),
        }
        state["published"] = published
        save_announcement_state(config.schedule_announcement_state_file, state)

    logger.info(
        "Published Premium schedule announcement for %s as message %s",
        target_date,
        message_id,
    )
    return message_id


async def schedule_announcement_watchdog(
    telegram_client: TelegramClient,
    *,
    config: Settings = settings,
) -> None:
    warned_for_date: date | None = None
    while True:
        try:
            if config.schedule_announcements_enabled:
                target_date = announcement_target_date(
                    datetime.now(MOSCOW),
                    hour=config.schedule_announcements_hour,
                    minute=config.schedule_announcements_minute,
                )
                if target_date is not None:
                    try:
                        await publish_schedule_announcement(
                            telegram_client,
                            target_date,
                            config=config,
                        )
                    except ScheduleEmojiConfigurationError as exc:
                        if warned_for_date != target_date:
                            logger.warning(
                                "Schedule announcement for %s is waiting for Premium emoji setup: %s",
                                target_date,
                                exc,
                            )
                            warned_for_date = target_date
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Schedule announcement watchdog failed")
        await asyncio.sleep(30)


__all__ = [
    "RenderedScheduleAnnouncement",
    "ScheduleEmojiConfigurationError",
    "announcement_target_date",
    "extract_custom_emoji_assignments",
    "load_announcement_state",
    "missing_required_emoji_keys",
    "normalize_emoji_key",
    "preview_schedule_announcement",
    "publish_schedule_announcement",
    "render_schedule_announcement",
    "save_announcement_state",
    "schedule_announcement_watchdog",
    "store_emoji_assignments",
    "utf16_length",
]
