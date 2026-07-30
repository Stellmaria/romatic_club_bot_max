"""Framework presentation helpers shared by UID admin routers.

This module creates keyboards and renders responses, but registers no handlers.
"""

from __future__ import annotations

from typing import Any, Iterable

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.uid_admin_shared import (
    REQUIRED_CONFIRMS,
    _days_ago,
    _fmt_dt,
    _mask_uid,
    _uidv_counts,
)
from bot.services.uid_verification import (
    get_uid_profile_binding,
    get_uid_verification_request,
    get_whois_admin_payload,
)


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
_REV_ALLOWED = {key for key, _ in _REV_FLAG_TITLES}
_REV_ORDER = {key: index for index, (key, _) in enumerate(_REV_FLAG_TITLES)}


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

    ver_block = "\nUID-верификация: —"
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

        ver_block = (
            f"\nUID-верификация: <b>{st}</b>\n"
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


__all__ = [
    "_REV_ALLOWED",
    "_REV_FLAG_TITLES",
    "_REV_ORDER",
    "_cnt",
    "_fmt_conf_status",
    "_kb_req_actions",
    "_kb_req_list",
    "_kb_uidv_revision",
    "_kb_verif_menu",
    "_render_req",
    "_render_uid_verif_view",
    "_render_whois",
    "_render_whois_by_uid",
    "_rev_flags_to_lines",
    "_send_media_any",
    "_sort_rev_flags",
    "_unpack_media",
    "safe_call_answer",
    "safe_edit",
]
