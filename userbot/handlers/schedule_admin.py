"""Private owner commands for Premium schedule announcement setup."""

from __future__ import annotations

from datetime import datetime, timedelta

from telethon import events

from bot.core.settings import ADMINS, ADMINS_OWNERS, settings
from bot.core.time import MOSCOW
from userbot.schedule_announcements import (
    extract_custom_emoji_assignments,
    missing_required_emoji_keys,
    preview_schedule_announcement,
    store_emoji_assignments,
)


async def _is_authorized(event: events.NewMessage.Event) -> bool:
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
                "Ответьте этой командой на сообщение-шаблон вида:\n\n"
                "header = 🦋\n"
                "card = 🎴\n"
                "diamond = 💎\n"
                "tea = ☕\n"
                "hero:Сонхва = 🧑\n\n"
                "Справа должны стоять именно кастомные Premium-эмодзи."
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
            "\n\nОбязательный набор готов."
            if not missing
            else "\n\nЕщё нужны ключи: " + ", ".join(missing)
        )
        await event.reply(
            "Сохранены Premium-эмодзи: " + ", ".join(sorted(assignments)) + suffix
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
        state_path = settings.schedule_announcement_state_file
        await event.reply(
            "Автопубликация расписания: "
            + ("включена" if settings.schedule_announcements_enabled else "выключена")
            + f"\nВремя: {settings.schedule_announcements_hour:02d}:"
            f"{settings.schedule_announcements_minute:02d} МСК"
            + f"\nФайл состояния: {state_path}"
        )


__all__ = ["on_schedule_admin_command"]
