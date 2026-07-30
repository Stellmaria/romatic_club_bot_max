from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.core.time import to_moscow
from bot.services.outbox import TelegramOutboxService
from config import ADMINS

router = Router(name="outbox_admin")


async def _require_admin(message: Message) -> bool:
    if message.from_user and message.from_user.id in ADMINS:
        return True
    await message.answer("Нет доступа.")
    return False


def _format_time(value: object) -> str:
    if not isinstance(value, datetime):
        return "—"
    return to_moscow(value).strftime("%d.%m.%Y %H:%M:%S МСК")


def _parse_id_and_note(message: Message) -> tuple[int | None, str | None]:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        return None, None
    return int(parts[1]), parts[2].strip() if len(parts) == 3 else None


async def _send_html_chunks(message: Message, lines: list[str], *, limit: int = 3500) -> None:
    chunk: list[str] = []
    size = 0
    for line in lines:
        added = len(line) + (1 if chunk else 0)
        if chunk and size + added > limit:
            await message.answer("\n".join(chunk), parse_mode="HTML")
            chunk, size = [], 0
        chunk.append(line)
        size += added
    if chunk:
        await message.answer("\n".join(chunk), parse_mode="HTML")


@router.message(Command("outbox_status"), F.chat.type == "private")
async def outbox_status(message: Message) -> None:
    if not await _require_admin(message):
        return
    service = await TelegramOutboxService.create()
    summary = await service.diagnostic_summary()
    lines = ["📬 <b>Telegram outbox</b>"]
    for row in summary["counts"]:
        lines.append(
            f"• <code>{escape(str(row['status']))}</code> / "
            f"<code>{escape(str(row['delivery_state']))}</code>: "
            f"<b>{int(row['count'])}</b>"
        )
    if len(lines) == 1:
        lines.append("Очередь пуста.")
    oldest = summary.get("oldest_pending")
    if oldest:
        lines.extend(
            [
                "",
                f"Самое старое pending: <code>#{int(oldest['outbox_id'])}</code>",
                f"Создано: {_format_time(oldest.get('created_at'))}",
                f"Доступно: {_format_time(oldest.get('available_at'))}",
            ]
        )
    await _send_html_chunks(message, lines)


@router.message(Command("outbox_failed"), F.chat.type == "private")
async def outbox_failed(message: Message) -> None:
    if not await _require_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    limit = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 20
    service = await TelegramOutboxService.create()
    rows = await service.list_failed(limit=limit)
    if not rows:
        await message.answer("✅ Failed-записей нет.")
        return

    lines = ["⚠️ <b>Последние failed-записи</b>"]
    for row in rows:
        certainty = str(row["delivery_state"])
        action = "можно retry" if certainty == "confirmed_not_sent" else "сначала проверить"
        error = escape(str(row.get("last_error") or "—")[:180])
        lines.append(
            f"\n<code>#{int(row['outbox_id'])}</code> · "
            f"{escape(str(row.get('topic') or 'legacy'))} · chat <code>{int(row['chat_id'])}</code>\n"
            f"{escape(certainty)} ({action}), попыток {int(row['attempts'])}/{int(row['max_attempts'])}\n"
            f"{error}"
        )
    lines.extend(
        [
            "",
            "Безопасный повтор: <code>/outbox_retry ID</code>",
            "Подтвердить доставку unknown: <code>/outbox_confirm ID</code>",
        ]
    )
    await _send_html_chunks(message, lines)


@router.message(Command("outbox_retry"), F.chat.type == "private")
async def outbox_retry(message: Message) -> None:
    if not await _require_admin(message):
        return
    outbox_id, note = _parse_id_and_note(message)
    if outbox_id is None:
        await message.answer("Формат: <code>/outbox_retry ID [комментарий]</code>", parse_mode="HTML")
        return
    service = await TelegramOutboxService.create()
    changed = await service.requeue_confirmed_not_sent(
        outbox_id,
        reviewed_by=message.from_user.id,
        note=note,
    )
    if changed:
        await message.answer(f"✅ Запись <code>#{outbox_id}</code> возвращена в pending.", parse_mode="HTML")
    else:
        await message.answer(
            "Повтор заблокирован: запись не failed либо результат доставки не подтверждён как not-sent.",
        )


@router.message(Command("outbox_confirm"), F.chat.type == "private")
async def outbox_confirm(message: Message) -> None:
    if not await _require_admin(message):
        return
    outbox_id, note = _parse_id_and_note(message)
    if outbox_id is None:
        await message.answer("Формат: <code>/outbox_confirm ID [комментарий]</code>", parse_mode="HTML")
        return
    service = await TelegramOutboxService.create()
    changed = await service.confirm_delivered(
        outbox_id,
        reviewed_by=message.from_user.id,
        note=note,
    )
    if changed:
        await message.answer(
            f"✅ Доставка <code>#{outbox_id}</code> подтверждена без повторной отправки.",
            parse_mode="HTML",
        )
    else:
        await message.answer("Подтверждение не применено: нужна failed-запись со статусом unknown.")
