"""User-facing HTML fragments and formatting helpers for the userbot."""

from __future__ import annotations

import html
import random


RULES_TEXT = (
    "📌 <b>ПРАВИЛА СТАВОК</b>\n\n"
    "1) Ставка = <b>только ответом на пост лота</b>.\n"
    "   Ответы на ставки/флуд → удаление + мут 1 мин.\n\n"
    "2) Формат: <code>300</code>, <code>1 000</code>, <code>10k</code>/<code>10к</code>.\n\n"
    "3) Шаг валюты:\n"
    "   • 💎/🪙 → <b>кратно 10</b>\n"
    "   • 🍵 → <b>кратно 2</b>\n\n"
    "4) Ниже минимума: бот пишет «не принята», сообщение <b>не удаляет</b>.\n\n"
    "5) Нельзя редактировать/удалять ставку вручную → предупреждение.\n\n"
    "🛠 <b>Исправить ошибку (60 сек)</b> (ответом на свою ставку):\n"
    "• <code>/oops</code> — отменить\n"
    "• <code>/oops 810</code> — исправить сумму в учёте\n\n"
    "Подробнее: https://teletype.in/@velassya/karty_kr_pravila"
)

WARN_TEXTS = [
    "@{username}, предупреждение. (предов: {warnings}/4)",
    "@{username}, правила читать полезно. (предов: {warnings}/4)",
]


def user_link(user_id: int, username: str | None = None) -> str:
    """Return a safe clickable Telegram user link."""

    label = f"@{username}" if username else str(user_id)
    return f'<a href="tg://user?id={int(user_id)}">{html.escape(label)}</a>'


def mention(username: str | None, user_id: int) -> str:
    """Prefer a username while retaining a clickable id fallback."""

    return f"@{username}" if username else f'<a href="tg://user?id={user_id}">{user_id}</a>'


def random_warning(username: str | None, user_id: int, warnings: int) -> str:
    template = random.choice(WARN_TEXTS)
    return template.format(username=(username or f"id{user_id}"), warnings=warnings)


__all__ = ["RULES_TEXT", "WARN_TEXTS", "mention", "random_warning", "user_link"]
