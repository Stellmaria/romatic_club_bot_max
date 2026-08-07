"""Canonical Telegram HTML formatters for operational audit messages."""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from bot.core.time import moscow_now, to_moscow

DT_FMT = "%d.%m.%Y %H:%M:%S"

EXCHANGE_MODE_LABELS = {
    "card": "Одна карта",
    "deck": "Колода целиком",
    "deck_split": "Разбор колоды",
}

CURRENCY_LABELS = {
    "кристаллы": "💎 алмазы",
    "алмазы": "💎 алмазы",
    "diamond": "💎 алмазы",
    "diamonds": "💎 алмазы",
    "чашки": "🍵 чай",
    "чай": "🍵 чай",
    "cup": "🍵 чай",
    "cups": "🍵 чай",
    "tea": "🍵 чай",
    "сокровища": "🪙 сокровища",
    "treasure": "🪙 сокровища",
    "treasures": "🪙 сокровища",
}


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def format_audit_timestamp(value: datetime | None = None) -> str:
    """Render an audit timestamp using the canonical Moscow business timezone."""

    moment = moscow_now() if value is None else to_moscow(value)
    return f"🕒 {moment.strftime(DT_FMT)} (МСК)"


def format_action_footer(action: str) -> str:
    """Render one canonical action footer without duplicated transport wording."""

    normalized = str(action or "unknown").strip().rstrip(".")
    if normalized.endswith(" через бота"):
        normalized = normalized[: -len(" через бота")].rstrip()
    return f"Действие: <code>{html.escape(normalized)}</code> через бота."


def format_user_ref(
    *,
    user_id: int | None,
    username: str | None = None,
    full_name: str | None = None,
) -> str:
    """Render a clickable Telegram user reference with a stable numeric ID."""

    clean_username = str(username or "").strip().lstrip("@")
    clean_name = str(full_name or "").strip()
    if user_id:
        uid = int(user_id)
        label = f"@{clean_username}" if clean_username else clean_name or f"id{uid}"
        return (
            f"<a href='tg://user?id={uid}'>{html.escape(label)}</a> "
            f"(id: <code>{uid}</code>)"
        )
    if clean_username:
        safe_username = html.escape(clean_username)
        return f"<a href='https://t.me/{safe_username}'>@{safe_username}</a>"
    return html.escape(clean_name or "—")


def format_exchange_mode(value: object) -> str:
    key = _as_str(value, "").strip().lower()
    return EXCHANGE_MODE_LABELS.get(key, _as_str(value, "—") or "—")


def format_currency(value: object) -> str:
    key = _as_str(value, "").strip().lower()
    if key in CURRENCY_LABELS:
        return CURRENCY_LABELS[key]
    return html.escape(_as_str(value, "—") or "—")


def _compat_timestamp(value: str | None) -> str:
    text = str(value or "").strip()
    return f"🕒 {html.escape(text)} (МСК)" if text else format_audit_timestamp()


def format_exchange_new_request_log(
    *,
    batch_id: int,
    created_at_msk: str | None,
    sender_username: str | None,
    sender_id: int | None,
    deck_id: int | None,
    deck_name: str | None,
    mode: str,
    items_count: int,
    price: int | None,
    currency: str,
    has_proof: bool,
    comment: str | None,
) -> str:
    """Format a newly submitted exchange batch in the canonical audit style."""

    sender = format_user_ref(user_id=sender_id, username=sender_username)
    deck_title = str(deck_name or "").strip() or (f"№{deck_id}" if deck_id else "—")
    price_text = "—" if price is None else str(int(price))
    comment_text = str(comment or "").strip() or "-"
    return "\n".join(
        [
            "🛒 <b>Новая заявка на биржу</b>",
            _compat_timestamp(created_at_msk),
            f"🙍‍♂️ Отправитель: {sender}",
            f"🆔 Batch: <code>{int(batch_id)}</code>",
            f"📚 Колода: <b>{html.escape(deck_title)}</b>",
            f"🧩 Режим: <b>{html.escape(format_exchange_mode(mode))}</b>",
            f"🃏 Карт: <b>{int(items_count)}</b>",
            f"💰 Цена: <b>{price_text} {format_currency(currency)}</b>",
            f"📸 Пруф: <b>{'✅ Да' if has_proof else '❌ Нет'}</b>",
            f"💬 Комментарий: <i>{html.escape(comment_text)}</i>",
            format_action_footer("exchange_add_request"),
        ]
    )


def format_exchange_approved_log(
    *,
    created_at_msk: str | None,
    batch_id: int,
    admin_html: str,
    user_html: str,
    deck_title: str,
    mode: str,
    items_count: int,
    price: int | None,
    currency: str,
    has_proof: bool,
    comment: str | None,
    items_preview: list[str] | None = None,
) -> str:
    """Format exchange approval using the same field order as submission logs."""

    price_text = "—" if price is None else str(int(price))
    comment_text = str(comment or "").strip() or "-"
    lines = [
        "✅ <b>Биржа: заявка одобрена</b>",
        _compat_timestamp(created_at_msk),
        f"👤 Админ: {admin_html}",
        f"🙍‍♂️ Отправитель: {user_html}",
        f"🆔 Batch: <code>{int(batch_id)}</code>",
        f"📚 Колода: <b>{html.escape(str(deck_title or '—'))}</b>",
        f"🧩 Режим: <b>{html.escape(format_exchange_mode(mode))}</b>",
        f"🃏 Карт: <b>{int(items_count)}</b>",
        f"💰 Цена: <b>{price_text} {format_currency(currency)}</b>",
        f"📸 Пруф: <b>{'✅ Да' if has_proof else '❌ Нет'}</b>",
        f"💬 Комментарий: <i>{html.escape(comment_text)}</i>",
    ]
    preview = list(items_preview or [])
    if preview:
        lines.extend(["", "🃏 <b>Состав (превью):</b>", *preview])
    lines.append(format_action_footer("exchange_approve"))
    return "\n".join(lines)


def format_exchange_moderation_log(
    *,
    action_title: str,
    action_code: str,
    when_msk: str | None,
    admin_user: Any,
    batch_id: int,
    sender_username: str | None,
    sender_id: int | None,
    deck_name: str,
    deck_id: int | None,
    mode: str,
    items_count: int,
    price: int | None,
    currency: str,
    has_proof: bool,
    comment: str | None,
    moderator_comment: str | None,
) -> str:
    """Format exchange rejection/deletion with canonical labels and footer."""

    admin = format_user_ref(
        user_id=getattr(admin_user, "id", None),
        username=getattr(admin_user, "username", None),
        full_name=getattr(admin_user, "full_name", None),
    )
    sender = format_user_ref(user_id=sender_id, username=sender_username)
    deck_title = str(deck_name or "").strip() or (f"№{deck_id}" if deck_id else "—")
    price_text = "—" if price is None else str(int(price))
    comment_text = str(comment or "").strip() or "-"
    moderator_text = str(moderator_comment or "").strip() or "—"
    icon = "🗑️" if "delete" in action_code else "❌"
    return "\n".join(
        [
            f"{icon} <b>{html.escape(action_title)}</b>",
            _compat_timestamp(when_msk),
            f"👤 Админ: {admin}",
            f"🙍‍♂️ Отправитель: {sender}",
            f"🆔 Batch: <code>{int(batch_id)}</code>",
            f"📚 Колода: <b>{html.escape(deck_title)}</b>",
            f"🧩 Режим: <b>{html.escape(format_exchange_mode(mode))}</b>",
            f"🃏 Карт: <b>{int(items_count)}</b>",
            f"💰 Цена: <b>{price_text} {format_currency(currency)}</b>",
            f"📸 Пруф: <b>{'✅ Да' if has_proof else '❌ Нет'}</b>",
            f"💬 Комментарий: <i>{html.escape(comment_text)}</i>",
            f"📝 Комментарий модератора: <i>{html.escape(moderator_text)}</i>",
            format_action_footer(action_code),
        ]
    )


def format_bid_log(
    *,
    auction_id: int,
    bidder_id: int,
    bidder_username: str | None,
    amount: int,
    currency: object,
    message_id: int,
) -> str:
    """Format an accepted bid audit record."""

    return "\n".join(
        [
            "💬 <b>Новая ставка</b>",
            format_audit_timestamp(),
            (
                "🙍‍♂️ Участник: "
                + format_user_ref(user_id=bidder_id, username=bidder_username)
            ),
            f"🎴 Лот №<code>{int(auction_id)}</code>",
            f"💰 Ставка: <b>{int(amount)} {format_currency(currency)}</b>",
            f"💬 msg_id: <code>{int(message_id)}</code>",
            format_action_footer("place_bid"),
        ]
    )


def format_admin_bid_deleted_log(
    *,
    admin_id: int,
    admin_username: str | None,
    auction_id: int,
    bidder_id: int,
    bidder_username: str | None,
    amount: int,
    currency: object,
    warnings_count: int,
    is_banned: bool,
) -> str:
    """Format a moderator-deleted bid with the context needed for audit."""

    return "\n".join(
        [
            "🗑️ <b>Ставка удалена администратором</b>",
            format_audit_timestamp(),
            "👤 Админ: "
            + format_user_ref(user_id=admin_id, username=admin_username),
            "🙍‍♂️ Участник: "
            + format_user_ref(user_id=bidder_id, username=bidder_username),
            f"🎴 Лот №<code>{int(auction_id)}</code>",
            f"💰 Ставка: <b>{int(amount)} {format_currency(currency)}</b>",
            f"⚠️ Предупреждений: <b>{int(warnings_count)}/4</b>",
            f"🚫 Бан: <b>{'Да' if is_banned else 'Нет'}</b>",
            format_action_footer("delete_bid"),
        ]
    )


def format_audit_event(
    *,
    title: str,
    action: str,
    actor: Mapping[str, object] | None = None,
    details: Iterable[str] = (),
) -> str:
    """Build a small canonical audit message for operational one-off events."""

    lines = [title, format_audit_timestamp()]
    if actor is not None:
        lines.append(
            "👤 Админ: "
            + format_user_ref(
                user_id=actor.get("user_id") or actor.get("id"),  # type: ignore[arg-type]
                username=_as_str(actor.get("username"), "") or None,
                full_name=_as_str(actor.get("full_name"), "") or None,
            )
        )
    lines.extend(str(line) for line in details if str(line).strip())
    lines.append(format_action_footer(action))
    return "\n".join(lines)


__all__ = [
    "format_action_footer",
    "format_admin_bid_deleted_log",
    "format_audit_event",
    "format_audit_timestamp",
    "format_bid_log",
    "format_currency",
    "format_exchange_approved_log",
    "format_exchange_moderation_log",
    "format_exchange_mode",
    "format_exchange_new_request_log",
    "format_user_ref",
]
