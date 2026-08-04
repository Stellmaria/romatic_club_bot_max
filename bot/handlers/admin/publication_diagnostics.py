from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.core.legacy_config import legacy_config
from bot.services.admin_diagnostics import AdminDiagnosticsQueries

router = Router(name="publication_diagnostics")


@router.message(Command("publication_diag"), F.chat.type == "private")
async def publication_diagnostics(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id not in legacy_config.ADMINS:
        await message.answer("Нет доступа.")
        return

    health = await (await AdminDiagnosticsQueries.create()).auction_publication_health()
    counts = {str(row["status"]): int(row["count"]) for row in health["counts"]}
    lines = [
        "📡 <b>Публикация аукционов</b>",
        f"publishing: <b>{counts.get('publishing', 0)}</b>",
        f"deferred: <b>{counts.get('publication_deferred', 0)}</b>",
        f"failed: <b>{counts.get('publication_failed', 0)}</b>",
        f"несовместимые строки: <b>{len(health['invalid'])}</b>",
    ]
    oldest = health.get("oldest_deferred")
    if oldest:
        lines.append(
            "самая старая deferred: "
            f"<code>{int(oldest['auction_id'])}</code> · "
            f"{escape(str(oldest.get('age') or '—'))}"
        )
    for row in health["invalid"][:20]:
        lines.append(
            "⚠️ "
            f"<code>{int(row['auction_id'])}</code> "
            f"{escape(str(row['status']))} / "
            f"message_id={escape(str(row.get('message_id')))}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
