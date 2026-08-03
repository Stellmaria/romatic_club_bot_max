from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.legacy_config import legacy_config
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.telegram.callback_parser import split_callback_data
from db.user_list_queries import (
    PageCursor,
    UserListPage,
    list_admins_page,
    list_trusted_users_page,
    list_users_page,
)

router = Router(name=__name__)

_PAGE_SIZE = 20
_LIST_CALLBACK = "aul"
_LEGACY_LIST_CALLBACK = "admin_user_list"

_KIND_TO_CODE = {"admins": "a", "users": "u", "trusted": "t"}
_CODE_TO_KIND = {code: kind for kind, code in _KIND_TO_CODE.items()}

_TITLES = {
    "admins": "👤 <b>Администраторы</b>",
    "users": "👥 <b>Пользователи</b>",
    "trusted": "🤝 <b>Доверенные пользователи</b>",
}


@dataclass(frozen=True, slots=True)
class _ListRequest:
    kind: str
    cursor: PageCursor | None
    legacy_reset: bool = False


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
    """Compatibility helper retained for presentation-only tests."""

    username = str(row.get("username") or "").strip().casefold()
    user_id = _safe_int(row.get("user_id")) or 0
    return username or "\uffff", user_id


def _owner_ids() -> list[int]:
    result: set[int] = set()
    for raw_user_id in legacy_config.ADMINS_OWNERS:
        user_id = _safe_int(raw_user_id)
        if user_id is not None:
            result.add(user_id)
    return sorted(result)


async def _load_page(kind: str, cursor: PageCursor | None) -> UserListPage:
    values = cursor.values if cursor else ()
    if kind == "users":
        after_user_id = _safe_int(values[0]) if len(values) == 1 else None
        return await list_users_page(
            limit=_PAGE_SIZE,
            after_user_id=after_user_id,
        )
    if kind == "admins":
        after_owner_rank = _safe_int(values[0]) if len(values) == 2 else None
        after_user_id = _safe_int(values[1]) if len(values) == 2 else None
        return await list_admins_page(
            _owner_ids(),
            limit=_PAGE_SIZE,
            after_owner_rank=after_owner_rank,
            after_user_id=after_user_id,
        )
    if kind == "trusted":
        after_username = values[0] if len(values) == 2 else None
        after_user_id = _safe_int(values[1]) if len(values) == 2 else None
        return await list_trusted_users_page(
            limit=_PAGE_SIZE,
            after_username=after_username,
            after_user_id=after_user_id,
        )
    raise ValueError(f"unsupported admin user list: {kind}")


def _callback_data(kind: str, cursor: PageCursor | None = None) -> str:
    code = _KIND_TO_CODE[kind]
    if cursor is None:
        payload = f"{_LIST_CALLBACK}|{code}"
    else:
        payload = "|".join((_LIST_CALLBACK, code, *cursor.values))
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("admin user list cursor exceeds Telegram callback limit")
    return payload


def _parse_request(payload: object) -> _ListRequest | None:
    parts = split_callback_data(payload, "|")
    if not parts:
        return None

    if parts[0] == _LEGACY_LIST_CALLBACK:
        if len(parts) != 3:
            return None
        if parts[1] == "close":
            return _ListRequest("close", None)
        if parts[1] not in _TITLES or _safe_int(parts[2]) is None:
            return None
        return _ListRequest(parts[1], None, legacy_reset=parts[2] != "0")

    if parts[0] != _LIST_CALLBACK or len(parts) < 2:
        return None
    if parts[1] == "x" and len(parts) == 2:
        return _ListRequest("close", None)

    kind = _CODE_TO_KIND.get(parts[1])
    if kind is None:
        return None
    values = tuple(parts[2:])
    expected_values = {"users": {0, 1}, "admins": {0, 2}, "trusted": {0, 2}}
    if len(values) not in expected_values[kind]:
        return None
    if kind == "users" and values and _safe_int(values[0]) is None:
        return None
    if kind == "admins" and values:
        rank = _safe_int(values[0])
        user_id = _safe_int(values[1])
        if rank not in {0, 1} or user_id is None:
            return None
    if kind == "trusted" and values:
        if not values[0] or _safe_int(values[1]) is None:
            return None
    return _ListRequest(kind, PageCursor(values) if values else None)


def _format_row(kind: str, row: dict[str, Any], number: int) -> str:
    if kind == "admins":
        marker = "👑" if row.get("is_owner") else "🛡"
    elif kind == "trusted":
        marker = "👑🤝" if row.get("is_luxury") else "🤝"
    else:
        marker = "👑" if row.get("is_luxury") else "👤"

    suffix = " · владелец" if kind == "admins" and row.get("is_owner") else ""
    return f"{number}. {marker} {_user_ref(row)}{suffix}"


def _list_keyboard(
    kind: str,
    current_cursor: PageCursor | None,
    next_cursor: PageCursor | None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    navigation: list[InlineKeyboardButton] = []
    if current_cursor is not None:
        navigation.append(
            InlineKeyboardButton(
                text="⏮ В начало",
                callback_data=_callback_data(kind),
            )
        )
    if next_cursor is not None:
        navigation.append(
            InlineKeyboardButton(
                text="Далее ➡️",
                callback_data=_callback_data(kind, next_cursor),
            )
        )
    if navigation:
        kb.row(*navigation)
    else:
        kb.row(
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=_callback_data(kind),
            )
        )
    kb.row(InlineKeyboardButton(text="✖ Закрыть", callback_data=f"{_LIST_CALLBACK}|x"))
    return kb.as_markup()


def _render_page(
    kind: str,
    page: UserListPage,
    current_cursor: PageCursor | None,
) -> tuple[str, InlineKeyboardMarkup]:
    rows = page.rows
    lines = [
        _TITLES[kind],
        f"Показано: <b>{len(rows)}</b> · SQL keyset pagination",
        "",
    ]
    if rows:
        lines.extend(
            _format_row(kind, row, number)
            for number, row in enumerate(rows, start=1)
        )
    else:
        lines.append("Список пуст.")
    if page.next_cursor is not None:
        lines.extend(("", "Есть следующая страница."))

    return (
        "\n".join(lines),
        _list_keyboard(kind, current_cursor, page.next_cursor),
    )


async def _send_list(message: Message, kind: str) -> None:
    page = await _load_page(kind, None)
    text, keyboard = _render_page(kind, page, None)
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


@router.callback_query(
    F.data.startswith(f"{_LIST_CALLBACK}|")
    | F.data.startswith(f"{_LEGACY_LIST_CALLBACK}|")
)
@admin_only
async def paginate_admin_user_list(call: CallbackQuery) -> None:
    request = _parse_request(call.data)
    if request is None:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение недоступно.", show_alert=True)
        return

    if request.kind == "close":
        try:
            await message.delete()
        except Exception:
            pass
        await call.answer()
        return

    page = await _load_page(request.kind, request.cursor)
    text, keyboard = _render_page(request.kind, page, request.cursor)
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
    if request.legacy_reset:
        await call.answer("Список обновлён, навигация начата заново.")
    else:
        await call.answer()


__all__ = [
    "router",
    "show_admins_list",
    "show_users_list",
    "show_trusted_list",
    "paginate_admin_user_list",
]
