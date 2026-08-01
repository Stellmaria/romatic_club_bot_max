"""Pure Telegram media extraction helpers for administrative adapters."""

from aiogram import types


def extract_media_file_id(message: types.Message) -> str | None:
    """Return the supported media file ID carried by ``message``."""

    if message.photo:
        return message.photo[-1].file_id
    if message.video:
        return message.video.file_id
    if message.animation:
        return message.animation.file_id
    document = message.document
    if document and (document.mime_type or "").startswith("video/"):
        return document.file_id
    return None


__all__ = ("extract_media_file_id",)
