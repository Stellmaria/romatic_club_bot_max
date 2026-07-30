from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.exchange_diagnostics import ExchangeDiagnosticsService
from ..common import h

router = Router(name="auction_exchange_diagnostics_media")

@router.message(Command("fileid"), F.chat.type == "private")
async def cmd_fileid(message: Message):
    """Админская команда: ответь на медиа и получи file_id/unique_id.
    Работает для video/animation/photo/document/voice/video_note/sticker.
    """
    diagnostics = await ExchangeDiagnosticsService.create()
    if not await diagnostics.is_admin(int(message.from_user.id)):
        return

    rep = message.reply_to_message
    if not rep:
        await message.answer("Ответь на сообщение с медиа (видео/фото/гиф/документ) и напиши /fileid.")
        return

    kind = None
    file_id = None
    unique_id = None

    if rep.video:
        kind = "video"
        file_id = rep.video.file_id
        unique_id = rep.video.file_unique_id
    elif rep.animation:
        kind = "animation"
        file_id = rep.animation.file_id
        unique_id = rep.animation.file_unique_id
    elif rep.photo:
        kind = "photo"
        ph = rep.photo[-1]
        file_id = ph.file_id
        unique_id = ph.file_unique_id
    elif rep.document:
        kind = "document"
        file_id = rep.document.file_id
        unique_id = rep.document.file_unique_id
    elif rep.voice:
        kind = "voice"
        file_id = rep.voice.file_id
        unique_id = rep.voice.file_unique_id
    elif rep.video_note:
        kind = "video_note"
        file_id = rep.video_note.file_id
        unique_id = rep.video_note.file_unique_id
    elif rep.sticker:
        kind = "sticker"
        file_id = rep.sticker.file_id
        unique_id = rep.sticker.file_unique_id

    if not file_id:
        await message.answer("Не вижу медиа в ответе. Нужен reply на видео/фото/гиф/документ.")
        return

    await message.answer(
        f"✅ <b>{kind}</b>\n"
        f"<b>file_id:</b> <code>{h(file_id, '')}</code>\n"
        f"<b>unique_id:</b> <code>{h(unique_id, '')}</code>",
        parse_mode="HTML",
    )
