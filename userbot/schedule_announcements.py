"""Daily auction schedule previews and publication by the Premium userbot."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from telethon import Button, TelegramClient
from telethon.tl.types import MessageEntityCustomEmoji

from bot.core.settings import UserbotSettings
from bot.core.time import MOSCOW, to_moscow
from bot.domain.schedule_lots import schedule_lot_display_name, special_schedule_asset
from db.schedule_setup import (
    get_emoji_assets,
    get_preview_target,
    get_publication_review,
    get_schedule_lots_for_day,
    mark_publication_published,
    record_pending_preview,
)

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
_PREVIEW_HOUR = 22
_PREVIEW_MINUTE = 30


class ScheduleEmojiConfigurationError(RuntimeError):
    """Raised when a schedule cannot be rendered with verified Premium assets."""


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
    """Read the legacy JSON registry retained for backwards-compatible commands."""

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


def _value_emoji_id(value: object) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("custom_emoji_id")
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        parsed = 0
    return parsed or None


def _asset_id(assets: Mapping[str, object], *keys: str) -> int | None:
    for key in keys:
        value = assets.get(normalize_emoji_key(key))
        parsed = _value_emoji_id(value)
        if parsed:
            return parsed
    return None


class _RichTextBuilder:
    def __init__(self, assets: Mapping[str, object]) -> None:
        self._assets = assets
        self._parts: list[str] = []
        self._entities: list[MessageEntityCustomEmoji] = []
        self._offset = 0

    def text(self, value: object) -> None:
        rendered = str(value)
        self._parts.append(rendered)
        self._offset += utf16_length(rendered)

    def emoji_id(self, fallback: str, document_id: object) -> None:
        parsed_id = _value_emoji_id(document_id)
        self._parts.append(fallback)
        length = utf16_length(fallback)
        if parsed_id:
            self._entities.append(
                MessageEntityCustomEmoji(
                    offset=self._offset,
                    length=length,
                    document_id=parsed_id,
                )
            )
        self._offset += length

    def emoji(self, fallback: str, *keys: str) -> None:
        self.emoji_id(fallback, _asset_id(self._assets, *keys))

    def build(self) -> RenderedScheduleAnnouncement:
        return RenderedScheduleAnnouncement(
            text="".join(self._parts),
            entities=tuple(self._entities),
        )


def _normalize_rarity(value: object) -> str | None:
    normalized = normalize_emoji_key(value)
    aliases = {
        "bronze": "bronze",
        "бронза": "bronze",
        "бронзовая": "bronze",
        "silver": "silver",
        "серебро": "silver",
        "серебряная": "silver",
        "gold": "gold",
        "золото": "gold",
        "золотая": "gold",
        "epic": "epic",
        "эпик": "epic",
        "diamond": "epic",
        "diamonds": "epic",
        "алмаз": "epic",
        "алмазная": "epic",
    }
    return aliases.get(normalized)


def _normalize_reward_type(value: object) -> str | None:
    normalized = normalize_emoji_key(value)
    if normalized in {"diamonds", "diamond", "алмазы", "алмаз", "gems"}:
        return "diamonds"
    if normalized in {"tea", "cups", "cup", "чай", "чашка", "чашки"}:
        return "tea"
    return None


def _expected_reward(rarity: object, reward_type: object) -> int | None:
    normalized_rarity = _normalize_rarity(rarity)
    normalized_type = _normalize_reward_type(reward_type)
    if not normalized_rarity or not normalized_type:
        return None
    diamonds = {"bronze": 20, "silver": 40, "gold": 80, "epic": 120}
    tea = {"bronze": 2, "silver": 4, "gold": 8, "epic": 12}
    return (diamonds if normalized_type == "diamonds" else tea)[normalized_rarity]


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


def _start_currency_label(raw_currency: object) -> str:
    value = normalize_emoji_key(raw_currency)
    has_tea = any(token in value for token in ("tea", "чай", "cup"))
    has_diamonds = any(token in value for token in ("diamond", "алмаз", "gem"))
    if has_tea and has_diamonds:
        return "за чай и алмазы"
    if has_tea:
        return "за чай"
    if has_diamonds:
        return "за алмазы"
    return ""


def _append_reward(
    builder: _RichTextBuilder,
    *,
    amount: object,
    reward_type: object,
) -> None:
    try:
        parsed_amount = int(amount or 0)
    except (TypeError, ValueError):
        parsed_amount = 0
    if not parsed_amount:
        return
    normalized = _normalize_reward_type(reward_type)
    builder.text(f" +{parsed_amount}")
    if normalized == "diamonds":
        builder.emoji("💎", "currency:diamonds", "diamond")
    elif normalized == "tea":
        builder.emoji("☕", "currency:tea", "tea")
    else:
        builder.text("?")


def render_schedule_announcement(
    target_date: date,
    lots: Sequence[Mapping[str, Any]],
    emoji_ids: Mapping[str, object],
) -> RenderedScheduleAnnouncement:
    """Render the approved visual template while retaining legacy test inputs."""

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

        rarity = _normalize_rarity(lot.get("rarity"))
        if rarity and not whole_deck and not special_asset:
            builder.emoji("🔹", f"rarity:{rarity}")
            builder.text(" ")

        builder.text(schedule_lot_display_name(lot))

        deck_emoji_id = lot.get("deck_emoji_id")
        if deck_emoji_id and not special_asset:
            builder.text(" ")
            builder.emoji_id("🗂", deck_emoji_id)

        if whole_deck:
            _append_reward(
                builder,
                amount=lot.get("deck_diamonds"),
                reward_type="diamonds",
            )
            _append_reward(builder, amount=lot.get("deck_tea"), reward_type="tea")
        else:
            reward_amount = lot.get("obtain_amount")
            reward_type = lot.get("obtain_type")
            if reward_amount in (None, ""):
                # Compatibility with the first implementation and its tests.
                reward_amount = lot.get("start_price")
                reward_type = lot.get("currency")
            _append_reward(builder, amount=reward_amount, reward_type=reward_type)

        comment = _public_comment(lot)
        start_label_text = (
            comment
            if comment.casefold().startswith("за ")
            else _start_currency_label(lot.get("currency"))
        )
        if start_label_text:
            builder.text(f" ({start_label_text})")
        if index != len(sorted_lots) - 1:
            builder.text("\n")

    return builder.build()


def missing_required_emoji_keys(emoji_ids: Mapping[str, object]) -> tuple[str, ...]:
    normalized = {
        normalize_emoji_key(key) for key, value in emoji_ids.items() if _value_emoji_id(value)
    }
    return tuple(sorted(_REQUIRED_EMOJI_KEYS - normalized))


def schedule_configuration_issues(
    lots: Sequence[Mapping[str, Any]],
    assets: Mapping[str, object],
) -> tuple[str, ...]:
    issues: list[str] = []
    for lot in lots:
        auction_id = int(lot.get("auction_id") or 0)
        whole_deck = bool(lot.get("whole_deck"))
        special_asset = special_schedule_asset(lot)
        if special_asset:
            if not _asset_id(assets, special_asset.key):
                issues.append(f"не настроен эмодзи для {special_asset.label}")
            reward_type = _normalize_reward_type(lot.get("obtain_type") or lot.get("currency"))
            if reward_type and not _asset_id(
                assets,
                f"currency:{reward_type}",
                "diamond" if reward_type == "diamonds" else "tea",
            ):
                issues.append(f"не настроен эмодзи награды {reward_type}")
            continue

        deck_id = lot.get("resolved_deck_id") or lot.get("deck_id")
        if not deck_id:
            issues.append(f"лот {auction_id}: не определена колода")
        elif not lot.get("deck_emoji_id"):
            issues.append(f"колода {deck_id}: не настроен Premium-эмодзи")

        if whole_deck:
            if not _asset_id(assets, "whole_deck"):
                issues.append("не настроен общий эмодзи «Вся колода»")
            continue

        if not lot.get("card_id"):
            issues.append(f"лот {auction_id}: карточка не найдена в каталоге")
            continue
        if not lot.get("card_emoji_id"):
            issues.append(f"карта {lot.get('card_id')}: не настроен мини-эмодзи")
        elif not bool(lot.get("card_emoji_verified")):
            issues.append(f"карта {lot.get('card_id')}: эмодзи и экономика не подтверждены")

        rarity = _normalize_rarity(lot.get("rarity"))
        reward_type = _normalize_reward_type(lot.get("obtain_type"))
        expected = _expected_reward(rarity, reward_type)
        try:
            actual = int(lot.get("obtain_amount") or 0)
        except (TypeError, ValueError):
            actual = 0
        if not rarity:
            issues.append(f"карта {lot.get('card_id')}: неизвестная редкость")
        elif not reward_type:
            issues.append(f"карта {lot.get('card_id')}: неизвестный тип награды")
        elif actual != expected:
            issues.append(f"карта {lot.get('card_id')}: награда {actual}, ожидалось {expected}")
        elif not _asset_id(assets, f"rarity:{rarity}"):
            issues.append(f"не настроен эмодзи редкости {rarity}")

        if reward_type and not _asset_id(
            assets,
            f"currency:{reward_type}",
            "diamond" if reward_type == "diamonds" else "tea",
        ):
            issues.append(f"не настроен эмодзи награды {reward_type}")

    return tuple(dict.fromkeys(issues))


async def store_emoji_assignments(
    assignments: Mapping[str, int],
    *,
    config: UserbotSettings,
) -> tuple[str, ...]:
    """Retain the original JSON import command for existing deployments."""

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
    config: UserbotSettings,
) -> RenderedScheduleAnnouncement | None:
    del config
    lots = await get_schedule_lots_for_day(target_date)
    if not lots:
        return None
    assets = await get_emoji_assets()
    return render_schedule_announcement(target_date, lots, assets)


async def send_schedule_review_preview(
    telegram_client: TelegramClient,
    target_date: date,
) -> int | None:
    review = await get_publication_review(target_date)
    if review and review.get("preview_message_id"):
        return int(review["preview_message_id"])

    target = await get_preview_target()
    if not target:
        raise ScheduleEmojiConfigurationError(
            "не задан админский чат: выполните /set расписание в нужной ветке"
        )

    lots = await get_schedule_lots_for_day(target_date)
    if not lots:
        return None
    assets = await get_emoji_assets()
    issues = schedule_configuration_issues(lots, assets)
    if issues:
        raise ScheduleEmojiConfigurationError("; ".join(issues))

    rendered = render_schedule_announcement(target_date, lots, assets)
    buttons = [
        [
            Button.inline(
                "✅ Всё верно",
                data=f"sched:approve:{target_date.isoformat()}".encode(),
            ),
            Button.inline(
                "❌ Отклонить",
                data=f"sched:reject:{target_date.isoformat()}".encode(),
            ),
        ]
    ]
    message = await telegram_client.send_message(
        int(target["chat_id"]),
        rendered.text,
        formatting_entities=list(rendered.entities),
        buttons=buttons,
        link_preview=False,
        reply_to=int(target["thread_id"]) if target.get("thread_id") else None,
    )
    await record_pending_preview(
        target_date,
        chat_id=int(target["chat_id"]),
        thread_id=int(target["thread_id"]) if target.get("thread_id") else None,
        message_id=int(message.id),
    )
    return int(message.id)


async def _approved_preview_message(
    telegram_client: TelegramClient,
    review: Mapping[str, Any],
) -> Any | None:
    chat_id = review.get("preview_chat_id")
    message_id = review.get("preview_message_id")
    if not chat_id or not message_id:
        return None
    preview = await telegram_client.get_messages(int(chat_id), ids=int(message_id))
    if not preview or not getattr(preview, "message", None):
        return None
    return preview


async def publish_schedule_announcement(
    telegram_client: TelegramClient,
    target_date: date,
    *,
    config: UserbotSettings,
) -> int | None:
    review = await get_publication_review(target_date)
    if review and review.get("status") == "published" and review.get("channel_message_id"):
        return int(review["channel_message_id"])

    approved_preview = None
    if review and review.get("status") == "approved":
        approved_preview = await _approved_preview_message(telegram_client, review)
        if approved_preview is None:
            raise ScheduleEmojiConfigurationError(
                "подтверждённое превью не найдено; публикация остановлена"
            )

    if approved_preview is not None:
        publication_text = str(approved_preview.message)
        publication_entities = list(getattr(approved_preview, "entities", None) or ())
    else:
        # Compatibility path for explicit/manual calls made outside the reviewed
        # daily watchdog. The automatic 23:00 flow always uses an approved preview.
        lots = await get_schedule_lots_for_day(target_date)
        if not lots:
            logger.info("No live auctions for %s; schedule announcement not published", target_date)
            return None
        assets = await get_emoji_assets()
        issues = schedule_configuration_issues(lots, assets)
        if config.schedule_announcements_require_custom_emoji and issues:
            raise ScheduleEmojiConfigurationError("; ".join(issues))
        rendered = render_schedule_announcement(target_date, lots, assets)
        publication_text = rendered.text
        publication_entities = list(rendered.entities)

    message = await telegram_client.send_message(
        config.auction_channel_id,
        publication_text,
        formatting_entities=publication_entities,
        link_preview=False,
        send_as=config.auction_channel_id,
    )
    message_id = int(message.id)
    await mark_publication_published(target_date, channel_message_id=message_id)
    logger.info(
        "Published approved Premium schedule announcement for %s as message %s",
        target_date,
        message_id,
    )
    return message_id


async def _send_blocked_preview_notice(
    telegram_client: TelegramClient,
    target_date: date,
    error_text: str,
) -> None:
    target = await get_preview_target()
    if not target:
        return
    trimmed = error_text[:3500]
    await telegram_client.send_message(
        int(target["chat_id"]),
        "⚠️ <b>Превью расписания не собрано</b>\n\n"
        f"Дата: <b>{target_date:%d.%m.%Y}</b>\n"
        f"Причины: {trimmed}\n\n"
        "Исправьте карточки через /schedule_setup и проверьте /schedule_audit.",
        parse_mode="html",
        reply_to=int(target["thread_id"]) if target.get("thread_id") else None,
    )


async def schedule_announcement_watchdog(
    telegram_client: TelegramClient,
    *,
    config: UserbotSettings,
) -> None:
    warned: dict[date, str] = {}
    while True:
        try:
            if config.schedule_announcements_enabled:
                now = datetime.now(MOSCOW)
                preview_date = announcement_target_date(
                    now,
                    hour=_PREVIEW_HOUR,
                    minute=_PREVIEW_MINUTE,
                )
                if preview_date is not None:
                    try:
                        preview_message_id = await send_schedule_review_preview(
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
                            await _send_blocked_preview_notice(
                                telegram_client,
                                preview_date,
                                error_text,
                            )
                            warned[preview_date] = error_text

                publication_date = announcement_target_date(
                    now,
                    hour=config.schedule_announcements_hour,
                    minute=config.schedule_announcements_minute,
                )
                if publication_date is not None:
                    review = await get_publication_review(publication_date)
                    if review and review.get("status") == "approved":
                        await publish_schedule_announcement(
                            telegram_client,
                            publication_date,
                            config=config,
                        )
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
    "schedule_configuration_issues",
    "send_schedule_review_preview",
    "store_emoji_assignments",
    "utf16_length",
]
