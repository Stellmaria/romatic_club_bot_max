from __future__ import annotations

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup


async def answer_media_any(
    message: types.Message,
    file_id: str,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    protect_content: bool = False,
) -> types.Message | None:
    """Send an existing Telegram file as photo, video or animation."""
    media_id = (file_id or "").strip()
    if not media_id:
        return None

    attempts = (
        message.answer_photo,
        message.answer_video,
        message.answer_animation,
    )
    argument_names = ("photo", "video", "animation")
    for sender, argument_name in zip(attempts, argument_names):
        try:
            return await sender(
                **{argument_name: media_id},
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                protect_content=protect_content,
            )
        except Exception:
            continue
    return None


async def bot_send_media_any(
    bot: Bot,
    *,
    chat_id: int | str,
    file_id: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
    disable_notification: bool = False,
    protect_content: bool = False,
    raise_on_failure: bool = False,
) -> types.Message | None:
    """Send a Telegram file as photo, video or animation.

    ``raise_on_failure`` is intended for durable publication flows. It prevents
    an unknown delivery error from being converted into a successful text-only
    post and avoids trying another media method after a network-style failure.
    """
    media_id = (file_id or "").strip()
    if not media_id:
        return None

    last_error: Exception | None = None

    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=media_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
            protect_content=protect_content,
        )
    except TelegramBadRequest as exc:
        last_error = exc
        message = str(exc).lower()
        if not any(
            marker in message
            for marker in ("video as photo", "type video", "animation as photo")
        ):
            raise
    except Exception as exc:
        if raise_on_failure:
            raise
        last_error = exc

    try:
        return await bot.send_video(
            chat_id=chat_id,
            video=media_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            supports_streaming=True,
            disable_notification=disable_notification,
            protect_content=protect_content,
        )
    except TelegramBadRequest as exc:
        last_error = exc
    except Exception as exc:
        if raise_on_failure:
            raise
        last_error = exc

    try:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=media_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
            protect_content=protect_content,
        )
    except Exception as exc:
        last_error = exc

    if raise_on_failure and last_error is not None:
        raise last_error
    return None


async def safe_send_media(
    bot: Bot,
    *,
    chat_id: int,
    file_id: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
    protect_content: bool = False,
) -> bool:
    """Send media when possible, falling back to an HTML text message."""
    media_id = (file_id or "").strip()
    if media_id:
        attempts = (
            (bot.send_photo, "photo", {}),
            (bot.send_video, "video", {"supports_streaming": True}),
            (bot.send_animation, "animation", {}),
        )
        for sender, argument_name, extra in attempts:
            try:
                await sender(
                    chat_id,
                    **{argument_name: media_id},
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    protect_content=protect_content,
                    **extra,
                )
                return True
            except Exception:
                continue

    await bot.send_message(
        chat_id,
        caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        protect_content=protect_content,
    )
    return False
