"""Pure parsing and formatting helpers for UID administration."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
UID_HEX_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
MASTER_REASON_RE = re.compile(r"^(\d{1,4})\s*(?:d|д)?\s+(.+)$", re.IGNORECASE)
REQUIRED_CONFIRMS = 3


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


def _parse_master_reason(text: str) -> tuple[str, int | None]:
    s = (text or "").strip()
    if not s or s in ("-", "—"):
        return "", None

    m = MASTER_REASON_RE.match(s)
    if m:
        return (m.group(2) or "").strip(), int(m.group(1))
    return s, None


__all__ = [
    "MASTER_REASON_RE",
    "REQUIRED_CONFIRMS",
    "UID_HEX_RE",
    "USERNAME_RE",
    "_days_ago",
    "_fmt_dt",
    "_mask_uid",
    "_parse_ban_reason_and_until",
    "_parse_master_reason",
    "_parse_user_ban_reason_and_until",
    "_uidv_counts",
    "_uidv_user_line",
]
