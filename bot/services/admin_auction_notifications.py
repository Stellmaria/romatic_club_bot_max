"""Owner notifications for administrative auction changes."""

from typing import Any

from aiogram import Bot
from aiogram.types import User

from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.telegram.media import bot_send_media_any
from db.auctions import get_lot_by_id, get_lot_owners


async def notify_owners_lot_changed(
    bot: Bot,
    *,
    auction_id: int,
    admin_user: User,
    title: str,
    changes: list[tuple[str, object, object]] | None = None,
    stage_label: str | None = None,
    body: str | None = None,
    text: str | None = None,
    **_ignored: Any,
) -> None:
    """Notify every unique owner after an administrative lot change.

    Both the historical ``changes`` payload and the newer pre-rendered
    ``body``/``text`` payload are accepted while old FSM callbacks drain.
    """

    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners(int(auction_id))
    if not lot or not owners:
        return

    moderator_tag = admin_tag(admin_user)
    thanks_keyboard = await build_thanks_kb(int(auction_id), moderator_tag)

    def render_value(value: object) -> str:
        if value is None:
            return "—"
        rendered = str(value).strip()
        return rendered or "—"

    final_body = body if body is not None else (text or "")
    if changes:
        lines = [
            f"• <b>{render_value(field)}:</b> "
            f"<code>{render_value(old)}</code> → "
            f"<code>{render_value(new)}</code>"
            for field, old, new in changes
        ]
        change_block = "<b>Что изменили:</b>\n" + "\n".join(lines)
    elif final_body.strip():
        change_block = final_body.strip()
    else:
        change_block = "<b>Что изменили:</b>\n• —"

    caption = (
        f"🛠 <b>{title}</b>\n\n"
        f"Лот: <b>{lot.get('card_name') or '—'}</b> — "
        f"<i>{lot.get('hero_name') or '—'}</i>\n"
        f"ID: <code>{auction_id}</code>\n"
        f"Статус: <b>{(stage_label or '').strip() or '—'}</b>\n\n"
        f"{change_block}\n\n"
        f"👤 <b>Кто изменил:</b> {moderator_tag}\n"
        "Если хочешь, можешь сказать спасибо ниже ❤️\n"
    )

    media_id = lot.get("image_id") or lot.get("photo_id")
    sent: set[int] = set()
    for owner in owners:
        try:
            user_id = int(owner["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if user_id in sent:
            continue
        sent.add(user_id)
        try:
            await bot_send_media_any(
                bot,
                chat_id=user_id,
                file_id=media_id,
                caption=caption,
                reply_markup=thanks_keyboard,
            )
        except Exception:
            # A blocked bot or unavailable private chat must not roll back the
            # already committed moderation change.
            continue


__all__ = ("notify_owners_lot_changed",)
