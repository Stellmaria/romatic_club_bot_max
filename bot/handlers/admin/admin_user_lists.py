from __future__ import annotations

import html
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.helper.new.wrapper import admin_only
from db.admin import list_admins
from db.users import get_all_trusted_users, get_all_users
from bot.telegram.callback_parser import split_callback_data


router = Router(name=__name__)

_PAGE_SIZE = 20
_LIST_CALLBACK = "admin_user_list"


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _display_name(row: dict[str, Any]) -> str:
    username = str(row.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    user_id = _safe_int(row.get("user_id"))
    if user_id is not None:
        return f"id:{user_id}"
    return "без идентификатора"


def _user_ref(row: dict[str, Any]) -> str:
    label = html.escape(_display_name(row), quote=False)
    user_id = _safe_int(row.get("user_id"))
    if user_id is None:
        return label
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def _sort_key(row: dict[str, Any]) -> tuple[str, int]:
    username = str(row.get("username") or "").strip().casefold()
    user_id = _safe_int(row.get("user_id")) or 0
    return username or "\uffff", user_id


async def _load_admins() -> list[dict[str, Any]]:
    database_admins, users = await list_admins(), await get_all_users()
    users_by_id = {
        int(row["user_id"]): dict(row)
        for row in users
        if _safe_int(row.get("user_id")) is not None
    }

    merged: dict[int, dict[str, Any]] = {}
    for raw in database_admins:
        row = dict(raw)
        user_id = _safe_int(row.get("user_id"))
        if user_id is None:
            continue
        user = users_by_id.get(user_id, {})
        merged[user_id] = {
            "user_id": user_id,
            "username": row.get("username") or user.get("username"),
            "is_owner": False,
        }

    for raw_owner_id in ADMINS_OWNERS:
        owner_id = _safe_int(raw_owner_id)
        if owner_id is None:
            continue
        user = users_by_id.get(owner_id, {})
        current = merged.setdefault(
            owner_id,
            {
                "user_id": owner_id,
                "username": user.get("username"),
                "is_owner": True,
            },
        )
        current["is_owner"] = True
        current["username"] = current.get("username") or user.get("username")

    return sorted(
        merged.values(),
        key=lambda row: (not bool(row.get("is_owner")), *_sort_key(row)),
    )


async def _load_users() -> list[dict[str, Any]]:
    return sorted((dict(row) for row in await get_all_users()), key=_sort_key)


async def _load_trusted() -> list[dict[str, Any]]:
    return sorted((dict(row) for row in await get_all_trusted_users()), key=_sort_key)


_LOADERS: dict[str, Callable[[], Awaitable[list[dict[str, Any]]]]] = {
    "admins": _load_admins,
    "users": _load_users,
    "trusted": _load_trusted,
}

_TITLES = {
    "admins": "👤 <b>Администраторы</b>",
    "users": "👥 <b>Пользователи</b>",
    "trusted": "🤝 <b>Доверенные пользователи</b>",
}


def _format_row(kind: str, row: dict[str, Any], number: int) -> str:
    if kind == "admins":
        marker = "👑" if row.get("is_owner") else "🛡"
    elif kind == "trusted":
        marker = "👑🤝" if row.get("is_luxury") else "🤝"
    else:
        marker = "👑" if row.get("is_luxury") else "👤"

    suffix = " · владелец" if kind == "admins" and row.get("is_owner") else ""
    return f"{number}. {marker} {_user_ref(row)}{suffix}"


def _list_keyboard(kind: str, page: int, pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"{_LIST_CALLBACK}|{kind}|{page - 1}",
            )
        )
    navigation.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{pages}",
            callback_data="noop",
        )
    )
    if page + 1 < pages:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"{_LIST_CALLBACK}|{kind}|{page + 1}",
            )
        )
    kb.row(*navigation)
    kb.row(InlineKeyboardButton(text="✖ Закрыть", callback_data=f"{_LIST_CALLBACK}|close|0"))
    return kb.as_markup()


def _render_page(
    kind: str,
    rows: Sequence[dict[str, Any]],
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    total = len(rows)
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    start = page * _PAGE_SIZE
    chunk = rows[start : start + _PAGE_SIZE]

    lines = [
        _TITLES[kind],
        f"Всего: <b>{total}</b> · страница <b>{page + 1}/{pages}</b>",
        "",
    ]
    if chunk:
        lines.extend(
            _format_row(kind, row, start + index)
            for index, row in enumerate(chunk, start=1)
        )
    else:
        lines.append("Список пуст.")

    return "\n".join(lines), _list_keyboard(kind, page, pages)


async def _send_list(message: Message, kind: str, page: int = 0) -> None:
    rows = await _LOADERS[kind]()
    text, keyboard = _render_page(kind, rows, page)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@router.message(F.text == "👤 Список админов", F.chat.type == "private")
@admin_only
async def show_admins_list(message: Message) -> None:
    await _send_list(message, "admins")


@router.message(F.text == "👥 Список пользователей", F.chat.type == "private")
@admin_only
async def show_users_list(message: Message) -> None:
    await _send_list(message, "users")


@router.message(F.text == "🤝 Список доверенных", F.chat.type == "private")
@admin_only
async def show_trusted_list(message: Message) -> None:
    await _send_list(message, "trusted")


@router.callback_query(F.data.startswith(f"{_LIST_CALLBACK}|"))
@admin_only
async def paginate_admin_user_list(call: CallbackQuery) -> None:
    try:
        _, kind, raw_page = split_callback_data(str(call.data or ""), "|", 2)
        page = int(raw_page)
    except (TypeError, ValueError):
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение недоступно.", show_alert=True)
        return

    if kind == "close":
        try:
            await message.delete()
        except Exception:
            pass
        await call.answer()
        return

    loader = _LOADERS.get(kind)
    if loader is None:
        await call.answer("Неизвестный список.", show_alert=True)
        return

    rows = await loader()
    text, keyboard = _render_page(kind, rows, page)
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    await call.answer()


__all__ = [
    "router",
    "show_admins_list",
    "show_users_list",
    "show_trusted_list",
    "paginate_admin_user_list",
]
