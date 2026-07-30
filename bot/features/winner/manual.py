"""Manual winner-result field editing workflow."""

from __future__ import annotations

from aiogram import Bot, types

from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

from .common import PENDING_WIN_FIELD_EDIT, _admin_tag, _cb_last_int, _log_admin
from .presentation import (
    _get_manual_result,
    _refresh_print_win_menu_by_ids,
    _resolve_user_ref,
    _upsert_manual_result,
)


def _parse_amount_text(raw: str) -> int | None:
    txt = (raw or "").strip().replace(" ", "").lower().replace("к", "k")
    if not txt:
        return None
    if txt.endswith("k"):
        base = txt[:-1]
        if not base.isdigit():
            return None
        return int(base) * 1000
    if not txt.isdigit():
        return None
    return int(txt)


async def cb_print_win_edit_manual_winner(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "winner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "🏆 <b>Сменить победителя</b>\n\n"
        "Пришли <code>@username</code> или числовой <code>id</code>.\n"
        "Если победителя нет (ставок не было) — пришли <code>-</code>.",
        parse_mode="HTML",
    )


async def cb_print_win_edit_manual_owner(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "owner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "👑 <b>Сменить владельца</b>\n\n"
        "Пришли <code>@username</code> или числовой <code>id</code>.\n"
        "Чтобы сбросить ручного владельца и брать из <code>auction_owners</code> — пришли <code>-</code>.",
        parse_mode="HTML",
    )


async def cb_print_win_edit_manual_amount(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "amount",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "💰 <b>Сменить цену</b>\n\n"
        "Пришли число (можно <code>6700</code> или <code>6k</code>).\n"
        "Чтобы сбросить ручную цену и брать из ставок — пришли <code>-</code>.",
        parse_mode="HTML",
    )


async def cb_print_win_clear_manual(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    await winner_repository.delete_manual_result(await get_db_pool(), auction_id)

    await call.answer("🧹 Ручной итог сброшен.")
    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        auction_id=auction_id,
        admin_user=call.from_user,
    )


async def msg_print_win_edit_single_field(message: types.Message, bot: Bot):
    st = PENDING_WIN_FIELD_EDIT.pop(message.from_user.id, None)
    if not st:
        return

    auction_id = int(st["auction_id"])
    field = st["field"]
    raw = (message.text or "").strip()

    prev = await _get_manual_result(auction_id) or {}
    winner_user_id = prev.get("winner_user_id")
    winner_username = prev.get("winner_username")
    owner_user_id = prev.get("owner_user_id")
    owner_username = prev.get("owner_username")
    amount = prev.get("amount")
    moderator_comment_prev = prev.get("moderator_comment")

    moderator_comment_new: str | None = None  # None = не трогаем (COALESCE сохранит старое)

    if field == "winner":
        if raw == "-":
            winner_user_id, winner_username = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            if uid is None and uname is None:
                await message.answer(
                    "❌ Не понял победителя. Дай @username или числовой id (или '-')",
                    parse_mode="HTML",
                )
                return
            winner_user_id, winner_username = uid, uname

    elif field == "owner":
        if raw == "-":
            owner_user_id, owner_username = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            if uid is None and uname is None:
                await message.answer(
                    "❌ Не понял владельца. Дай @username или числовой id (или '-')",
                    parse_mode="HTML",
                )
                return
            owner_user_id, owner_username = uid, uname

    elif field == "amount":
        if raw == "-":
            amount = None
        else:
            val = _parse_amount_text(raw)
            if val is None:
                await message.answer(
                    "❌ Цена должна быть числом (пример: 6700 или 6k) или '-'.", parse_mode="HTML"
                )
                return

            cur = (
                await winner_repository.get_auction_currency(
                    await get_db_pool(),
                    auction_id,
                )
                or ""
            ).lower()
            if cur in {"алмазы", "diamond", "diamonds"} and val % 10 != 0:
                await message.answer(
                    "Для 💎 ставка/цена должна быть кратной 10.", parse_mode="HTML"
                )
                return
            if cur in {"чашки", "cups"} and val % 2 != 0:
                await message.answer("Для 🍵 ставка/цена должна быть чётной.", parse_mode="HTML")
                return

            amount = val

    elif field == "comment":
        if raw == "-":
            moderator_comment_new = ""  # очистить
        else:
            txt = raw.strip()
            if len(txt) > 900:
                await message.answer(
                    "❌ Слишком длинно. Комментарий должен быть до 900 символов.", parse_mode="HTML"
                )
                return
            moderator_comment_new = txt

    await _upsert_manual_result(
        auction_id,
        winner_user_id=int(winner_user_id) if winner_user_id else None,
        winner_username=winner_username,
        owner_user_id=int(owner_user_id) if owner_user_id else None,
        owner_username=owner_username,
        amount=int(amount) if amount is not None else None,
        updated_by=int(message.from_user.id),
        moderator_comment=moderator_comment_new,  # None => не затираем старый
    )

    await message.answer("✅ Обновлено.", parse_mode="HTML")

    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=int(st["menu_chat_id"]),
        message_id=int(st["menu_message_id"]),
        auction_id=auction_id,
        admin_user=message.from_user,
    )

    await _log_admin(
        bot,
        f"✎ Админ {_admin_tag(message.from_user)} обновил поле <b>{field}</b> для лота <b>{auction_id}</b>.",
    )
