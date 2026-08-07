from html import escape

from aiogram import F, Router, types
from aiogram.filters import Command

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.custom_emojis import CustomEmojiService

router = Router(name="emoji_setup")


def _first_custom_emoji_id(msg: types.Message) -> str | None:
    for entity in msg.entities or []:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            return entity.custom_emoji_id
    for entity in getattr(msg, "caption_entities", None) or []:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            return entity.custom_emoji_id
    sticker = getattr(msg, "sticker", None)
    if (
        sticker
        and getattr(sticker, "is_custom_emoji", False)
        and getattr(sticker, "custom_emoji_id", None)
    ):
        return sticker.custom_emoji_id
    return None


@router.message(Command("em_migrate"), F.chat.type == "private")
@admin_only
async def em_migrate(message: types.Message) -> None:
    try:
        await (await CustomEmojiService.create()).ensure_schema()
        await message.answer("✅ Таблица custom_emojis готова.")
    except Exception as exc:  # noqa: BLE001 - report operator-facing setup failures
        await message.answer(
            f"❌ Не удалось создать таблицу: <code>{escape(str(exc))}</code>",
            parse_mode="HTML",
        )


@router.message(Command("em_add"), F.chat.type == "private")
@admin_only
async def em_add(message: types.Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not message.reply_to_message:
        await message.answer("Ответь на СООБЩЕНИЕ с прем-эмодзи и напиши: /em_add <name>")
        return
    name = parts[1].strip().lower()
    emoji_id = _first_custom_emoji_id(message.reply_to_message)
    if not emoji_id:
        await message.answer(
            "В реплае не вижу premium custom emoji. "
            "Пришли именно прем-эмодзи, не обычный Unicode."
        )
        return
    try:
        await (await CustomEmojiService.create()).save(name, emoji_id)
    except Exception as exc:  # noqa: BLE001 - admin gets the storage error in chat
        await message.answer(
            "❌ Ошибка БД: <code>{}</code>\n"
            "Подозрение: нет таблицы — запусти /em_migrate.".format(escape(str(exc))),
            parse_mode="HTML",
        )
        return
    await message.answer(
        f"✅ Сохранено: {name} → <code>{emoji_id}</code>",
        parse_mode="HTML",
    )


@router.message(Command("em_list"), F.chat.type == "private")
@admin_only
async def em_list(message: types.Message) -> None:
    try:
        rows = await (await CustomEmojiService.create()).list_all()
    except Exception as exc:  # noqa: BLE001 - admin gets the storage error in chat
        await message.answer(
            f"❌ Ошибка БД: <code>{escape(str(exc))}</code>",
            parse_mode="HTML",
        )
        return
    if not rows:
        await message.answer("Пусто. Добавь через /em_add по реплаю на прем-эмодзи.")
        return
    text = "<b>Сохранённые эмодзи</b>\n" + "\n".join(
        f"• <b>{row['name']}</b> → <code>{row['emoji_id']}</code>" for row in rows
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("em_del"), F.chat.type == "private")
@admin_only
async def em_del(message: types.Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /em_del <name>")
        return
    name = parts[1].strip().lower()
    deleted_name = await (await CustomEmojiService.create()).delete(name)
    await message.answer(
        f"🗑 Удалено: {deleted_name}" if deleted_name else "Такого имени нет."
    )
