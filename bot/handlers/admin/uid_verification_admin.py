import html
import re
from typing import Any

from aiogram import Router, types, Bot, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from db.db import (
    list_uid_verification_requests,
    get_uid_verification_request,
    approve_uid_verification_request,
    reject_uid_verification_request,
    get_user_basic_info_by_username,
    get_whois_admin_payload, remove_uid_ban, list_uid_bans, upsert_uid_ban, get_user_verified_uid,
    get_user_by_username,
    set_uid_verification_request_revision, execute, get_user_id_by_username, unban_user, fetch, fetchrow, get_uid_owner,
    get_user_id_by_uid_any, get_uid_profile_binding,
)
from fsm_states import ModActionFSM, UIDVerificationRevisionFSM

router = Router()

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
REQUIRED_CONFIRMS = 3  # готово к проверке, когда confirmed >= 3

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from bot.handlers.auctions import build_thanks_kb, admin_tag  # да, берём как в /print_win
UID_HEX_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def _mask_uid(uid: str) -> str:
    s = (uid or "").strip()
    if not s:
        return "—"
    if len(s) <= 10:
        return "••••"
    return f"{s[:4]}…{s[-4:]}"


def _uidv_counts(req: dict) -> tuple[int, int, int]:
    confs = req.get("confirmations") or []
    confirmed = sum(1 for c in confs if (c.get("status") or "") == "confirmed")
    rejected = sum(1 for c in confs if (c.get("status") or "") == "rejected")
    pending = sum(1 for c in confs if (c.get("status") or "") == "pending")
    return confirmed, rejected, pending


def _uidv_user_line(req: dict) -> str:
    uname = (req.get("username") or "").strip()
    if uname:
        return f"@{uname}"
    return f"id{req.get('user_id')}"


def _mask_uid(uid: str) -> str:
    s = (uid or "").strip()
    if len(s) <= 8:
        return s
    return f"{s[:4]}…{s[-4:]}"


def _parse_ban_reason_and_until(text: str):
    s = (text or "").strip()
    if not s or s in ("-", "—"):
        return "", None

    # формат: "7 причина..." или "7d причина..." или "7д причина..."
    m = re.match(r"^(\d{1,4})\s*(?:d|д)?\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        days = int(m.group(1))
        reason = (m.group(2) or "").strip()
        until = datetime.now(ZoneInfo("UTC")) + timedelta(days=days)
        return reason, until

    return s, None


async def _resolve_uid_from_text(text: str) -> tuple[str | None, str | None]:
    t = (text or "").strip()

    if not t:
        return None, "empty"

    # прямой UID
    if UID_HEX_RE.fullmatch(t):
        return t.lower(), None

    # user_id
    if t.isdigit():
        uid = await get_user_verified_uid(int(t))
        if not uid:
            return None, "no_uid"
        return str(uid), None

    # username
    uname = t.lstrip("@").strip()
    if not uname:
        return None, "empty"

    u = await get_user_by_username(uname)
    if not u:
        return None, "not_in_db"

    uid = await get_user_verified_uid(int(u["user_id"]))
    if not uid:
        return None, "no_uid"

    return str(uid), None


# ========== МЕНЮ UID-БАНОВ В АДМИНКЕ ==========

@router.message(F.text == "⛔ UID-бан", F.chat.type == "private")
@admin_only
async def uid_ban_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⛔ UID-блокировки:",
        reply_markup=menu_keyboard(
            ["💣 Мастер-бан", "🧹 Мастер-разбан"],
            ["⛔ Забанить UID", "✅ Разбанить UID"],
            ["🚫 Забанить пользователя", "✅ Разбанить пользователя"],
            ["📋 Список UID-банов", "📋 Список банов пользователей"],
            ["⬅️ Назад"],
        ),
    )


@router.message(F.text == "⛔ Забанить UID", F.chat.type == "private")
@admin_only
async def uid_ban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_uid_ban_target)
    await message.answer(
        "Пришли UID (24 hex) или @username / user_id.\n"
        "Если шлёшь @username — он должен быть в базе (нажал /start)."
    )


@router.message(ModActionFSM.waiting_for_uid_ban_target, F.chat.type == "private")
@admin_only
async def uid_ban_got_target(message: Message, state: FSMContext):
    uid, err = await _resolve_uid_from_text(message.text or "")
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    await state.update_data(uid_ban_uid=uid)
    await state.set_state(ModActionFSM.waiting_for_uid_ban_reason)
    await message.answer(
        "Теперь пришли причину.\n"
        "Можно указать срок в днях в начале: например\n"
        "<code>7 мошенник</code>\n"
        "Или просто текст причины.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_uid_ban_reason, F.chat.type == "private")
@admin_only
async def uid_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = str(data.get("uid_ban_uid") or "").strip()
    if not uid:
        await state.clear()
        await message.answer("Потерял UID в состоянии. Начни заново.")
        return

    reason, banned_until = _parse_ban_reason_and_until(message.text or "")

    row = await upsert_uid_ban(
        uid,
        banned_by=message.from_user.id,
        reason=reason,
        banned_until=banned_until,
    )

    # лог: UID не палим полностью
    until_txt = "навсегда" if not row.get("banned_until") else str(row["banned_until"])
    log_text = (
        f"⛔ <b>UID добавлен в ЧС</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"UID: <code>{_mask_uid(uid)}</code>\n"
        f"До: <b>{html.escape(until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(f"✅ Готово. UID в ЧС: <code>{_mask_uid(uid)}</code>", parse_mode="HTML")


@router.message(F.text == "✅ Разбанить UID", F.chat.type == "private")
@admin_only
async def uid_unban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_uid_unban_target)
    await message.answer("Пришли UID (24 hex) или @username / user_id для разбана.")


@router.message(ModActionFSM.waiting_for_uid_unban_target, F.chat.type == "private")
@admin_only
async def uid_unban_got_target(message: Message, state: FSMContext):
    uid, err = await _resolve_uid_from_text(message.text or "")
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    ok = await remove_uid_ban(uid)

    if ok:
        log_text = (
            f"✅ <b>UID удалён из ЧС</b>\n"
            f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
            f"UID: <code>{_mask_uid(uid)}</code>"
        )
        await send_admin_log(message.bot, log_text)

        await message.answer(f"✅ Разбанено: <code>{_mask_uid(uid)}</code>", parse_mode="HTML")
    else:
        await message.answer("UID не найден в ЧС.")

    await state.clear()

from bot.uid_crypto import mask_uid, mask_uid_by_last4
@router.message(F.text == "📋 Список UID-банов", F.chat.type == "private")
@admin_only
async def uid_ban_list(message: Message):
    items = await list_uid_bans(limit=50, offset=0, only_active=True)
    if not items:
        await message.answer("Список пуст.")
        return

    lines = ["⛔ Активные UID-баны (до 50):", ""]
    for it in items:
        last4 = it.get("uid_last4") or ""
        until = it.get("banned_until")
        reason = it.get("reason") or "—"
        until_txt = "навсегда" if not until else str(until)
        lines.append(f"• <code>{mask_uid_by_last4(last4)}</code> • до: <b>{html.escape(until_txt)}</b> • {html.escape(reason)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ========== КОМАНДЫ ==========

@router.message(Command("uidban"), F.chat.type == "private")
@admin_only
async def cmd_uidban(message: Message):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Формат: /uidban <uid|@username|user_id> [текст причины | '7 причина']")
        return

    uid, err = await _resolve_uid_from_text(parts[1])
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    reason_raw = parts[2] if len(parts) >= 3 else "-"
    reason, banned_until = _parse_ban_reason_and_until(reason_raw)

    await upsert_uid_ban(uid, banned_by=message.from_user.id, reason=reason, banned_until=banned_until)
    await message.answer(f"✅ UID в ЧС: <code>{_mask_uid(uid)}</code>", parse_mode="HTML")


@router.message(Command("uidunban"), F.chat.type == "private")
@admin_only
async def cmd_uidunban(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /uidunban <uid|@username|user_id>")
        return

    uid, err = await _resolve_uid_from_text(parts[1])
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    ok = await remove_uid_ban(uid)
    await message.answer("✅ Разбанено." if ok else "UID не найден в ЧС.")


@router.message(Command("uidbans"), F.chat.type == "private")
@admin_only
async def cmd_uidbans(message: Message):
    items = await list_uid_bans(limit=50, offset=0, only_active=True)
    if not items:
        await message.answer("Список пуст.")
        return

    lines = ["⛔ Активные UID-баны (до 50):", ""]
    for it in items:
        last4 = it.get("uid_last4") or ""
        until = it.get("banned_until")
        reason = it.get("reason") or "—"
        until_txt = "навсегда" if not until else str(until)
        lines.append(
            f"• <code>{mask_uid_by_last4(last4)}</code> • до: <b>{html.escape(until_txt)}</b> • {html.escape(reason)}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


def _kb_verif_menu() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏳ Ожидают подтверждений", callback_data="uidv|list|awaiting|0")
    kb.button(text="🕒 Готовы к проверке", callback_data="uidv|list|ready|0")
    kb.button(text="⚠️ Конфликты", callback_data="uidv|list|conflict|0")
    kb.adjust(1)
    return kb.as_markup()


def _kb_req_list(items: list[dict], *, status: str, page: int, has_more: bool) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for it in items:
        req_id = it["id"]
        user = it.get("username") or f"id{it['user_id']}"

        cc = int(it.get("confirmed_cnt") or 0)
        tt = int(it.get("total_cnt") or 0)

        cc_disp = min(cc, REQUIRED_CONFIRMS)

        kb.button(
            text=f"#{req_id} {user} • ✅{cc_disp}/{REQUIRED_CONFIRMS} • запросов {tt}",
            callback_data=f"uidv|view|{req_id}",
        )

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️ Назад", callback_data=f"uidv|list|{status}|{page - 1}")
    nav.button(text="🏠 Меню", callback_data="uidv|menu")
    if has_more:
        nav.button(text="➡️ Далее", callback_data=f"uidv|list|{status}|{page + 1}")
    nav.adjust(3)

    kb.adjust(1)
    kb.attach(nav)
    return kb.as_markup()


def _kb_req_actions(req_id: int, *, can_approve: bool) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Пруфы", callback_data=f"uidv|proof|{req_id}")
    kb.button(text="🧾 Сделки", callback_data=f"uidv|deals|{req_id}")

    if can_approve:
        kb.button(text="✅ Одобрить", callback_data=f"uidv|approve|{req_id}")
    else:
        kb.button(text="⏳ Ждём 3/3", callback_data=f"uidv|approve_blocked|{req_id}")

    kb.button(text="🔧 На доработку", callback_data=f"uidv|rev|{req_id}")
    kb.button(text="❌ Отклонить", callback_data=f"uidv|reject|{req_id}")
    kb.button(text="🏠 Меню", callback_data="uidv|menu")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()
async def safe_call_answer(call: types.CallbackQuery, text: str = "", *, show_alert: bool = False) -> None:
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        # query слишком старый / уже отвечен / invalid — не роняем апдейт
        pass
    except Exception:
        pass

async def safe_edit(call: types.CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        raise


def _unpack_media(packed: str | dict) -> tuple[str | None, str]:
    # json-формат
    if isinstance(packed, dict):
        mt = (packed.get("type") or packed.get("media_type") or "").strip().lower()
        fid = (packed.get("file_id") or packed.get("id") or "").strip()
        if mt and fid:
            return mt, fid
        s = (packed.get("value") or "").strip()
        return None, s

    s = (packed or "").strip()
    if not s:
        return None, ""

    # строковый формат "photo:<file_id>"
    if ":" in s:
        mt, fid = s.split(":", 1)
        mt = mt.strip().lower()
        fid = fid.strip()
        if mt and fid:
            return mt, fid
        return None, s

    # fallback: если кто-то сохранил просто file_id
    # (обычно фото начинается на AgACAg...)
    if s.startswith("AgACAg"):
        return "photo", s

    return None, s


async def _send_media_any(
        bot: Bot,
        chat_id: int,
        packed: str,
        *,
        caption: str | None = None,
        protect_content: bool = False,
        parse_mode: str | None = "HTML",
) -> None:
    kind, fid = _unpack_media(packed)
    if kind == "document":
        await bot.send_document(chat_id, fid, caption=caption, parse_mode=parse_mode, protect_content=protect_content)
    elif kind == "video":
        await bot.send_video(chat_id, fid, caption=caption, parse_mode=parse_mode, protect_content=protect_content)
    else:
        await bot.send_photo(chat_id, fid, caption=caption, parse_mode=parse_mode, protect_content=protect_content)


def _cnt(req: dict[str, Any]) -> tuple[int, int]:
    confirmations = req.get("confirmations") or []
    cc = sum(1 for c in confirmations if (c.get("status") or "") == "confirmed")
    tt = len(confirmations)
    return int(cc), int(tt)


async def _render_uid_verif_view(call: types.CallbackQuery, req_id: int, req: dict[str, Any] | None = None) -> None:
    if req is None:
        req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    await _render_req(call, req_id, req)


def _fmt_conf_status(st: str) -> str:
    s = (st or "").strip().lower()
    return {
        "pending": "⏳ ожидает",
        "confirmed": "✅ подтвердил",
        "rejected": "❌ отклонил",
        "unreachable": "🚫 недоступен",
        "expired": "⌛ истёк",
    }.get(s, s or "—")


async def _render_req(call: types.CallbackQuery, req_id: int, req: dict[str, Any]) -> None:
    username = (req.get("username") or "").strip()
    user_disp = f"@{username}" if username else f"id{req.get('user_id')}"
    status = req.get("status") or "—"
    code = req.get("verification_code") or req.get("challenge_code") or "—"
    created_at = req.get("created_at") or "—"

    confirmations = req.get("confirmations") or []
    confirmed, rejected, pending = _uidv_counts(req)
    total = len(confirmations)

    lines = []
    for c in confirmations:
        u = (c.get("counterparty_username") or "").strip()
        u_disp = f"@{u.lstrip('@')}" if u else f"id{c.get('counterparty_user_id')}"
        st = _fmt_conf_status(c.get("status") or "")
        lines.append(f"• {u_disp}: <b>{st}</b>")

    conf_block = "\n".join(lines) if lines else "—"

    can_approve = (confirmed >= REQUIRED_CONFIRMS)

    text = (
        f"🧾 <b>Заявка #{req_id}</b>\n"
        f"Пользователь: <b>{user_disp}</b> (id: <code>{req.get('user_id')}</code>)\n"
        f"Статус: <b>{status}</b>\n"
        f"Создана: <code>{created_at}</code>\n"
        f"Код: <code>{code}</code>\n"
        f"Подтверждений: <b>{confirmed}</b> / <b>{REQUIRED_CONFIRMS}</b> (запросов отправлено: <b>{total}</b>)\n\n"
        f"<b>Подтверждения:</b>\n{conf_block}\n\n"
        f"<i>UID в тексте не показываем. Смотри пруфы/скрины.</i>"
    )

    await safe_edit(call, text, reply_markup=_kb_req_actions(req_id, can_approve=can_approve))


@router.message(Command("verif"), F.chat.type == "private")
@admin_only
async def verif_menu_cmd(message: types.Message) -> None:
    await message.answer("🧾 Меню UID-верификаций:", reply_markup=_kb_verif_menu())


@router.message(F.text == "🧾 Верификация", F.chat.type == "private")
@admin_only
async def verif_menu_button(message: types.Message) -> None:
    await message.answer("🧾 Меню UID-верификаций:", reply_markup=_kb_verif_menu())


@router.callback_query(F.data == "uidv|menu")
@admin_only
async def verif_menu_cb(call: types.CallbackQuery) -> None:
    await safe_edit(call, "🧾 Меню UID-верификаций:", reply_markup=_kb_verif_menu())
    await call.answer()


@router.callback_query(F.data.startswith("uidv|list|"))
@admin_only
async def verif_list(call: types.CallbackQuery) -> None:
    parts = (call.data or "").split("|")
    if len(parts) != 4:
        await call.answer("Кривые данные.", show_alert=True)
        return

    status = parts[2]
    page = int(parts[3])
    limit = 10
    offset = page * limit

    title_map = {
        "awaiting": "Ожидают подтверждений",
        "ready": "Готовы к проверке",
        "revision": "На доработке",
        "conflict": "Конфликты",
        "pending": "Ожидают подтверждений",
        "approved": "Одобрены",
        "rejected": "Отклонены",
    }
    title = title_map.get(status, status)

    items: list[dict]
    has_more = False

    if status in ("awaiting", "ready"):
        raw = await list_uid_verification_requests(status="pending", limit=500, offset=0)

        def _ok(it: dict) -> bool:
            cc = int(it.get("confirmed_cnt") or 0)
            if status == "awaiting":
                return cc < REQUIRED_CONFIRMS
            return cc >= REQUIRED_CONFIRMS

        filtered = [it for it in raw if _ok(it)]
        items = filtered[offset: offset + limit]
        has_more = len(filtered) > offset + limit

    else:
        # conflict / approved / rejected / pending
        items = await list_uid_verification_requests(status=status, limit=limit, offset=offset)
        has_more = len(items) == limit

    if not items:
        await safe_edit(call, f"Пусто: <b>{title}</b>.", reply_markup=_kb_verif_menu())
        await call.answer()
        return

    await safe_edit(
        call,
        f"🧾 Заявки: <b>{title}</b>\nВыбери заявку:",
        reply_markup=_kb_req_list(items, status=status, page=page, has_more=has_more),
    )
    await call.answer()


@router.callback_query(F.data.startswith("uidv|view|") | F.data.startswith("uidv|view_one|"))
@admin_only
async def verif_view(call: types.CallbackQuery) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    await _render_req(call, req_id, req)
    await call.answer()


@router.callback_query(F.data.startswith("uidv|proof|"))
@admin_only
async def verif_send_proof(call: types.CallbackQuery, bot: Bot) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await safe_call_answer(call, "Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await safe_call_answer(call, "Заявка не найдена.", show_alert=True)
        return

    proofs: list[tuple[str, str]] = []
    profile = (req.get("profile_proof_file_id") or "").strip()
    uidp = (req.get("uid_proof_file_id") or "").strip()
    reg = (req.get("reg_date_proof_file_id") or "").strip()

    if profile:
        proofs.append(("Профиль (код + рег. дата)", profile))
    if uidp:
        proofs.append(("UID (если отдельно)", uidp))
    if reg and reg != profile:
        proofs.append(("Дата регистрации (если отдельно)", reg))

    if not proofs:
        await safe_call_answer(call, "Пруфы не найдены.", show_alert=True)
        return

    # ✅ СРАЗУ отвечаем на callback (иначе “query is too old”)
    await safe_call_answer(call, "Отправляю пруфы в ЛС…")

    to_chat = call.from_user.id
    sent = 0
    forbidden = False
    last_err: Exception | None = None

    for title, packed in proofs:
        try:
            await _send_media_any(
                bot,
                to_chat,
                packed,
                caption=f"{title} • заявка #{req_id}",
                # protect_content=False,  # включи если надо запретить пересылку/сейв
            )
            sent += 1
        except TelegramForbiddenError as e:
            forbidden = True
            last_err = e
            break
        except Exception as e:
            last_err = e
            continue

    # лог оставляем как был
    try:
        await send_admin_log(
            bot,
            "👁 <b>Админ открыл пруфы UID-верификации</b>\n"
            f"Заявка: <code>{req_id}</code>\n"
            f"Админ: @{call.from_user.username or 'id' + str(call.from_user.id)}"
        )
    except Exception:
        pass

    # ✅ Делаем ВИДИМУЮ реакцию
    if sent > 0:
        # в приватке можно написать сообщение (не зависит от callback timeout)
        if call.message and call.message.chat.type == "private":
            await call.message.answer("✅ Пруфы отправлены.")
        else:
            await safe_call_answer(call, "✅ Пруфы отправлены.")
        return

    # ничего не отправилось
    if forbidden:
        await safe_call_answer(
            call,
            "⚠️ Не могу отправить в ЛС. Открой личку с ботом и нажми /start (или разблокируй бота).",
            show_alert=True,
        )
    else:
        await safe_call_answer(
            call,
            "⚠️ Не удалось отправить пруфы (ошибка отправки).",
            show_alert=True,
        )
@router.callback_query(F.data.startswith("uidv|deals|"))
@admin_only
async def verif_send_deals(call: types.CallbackQuery, bot: Bot) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await safe_call_answer(call, "Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await safe_call_answer(call, "Заявка не найдена.", show_alert=True)
        return

    deals: list[str] = list(req.get("deal_file_ids") or [])
    names: list[str] = list(req.get("counterparty_usernames") or [])

    if not deals:
        await safe_call_answer(call, "Сделки не найдены.", show_alert=True)
        return

    await safe_call_answer(call, "Отправляю сделки в ЛС…")

    to_chat = call.from_user.id
    sent = 0
    forbidden = False
    last_err: Exception | None = None

    for i, packed in enumerate(deals, start=1):
        uname = names[i - 1] if i - 1 < len(names) else "—"
        uname_disp = f"@{str(uname).lstrip('@')}" if uname and uname != "—" else "—"
        try:
            await _send_media_any(
                bot,
                to_chat,
                packed,
                caption=f"Сделка {i} • {uname_disp} • заявка #{req_id}",
                # protect_content=False,
            )
            sent += 1
        except TelegramForbiddenError as e:
            forbidden = True
            last_err = e
            break
        except Exception as e:
            last_err = e
            continue

    try:
        await send_admin_log(
            bot,
            "👁 <b>Админ открыл сделки UID-верификации</b>\n"
            f"Заявка: <code>{req_id}</code>\n"
            f"Админ: @{call.from_user.username or 'id' + str(call.from_user.id)}"
        )
    except Exception:
        pass

    if sent > 0:
        if call.message and call.message.chat.type == "private":
            await call.message.answer("✅ Сделки отправлены.")
        else:
            await safe_call_answer(call, "✅ Сделки отправлены.")
        return

    if forbidden:
        await safe_call_answer(
            call,
            "⚠️ Не могу отправить в ЛС. Открой личку с ботом и нажми /start (или разблокируй бота).",
            show_alert=True,
        )
    else:
        await safe_call_answer(
            call,
            "⚠️ Не удалось отправить сделки (ошибка отправки).",
            show_alert=True,
        )

@router.callback_query(F.data.startswith("uidv|approve|"))
@admin_only
async def verif_approve(call: types.CallbackQuery, bot: Bot):
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    confirmed, rejected, pending = _uidv_counts(req)

    if confirmed < REQUIRED_CONFIRMS:
        await call.answer(f"Нельзя одобрить: подтверждений {confirmed}/{REQUIRED_CONFIRMS}.", show_alert=True)
        try:
            await _render_uid_verif_view(call, req_id)
        except Exception:
            pass
        return

    moderator = admin_tag(call.from_user)

    # Кнопка "Спасибо" нужна только в ЛС пользователю
    thanks_kb = None
    try:
        thanks_kb = await build_thanks_kb(int(req_id), moderator)
    except Exception:
        thanks_kb = None

    res = await approve_uid_verification_request(request_id=req_id, admin_id=call.from_user.id)
    if isinstance(res, tuple):
        ok, reason = bool(res[0]), res[1]
    else:
        ok, reason = bool(res), None

    if not ok:
        await call.answer(f"Не удалось: {reason or 'уже обработано/не найдено'}", show_alert=True)
        return

    req_after = await get_uid_verification_request(request_id=req_id) or req

    # ЛС пользователю: добавляем модератора + кнопка спасибо
    try:
        await bot.send_message(
            chat_id=int(req_after["user_id"]),
            protect_content=False,
            text=(
                "✅ <b>UID-верификация одобрена</b>\n\n"
                f"Заявка: <code>#{req_id}</code>\n"
                f"Модератор: {moderator}\n\n"
                "Если хочешь, можешь поблагодарить модератора кнопкой ниже."
            ),
            reply_markup=thanks_kb,
            parse_mode="HTML",
        )
    except Exception:
        pass

    c2, r2, p2 = _uidv_counts(req_after)
    user_line = _uidv_user_line(req_after)

    # Логи: БЕЗ кнопки спасибо
    await send_admin_log(
        bot,
        "uidv",
        "✅ <b>UID-верификация одобрена</b>\n"
        f"Заявка: <code>#{req_id}</code>\n"
        f"Пользователь: {user_line} (id=<code>{req_after.get('user_id')}</code>)\n"
        f"Подтверждения: <b>{c2}/{REQUIRED_CONFIRMS}</b> (pending={p2}, rejected={r2})\n"
        f"Админ: {moderator}",
    )

    await call.answer("✅ Одобрено")

    try:
        await _render_uid_verif_view(call, req_id)
    except Exception:
        pass


@router.callback_query(F.data.startswith("uidv|reject|"))
@admin_only
async def verif_reject(call: types.CallbackQuery, state: FSMContext) -> None:
    req_id = int((call.data or "").split("|")[2])
    await state.set_state(ModActionFSM.waiting_for_reject_uid_verification_reason)
    await state.update_data(uidv_reject_req_id=req_id)
    await call.message.answer(f"Напиши причину отклонения заявки #{req_id} текстом:")
    await call.answer()


@router.message(ModActionFSM.waiting_for_reject_uid_verification_reason, F.chat.type == "private")
@admin_only
async def verif_reject_reason(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    req_id = int(data.get("uidv_reject_req_id") or 0)
    reason = (message.text or "").strip()

    if not req_id or not reason:
        await message.answer("Нужна причина текстом.")
        return

    await state.clear()

    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await message.answer("Заявка не найдена.")
        return

    moderator = admin_tag(message.from_user)

    # Кнопка "Спасибо" нужна только в ЛС пользователю
    thanks_kb = None
    try:
        thanks_kb = await build_thanks_kb(int(req_id), moderator)
    except Exception:
        thanks_kb = None

    # ВАЖНО: db.py ждёт admin_comment, не reason
    res = await reject_uid_verification_request(
        request_id=req_id,
        admin_id=message.from_user.id,
        admin_comment=reason,
    )
    if isinstance(res, tuple):
        ok, db_reason = bool(res[0]), res[1]
    else:
        ok, db_reason = bool(res), None

    if not ok:
        await message.answer(f"Не удалось отклонить: {db_reason or 'ошибка'}.")
        return

    req_after = await get_uid_verification_request(request_id=req_id) or req

    # ЛС пользователю: добавляем модератора + кнопка спасибо
    try:
        await bot.send_message(
            chat_id=int(req_after["user_id"]),
            protect_content=False,
            text=(
                "❌ <b>UID-верификация отклонена</b>\n\n"
                f"Заявка: <code>#{req_id}</code>\n"
                f"Модератор: {moderator}\n"
                f"Причина: {reason}\n\n"
                "Можешь отправить заявку заново, когда исправишь проблему."
            ),
            reply_markup=thanks_kb,
            parse_mode="HTML",
        )
    except Exception:
        pass

    confirmed, rejected, pending = _uidv_counts(req_after)
    user_line = _uidv_user_line(req_after)

    # Логи: БЕЗ кнопки спасибо
    await send_admin_log(
        bot,
        "uidv",
        "❌ <b>UID-верификация отклонена</b>\n"
        f"Заявка: <code>#{req_id}</code>\n"
        f"Пользователь: {user_line} (id=<code>{req_after.get('user_id')}</code>)\n"
        f"Подтверждения: <b>{confirmed}/{REQUIRED_CONFIRMS}</b> (pending={pending}, rejected={rejected})\n"
        f"Причина: {reason}\n"
        f"Админ: {moderator}",
    )

    await message.answer(f"Отклонено ❌\nПричина: {reason}")


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _days_ago(dt) -> str:
    if not dt:
        return "—"
    try:
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = (now - dt).days
        return f"{d} дн."
    except Exception:
        return "—"


def _extract_user_id_from_message(msg: types.Message) -> int | None:
    # reply
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return int(msg.reply_to_message.from_user.id)

    # старое forward_from
    if msg.forward_from:
        return int(msg.forward_from.id)

    # новое forward_origin
    origin = getattr(msg, "forward_origin", None)
    if origin:
        sender = getattr(origin, "sender_user", None)
        if sender:
            return int(sender.id)

    return None
UID_HEX_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


async def _resolve_whois_target_from_text_or_message(message: types.Message, raw: str | None = None) -> int | None:
    user_id = _extract_user_id_from_message(message)
    if user_id:
        return int(user_id)

    txt = (raw or "").strip()
    if not txt:
        return None

    if UID_HEX_RE.fullmatch(txt):
        return await get_user_id_by_uid_any(txt)

    if txt.lower().startswith("id") and txt[2:].isdigit():
        return int(txt[2:])

    if txt.isdigit():
        return int(txt)

    u = txt.lstrip("@").strip()
    if USERNAME_RE.fullmatch(u):
        info = await get_user_basic_info_by_username(username=u)
        if info:
            return int(info["user_id"])

    return None
async def _render_whois_by_uid(message: types.Message, uid: str) -> None:
    data = await get_uid_profile_binding(uid)
    if not data:
        await message.answer(
            "<b>WHOIS по UID</b>\n"
            f"UID: <code>{_mask_uid(uid)}</code>\n"
            "Статус: не найден в базе\n"
            "UID в ЧС: <b>❌ нет</b>",
            parse_mode="HTML",
        )
        return

    verified = data.get("verified")
    request = data.get("request")
    is_banned = bool(data.get("is_banned"))

    lines = [
        "<b>WHOIS по UID</b>",
        f"UID: <code>{_mask_uid(uid)}</code>",
        f"UID в ЧС: <b>{'✅ есть' if is_banned else '❌ нет'}</b>",
    ]

    if verified:
        uname = (verified.get("username") or "").strip()
        username_line = f"@{uname}" if uname else "—"
        lines.extend([
            "",
            "<b>Есть verified-привязка</b>",
            f"TG ID: <code>{verified.get('user_id')}</code>",
            f"Username: <b>{username_line}</b>",
            f"Имя: {verified.get('full_name') or '—'}",
            f"Статус: <b>{verified.get('status') or '—'}</b>",
            f"Подтверждён: <code>{_fmt_dt(verified.get('verified_at'))}</code>",
        ])
    elif request:
        uname = (request.get("username") or "").strip()
        username_line = f"@{uname}" if uname else "—"
        lines.extend([
            "",
            "<b>Есть заявка на верификацию</b>",
            f"Заявка: <code>#{request.get('id')}</code>",
            f"TG ID: <code>{request.get('user_id')}</code>",
            f"Username: <b>{username_line}</b>",
            f"Имя: {request.get('full_name') or '—'}",
            f"Статус заявки: <b>{request.get('status') or '—'}</b>",
            f"Создана: <code>{_fmt_dt(request.get('created_at'))}</code>",
            f"Решение: <code>{_fmt_dt(request.get('decided_at'))}</code>",
        ])
    else:
        lines.extend([
            "",
            "Привязки к TG-профилю не найдено.",
        ])

    await message.answer("\n".join(lines), parse_mode="HTML")
@router.message(Command("whois"), F.chat.type == "private")
@admin_only
async def cmd_whois(message: types.Message, state: FSMContext):
    await state.clear()

    # 1) reply/forward
    user_id = _extract_user_id_from_message(message)

    # 2) аргумент
    arg = None
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        arg = parts[1].strip()

    # если передан UID hex — отдельный режим
    if not user_id and arg and UID_HEX_RE.fullmatch(arg):
        await _render_whois_by_uid(message, arg.lower())
        return

    # обычный поиск по id / username
    if not user_id and arg:
        if arg.lower().startswith("id") and arg[2:].isdigit():
            user_id = int(arg[2:])
        elif arg.isdigit():
            user_id = int(arg)
        else:
            u = arg.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    user_id = int(info["user_id"])

    if user_id:
        await _render_whois(message, user_id)
        return

    await state.set_state(ModActionFSM.waiting_for_whois_target)
    await message.answer(
        "Перешли сообщение пользователя (или ответь на него), либо пришли @username / user_id / UID.\n"
        "Отмена: /cancel"
    )

@router.message(ModActionFSM.waiting_for_whois_target, F.chat.type == "private")
@admin_only
async def whois_waiting_target(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer("Ок, отменено.")
        return

    user_id = _extract_user_id_from_message(message)

    # UID hex
    if not user_id and UID_HEX_RE.fullmatch(txt):
        await state.clear()
        await _render_whois_by_uid(message, txt.lower())
        return

    if not user_id and txt:
        if txt.lower().startswith("id") and txt[2:].isdigit():
            user_id = int(txt[2:])
        elif txt.isdigit():
            user_id = int(txt)
        else:
            u = txt.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    user_id = int(info["user_id"])

    if not user_id:
        await message.answer(
            "Не смог определить пользователя.\n"
            "Нужен reply/forward, @username, user_id или UID.\n"
            "Отмена: /cancel"
        )
        return

    await state.clear()
    await _render_whois(message, user_id)


from datetime import datetime, timezone


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        # если без tzinfo, считаем UTC
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _days_ago(dt) -> str:
    if not dt:
        return "—"
    try:
        now = datetime.now(timezone.utc)
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = (now - dt).days
        return f"{d} дн."
    except Exception:
        return "—"


async def _render_whois(message: types.Message, user_id: int) -> None:
    payload = await get_whois_admin_payload(user_id=int(user_id))
    if not payload:
        await message.answer("Не найден в базе. Возможно, не нажимал /start или бот в ЧС.")
        return

    u = payload["user"]
    lots_posted = int(payload.get("lots_posted") or 0)
    ver = payload.get("uid_verif")
    uid_record = payload.get("uid_record") or {}
    is_uid_in_blacklist = bool(uid_record.get("is_banned"))
    uid_ban_line = "✅ есть в ЧС" if is_uid_in_blacklist else "❌ нет в ЧС"
    uid_in_blacklist = bool(payload.get("uid_in_blacklist"))
    user_in_blacklist = bool(payload.get("user_in_blacklist"))
    in_blacklist = bool(payload.get("in_blacklist"))

    uid_ban_line = "✅ есть" if uid_in_blacklist else "❌ нет"
    user_ban_line = "✅ есть" if user_in_blacklist else "❌ нет"
    black_line = "✅ да" if in_blacklist else "❌ нет"
    uname = (u.get("username") or "").strip()
    username_line = f"@{uname}" if uname else "—"

    role = []
    if u.get("is_admin"):
        role.append("админ")
    if u.get("is_luxury"):
        role.append("лакшери")
    if u.get("is_trusted"):
        role.append("trusted")
    role_line = ", ".join(role) if role else "обычный"

    created_at = u.get("created_at")
    reg_line = f"{_fmt_dt(created_at)} ({_days_ago(created_at)})"
    pm_opened = bool(u.get("pm_opened"))
    last_pm = u.get("last_pm_at")
    unreach = payload.get("unreachable")

    pm_block = ""
    if not pm_opened:
        pm_block = (
            "\nЛС с ботом: ❌ <b>не открывал</b>\n"
            "<tg-spoiler>"
            "Бот не может написать пользователю первым. Нужно, чтобы он нажал /start."
            "</tg-spoiler>"
        )
    else:
        if unreach:
            reason = (unreach.get("reason") or "—").strip()
            last_seen = _fmt_dt(unreach.get("last_seen"))
            pm_block = (
                "\nЛС с ботом: 🚫 <b>недоступен</b>\n"
                f"Последняя ошибка: <code>{reason}</code> • <code>{last_seen}</code>\n"
                "<tg-spoiler>"
                "🚫 <b>Недоступен</b> = бот пытался написать, но не смог доставить сообщение.\n"
                "Чаще всего причины:\n"
                "• пользователь не нажимал /start (бот не может написать первым)\n"
                "• пользователь заблокировал бота / кинул в спам\n"
                "• аккаунт удалён / деактивирован\n"
                "• реже: временная ошибка Telegram"
                "</tg-spoiler>"
            )
        else:
            pm_block = (
                "\nЛС с ботом: ✅ <b>открыт</b>\n"
                f"Последний контакт: <code>{_fmt_dt(last_pm)}</code>"
            )
    # счётчики подтверждений у контрагента (если миграция уже есть)
    conf_done = u.get("uid_verif_confirmed_count")
    conf_rej = u.get("uid_verif_rejected_count")
    last_conf = u.get("uid_verif_last_confirmed_at")
    last_rej = u.get("uid_verif_last_rejected_at")

    counter_block = ""
    if conf_done is not None and conf_rej is not None:
        counter_block = (
            f"\nПодтверждал чужие сделки: ✅<b>{int(conf_done)}</b> / ❌<b>{int(conf_rej)}</b>\n"
            f"Последнее ✅: <code>{_fmt_dt(last_conf)}</code> • Последнее ❌: <code>{_fmt_dt(last_rej)}</code>"
        )

    binding_verified = str(uid_record.get("status") or "").lower() == "verified"
    binding_last4 = str(uid_record.get("uid_last4") or "").strip()
    if binding_verified:
        suffix = f" • UID …{html.escape(binding_last4)}" if binding_last4 else ""
        ver_block = f"\nUID-верификация: <b>✅ подтверждена</b>{suffix}"
    else:
        ver_block = "\nUID-верификация: <b>—</b>"

    if ver:
        req_id = int(ver["id"])
        st = (ver.get("status") or "—").strip()
        v_created = ver.get("created_at")
        v_decided = ver.get("decided_at")
        code = ver.get("verification_code") or ver.get("challenge_code") or "—"

        requested = list(ver.get("counterparty_usernames") or [])
        confirmations = list(ver.get("confirmations") or [])

        # нормализуем список "кому отправлял"
        req_users = []
        seen = set()
        for x in requested:
            s = (str(x) or "").strip().lstrip("@").lower()
            if not s or s in seen:
                continue
            seen.add(s)
            req_users.append("@" + s)

        confirmed = []
        rejected = []
        pending = []
        unreachable = []
        expired = []

        for c in confirmations:
            cu = (c.get("counterparty_username") or "").strip().lstrip("@").lower()
            cu_disp = "@" + cu if cu else f"id{c.get('counterparty_user_id')}"
            cs = (c.get("status") or "pending").strip().lower()
            when = _fmt_dt(c.get("decided_at") or c.get("created_at"))

            if cs == "confirmed":
                confirmed.append(f"{cu_disp} (<code>{when}</code>)")
            elif cs == "rejected":
                rejected.append(f"{cu_disp} (<code>{when}</code>)")
            elif cs == "unreachable":
                unreachable.append(cu_disp)
            elif cs == "expired":
                expired.append(cu_disp)
            else:
                pending.append(cu_disp)

        ver_block += (
            f"\nПоследняя заявка UID: <b>{st}</b>\n"
            f"Заявка: <code>#{req_id}</code>\n"
            f"Код: <code>{code}</code>\n"
            f"Создана: <code>{_fmt_dt(v_created)}</code> • Решение: <code>{_fmt_dt(v_decided)}</code>\n"
            f"Кому отправлял запросы: {', '.join(req_users) if req_users else '—'}\n"
            f"Подтвердили: {', '.join(confirmed) if confirmed else '—'}\n"
            f"Отклонили: {', '.join(rejected) if rejected else '—'}\n"
            f"Ожидают: {', '.join(pending) if pending else '—'}\n"
            f"Недоступны: {', '.join(unreachable) if unreachable else '—'}\n"
            f"Истекли: {', '.join(expired) if expired else '—'}"
        )

        if unreachable:
            ver_block += (
                "\n<tg-spoiler>"
                "🚫 <b>Недоступен</b> = бот не смог доставить запрос подтверждения.\n"
                "Причины:\n"
                "• пользователь не нажимал /start (бот не может написать первым)\n"
                "• пользователь заблокировал бота / кинул в спам\n"
                "• аккаунт удалён / деактивирован\n"
                "• реже: временная ошибка Telegram"
                "</tg-spoiler>"
            )

    await message.answer(
        "<b>WHOIS</b>\n"
        f"ID: <code>{u['user_id']}</code>\n"
        f"Username: <b>{username_line}</b>\n"
        f"Имя: {u.get('full_name') or '—'}\n"
        f"Роль: <b>{role_line}</b>\n"
        f"В Максе с: <code>{reg_line}</code>"
        f"{pm_block}\n"
        f"Выставлял лотов (auction_owners): <b>{lots_posted}</b>\n"
        f"UID в ЧС: <b>{uid_ban_line}</b>\n"
        f"Пользователь в ЧС: <b>{user_ban_line}</b>\n"
        f"{counter_block}"
        f"{ver_block}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("uidv|approve_blocked|"))
@admin_only
async def verif_approve_blocked(call: types.CallbackQuery) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return
    req_id = int(parts[2])

    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    confirmed, rejected, pending = _uidv_counts(req)

    await call.answer(
        f"Нельзя одобрить: подтверждений {confirmed}/{REQUIRED_CONFIRMS}.",
        show_alert=True
    )


# ==================== UID verification: "на доработку" (revision) ====================

from typing import Iterable
import html
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Bot

# оставляем только то, что ты хочешь
_REV_FLAG_TITLES: list[tuple[str, str]] = [
    ("profile", "📷 Профиль + код"),
    ("deal1_screen", "🤝 Сделка 1: скрин"),
    ("deal1_username", "🤝 Сделка 1: ник"),
    ("deal2_screen", "🤝 Сделка 2: скрин"),
    ("deal2_username", "🤝 Сделка 2: ник"),
    ("deal3_screen", "🤝 Сделка 3: скрин"),
    ("deal3_username", "🤝 Сделка 3: ник"),
    ("deal4_screen", "🤝 Сделка 4: скрин"),
    ("deal4_username", "🤝 Сделка 4: ник"),
    ("deal5_screen", "🤝 Сделка 5: скрин"),
    ("deal5_username", "🤝 Сделка 5: ник"),
    ("extra", "➕ Доп. пруфы"),
    ("other", "📝 Другое"),
]

_REV_ALLOWED = {k for k, _ in _REV_FLAG_TITLES}
_REV_ORDER = {k: i for i, (k, _) in enumerate(_REV_FLAG_TITLES)}


def _sort_rev_flags(flags: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for f in (flags or []):
        s = str(f).strip()
        if not s or s in seen:
            continue
        if s not in _REV_ALLOWED:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out, key=lambda x: _REV_ORDER.get(x, 10_000))


def _kb_uidv_revision(req_id: int, chosen: Iterable[str], reason: str) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chosen_set = set(chosen or [])

    # ВАЖНО: callback_data ровно 4 части: uidv|rev_toggle|id|flag
    for key, title in _REV_FLAG_TITLES:
        mark = "✅ " if key in chosen_set else "☐ "
        kb.button(text=f"{mark}{title}", callback_data=f"uidv|rev_toggle|{req_id}|{key}")

    kb.button(
        text=("✏️ Причина ✅" if (reason or "").strip() else "✏️ Причина"),
        callback_data=f"uidv|rev_reason|{req_id}",
    )
    kb.button(text="📨 Отправить на доработку", callback_data=f"uidv|rev_send|{req_id}")
    kb.button(text="⬅️ Назад", callback_data=f"uidv|view|{req_id}")
    kb.adjust(1)
    return kb.as_markup()


def _rev_flags_to_lines(flags: list[str]) -> list[str]:
    m = {k: t for k, t in _REV_FLAG_TITLES}
    return [f"• {m.get(f, f)}" for f in flags]


@router.callback_query(F.data.startswith("uidv|rev|"))
@admin_only
async def uidv_revision_start(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    req = await get_uid_verification_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
    await state.update_data(uidv_rev_req_id=req_id, uidv_rev_flags=[], uidv_rev_reason="")

    txt = (
        f"🔧 <b>На доработку</b> (заявка <b>#{req_id}</b>)\n\n"
        f"Отметь, что нужно исправить, добавь причину, затем отправь пользователю."
    )
    try:
        await call.message.edit_text(txt, reply_markup=_kb_uidv_revision(req_id, [], ""))
    except Exception:
        await call.message.answer(txt, reply_markup=_kb_uidv_revision(req_id, [], ""))


@router.callback_query(F.data.startswith("uidv|rev_toggle|"))
@admin_only
async def uidv_revision_toggle(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")

    # поддержка обоих форматов:
    # новый: uidv|rev_toggle|<id>|<flag>  (len=4)
    # старый/кривой: uidv|rev_toggle|<id>|X|<flag> (len>=5)
    if len(parts) < 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    flag = (parts[3] if len(parts) == 4 else parts[4] if len(parts) > 4 else "").strip()

    data = await state.get_data()
    if int(data.get("uidv_rev_req_id") or 0) != req_id:
        await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
        await state.update_data(uidv_rev_req_id=req_id, uidv_rev_flags=[], uidv_rev_reason="")
        data = await state.get_data()

    chosen = set(data.get("uidv_rev_flags") or [])
    reason = str(data.get("uidv_rev_reason") or "")

    if flag in _REV_ALLOWED:
        if flag in chosen:
            chosen.remove(flag)
        else:
            chosen.add(flag)

    chosen_list = _sort_rev_flags(chosen)
    await state.update_data(uidv_rev_flags=chosen_list)

    try:
        await call.answer()
    except Exception:
        pass

    try:
        await call.message.edit_reply_markup(reply_markup=_kb_uidv_revision(req_id, chosen_list, reason))
    except Exception:
        # если вдруг Telegram не дал редактировать markup, не падаем
        pass


@router.callback_query(F.data.startswith("uidv|rev_reason|"))
@admin_only
async def uidv_revision_reason(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    await state.set_state(UIDVerificationRevisionFSM.waiting_reason)
    await state.update_data(uidv_rev_req_id=req_id)

    try:
        await call.answer()
    except Exception:
        pass

    await call.message.answer(
        "✏️ Напиши причину/комментарий, что именно не так и что нужно исправить.\n"
        "Можно коротко, но по делу.",
    )


@router.message(UIDVerificationRevisionFSM.waiting_reason, F.chat.type == "private")
@admin_only
async def uidv_revision_reason_msg(message: types.Message, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    data = await state.get_data()
    req_id = int(data.get("uidv_rev_req_id") or 0)
    chosen = _sort_rev_flags(data.get("uidv_rev_flags") or [])

    await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
    await state.update_data(uidv_rev_reason=reason)

    txt = f"🔧 <b>На доработку</b> (заявка <b>#{req_id}</b>)\n\nПричина сохранена."
    await message.answer(txt, reply_markup=_kb_uidv_revision(req_id, chosen, reason))


@router.callback_query(F.data.startswith("uidv|rev_send|"))
@admin_only
async def uidv_revision_send(call: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    data = await state.get_data()
    chosen = _sort_rev_flags(data.get("uidv_rev_flags") or [])
    reason = (data.get("uidv_rev_reason") or "").strip()

    if not chosen:
        await call.answer("Сначала отметь, что исправлять.", show_alert=True)
        return
    if not reason:
        await call.answer("Нужна причина (кнопка «Причина»).", show_alert=True)
        return

    req = await get_uid_verification_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    admin_u = call.from_user

    admin_id = call.from_user.id
    admin_username = call.from_user.username or call.from_user.full_name

    ok = await set_uid_verification_request_revision(
        req_id,
        moderator_id=admin_id,
        moderator_username=admin_username,
        reason=reason,
        flags=chosen,  # НЕ выкидывай это, иначе ты сам себе сотрёшь "что исправлять"
    )

    if not ok:
        await call.answer("Не удалось обновить заявку.", show_alert=True)
        return

    user_id = int(req.get("user_id") or 0)
    moderator = admin_tag(admin_u)
    lines = "\n".join(_rev_flags_to_lines(chosen))

    kb = InlineKeyboardBuilder()
    kb.button(text="🔧 Исправить заявку", callback_data=f"uidv_fix|{req_id}")
    kb.button(text="📌 Показать мою заявку", callback_data="uidv|start")
    kb.adjust(1)

    text_user = (
        f"🔧 Заявка на верификацию требует доработки\n\n"
        f"Заявка: #{req_id}\n"
        f"Модератор: {moderator}\n\n"
        f"EX_MODE_DECK_SPLITНужно исправить:\n{lines}\n\n"
        f"Причина:\n{html.escape(reason)}\n\n"
        f"Нажми «🔧 Исправить заявку» и досылай только то, что отмечено."
    )

    try:
        await bot.send_message(user_id, text_user, reply_markup=kb.as_markup(), protect_content=False)
    except Exception:
        pass

    try:
        await call.answer("Отправлено ✅")
    except Exception:
        pass

    await state.clear()

# ========== БАНЫ ПОЛЬЗОВАТЕЛЕЙ (TG user_id) ==========

def _parse_user_ban_reason_and_until(text: str):
    """Формат: '7 причина' (7 дней) или просто 'причина'.
    Если без срока — ставим 10 лет (как у тебя в db.ban_user).
    Здесь naive datetime, чтобы совпадало с user_bans/is_user_banned.
    """
    s = (text or "").strip()
    if not s or s in ("-", "—"):
        return "", datetime.now() + timedelta(days=365 * 10)

    m = re.match(r"^(\d{1,4})\s*(?:d|д)?\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        days = int(m.group(1))
        reason = (m.group(2) or "").strip()
        until = datetime.now() + timedelta(days=days)
        return reason, until

    return s, datetime.now() + timedelta(days=365 * 10)


async def _resolve_user_id_from_text(text: str) -> tuple[int | None, str | None, str | None]:
    """Возвращает (user_id, username_without_at, err)."""
    t = (text or "").strip()
    if not t:
        return None, None, "empty"

    if t.isdigit():
        return int(t), None, None

    uname = t.lstrip("@").strip()
    if not uname:
        return None, None, "empty"

    uid = await get_user_id_by_username(uname)
    if not uid:
        return None, uname, "not_in_db"
    return int(uid), uname, None


@router.message(F.text == "🚫 Забанить пользователя", F.chat.type == "private")
@admin_only
async def user_ban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_user_ban_target)
    await message.answer(
        "Пришли @username или user_id пользователя, которого надо забанить.\n"
        "Если шлёшь @username — он должен быть в базе (нажал /start)."
    )


@router.message(ModActionFSM.waiting_for_user_ban_target, F.chat.type == "private")
@admin_only
async def user_ban_got_target(message: Message, state: FSMContext):
    user_id, uname, err = await _resolve_user_id_from_text(message.text or "")
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или пришлите user_id."
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(user_ban_user_id=int(user_id), user_ban_username=(uname or "").strip() or None)
    await state.set_state(ModActionFSM.waiting_for_user_ban_reason)
    await message.answer(
        "Теперь пришли причину.\n"
        "Можно указать срок в днях в начале: например <code>7 спам</code>.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_user_ban_reason, F.chat.type == "private")
@admin_only
async def user_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("user_ban_user_id") or 0)
    uname = (data.get("user_ban_username") or "").strip() or None

    if user_id <= 0:
        await state.clear()
        await message.answer("Потерял user_id в состоянии. Начни заново.")
        return

    reason, banned_until = _parse_user_ban_reason_and_until(message.text or "")

    await execute(
        "INSERT INTO public.user_bans (user_id, banned_until, reason) VALUES ($1, $2, $3)",
        int(user_id),
        banned_until,
        (reason or "").strip(),
    )

    until_txt = str(banned_until)
    who = f"@{uname}" if uname else f"id{user_id}"
    log_text = (
        f"🚫 <b>Пользователь забанен</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"Кого: <b>{html.escape(who)}</b> (id <code>{user_id}</code>)\n"
        f"До: <b>{html.escape(until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"✅ Готово. Забанен: <b>{html.escape(who)}</b> до <b>{html.escape(until_txt)}</b>",
        parse_mode="HTML",
    )


@router.message(F.text == "✅ Разбанить пользователя", F.chat.type == "private")
@admin_only
async def user_unban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_user_unban_target)
    await message.answer("Пришли @username или user_id для разбана пользователя.")


@router.message(ModActionFSM.waiting_for_user_unban_target, F.chat.type == "private")
@admin_only
async def user_unban_got_target(message: Message, state: FSMContext):
    user_id, uname, err = await _resolve_user_id_from_text(message.text or "")
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или пришлите user_id."
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await unban_user(int(user_id))
    who = f"@{uname}" if uname else f"id{user_id}"
    log_text = (
        f"✅ <b>Пользователь разбанен</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"Кого: <b>{html.escape(who)}</b> (id <code>{int(user_id)}</code>)"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(f"✅ Разбанено: <b>{html.escape(who)}</b>", parse_mode="HTML")


@router.message(F.text == "📋 Список банов пользователей", F.chat.type == "private")
@admin_only
async def user_ban_list(message: Message):
    rows = await fetch(
        """
        SELECT ub.user_id,
               u.username,
               ub.banned_until,
               ub.reason
        FROM public.user_bans ub
        LEFT JOIN public.users u ON u.user_id = ub.user_id
        WHERE ub.banned_until > NOW()
        ORDER BY ub.banned_until DESC
        LIMIT 50
        """
    )

    if not rows:
        await message.answer("Список пуст.")
        return

    lines = ["🚫 Активные баны пользователей (до 50):", ""]
    for r in rows:
        uid = int(r.get("user_id") or 0)
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id{uid}"
        until = r.get("banned_until")
        reason = (r.get("reason") or "").strip() or "—"
        lines.append(
            f"• <b>{html.escape(who)}</b> (id <code>{uid}</code>) • до: <b>{html.escape(str(until))}</b> • {html.escape(reason)}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")

MASTER_REASON_RE = re.compile(r"^(\d{1,4})\s*(?:d|д)?\s+(.+)$", re.IGNORECASE)


def _parse_master_reason(text: str) -> tuple[str, int | None]:
    s = (text or "").strip()
    if not s or s in ("-", "—"):
        return "", None

    m = MASTER_REASON_RE.match(s)
    if m:
        return (m.group(2) or "").strip(), int(m.group(1))
    return s, None


def _extract_uid_anywhere(text: str) -> str | None:
    for tok in (text or "").strip().split():
        if UID_HEX_RE.fullmatch(tok):
            return tok.lower()
    return None


def _extract_user_anywhere(text: str) -> str | None:
    # берём первый токен, который НЕ UID
    for tok in (text or "").strip().split():
        if not UID_HEX_RE.fullmatch(tok):
            return tok.strip()
    return None


async def _resolve_master_user(text: str) -> tuple[int | None, str | None, str | None]:
    """
    Возвращает (user_id, username_without_at, err).
    username можно дать только если он в БД (нажал /start), иначе просим user_id.
    """
    t = (text or "").strip()
    if not t:
        return None, None, "empty"

    if t.isdigit():
        uid = int(t)
        un = await fetchrow("SELECT username FROM public.users WHERE user_id=$1 LIMIT 1", uid)
        username = (un.get("username") or "").strip() if un else ""
        return uid, (username or None), None

    uname = t.lstrip("@").strip()
    if not uname:
        return None, None, "empty"

    user_id = await get_user_id_by_username(uname)
    if not user_id:
        return None, uname, "not_in_db"

    return int(user_id), uname, None


@router.message(F.text == "💣 Мастер-бан", F.chat.type == "private")
@admin_only
async def master_ban_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ModActionFSM.waiting_for_master_ban_user)
    await message.answer(
        "💣 <b>Мастер-бан</b>\n\n"
        "Шаг 1/3: пришли <b>@username</b> или <b>user_id</b>.\n"
        "Можно одной строкой: <code>UID @username</code> или <code>UID user_id</code> (через пробел).",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_user, F.chat.type == "private")
@admin_only
async def master_ban_got_user(message: Message, state: FSMContext):
    text = message.text or ""

    uid = _extract_uid_anywhere(text)
    user_token = _extract_user_anywhere(text)

    # если прислали одной строкой "UID @user" / "UID 123"
    if uid and user_token:
        user_id, username, err = await _resolve_master_user(user_token)
        if not user_id:
            if err == "not_in_db":
                await message.answer(
                    "Этого @username нет в базе.\n"
                    "Пусть нажмёт /start у бота или пришли <b>user_id</b>.",
                    parse_mode="HTML",
                )
            else:
                await message.answer("Нужно прислать @username или user_id.")
            return

        await state.update_data(master_user_id=int(user_id), master_username=username, master_uid=uid)
        await state.set_state(ModActionFSM.waiting_for_master_ban_reason)
        await message.answer(
            "Шаг 3/3: причина.\n"
            "Можно со сроком: <code>7 мошенник</code>\n"
            "Или просто текст.\n"
            "Если без причины: <code>-</code>",
            parse_mode="HTML",
        )
        return

    # обычный режим: ждём только TG на шаге 1
    user_id, username, err = await _resolve_master_user(text)
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "Этого @username нет в базе.\n"
                "Пусть нажмёт /start у бота или пришли <b>user_id</b>.",
                parse_mode="HTML",
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(master_user_id=int(user_id), master_username=username)
    await state.set_state(ModActionFSM.waiting_for_master_ban_uid)
    await message.answer(
        "Шаг 2/3: теперь пришли <b>UID</b> (24 hex).\n"
        "Можно просто UID, без лишних слов.",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_uid, F.chat.type == "private")
@admin_only
async def master_ban_got_uid(message: Message, state: FSMContext):
    uid = _extract_uid_anywhere(message.text or "")
    if not uid:
        await message.answer("UID должен быть ровно 24 hex символа. Пришли UID ещё раз.")
        return

    await state.update_data(master_uid=uid)
    await state.set_state(ModActionFSM.waiting_for_master_ban_reason)
    await message.answer(
        "Шаг 3/3: причина.\n"
        "Можно со сроком: <code>7 мошенник</code>\n"
        "Или просто текст.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_reason, F.chat.type == "private")
@admin_only
async def master_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("master_user_id") or 0)
    username = (data.get("master_username") or "").strip() or None
    uid = (data.get("master_uid") or "").strip().lower()

    if user_id <= 0 or not uid:
        await state.clear()
        await message.answer("Потерял данные (user/uid) в состоянии. Начни заново.")
        return

    reason, days = _parse_master_reason(message.text or "")

    # UID-ban: если days нет -> навсегда
    uid_until = None if days is None else (datetime.now(ZoneInfo("UTC")) + timedelta(days=int(days)))

    # TG-ban: если days нет -> 10 лет (как у тебя в банах пользователя)
    user_until = (datetime.now() + timedelta(days=int(days))) if days is not None else (datetime.now() + timedelta(days=365 * 10))

    # (опционально) проверка владельца UID и предупреждение в лог
    owner = await get_uid_owner(uid)
    owner_user_id = int(owner.get("user_id")) if owner and owner.get("user_id") else None
    mismatch_note = ""
    if owner_user_id and owner_user_id != user_id:
        mismatch_note = f"\n⚠️ UID принадлежит другому user_id: <code>{owner_user_id}</code>"

    # применяем оба бана
    await upsert_uid_ban(uid, banned_by=message.from_user.id, reason=reason, banned_until=uid_until)

    await execute("DELETE FROM public.user_bans WHERE user_id=$1", int(user_id))
    await execute(
        "INSERT INTO public.user_bans (user_id, banned_until, reason) VALUES ($1, $2, $3)",
        int(user_id),
        user_until,
        (reason or "").strip(),
    )

    who = f"@{username}" if username else f"id{user_id}"
    uid_txt = _mask_uid(uid)
    uid_until_txt = "навсегда" if uid_until is None else str(uid_until)

    log_text = (
        f"💣 <b>МАСТЕР-БАН</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"TG: <b>{html.escape(who)}</b> (id <code>{user_id}</code>)\n"
        f"UID: <code>{html.escape(uid_txt)}</code>\n"
        f"TG до: <b>{html.escape(str(user_until))}</b>\n"
        f"UID до: <b>{html.escape(uid_until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
        f"{mismatch_note}"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"✅ Готово.\nTG забанен: <b>{html.escape(who)}</b>\nUID забанен: <code>{html.escape(uid_txt)}</code>",
        parse_mode="HTML",
    )


@router.message(F.text == "🧹 Мастер-разбан", F.chat.type == "private")
@admin_only
async def master_unban_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ModActionFSM.waiting_for_master_unban_user)
    await message.answer(
        "🧹 <b>Мастер-разбан</b>\n\n"
        "Шаг 1/2: пришли <b>@username</b> или <b>user_id</b>.\n"
        "Можно одной строкой: <code>UID @username</code> или <code>UID user_id</code> (через пробел).",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_unban_user, F.chat.type == "private")
@admin_only
async def master_unban_got_user(message: Message, state: FSMContext):
    text = message.text or ""
    uid = _extract_uid_anywhere(text)
    user_token = _extract_user_anywhere(text)

    if uid and user_token:
        user_id, username, err = await _resolve_master_user(user_token)
        if not user_id:
            await message.answer("Нужно прислать @username или user_id.")
            return
        await state.update_data(master_user_id=int(user_id), master_username=username, master_uid=uid)
        # сразу делаем разбан
        await _do_master_unban(message, state)
        return

    user_id, username, err = await _resolve_master_user(text)
    if not user_id:
        if err == "not_in_db":
            await message.answer("Этого @username нет в базе. Пришли user_id или UID.")
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(master_user_id=int(user_id), master_username=username)
    await state.set_state(ModActionFSM.waiting_for_master_unban_uid)
    await message.answer("Шаг 2/2: пришли UID (24 hex).", parse_mode="HTML")


@router.message(ModActionFSM.waiting_for_master_unban_uid, F.chat.type == "private")
@admin_only
async def master_unban_got_uid(message: Message, state: FSMContext):
    uid = _extract_uid_anywhere(message.text or "")
    if not uid:
        await message.answer("UID должен быть 24 hex. Пришли UID ещё раз.")
        return

    await state.update_data(master_uid=uid)
    await _do_master_unban(message, state)


async def _do_master_unban(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("master_user_id") or 0)
    username = (data.get("master_username") or "").strip() or None
    uid = (data.get("master_uid") or "").strip().lower()

    did_uid = False
    did_user = False

    if uid:
        did_uid = await remove_uid_ban(uid)

    if user_id > 0:
        await unban_user(int(user_id))
        did_user = True

    who = f"@{username}" if username else f"id{user_id}"
    uid_txt = _mask_uid(uid) if uid else "—"

    log_text = (
        f"🧹 <b>МАСТЕР-РАЗБАН</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"TG: <b>{html.escape(who)}</b> ({'✅' if did_user else '⚠️'})\n"
        f"UID: <code>{html.escape(uid_txt)}</code> ({'✅' if did_uid else '⚠️'})"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"Готово.\nTG: {'✅' if did_user else '⚠️'}\nUID: {'✅' if did_uid else '⚠️'}"
    )