from __future__ import annotations

import html
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.admin.helper.new.admin_actions import send_admin_log
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.auction_media import (
    configure_media_asset,
    get_configured_media,
    remove_media_asset,
    resolve_media_asset,
)
from bot.domain.media_assets import VALID_MEDIA_TYPES
from db.repositories.admin import log_audit_action

router = Router(name="admin_media_assets")


@dataclass(frozen=True)
class ExtractedMedia:
    file_id: str
    media_type: str
    file_unique_id: str | None = None
    thumb_file_id: str | None = None


def _extract_media(message: Message) -> ExtractedMedia | None:
    if message.photo:
        item = message.photo[-1]
        return ExtractedMedia(item.file_id, "photo", item.file_unique_id)
    if message.video:
        thumb = message.video.thumbnail.file_id if message.video.thumbnail else None
        return ExtractedMedia(message.video.file_id, "video", message.video.file_unique_id, thumb)
    if message.animation:
        thumb = message.animation.thumbnail.file_id if message.animation.thumbnail else None
        return ExtractedMedia(message.animation.file_id, "animation", message.animation.file_unique_id, thumb)
    if message.document:
        media_type = "video" if (message.document.mime_type or "").startswith("video/") else "document"
        return ExtractedMedia(message.document.file_id, media_type, message.document.file_unique_id)
    return None


def _raw_command(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _usage() -> str:
    return (
        "<b>Настройка медиа аукциона</b>\n\n"
        "<code>/set_media deck 22 FILE_ID video</code>\n"
        "<code>/set_media card 145 FILE_ID video</code>\n"
        "<code>/set_media auction 301 FILE_ID photo</code>\n"
        "<code>/set_media rarity gold FILE_ID video</code>\n"
        "<code>/set_media service friends_plus FILE_ID video</code>\n"
        "<code>/set_media spins 50 FILE_ID video</code>\n\n"
        "Для колоды есть короткая форма:\n"
        "<code>/deck_media 22 FILE_ID video</code>\n\n"
        "Можно отправить команду подписью к медиа или ответом на сообщение с медиа. "
        "Тогда FILE_ID указывать не нужно.\n\n"
        "Просмотр: <code>/get_media deck 22</code>\n"
        "Удаление: <code>/delete_media deck 22</code>\n"
        "Список: <code>/media_list [deck]</code>"
    )


def _short_file_id(file_id: str) -> str:
    value = (file_id or "").strip()
    if len(value) <= 30:
        return value
    return f"{value[:14]}…{value[-10:]}"


@router.message(
    F.caption.regexp(r"^/(?:set_media|setmedia|deck_media)(?:@\w+)?(?:\s|$)"),
    F.chat.type == "private",
)
@router.message(Command("set_media", "setmedia", "deck_media"), F.chat.type == "private")
@admin_only
async def cmd_set_media(message: Message) -> None:
    raw = _raw_command(message)
    parts = raw.split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    source = _extract_media(message.reply_to_message or message)

    if command == "/deck_media":
        if len(parts) < 2:
            await message.answer(_usage(), parse_mode="HTML")
            return
        target_kind = "deck"
        target_key = parts[1]
        tail = parts[2:]
    else:
        if len(parts) < 3:
            await message.answer(_usage(), parse_mode="HTML")
            return
        target_kind = parts[1]
        target_key = parts[2]
        tail = parts[3:]

    explicit_type: str | None = None
    if source:
        file_id = source.file_id
        if tail and tail[0].lower() in VALID_MEDIA_TYPES | {"фото", "видео", "gif", "картинка"}:
            explicit_type = tail[0]
        media_type = explicit_type or source.media_type
        unique_id = source.file_unique_id
        thumb_id = source.thumb_file_id
    else:
        if not tail:
            await message.answer("Не указан FILE_ID.\n\n" + _usage(), parse_mode="HTML")
            return
        file_id = tail[0]
        explicit_type = tail[1] if len(tail) > 1 else None
        media_type = explicit_type
        unique_id = None
        thumb_id = None

    try:
        result = await configure_media_asset(
            target_kind=target_kind,
            target_key=target_key,
            file_id=file_id,
            media_type=media_type,
            file_unique_id=unique_id,
            thumb_file_id=thumb_id,
            updated_by=message.from_user.id if message.from_user else None,
        )
    except LookupError as exc:
        errors = {
            "deck_not_found": "Колода не найдена.",
            "card_not_found": "Карта не найдена.",
            "auction_not_found": "Аукцион не найден.",
        }
        await message.answer(errors.get(str(exc), f"Объект не найден: <code>{html.escape(str(exc))}</code>"), parse_mode="HTML")
        return
    except ValueError as exc:
        errors = {
            "unsupported_target_kind": "Неизвестный тип цели.",
            "target_key_must_be_integer": "ID должен быть числом.",
            "target_key_must_be_positive": "ID должен быть больше нуля.",
            "unsupported_media_type": "Тип медиа: photo, video, animation или document.",
            "unsupported_rarity": "Редкость: bronze, silver, gold, diamond или any.",
            "empty_file_id": "FILE_ID пустой.",
        }
        await message.answer(errors.get(str(exc), f"Ошибка параметров: <code>{html.escape(str(exc))}</code>"), parse_mode="HTML")
        return
    except Exception as exc:
        await message.answer(
            "Не удалось сохранить медиа. Проверь, что миграция 008 применена.\n"
            f"<code>{html.escape(str(exc).splitlines()[0][:500])}</code>",
            parse_mode="HTML",
        )
        return

    warning = ""
    if result.get("sync_warning"):
        warning = (
            "\n\n⚠️ Запись в реестре сохранена, но старое поле сущности не обновилось:\n"
            f"<code>{html.escape(str(result['sync_warning']))}</code>"
        )

    await message.answer(
        "✅ <b>Медиа сохранено в базе</b>\n\n"
        f"Цель: <b>{html.escape(str(result['description']))}</b>\n"
        f"Ключ: <code>{html.escape(str(result['target_kind']))}:{html.escape(str(result['target_key']))}</code>\n"
        f"Тип: <code>{html.escape(str(result['media_type']))}</code>\n"
        f"file_id: <code>{html.escape(str(result['file_id']))}</code>\n"
        f"Синхронизировано старых строк: <b>{int(result.get('synced_rows') or 0)}</b>"
        f"{warning}",
        parse_mode="HTML",
    )

    try:
        actor = message.from_user
        actor_text = f"@{actor.username}" if actor and actor.username else f"id:{actor.id if actor else 0}"
        await send_admin_log(
            message.bot,
            "🎞️ <b>Настроено медиа аукциона</b>\n"
            f"Админ: <b>{html.escape(actor_text)}</b>\n"
            f"Цель: <code>{html.escape(str(result['target_kind']))}:{html.escape(str(result['target_key']))}</code>\n"
            f"Тип: <code>{html.escape(str(result['media_type']))}</code>\n"
            f"file_id: <code>{html.escape(_short_file_id(str(result['file_id'])))}</code>",
        )
        if actor:
            await log_audit_action(
                user_id=actor.id,
                action_type="set_auction_media",
                auction_id=int(result["target_key"]) if result["target_kind"] == "auction" else None,
                details=(
                    f"target={result['target_kind']}:{result['target_key']} "
                    f"media_type={result['media_type']} file_id={result['file_id']}"
                ),
            )
    except Exception:
        pass


@router.message(Command("get_media"), F.chat.type == "private")
@admin_only
async def cmd_get_media(message: Message) -> None:
    parts = _raw_command(message).split()
    if len(parts) != 3:
        await message.answer("Формат: <code>/get_media deck 22</code>", parse_mode="HTML")
        return
    try:
        asset = await resolve_media_asset(parts[1], parts[2])
    except Exception as exc:
        await message.answer(f"Ошибка: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return
    if not asset:
        await message.answer("Для этой цели отдельное медиа не настроено.")
        return
    await message.answer(
        f"Цель: <code>{html.escape(asset['target_kind'])}:{html.escape(asset['target_key'])}</code>\n"
        f"Тип: <code>{html.escape(asset['media_type'])}</code>\n"
        f"file_id: <code>{html.escape(asset['file_id'])}</code>",
        parse_mode="HTML",
    )


@router.message(Command("delete_media"), F.chat.type == "private")
@admin_only
async def cmd_delete_media(message: Message) -> None:
    parts = _raw_command(message).split()
    if len(parts) != 3:
        await message.answer("Формат: <code>/delete_media deck 22</code>", parse_mode="HTML")
        return
    try:
        deleted = await remove_media_asset(parts[1], parts[2])
    except Exception as exc:
        await message.answer(f"Ошибка: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return
    await message.answer("✅ Настройка удалена." if deleted else "Настройка не найдена.")


@router.message(Command("media_list"), F.chat.type == "private")
@admin_only
async def cmd_media_list(message: Message) -> None:
    parts = _raw_command(message).split()
    kind = parts[1] if len(parts) > 1 else None
    try:
        rows = await get_configured_media(kind)
    except Exception as exc:
        await message.answer(f"Ошибка: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return
    if not rows:
        await message.answer("Настроенных медиа пока нет.")
        return
    lines = ["<b>Настроенные медиа:</b>"]
    for row in rows[:100]:
        lines.append(
            f"• <code>{html.escape(row['target_kind'])}:{html.escape(row['target_key'])}</code> "
            f"— {html.escape(row['media_type'])} — <code>{html.escape(_short_file_id(row['file_id']))}</code>"
        )
    if len(rows) > 100:
        lines.append(f"…ещё {len(rows) - 100}")
    await message.answer("\n".join(lines), parse_mode="HTML")
