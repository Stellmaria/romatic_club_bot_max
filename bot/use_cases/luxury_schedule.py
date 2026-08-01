"""Application use case for the read-only luxury schedule flow."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from html import escape as html_escape
from typing import Awaitable, Callable, Mapping, MutableMapping, Sequence

Lot = MutableMapping[str, object]


@dataclass(frozen=True)
class LuxuryScheduleView:
    selected_date: date
    messages: tuple[str, ...]
    has_lots: bool


class LuxuryScheduleAccessDenied(PermissionError):
    """Raised when the actor is not entitled to the luxury schedule."""


class LuxuryScheduleUseCase:
    def __init__(
        self,
        *,
        is_luxury_user: Callable[[int], Awaitable[bool]],
        get_all_decks: Callable[[], Awaitable[Sequence[Mapping[str, object]]]],
        get_last_nonempty_deck_id: Callable[[], Awaitable[int]],
        get_lots: Callable[[date], Awaitable[list[Lot]]],
        get_cards_meta: Callable[[list[int]], Awaitable[Mapping[int, Mapping[str, object]]]],
        get_max_obtain_for_rarity: Callable[[str], Awaitable[Mapping[str, object] | None]],
        get_obtain_variants_for_rarity: Callable[[str], Awaitable[object]],
        get_deck_treasure_sum: Callable[[int], Awaitable[int]],
        get_deck_obtain_totals: Callable[[int], Awaitable[Mapping[str, object]]],
    ) -> None:
        self._is_luxury_user = is_luxury_user
        self._get_all_decks = get_all_decks
        self._get_last_nonempty_deck_id = get_last_nonempty_deck_id
        self._get_lots = get_lots
        self._get_cards_meta = get_cards_meta
        self._get_max_obtain_for_rarity = get_max_obtain_for_rarity
        self._get_obtain_variants_for_rarity = get_obtain_variants_for_rarity
        self._get_deck_treasure_sum = get_deck_treasure_sum
        self._get_deck_obtain_totals = get_deck_obtain_totals

    async def execute(self, *, user_id: int, selected_date: date) -> LuxuryScheduleView:
        if not await self._is_luxury_user(user_id):
            raise LuxuryScheduleAccessDenied

        decks = await self._get_all_decks()
        deck_name = {int(row["deck_id"]): str(row["deck_name"]) for row in decks}
        last_nonempty = await self._get_last_nonempty_deck_id()
        decks_range = f"Колоды 1 — {last_nonempty}" if last_nonempty > 0 else "Колоды —"

        lots = await self._get_lots(selected_date)
        if not lots:
            return LuxuryScheduleView(
                selected_date=selected_date,
                messages=(f"На {selected_date.strftime('%d.%m.%Y')} лотов нет.",),
                has_lots=False,
            )

        await self._enrich_lots(lots)
        blocks = await self._render_blocks(
            lots,
            selected_date=selected_date,
            deck_name=deck_name,
            decks_range=decks_range,
        )
        return LuxuryScheduleView(
            selected_date=selected_date,
            messages=tuple(pack_blocks(blocks)),
            has_lots=True,
        )

    async def _enrich_lots(self, lots: list[Lot]) -> None:
        card_ids = [int(lot["card_id"]) for lot in lots if lot.get("card_id")]
        if not card_ids:
            return
        metadata = await self._get_cards_meta(card_ids)
        for lot in lots:
            card_id = int(lot.get("card_id") or 0)
            meta = metadata.get(card_id)
            if not meta:
                continue
            for key in ("hero_name", "deck_id", "rarity"):
                lot.setdefault(key, meta.get(key))
            if lot.get("gifts_cups") is None:
                lot["gifts_cups"] = meta.get("gifts_cups")
            if lot.get("gifts_diamonds") is None:
                lot["gifts_diamonds"] = meta.get("gifts_diamonds")

    async def _render_blocks(
        self,
        lots: list[Lot],
        *,
        selected_date: date,
        deck_name: Mapping[int, str],
        decks_range: str,
    ) -> list[str]:
        blocks = [f"🗓 <b>Аукционы на {selected_date.strftime('%d.%m.%Y')}</b> (МСК):"]
        rarity_max_cache: dict[str, Mapping[str, object]] = {}
        deck_totals_cache: dict[int, Mapping[str, object]] = {}
        deck_treasure_cache: dict[int, int] = {}

        for lot in lots:
            title = str(lot.get("title") or lot.get("lot_title") or lot.get("card_name") or "—")
            title_lower = title.lower()
            start_time = parse_datetime(lot.get("start_time"))
            end_time = parse_datetime(lot.get("end_time"))
            if start_time and end_time:
                time_span = f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}"
            elif start_time:
                time_span = start_time.strftime("%H:%M")
            else:
                time_span = "—"

            rarity = lot.get("rarity") or lot.get("tier") or lot.get("nominal")
            badge_line = rank_badge(str(rarity) if rarity else None)
            any_word = has_any_word(title_lower)
            rarity_from_title = (
                rarity_from_title_value(title_lower)
                if any_word or not int(lot.get("card_id") or 0)
                else None
            )
            whole_deck = is_whole_deck(title_lower)
            deck_id = int(lot.get("deck_id") or 0)
            parsed_deck_id = parse_deck_no_from_title(title)
            if whole_deck and parsed_deck_id:
                deck_id = parsed_deck_id

            hero = str(lot.get("hero_name") or "—")
            number = f" • №{lot['num']}" if lot.get("num") else ""
            title_line = (
                f"<b><u>{html_escape(title)}</u></b>  "
                f"<b>({html_escape(hero)})</b>{html_escape(number)}"
            )

            if rarity_from_title:
                badge_line = rank_badge(rarity_from_title)
                maximum = rarity_max_cache.get(rarity_from_title)
                if maximum is None:
                    maximum = await self._get_max_obtain_for_rarity(rarity_from_title) or {}
                    if int(maximum.get("cups") or 0) == 0 and int(maximum.get("diamonds") or 0) == 0:
                        maximum = normalize_obtain_variants(
                            await self._get_obtain_variants_for_rarity(rarity_from_title)
                        )
                    rarity_max_cache[rarity_from_title] = maximum
                what_line = reward_line("Что карта даёт", maximum)
                deck_line = f"📚 {decks_range}"
            elif whole_deck and deck_id:
                if deck_id not in deck_treasure_cache:
                    deck_treasure_cache[deck_id] = await self._get_deck_treasure_sum(deck_id)
                badge_line = f"💰 <b>Сокровищ: {deck_treasure_cache[deck_id]}</b>"
                if deck_id not in deck_totals_cache:
                    deck_totals_cache[deck_id] = await self._get_deck_obtain_totals(deck_id)
                what_line = reward_line("Что колода даёт", deck_totals_cache[deck_id])
                deck_line = f"📚 Колода {deck_id} — {html_escape(deck_name.get(deck_id, '—'))}"
            else:
                what_line = reward_line(
                    "Что карта даёт",
                    {"cups": lot.get("gifts_cups"), "diamonds": lot.get("gifts_diamonds")},
                )
                deck_line = f"📚 Колода {deck_id} — {html_escape(deck_name.get(deck_id, '—'))}"

            price = lot.get("start_price")
            price_part = f"{price} {currency_emoji(str(lot.get('currency') or ''))}" if price else "—"
            blocks.append(
                "— " + badge_line + "\n"
                f"   ⏰ {time_span}\n"
                f"   {title_line}\n"
                f"{what_line}\n"
                f"   {deck_line}\n"
                f"   💵 Старт: <b>{html_escape(str(price_part))}</b>"
            )
        return blocks


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def normalize_obtain_variants(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {
            "cups": value.get("cups") or value.get("tea") or 0,
            "diamonds": value.get("diamonds") or value.get("gems") or 0,
        }
    cups = diamonds = 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            kind = str(item.get("obtain_type") or item.get("type") or "").lower()
            amount = int(item.get("amount") or item.get("max") or item.get("obtain_amount") or 0)
            if "cup" in kind or "чаш" in kind or "tea" in kind:
                cups = max(cups, amount)
            if "diamond" in kind or "алмаз" in kind or "gem" in kind:
                diamonds = max(diamonds, amount)
    return {"cups": cups, "diamonds": diamonds}


def reward_line(label: str, values: Mapping[str, object]) -> str:
    parts: list[str] = []
    cups = int(values.get("cups") or 0)
    diamonds = int(values.get("diamonds") or 0)
    if diamonds:
        parts.append(f"💎{diamonds}")
    if cups:
        parts.append(f"☕{cups}")
    return f"   🎯 {label}: " + (" / ".join(parts) if parts else "—")


def pack_blocks(blocks: list[str], limit: int = 3800) -> list[str]:
    output: list[str] = []
    current = ""
    for block in blocks:
        addition = block.rstrip() + "\n\n"
        if current and len(current) + len(addition) > limit:
            output.append(current.rstrip())
            current = addition
        else:
            current += addition
    if current:
        output.append(current.rstrip())
    return output


def chunks(text: str, limit: int = 3800) -> list[str]:
    output: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        addition = len(line) + 1
        if current and current_length + addition > limit:
            output.append("\n".join(current))
            current = [line]
            current_length = addition
        else:
            current.append(line)
            current_length += addition
    if current:
        output.append("\n".join(current))
    return output


def has_any_word(title: str) -> bool:
    return any(
        word in (title or "").lower()
        for word in ("любой", "любая", "любое", "любые", "any ")
    )


def currency_emoji(currency: str | None) -> str:
    values = {
        "cups": "☕",
        "cup": "☕",
        "чашки": "☕",
        "чашка": "☕",
        "diamonds": "💎",
        "diamond": "💎",
        "алмазы": "💎",
        "алмаз": "💎",
    }
    normalized = (currency or "").strip().lower()
    return values.get(normalized, currency or "")


def normalize_rarity(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    for needle, result in (
        ("алмаз", "diamond"),
        ("diamond", "diamond"),
        ("зол", "gold"),
        ("gold", "gold"),
        ("сереб", "silver"),
        ("silver", "silver"),
        ("бронз", "bronze"),
        ("bronze", "bronze"),
    ):
        if needle in lowered:
            return result
    return None


def rank_badge(rarity_like: str | None) -> str:
    rarity = normalize_rarity(rarity_like)
    if not rarity:
        return "⭐ Редкость —"
    labels = {
        "diamond": "Алмазная",
        "gold": "Золотая",
        "silver": "Серебряная",
        "bronze": "Бронзовая",
    }
    medals = {"diamond": "💎", "gold": "🥇", "silver": "🥈", "bronze": "🥉"}
    return f"{medals[rarity]} <b>{labels[rarity]}</b>"


def rarity_from_title_value(title: str) -> str | None:
    match = re.search(
        r"\bлюбо[йяе]\s+(бронз\w*|серебр\w*|золот\w*|алмаз\w*)\b",
        title or "",
        re.I,
    )
    if not match:
        match = re.search(
            r"\b(бронз\w*|серебр\w*|золот\w*|алмаз\w*)\b",
            title or "",
            re.I,
        )
    token = match.group(1).lower() if match else ""
    for prefix, result in (
        ("бронз", "bronze"),
        ("серебр", "silver"),
        ("золот", "gold"),
        ("алмаз", "diamond"),
    ):
        if token.startswith(prefix):
            return result
    return None


def is_whole_deck(title: str) -> bool:
    lowered = (title or "").lower()
    return "вся колода" in lowered or "целая колода" in lowered


def parse_deck_no_from_title(title: str) -> int | None:
    match = re.search(r"(?:№|#)\s*(\d{1,2})", title or "", re.I)
    return int(match.group(1)) if match else None
