"""Winner and owner direct-message delivery."""

from __future__ import annotations

from aiogram import Bot, types

from .common import _admin_tag, _safe_pin_pm_message, _send_media_any, get_user, logger
from .feedback import _thanks_kb
from .presentation import _add_win_mailing, _build_print_win_context, _compose_user_win_text


async def _send_win_dm_to_targets(
    bot: Bot,
    *,
    auction_id: int,
    target: str,  # 'owner' | 'winner' | 'both'
    admin_user: types.User,
) -> tuple[int, int, list[dict], int | None]:
    """
    Отправляет ЛС победителю и/или владельцу.
    После успешной отправки закрепляет именно это сообщение в ЛС.
    Возвращает (ok, fail, deliveries, used_amount).
    """
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        return (
            0,
            1,
            [
                {
                    "role": "error",
                    "user_id": 0,
                    "username": None,
                    "ok": False,
                    "err": ctx.get("err"),
                    "pinned": False,
                }
            ],
            None,
        )

    moderator_tag = _admin_tag(admin_user)

    text = await _compose_user_win_text(
        auction_id=auction_id,
        link=str(ctx["link"]),
        lot_line=str(ctx["lot_line"]),
        amount=ctx.get("amount"),
        cur_emoji=str(ctx["cur_emoji"]),
        winner_user_id=ctx.get("winner_user_id"),
        winner_username=ctx.get("winner_username"),
        owner_mentions=str(ctx.get("owner_mentions") or "—"),
        moderator_tag=moderator_tag,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
        moderator_comment=ctx.get("moderator_comment"),
    )
    kb = await _thanks_kb(auction_id, moderator_tag)

    ok = 0
    fail = 0
    deliveries: list[dict] = []

    async def _try_send(uid: int, role: str, username: str | None):
        nonlocal ok, fail, deliveries
        try:
            photo = ctx.get("photo")
            sent_msg = None

            if photo and len(text) <= 900:
                sent_msg = await _send_media_any(
                    bot,
                    uid,
                    str(photo),
                    text,
                    reply_markup=kb,
                )
            else:
                sent_msg = await bot.send_message(
                    uid,
                    text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )

            pinned = False
            if sent_msg and getattr(sent_msg, "message_id", None):
                pinned = await _safe_pin_pm_message(bot, uid, sent_msg.message_id)

            try:
                await _add_win_mailing(auction_id, role, admin_user)
            except Exception as e:
                logger.warning(
                    "[print_win] не удалось записать mailing auction_id=%s role=%s uid=%s: %r",
                    auction_id,
                    role,
                    uid,
                    e,
                )

            ok += 1
            deliveries.append(
                {
                    "role": role,
                    "user_id": uid,
                    "username": username,
                    "ok": True,
                    "err": None,
                    "pinned": pinned,
                }
            )
        except Exception as e:
            fail += 1
            deliveries.append(
                {
                    "role": role,
                    "user_id": uid,
                    "username": username,
                    "ok": False,
                    "err": str(e),
                    "pinned": False,
                }
            )

    if target in {"winner", "both"}:
        wid = ctx.get("winner_user_id")
        if wid:
            u = await get_user(int(wid)) or {}
            await _try_send(int(wid), "winner", u.get("username"))
        else:
            fail += 1
            deliveries.append(
                {
                    "role": "winner",
                    "user_id": 0,
                    "username": None,
                    "ok": False,
                    "err": "winner not set",
                    "pinned": False,
                }
            )

    if target in {"owner", "both"}:
        for oid in ctx.get("owner_user_ids") or []:
            if not oid:
                continue
            u = await get_user(int(oid)) or {}
            await _try_send(int(oid), "owner", u.get("username"))

    return ok, fail, deliveries, ctx.get("amount")
