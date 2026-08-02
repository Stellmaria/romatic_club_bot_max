"""Owner commands and approval callbacks for Premium schedule publication."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from telethon import events

from bot.core.legacy_config import legacy_config
from bot.core.time import MOSCOW
from userbot.schedule_announcements import (
    extract_custom_emoji_assignments,
    missing_required_emoji_keys,
    preview_schedule_announcement,
    store_emoji_assignments,
)
from userbot.schedule_review_service import (
    decide_schedule_review,
    get_schedule_review,
    schedule_review_snapshot,
)

logger = logging.getLogger("userbot.schedule_admin")


async def _is_authorized(event: object) -> bool:
    if getattr(event, "out", False):
        return True
    sender_id = getattr(event, "sender_id", None)
    allowed = set(legacy_config.ADMINS_OWNERS or legacy_config.ADMINS)
    return bool(sender_id and int(sender_id) in allowed)


async def on_schedule_admin_command(event: events.NewMessage.Event) -> None:
    if not getattr(event, "is_private", False):
        return
    if not await _is_authorized(event):
        return

    text = str(getattr(event.message, "message", None) or "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()

    if command == "/schedule_emojis":
        source = await event.get_reply_message()
        if source is None:
            await event.reply(
                "Массовый шаблон оставлен для совместимости. "
                "Новый каталог настраивается в основном боте командой /schedule_setup.\n\n"
                "Для импорта ответьте на сообщение вида:\n"
                "header = 🦋\ncard = 🎴\ndiamond = 💎\ntea = ☕"
            )
            return

        assignments = extract_custom_emoji_assignments(source)
        if not assignments:
            await event.reply("В сообщении не найдено ни одного кастомного эмодзи.")
            return

        stored_keys = await store_emoji_assignments(assignments, config=legacy_config)
        missing = missing_required_emoji_keys(
            {key: assignments.get(key, 1) for key in stored_keys}
        )
        suffix = (
            "\n\nСтарый обязательный набор готов."
            if not missing
            else "\n\nВ старом наборе ещё нужны ключи: " + ", ".join(missing)
        )
        await event.reply(
            "Сохранены совместимые Premium-эмодзи: "
            + ", ".join(sorted(assignments))
            + suffix
        )
        return

    if command == "/schedule_preview":
        target_date = datetime.now(MOSCOW).date() + timedelta(days=1)
        try:
            rendered = await preview_schedule_announcement(
                target_date,
                config=legacy_config,
            )
        except Exception:
            logger.exception(
                "Failed to render manual schedule preview for %s",
                target_date,
            )
            await event.reply(
                "❌ Не удалось собрать превью расписания. "
                "Ошибка записана в журнал userbot."
            )
            return
        if rendered is None:
            await event.reply(f"На {target_date:%d.%m.%Y} нет живых лотов.")
            return
        await event.client.send_message(
            event.chat_id,
            rendered.text,
            formatting_entities=list(rendered.entities),
            link_preview=False,
        )
        return

    if command == "/schedule_status":
        target_date = datetime.now(MOSCOW).date() + timedelta(days=1)
        target, review = await schedule_review_snapshot(target_date)
        target_text = (
            f"чат {target['chat_id']}, ветка {target.get('thread_id') or 'основная'}"
            if target
            else "не задано"
        )
        review_text = str(review.get("status")) if review else "превью ещё не создано"
        await event.reply(
            "Автопубликация расписания: "
            + ("включена" if legacy_config.SCHEDULE_ANNOUNCEMENTS_ENABLED else "выключена")
            + "\nПревью: 22:30 МСК"
            + f"\nПубликация: {legacy_config.SCHEDULE_ANNOUNCEMENTS_HOUR:02d}:"
            f"{legacy_config.SCHEDULE_ANNOUNCEMENTS_MINUTE:02d} МСК"
            + f"\nАдминская ветка: {target_text}"
            + f"\nСтатус на {target_date:%d.%m.%Y}: {review_text}"
        )


async def on_schedule_review_callback(event: events.CallbackQuery.Event) -> None:
    if not await _is_authorized(event):
        await event.answer("Нет доступа", alert=True)
        return

    try:
        raw = bytes(event.data).decode("utf-8")
        _, action, raw_date = raw.split(":", 2)
        target_date = date.fromisoformat(raw_date)
    except (ValueError, TypeError):
        await event.answer("Некорректная кнопка", alert=True)
        return

    review = await get_schedule_review(target_date)
    if not review:
        await event.answer("Превью уже устарело или не найдено", alert=True)
        return
    if review.get("status") == "published":
        await event.answer("Расписание уже опубликовано", alert=True)
        return

    if action == "approve":
        await decide_schedule_review(
            target_date,
            approved=True,
            reviewed_by=int(event.sender_id),
        )
        await event.answer("Расписание подтверждено")
        status_text = (
            f"✅ Расписание на {target_date:%d.%m.%Y} подтверждено. "
            f"Публикация произойдёт в {legacy_config.SCHEDULE_ANNOUNCEMENTS_HOUR:02d}:"
            f"{legacy_config.SCHEDULE_ANNOUNCEMENTS_MINUTE:02d} МСК."
        )
    elif action == "reject":
        await decide_schedule_review(
            target_date,
            approved=False,
            reviewed_by=int(event.sender_id),
        )
        await event.answer("Публикация отклонена")
        status_text = (
            f"❌ Расписание на {target_date:%d.%m.%Y} отклонено и в канал не уйдёт."
        )
    else:
        await event.answer("Неизвестное действие", alert=True)
        return

    await event.client.send_message(
        event.chat_id,
        status_text,
        reply_to=int(event.message_id),
    )


__all__ = ["on_schedule_admin_command", "on_schedule_review_callback"]
