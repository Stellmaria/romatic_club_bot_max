"""Side-effect-free formatters for the admin interface."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from bot.core.time import to_moscow

DT_FMT = "%d.%m.%Y %H:%M:%S"
D_FMT = "%d.%m.%Y"
T_FMT = "%H:%M"

ACTION_LABELS = {
    "approve_lot": "✅ Лот подтверждён",
    "reject_lot": "❌ Лот отклонён",
    "edit_lot": "✏️ Лот отредактирован",
    "delete_lot": "🗑️ Лот удалён",
    "give_trusted": "🤝 Статус 'Доверенный' выдан",
    "remove_trusted": "❌ Статус 'Доверенный' снят",
    "add_admin": "🛠 Новый админ добавлен",
    "remove_admin": "🛠 Админ удалён",
    "add_deck": "🆕 Добавлена новая колода",
    "broadcast": "📣 Массовая рассылка отправлена",
    "add_card": "🆕 Карта добавлена",
    "add_lot": "🆕 Новая заявка на лот",
    "request_delete_lot": "🗑️ Запрос на удаление лота",
    "bind_by_template": "✅ Лот привязан по шаблону",
    "bind_success": "🔗 Лот успешно привязан",
    "missing_forward": "‼️ В обсуждении найден аукционный пост БЕЗ пересылки!",
    "move_lot": "⏱️ Перенос времени лота",
    "edit_pending": "✏️ Изменение заявки (модерация)",
    "edit_pending_field": "✏️ Изменение заявки (модерация)",
}

AUCTION_KIND_LABELS = {
    "standard": "⭐ Стандартный",
    "reverse": "✨ Обратный",
    "fast": "⚡ Быстрый",
    "free": "🪶 Свободный",
    "black": "👑 Чёрный",
    "exchange": "🛍 Биржа",
}

CURRENCY_EMOJI = {
    "кристаллы": "💎 алмазы",
    "алмазы": "💎 алмазы",
    "чашки": "🍵 чай",
    "чай": "🍵 чай",
    "сокровища": "🪙 сокровища",
}

ID_PATTERNS = (
    r"(?i)auction\s*id[:\s]*([0-9]+)",
    r"(?i)лот\s*№\s*([0-9]+)",
    r"№\s*([0-9]+)",
)


def _as_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def extract_auction_id(text: str | None) -> int | None:
    if not text:
        return None
    for pattern in ID_PATTERNS:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                pass
    return None


def format_field_change_block(
    field_title: str,
    old_value: Any,
    new_value: Any,
) -> str:
    return (
        "\n\n🧩 <b>Изменение поля</b>"
        f"\n📝 <b>Поле:</b> {html.escape(_as_str(field_title, '-'))}"
        f"\n📎 <b>Было:</b> {html.escape(_as_str(old_value, '—'))}"
        f"\n✅ <b>Стало:</b> {html.escape(_as_str(new_value, '—'))}"
    )


def _user_link_html(
    user: Mapping[str, Any] | None,
    *,
    label_prefix: str,
) -> str:
    if not user:
        return f"{label_prefix}: —"

    user_id = user.get("user_id") or user.get("id")
    username = _as_str(user.get("username"), "").strip().lstrip("@")
    full_name = _as_str(user.get("full_name"), "").strip()
    label = f"@{username}" if username else full_name or (f"id{user_id}" if user_id else "—")
    safe_label = html.escape(label)

    if user_id:
        try:
            normalized_user_id = int(user_id)
            return (
                f"{label_prefix}: "
                f"<a href='tg://user?id={normalized_user_id}'>{safe_label}</a> "
                f"(id: <code>{normalized_user_id}</code>)"
            )
        except (TypeError, ValueError):
            pass

    if username:
        safe_username = html.escape(username)
        return (
            f"{label_prefix}: "
            f"<a href='https://t.me/{safe_username}'>@{safe_username}</a>"
        )
    return f"{label_prefix}: {safe_label}"


def _auction_kind_label(value: object) -> str:
    key = _as_str(value, "standard").strip().lower() or "standard"
    return AUCTION_KIND_LABELS.get(key, AUCTION_KIND_LABELS["standard"])


def _currency_emoji(value: object) -> str:
    key = _as_str(value, "").lower()
    return CURRENCY_EMOJI.get(key, CURRENCY_EMOJI["алмазы"])


def _lot_main_info(lot: Mapping[str, Any], owners_text: str | None) -> list[str]:
    craft_value = lot.get("craft_uid_possible")
    if craft_value is True:
        craft_text = "✅ Да"
    elif craft_value is False:
        craft_text = "❌ Нет"
    else:
        craft_text = "—"

    hero_name = _as_str(lot.get("hero_name"), "").strip()
    deck_id = lot.get("deck_id")
    lines = [
        (
            "🎴 Лот №"
            f"{html.escape(str(lot.get('auction_id', '-')))}: "
            f"{html.escape(str(lot.get('card_name', '-')))}"
        )
    ]
    if hero_name:
        lines.append(f"👤 Герой: <b>{html.escape(hero_name)}</b>")
    if deck_id is not None and str(deck_id).strip():
        lines.append(f"🗂 Колода: <b>{html.escape(str(deck_id))}</b>")
    lines.extend(
        [
            f"⚙️ Тип: {html.escape(_auction_kind_label(lot.get('auction_kind')))}",
            f"🙍‍♂️ Владелец(ы): {owners_text or '-'}",
        ]
    )
    if _as_str(lot.get("auction_kind"), "standard").strip().lower() == "reverse":
        lines.append("💰 Ставки: на понижение")
    else:
        lines.append(
            "💰 Старт: "
            f"{html.escape(str(lot.get('start_price', '-')))} "
            f"{_currency_emoji(lot.get('currency'))}"
        )
    lines.append(f"🆔 Крафт на UID: {craft_text}")
    return lines


def _make_telegram_link(chat_id: int, message_id: int) -> str:
    numeric_chat_id = str(chat_id)
    if numeric_chat_id.startswith("-100"):
        numeric_chat_id = numeric_chat_id[4:]
    elif numeric_chat_id.startswith("-"):
        numeric_chat_id = numeric_chat_id[1:]
    return f"https://t.me/c/{numeric_chat_id}/{message_id}"


def _lot_block(
    lot: Mapping[str, Any],
    discussion_chat_id: int | None,
    discussion_message_id: int | None,
) -> list[str]:
    lines = [f"💬 Комментарий: {html.escape(str(lot.get('comment', '-') or '-'))}"]
    start = lot.get("start_time")
    end = lot.get("end_time")
    if start and end:
        try:
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            if isinstance(end, str):
                end = datetime.fromisoformat(end)
            if isinstance(start, datetime) and isinstance(end, datetime):
                start = to_moscow(start)
                end = to_moscow(end)
                lines.extend(
                    [
                        f"📅 Дата выхода: {start.strftime(D_FMT)}",
                        f"⏰ Время: {start.strftime(T_FMT)}–{end.strftime(T_FMT)} (МСК)",
                    ]
                )
        except (TypeError, ValueError):
            pass

    if discussion_chat_id is not None and discussion_message_id is not None:
        url = _make_telegram_link(discussion_chat_id, discussion_message_id)
        lines.append(f"<a href='{url}'>сообщение</a>")
    return lines


def format_admin_action_log(
    action: str,
    admin: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    lot: Mapping[str, Any] | None = None,
    owners_text: str | None = None,
    recipients: int | None = None,
    message_text: str | None = None,
    reason: str | None = None,
    discussion_chat_id: int | None = None,
    discussion_message_id: int | None = None,
) -> str:
    """Build the canonical Telegram HTML audit message for an admin action."""
    lines = [
        ACTION_LABELS.get(action, action),
        f"🕒 {datetime.now().strftime(DT_FMT)} (МСК)",
    ]
    if admin is not None:
        lines.append(_user_link_html(admin, label_prefix="👤 Админ"))

    if action == "add_deck" and lot is not None:
        lines.append(
            f"📚 Название колоды: {html.escape(_as_str(lot.get('deck_name'), '-'))}"
        )
    elif lot is not None:
        lines.extend(_lot_main_info(lot, owners_text))
        lines.extend(_lot_block(lot, discussion_chat_id, discussion_message_id))

    if action == "request_delete_lot" and reason:
        lines.append(f"❗️ Причина удаления: {html.escape(_as_str(reason, '-'))}")

    if action in {"give_trusted", "remove_trusted", "add_admin", "remove_admin"}:
        if target is not None:
            lines.append(_user_link_html(target, label_prefix="🙍‍♂️ Пользователь"))

    if action == "broadcast":
        if message_text:
            lines.append(f"💬 Текст рассылки: {html.escape(_as_str(message_text, ''))}")
        if recipients is not None:
            lines.append(f"📬 Получателей: {recipients}")

    lines.append(f"Действие: {html.escape(action)} через бота.")
    return "\n".join(lines)


def format_delete_request_log(
    *,
    auction_id: int,
    source: Mapping[str, Any],
    reason: object,
    lot_found: bool,
) -> str:
    """Build a deletion-request audit message from already loaded data."""
    start = source.get("start_time")
    end = source.get("end_time")
    start_msk = to_moscow(start) if isinstance(start, datetime) else None
    end_msk = to_moscow(end) if isinstance(end, datetime) else None
    date_text = (
        start_msk.strftime(D_FMT)
        if start_msk is not None
        else _as_str(source.get("date"), "-")
    )
    time_text = (
        f"{start_msk.strftime(T_FMT)}–{end_msk.strftime(T_FMT)}"
        if start_msk is not None and end_msk is not None
        else _as_str(source.get("time"), "-")
    )
    warning = "" if lot_found else (
        "\n⚠️ Лот не найден в текущем расписании "
        "(возможно перенесён/удалён)."
    )
    return (
        "🗑️ Запрос на удаление лота\n"
        f"🎴 Лот №{auction_id}: {html.escape(_as_str(source.get('card_name'), '-'))}\n"
        f"🙍‍♂️ Владелец(ы): {html.escape(_as_str(source.get('owners_text'), '-'))}\n"
        f"💰 Старт: {html.escape(_as_str(source.get('start_price'), '-'))} "
        f"{html.escape(_as_str(source.get('currency'), '-'))}\n"
        f"📅 Дата выхода: {html.escape(date_text)}\n"
        f"⏰ Время: {html.escape(time_text)} (МСК)\n"
        f"❗️ Причина удаления: {html.escape(_as_str(reason, '-'))}\n"
        "Действие: request_delete_lot через бота."
        f"{warning}"
    )


def format_owner_html(owner: Mapping[str, Any]) -> str:
    """Render one owner with links suitable for Telegram HTML messages."""
    user_id = owner.get("user_id")
    raw_username = owner.get("username")
    username = raw_username.strip() if isinstance(raw_username, str) else ""
    if not user_id:
        return "—"
    if username:
        safe_username = html.escape(username)
        return f'<a href="https://t.me/{safe_username}">@{safe_username}</a>'

    safe_user_id = html.escape(str(user_id))
    return "\n".join(
        [
            f"<code>https://t.me/{safe_user_id}</code>",
            f'<a href="tg://user?id={safe_user_id}">tg://user?id={safe_user_id}</a>',
            (
                f'<a href="tg://openmessage?user_id={safe_user_id}">'
                f"tg://openmessage?user_id={safe_user_id}</a>"
            ),
        ]
    )


def format_owners_block(owners: Iterable[Mapping[str, Any]]) -> str:
    """Render an owner list without performing database or Telegram calls."""
    items: list[str] = []
    for owner in owners:
        user_id = owner.get("user_id")
        username = owner.get("username")
        full_name = owner.get("full_name")

        label = username and f"@{username}" or full_name or user_id or "—"
        safe_label = html.escape(str(label))
        if user_id:
            items.append(f'<a href="tg://user?id={user_id}">{safe_label}</a>')
        else:
            items.append(safe_label)

    return ", ".join(items) if items else "—"


def format_pretty_owners_for_log(owners: Iterable[Mapping[str, Any]]) -> str:
    """Render owners exactly as the historical user-lot audit flow did."""

    items: list[str] = []
    for owner in owners:
        crown = "👑 " if owner.get("is_luxury", False) else ""
        username = owner.get("username")
        label = f"@{username}" if username else owner.get("full_name", owner["user_id"])
        items.append(
            f'{crown}<a href="tg://user?id={owner["user_id"]}">{label}</a>'
        )
    return ", ".join(items) if items else "-"


__all__ = [
    "extract_auction_id",
    "format_admin_action_log",
    "format_delete_request_log",
    "format_field_change_block",
    "format_owner_html",
    "format_owners_block",
    "format_pretty_owners_for_log",
]
