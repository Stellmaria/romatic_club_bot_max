from __future__ import annotations

import re
from datetime import date, datetime
from html import escape as _esc
from typing import Dict, List

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers.admin.helper.admin_keyboards import days_keyboard, months_keyboard
from db.legacy import (
    get_all_decks,
    get_deck_obtain_totals,
    get_deck_treasure_sum,
    get_auctions_by_date_with_owners,
    get_max_obtain_for_rarity,
    get_obtain_variants_for_rarity,
)
from db.legacy import is_luxury_user, get_cards_meta_bulk, \
    fetchrow
from bot.legacy_fsm import LuxScheduleFSM

router = Router(name="luxury_schedule")
PREFIX = "luxsched"

RARITY_RU = {
    "bronze": "Бронзовая", "silver": "Серебряная", "gold": "Золотая", "diamond": "Алмазная",
    "бронза": "Бронзовая", "серебро": "Серебряная", "золото": "Золотая", "алмаз": "Алмазная", "алмазы": "Алмазная",
    "бронзовая": "Бронзовая", "серебряная": "Серебряная", "золотая": "Золотая", "алмазная": "Алмазная",
}
RARITY_EMOJI = {
    "bronze": "🥉", "silver": "🥈", "gold": "🥇", "diamond": "💎",
    "бронзовая": "🥉", "серебряная": "🥈", "золотая": "🥇", "алмазная": "💎",
    "бронза": "🥉", "серебро": "🥈", "золото": "🥇", "алмаз": "💎", "алмазы": "💎",
}
CURRENCY_EMOJI = {"cups": "☕", "cup": "☕", "чашки": "☕", "чашка": "☕",
                  "diamonds": "💎", "diamond": "💎", "алмазы": "💎", "алмаз": "💎"}


def _has_any_word_any(title: str) -> bool:
    t = (title or "").lower()
    return any(x in t for x in ("любой", "любая", "любое", "любые", "any "))


async def _last_nonempty_deck_id() -> int:
    row = await fetchrow("SELECT COALESCE(MAX(deck_id),0) AS mx FROM cards")
    try:
        return int(row["mx"]) if row and row.get("mx") is not None else 0
    except Exception:
        try:
            return int(row[0]) if row else 0
        except Exception:
            return 0


def rarity_block(raw: str | None) -> str:
    r = (raw or "").strip().lower()
    name = RARITY_RU.get(r, raw or "—")
    emoji = RARITY_EMOJI.get(r, "")
    return f"{name} {emoji}".strip()


def currency_emoji(curr: str | None) -> str:
    c = (curr or "").strip().lower()
    return CURRENCY_EMOJI.get(c, curr or "")


def _chunks(s: str, limit: int = 3800) -> List[str]:
    lines = s.splitlines()
    acc, cur, cur_len = [], [], 0
    for ln in lines:
        add = len(ln) + 1
        if cur_len + add > limit:
            acc.append("\n".join(cur))
            cur, cur_len = [ln], add
        else:
            cur.append(ln);
            cur_len += add
    if cur:
        acc.append("\n".join(cur))
    return acc


def _kb_back_months() -> InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к месяцам", callback_data=f"{PREFIX}|back_months")]
    ])


def _extract_ymd_from_cb(data: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d{4})[-_/\.](\d{1,2})(?:[-_/\.](\d{1,2}))?", data)
    if not m:
        return None
    y = int(m.group(1));
    mo = int(m.group(2));
    d = int(m.group(3)) if m.group(3) else 0
    return (y, mo, d)


async def _enrich_lots_with_card_meta(lots: list[dict]) -> None:
    card_ids = [int(x["card_id"]) for x in lots if x.get("card_id")]
    if not card_ids:
        return

    meta = await get_cards_meta_bulk(card_ids)

    for lot in lots:
        cid = int(lot.get("card_id") or 0)
        m = meta.get(cid)
        if not m:
            continue

        lot.setdefault("hero_name", m["hero_name"])
        lot.setdefault("deck_id", m["deck_id"])
        lot.setdefault("rarity", m["rarity"])

        if lot.get("gifts_cups") is None:
            lot["gifts_cups"] = m["gifts_cups"]
        if lot.get("gifts_diamonds") is None:
            lot["gifts_diamonds"] = m["gifts_diamonds"]


@router.message(F.chat.type == "private", F.text.in_({"/vip_schedule"}))
async def lux_start(message: types.Message, state: FSMContext):
    if not await is_luxury_user(message.from_user.id):
        await message.answer(
            "Эта функция доступна только для Лакшери-пользователей.",
            protect_content=True,
        )
        return
    await state.clear()
    await state.set_state(LuxScheduleFSM.choosing_month)
    await message.answer(
        "Выберите месяц:",
        reply_markup=months_keyboard(prefix=PREFIX, auction_id=None),
        protect_content=True,
    )


@router.callback_query(LuxScheduleFSM.choosing_month, F.data.startswith(PREFIX))
async def lux_choose_month(call: types.CallbackQuery, state: FSMContext):
    ymd = _extract_ymd_from_cb(call.data)
    if not ymd:
        await call.answer("Неверный формат месяца", show_alert=True)
        return
    y, m, _ = ymd
    await state.update_data(year=y, month=m)
    await state.set_state(LuxScheduleFSM.choosing_day)
    await call.message.answer(
        "Выберите день:",
        reply_markup=days_keyboard(PREFIX, None, y, m),
        protect_content=True,
    )
    await call.answer()


def _pack_blocks(blocks: list[str], limit: int = 3800) -> list[str]:
    out, cur = [], ""
    for b in blocks:
        add = (b + "\n\n")
        if cur and len(cur) + len(add) > limit:
            out.append(cur.rstrip())
            cur = add
        else:
            cur += add
    if cur:
        out.append(cur.rstrip())
    return out


@router.callback_query(LuxScheduleFSM.choosing_day, F.data.startswith(PREFIX))
async def lux_choose_day(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data

    if data.endswith("|back_months") or data.endswith(":back_months"):
        await state.set_state(LuxScheduleFSM.choosing_month)
        await call.message.edit_text(
            "Выберите месяц:",
            reply_markup=months_keyboard(prefix=PREFIX, auction_id=None),
        )
        await call.answer()
        return

    def _pack_blocks(blocks: list[str], limit: int = 3800) -> list[str]:
        out, cur = [], ""
        for b in blocks:
            add = b.rstrip() + "\n\n"
            if cur and len(cur) + len(add) > limit:
                out.append(cur.rstrip())
                cur = add
            else:
                cur += add
        if cur:
            out.append(cur.rstrip())
        return out

    def _extract_ymd_from_cb(cb: str):
        return None

    def _has_any_word_any(title: str) -> bool:
        t = (title or "").lower()
        return any(x in t for x in ("любой", "любая", "любое", "любые", "any "))

    async def _last_nonempty_deck_id() -> int:
        row = await fetchrow("SELECT COALESCE(MAX(deck_id),0) AS mx FROM cards")
        try:
            return int(row["mx"]) if row and row.get("mx") is not None else 0
        except Exception:
            try:
                return int(row[0]) if row else 0
            except Exception:
                return 0

    def _coerce_selected_from_cb(cb: str) -> date | None:
        raw = _extract_ymd_from_cb(cb)
        if isinstance(raw, str):
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except Exception:
                pass
        elif isinstance(raw, (list, tuple)):
            if len(raw) == 1 and isinstance(raw[0], str):
                try:
                    return datetime.strptime(raw[0], "%Y-%m-%d").date()
                except Exception:
                    pass
            if len(raw) == 3:
                y, m, d = raw
                try:
                    return date(int(y), int(m), int(d))
                except Exception:
                    pass

        m = re.search(r"(?:^|[|:])day[|:](\d{4})[-_/\.](\d{1,2})[-_/\.](\d{1,2})", cb)
        if m:
            try:
                y, mo, d = map(int, m.groups())
                return date(y, mo, d)
            except Exception:
                pass

        m = re.search(r"(?:^|[|:])day[|:](\d{1,2})(?:[|:].*)?$", cb)
        if m:
            return None

        m = re.search(r"(\d{4})[-_/\.](\d{1,2})[-_/\.](\d{1,2})", cb)
        if m:
            try:
                y, mo, d = map(int, m.groups())
                return date(y, mo, d)
            except Exception:
                pass
        return None

    selected = _coerce_selected_from_cb(data)
    if selected is None:
        m = re.search(r"(?:^|[|:])day[|:](\d{1,2})(?:[|:].*)?$", data)
        if m:
            try:
                d = int(m.group(1))
                st = await state.get_data()
                y = int(st.get("year") or datetime.now().year)
                mo = int(st.get("month") or datetime.now().month)
                selected = date(y, mo, d)
            except Exception:
                selected = None

    if selected is None:
        await call.answer("Не удалось определить дату.", show_alert=True)
        return

    if not await is_luxury_user(call.from_user.id):
        await call.answer("Доступ ограничен.", show_alert=True)
        return

    decks = await get_all_decks()
    deck_name = {int(r["deck_id"]): r["deck_name"] for r in decks}

    last_nonempty = await _last_nonempty_deck_id()
    decks_range = f"Колоды 1 — {last_nonempty}" if last_nonempty > 0 else "Колоды —"

    lots = await get_auctions_by_date_with_owners(selected)
    if not lots:
        await call.message.answer(
            f"На {selected.strftime('%d.%m.%Y')} лотов нет.",
            reply_markup=_kb_back_months(),
            protect_content=True,
        )
        await call.answer()
        return

    await _enrich_lots_with_card_meta(lots)

    rarity_max_cache: dict[str | None, dict] = {}
    deck_totals_cache: dict[int, dict] = {}
    deck_treasure_cache: dict[int, int] = {}

    blocks: list[str] = [f"🗓 <b>Аукционы на {selected.strftime('%d.%m.%Y')}</b> (МСК):"]

    for a in lots:
        title = a.get("title") or a.get("lot_title") or a.get("card_name") or "—"
        t_lc = title.lower()

        st = a.get("start_time")
        et = a.get("end_time")
        if isinstance(st, str):
            try:
                st = datetime.fromisoformat(st)
            except Exception:
                st = None
        if isinstance(et, str):
            try:
                et = datetime.fromisoformat(et)
            except Exception:
                et = None

        if st and et:
            tspan = f"{st.strftime('%H:%M')}–{et.strftime('%H:%M')}"
        elif st:
            tspan = st.strftime("%H:%M")
        else:
            tspan = "—"

        base_rarity = a.get("rarity") or a.get("tier") or a.get("nominal")
        badge_line = rank_badge(base_rarity)

        any_word = _has_any_word_any(t_lc)
        rar_from_title = _rarity_from_title(t_lc) if any_word or not int(a.get("card_id") or 0) else None
        whole_deck = _is_whole_deck(t_lc)

        d_id = int(a.get("deck_id") or 0)
        parsed = _parse_deck_no_from_title(title)
        if whole_deck and parsed:
            d_id = parsed
        deck = deck_name.get(d_id, "—")
        hero = a.get("hero_name") or "—"
        num = f" • №{a['num']}" if a.get("num") else ""
        title_line = f"<b><u>{_esc(title)}</u></b>  <b>({_esc(hero)})</b>{_esc(num)}"

        if rar_from_title:
            badge_line = rank_badge(rar_from_title)

            if rar_from_title not in rarity_max_cache:
                mx = await get_max_obtain_for_rarity(rar_from_title)
                if not mx or (int(mx.get("cups") or 0) == 0 and int(mx.get("diamonds") or 0) == 0):
                    variants = await get_obtain_variants_for_rarity(rar_from_title)
                    cups = 0
                    dias = 0
                    if isinstance(variants, dict):
                        cups = int(variants.get("cups") or variants.get("tea") or 0)
                        dias = int(variants.get("diamonds") or variants.get("gems") or 0)
                    elif isinstance(variants, (list, tuple)):
                        for v in variants:
                            t = str(v.get("obtain_type") or v.get("type") or "").lower()
                            amt = int(v.get("amount") or v.get("max") or v.get("obtain_amount") or 0)
                            if "cup" in t or "чаш" in t or "tea" in t:
                                cups = max(cups, amt)
                            if "diamond" in t or "алмаз" in t or "gem" in t:
                                dias = max(dias, amt)
                    mx = {"cups": cups, "diamonds": dias}
                rarity_max_cache[rar_from_title] = mx
            else:
                mx = rarity_max_cache[rar_from_title]

            cups = int(mx.get("cups") or 0)
            dias = int(mx.get("diamonds") or 0)
            parts = []
            if cups:
                parts.append(f"☕{cups}")
            if dias:
                parts.append(f"💎{dias}")
            what_line = "   🎯 Что карта даёт: " + (" / ".join(parts) if parts else "—")
            deck_line = f"📚 {decks_range}"

        elif whole_deck and d_id:
            if d_id not in deck_treasure_cache:
                deck_treasure_cache[d_id] = await get_deck_treasure_sum(d_id)
            treasure_sum = deck_treasure_cache[d_id]
            badge_line = f"💰 <b>Сокровищ: {treasure_sum}</b>"

            if d_id not in deck_totals_cache:
                deck_totals_cache[d_id] = await get_deck_obtain_totals(d_id)
            totals = deck_totals_cache[d_id]
            parts = []
            if int(totals.get("diamonds") or 0):
                parts.append(f"💎{totals['diamonds']}")
            if int(totals.get("cups") or 0):
                parts.append(f"☕{totals['cups']}")
            what_line = "   🎯 Что колода даёт: " + (" / ".join(parts) if parts else "—")
            deck_line = f"📚 Колода {d_id} — {_esc(deck)}"

        else:
            cups = int(a.get("gifts_cups") or 0)
            dias = int(a.get("gifts_diamonds") or 0)
            parts = []
            if cups:
                parts.append(f"☕{cups}")
            if dias:
                parts.append(f"💎{dias}")
            what_line = "   🎯 Что карта даёт: " + (" / ".join(parts) if parts else "—")
            deck_line = f"📚 Колода {d_id} — {_esc(deck)}"

        curr = currency_emoji(a.get("currency"))
        price_part = f"{a.get('start_price')} {curr}" if a.get("start_price") else "—"

        blocks.append(
            "— " + badge_line + "\n"
                                f"   ⏰ {tspan}\n"
                                f"   {title_line}\n"
                                f"{what_line}\n"
                                f"   {deck_line}\n"
                                f"   💵 Старт: <b>{_esc(str(price_part))}</b>"
        )

    for part in _pack_blocks(blocks):
        await call.message.answer(
            _tg_clean(part),
            parse_mode="HTML",
            reply_markup=_kb_back_months(),
            protect_content=True,
        )
    await call.answer()


def _norm_rarity(r: str | None) -> str | None:
    if not r:
        return None
    t = str(r).lower()
    if "алмаз" in t or "diamond" in t:
        return "diamond"
    if "зол" in t or "gold" in t:
        return "gold"
    if "сереб" in t or "silver" in t:
        return "silver"
    if "бронз" in t or "bronze" in t:
        return "bronze"
    return None


RANK_MEDAL = {
    "diamond": "💎",
    "gold": "🥇",
    "silver": "🥈",
    "bronze": "🥉",
}


def rank_badge(rarity_like: str | None) -> str:
    key = _norm_rarity(rarity_like)
    if not key:
        return "⭐ Редкость —"
    return f"{RANK_MEDAL.get(key, '⭐')} <b>{RARITY_RU.get(key, key)}</b>"


def _rarity_from_title(title: str) -> str | None:
    import re

    t = (title or "").lower()
    m = re.search(
        r"\bлюбо[йяе]\s+(бронз\w*|серебр\w*|золот\w*|алмаз\w*)\b", t
    )
    token = m.group(1) if m else None
    if not token:
        m2 = re.search(r"\b(бронз\w*|серебр\w*|золот\w*|алмаз\w*)\b", t)
        token = m2.group(1) if m2 else None
    if not token:
        return None

    if token.startswith("бронз"):
        return "bronze"
    if token.startswith("серебр"):
        return "silver"
    if token.startswith("золот"):
        return "gold"
    if token.startswith("алмаз"):
        return "diamond"
    return None


def _is_any_card(title: str) -> bool:
    import re

    t = (title or "").lower()
    return bool(
        re.search(
            r"\bлюбо[йяе]\s+(бронз\w*|серебр\w*|золот\w*|алмаз\w*)\b", t
        )
    )


def _is_whole_deck(title: str) -> bool:
    t = (title or "").lower()
    return "вся колода" in t or "целая колода" in t


def _decks_range_text(decks: Dict[int, str]) -> str:
    if not decks:
        return "Колоды —"
    return f"Колоды 1–{max(decks.keys())}"


def _parse_deck_no_from_title(title: str) -> int | None:
    m = re.search(r"(?:№|#)\s*(\d{1,2})", title or "", flags=re.I)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


_BR_RE = re.compile(r"(?i)<br\s*/?>")


def _tg_clean(text: str) -> str:
    return _BR_RE.sub("\n", text or "")
