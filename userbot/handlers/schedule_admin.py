"""Owner commands and approval callbacks for Premium schedule publication."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from telethon import events

from bot.core.settings import ADMINS, ADMINS_OWNERS, settings
from bot.core.time import MOSCOW
from db.schedule_setup import (
    get_preview_target,
    get_publication_review,
    set_publication_review_status,
)
from userbot.schedule_announcements import (
    extract_custom_emoji_assignments,
    missing_required_emoji_keys,
    preview_schedule_announcement,
    store_emoji_assignments,
)


async def _is_authorized(event: object) -> bool:
    if getattr(event, "out", False):
        return True
    sender_id = getattr(event, "sender_id", None)
    allowed = set(ADMINS_OWNERS or ADMINS)
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

        stored_keys = await store_emoji_assignments(assignments)
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
        rendered = await preview_schedule_announcement(target_date)
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
        target = await get_preview_target()
        target_date = datetime.now(MOSCOW).date() + timedelta(days=1)
        review = await get_publication_review(target_date)
        target_text = (
            f"чат {target['chat_id']}, ветка {target.get('thread_id') or 'основная'}"
            if target
            else "не задано"
        )
        review_text = str(review.get("status")) if review else "превью ещё не создано"
        await event.reply(
            "Автопубликация расписания: "
            + ("включена" if settings.schedule_announcements_enabled else "выключена")
            + "\nПревью: 22:30 МСК"
            + f"\nПубликация: {settings.schedule_announcements_hour:02d}:"
            f"{settings.schedule_announcements_minute:02d} МСК"
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

    review = await get_publication_review(target_date)
    if not review:
        await event.answer("Превью уже устарело или не найдено", alert=True)
        return
    if review.get("status") == "published":
        await event.answer("Расписание уже опубликовано", alert=True)
        return

    if action == "approve":
        await set_publication_review_status(
            target_date,
            status="approved",
            reviewed_by=int(event.sender_id),
        )
        await event.answer("Расписание подтверждено")
        status_text = (
            f"✅ Расписание на {target_date:%d.%m.%Y} подтверждено. "
            f"Публикация произойдёт в {settings.schedule_announcements_hour:02d}:"
            f"{settings.schedule_announcements_minute:02d} МСК."
        )
    elif action == "reject":
        await set_publication_review_status(
            target_date,
            status="rejected",
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
