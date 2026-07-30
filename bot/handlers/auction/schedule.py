from __future__ import annotations

import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from html import escape

from aiogram import F, Router, types
from aiogram.filters import Command

from bot.core.time import to_moscow, utc_now
from bot.handlers.admin.services.schedule import _chunks
from bot.handlers.helper.helpers_users import _deck_tag, _emoji_by_currency
from db.db import (
    get_auctions_by_card_ref,
    get_auctions_in_range,
    is_admin,
    is_luxury_user,
)

router = Router(name="auction_schedule")

WORK_START = time(11, 0)
WORK_END = time(22, 31)
LUX_START = time(11, 0)
LUX_END = time(22, 31)
REG_START = time(12, 0)
REG_END = time(20, 31)
SLOT = timedelta(minutes=30)

_BR_RE = re.compile(r"(?i)<br\s*/?>")
_MONTHS_RU = {
    "янв": 1,
    "январь": 1,
    "фев": 2,
    "февраль": 2,
    "мар": 3,
    "март": 3,
    "апр": 4,
    "апрель": 4,
    "май": 5,
    "июн": 6,
    "июнь": 6,
    "июл": 7,
    "июль": 7,
    "авг": 8,
    "август": 8,
    "сен": 9,
    "сентябрь": 9,
    "окт": 10,
    "октябрь": 10,
    "ноя": 11,
    "ноябрь": 11,
    "дек": 12,
    "декабрь": 12,
}


def _tg_clean(text: str) -> str:
    return _BR_RE.sub("\n", text or "")


def _today_msk() -> date:
    return to_moscow(utc_now()).date()


def _parse_gaps_day(value: str | None, *, today: date | None = None) -> date | None:
    """Parse the single-day syntax accepted by ``/gaps``."""
    if not value or not value.strip():
        return None

    today = today or _today_msk()
    raw = value.strip().lower()
    if raw in {"сегодня", "today"}:
        return today
    if raw in {"завтра", "tomorrow"}:
        return today + timedelta(days=1)

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        pass

    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", raw)
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    if year_raw is None:
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        if candidate < today:
            try:
                candidate = date(today.year + 1, month, day)
            except ValueError:
                return None
        return candidate

    year = int(year_raw)
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_bounds(
    value: str | None,
    *,
    today: date | None = None,
) -> tuple[datetime, datetime, str]:
    """Parse the month syntax accepted by ``/gaps``."""
    today = today or _today_msk()
    year, month = today.year, today.month

    if value:
        raw = value.strip().lower().replace("/", "-").replace(".", "-")
        if raw in _MONTHS_RU:
            month = _MONTHS_RU[raw]
            year += int(month < today.month)
        elif re.fullmatch(r"\d{4}-\d{1,2}", raw):
            year, month = map(int, raw.split("-", 1))
        elif re.fullmatch(r"\d{1,2}-\d{4}", raw):
            month, year = map(int, raw.split("-", 1))
        elif re.fullmatch(r"\d{1,2}", raw):
            month = int(raw)
            year += int(month < today.month)
        else:
            parts = raw.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                if len(parts[0]) == 4:
                    year, month = map(int, parts)
                elif len(parts[1]) == 4:
                    month, year = map(int, parts)

    try:
        start = datetime(year, month, 1)
    except ValueError:
        start = datetime(today.year, today.month, 1)
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)
    return start, end, start.strftime("%m.%Y")


def _slot_iter_range(day: date, start: time, end: time) -> list[datetime]:
    current = datetime.combine(day, start)
    last = datetime.combine(day, end)
    result: list[datetime] = []
    while current <= last:
        result.append(current)
        current += SLOT
    return result


def _contiguous_blocks(slots: list[datetime]) -> list[tuple[datetime, datetime]]:
    if not slots:
        return []
    ordered = sorted(slots)
    result: list[tuple[datetime, datetime]] = []
    block_start = previous = ordered[0]
    for current in ordered[1:]:
        if current - previous == SLOT:
            previous = current
            continue
        result.append((block_start, previous + SLOT))
        block_start = previous = current
    result.append((block_start, previous + SLOT))
    return result


def _format_slots(slots: list[datetime]) -> str:
    if not slots:
        return "0 слотов — —"
    count = len(slots)
    n10, n100 = count % 10, count % 100
    if n10 == 1 and n100 != 11:
        word = "слот"
    elif n10 in (2, 3, 4) and not 12 <= n100 <= 14:
        word = "слота"
    else:
        word = "слотов"
    return f"{count} {word} — {', '.join(slot.strftime('%H:%M') for slot in slots)}"


def _format_blocks(slots: list[datetime]) -> list[str]:
    result: list[str] = []
    for start, end in _contiguous_blocks(slots):
        left = start.strftime("%H:%M")
        right = (end - SLOT).strftime("%H:%M")
        result.append(left if left == right else f"{left}–{right}")
    return result


async def _has_schedule_access(user_id: int) -> bool:
    return await is_admin(user_id) or await is_luxury_user(user_id)


@router.message(Command("when"), F.chat.type == "private")
async def cmd_when(message: types.Message) -> None:
    if not await _has_schedule_access(message.from_user.id):
        await message.answer(
            "Команда доступна только администраторам и Лакшери-пользователям.",
            protect_content=True,
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/when &lt;имя героя или card_id&gt;</code>",
            parse_mode="HTML",
            protect_content=True,
        )
        return

    raw = parts[1].strip()
    show_all = raw.lower().endswith(" all")
    if show_all:
        raw = raw[:-4].strip()
    statuses = ["pending", "scheduled", "active"]
    if show_all:
        statuses.append("finished")

    lots = await get_auctions_by_card_ref(raw, statuses=statuses)
    if not lots:
        await message.answer(
            "Ничего не найдено среди лотов с выбранной картой "
            "(card_id обязателен при оформлении).",
            protect_content=True,
        )
        return

    by_day: dict[date, list[dict]] = defaultdict(list)
    for lot in lots:
        by_day[to_moscow(lot["start_time"]).date()].append(lot)

    header = "🗓 Даты для карты" if raw.isdigit() else "🗓 Даты для героя"
    lines = [f"{header} «{escape(raw)}»:\n"]
    for day in sorted(by_day):
        lines.append(f"<b>{day.strftime('%d.%m.%Y')}</b>")
        seen: set[tuple[str, object]] = set()
        for lot in sorted(by_day[day], key=lambda item: to_moscow(item["start_time"])):
            start = to_moscow(lot["start_time"]).strftime("%H:%M")
            key = (start, lot.get("auction_id"))
            if key in seen:
                continue
            seen.add(key)
            price = lot.get("start_price")
            price_part = (
                f"  {price} {_emoji_by_currency(lot.get('currency'))}"
                if isinstance(price, int)
                else ""
            )
            lines.append(
                f"{start} 🃏({escape(lot.get('hero_name') or '-')})"
                f"{_deck_tag(lot.get('deck_id'))} "
                f"{escape(lot.get('card_name') or '-')}{price_part}"
            )
        lines.append("")

    await message.answer(
        "\n".join(lines).strip(),
        parse_mode="HTML",
        protect_content=True,
    )


@router.message(Command("gaps"), F.chat.type == "private")
async def cmd_gaps(message: types.Message) -> None:
    if not await _has_schedule_access(message.from_user.id):
        await message.answer(
            "Команда доступна только администраторам и Лакшери-пользователям.",
            protect_content=True,
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    argument = parts[1].strip() if len(parts) > 1 else None
    if argument and argument.lower() in {"help", "?", "помощь", "хелп", "-h", "--help"}:
        await message.answer(
            _tg_clean(
                "🕳 <b>/gaps — свободные места (дыры) в расписании</b>\n\n"
                "✅ <b>Месяц</b>:\n"
                "• <code>/gaps 2026-02</code>\n"
                "• <code>/gaps 02.2026</code>\n"
                "• <code>/gaps февраль</code> / <code>/gaps фев</code>\n"
                "• <code>/gaps 2</code>\n\n"
                "✅ <b>Один день</b>:\n"
                "• <code>/gaps 2026-01-15</code>\n"
                "• <code>/gaps 15.01</code>\n"
                "• <code>/gaps 15.01.2026</code>\n"
                "• <code>/gaps сегодня</code> / <code>/gaps завтра</code>\n\n"
                "ℹ️ Если год не указан, бот подставит текущий. Если дата/месяц уже "
                "прошли, возьмёт следующий год."
            ),
            parse_mode="HTML",
            protect_content=True,
        )
        return

    today = _today_msk()
    one_day = _parse_gaps_day(argument, today=today)
    if one_day:
        range_start = datetime.combine(one_day, time())
        range_end = range_start + timedelta(days=1)
        label = one_day.strftime("%d.%m.%Y")
    else:
        range_start, range_end, label = _month_bounds(argument, today=today)

    now = to_moscow(utc_now()).replace(tzinfo=None)
    lots = await get_auctions_in_range(
        range_start,
        range_end,
        statuses=["scheduled", "active"],
    )
    busy_starts: dict[date, set[time]] = defaultdict(set)
    for auction in lots:
        start = to_moscow(auction["start_time"]).replace(tzinfo=None)
        rounded_minute = 0 if start.minute < 30 else 30
        busy_starts[start.date()].add(time(start.hour, rounded_minute))

    def free_slots(day: date, start: time, end: time) -> list[datetime]:
        taken = busy_starts.get(day, set())
        slots = _slot_iter_range(day, start, end)
        if day == today:
            slots = [slot for slot in slots if slot >= now]
        return [slot for slot in slots if slot.time() not in taken]

    if one_day:
        show_free = free_slots(one_day, WORK_START, WORK_END)
        lux_free = free_slots(one_day, LUX_START, LUX_END)
        regular_free = free_slots(one_day, REG_START, REG_END)
        segments = _format_blocks(show_free)
        if not (segments or lux_free or regular_free):
            await message.answer(
                f"🎯 На <b>{label}</b> свободных слотов нет.",
                parse_mode="HTML",
                protect_content=True,
            )
            return

        lines = [
            f"🕳 Свободные слоты на <b>{label}</b> "
            f"(вывод: {WORK_START:%H:%M}–{WORK_END:%H:%M}, шаг 30 мин)\n",
            f"<b>Показ</b>: {', '.join(segments) if segments else '—'}",
            f"<b>Лакшери</b>: {_format_slots(lux_free)}",
            f"<b>Обычные</b>: {_format_slots(regular_free)}",
            "",
            f"Итого свободных стартов (показ): <b>{len(show_free)}</b>",
        ]
        await message.answer(
            _tg_clean("\n".join(lines)),
            parse_mode="HTML",
            protect_content=True,
        )
        return

    year, month = range_start.year, range_start.month
    first_day = today.day if (year, month) == (today.year, today.month) else 1
    lines = [
        f"🕳 Свободные слоты на <b>{label}</b> "
        f"(вывод: {WORK_START:%H:%M}–{WORK_END:%H:%M}, шаг 30 мин)\n"
    ]
    total_free = 0
    for day_number in range(first_day, monthrange(year, month)[1] + 1):
        day = date(year, month, day_number)
        show_free = free_slots(day, WORK_START, WORK_END)
        lux_free = free_slots(day, LUX_START, LUX_END)
        regular_free = free_slots(day, REG_START, REG_END)
        total_free += len(show_free)
        segments = _format_blocks(show_free)
        if segments or lux_free or regular_free:
            lines.append(
                f"<b>{day:%d.%m}</b>: {', '.join(segments) if segments else '—'}"
                f"  (Л: {_format_slots(lux_free)}; О: {_format_slots(regular_free)})"
            )

    if len(lines) == 1:
        await message.answer(
            f"🎯 На оставшиеся дни {label} свободных слотов нет.",
            parse_mode="HTML",
            protect_content=True,
        )
        return

    lines.append(
        f"\nИтого свободных слотов (по окну {WORK_START:%H:%M}–{WORK_END:%H:%M}): "
        f"<b>{total_free}</b>"
    )
    for part in _chunks(_tg_clean("\n".join(lines))):
        await message.answer(part, parse_mode="HTML", protect_content=True)
