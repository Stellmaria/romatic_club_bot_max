from __future__ import annotations

import re
from collections import defaultdict

from bot.handlers.admin.logs_admin import short_media_id


_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{3,})")

_USER_LINE_RE = re.compile(r"^@([A-Za-z0-9_]{3,})(.*)$")

_AUTHOR_TS_RE = re.compile(r"^.+,\s*\[\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}\]\s*$", re.I)

def _ex_mode_label(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("whole_deck", "deck"):
        return "Колода целиком"
    if m in ("card",):
        return "Карта"
    if m == "deck_split":
        return "Карта"
    return mode or "—"

def _cards_preview(items: list[dict], limit: int = 6) -> str:
    names: list[str] = []
    for it in items or []:
        hero = (it.get("hero_name") or "").strip()
        card = (it.get("card_name") or "").strip()
        qty = int(it.get("qty") or 1)
        base = f"{hero} — {card}".strip(" —")
        if not base:
            base = "—"
        if qty > 1:
            base = f"{base} ×{qty}"
        names.append(base)

    if not names:
        return "—"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" … +{len(names) - limit}"

def _parse_batch_ids(tokens: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for t in tokens:
        for part in (t or "").replace(";", ",").split(","):
            s = part.strip()
            if not s:
                continue
            if s.isdigit():
                i = int(s)
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out

def _short_media(v: object) -> str:
    # чтобы file_id не раздувал логи
    return short_media_id(v) if "short_media_id" in globals() else (str(v)[:12] + "…" if v else "—")

def _extract_usernames_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _USERNAME_RE.finditer(text or ""):
        un = (m.group(1) or "").strip()
        if not un:
            continue
        key = un.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(un)
    return out

def _chunk_lines(lines: list[str], max_len: int = 3500) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for line in lines:
        add_len = len(line) + 1
        if cur and (cur_len + add_len) > max_len:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = add_len
        else:
            cur.append(line)
            cur_len += add_len

    if cur:
        chunks.append("\n".join(cur))
    return chunks

def _norm(s: str) -> str:
    s = (s or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s

def _parse_qty_and_card(rest: str, cur_card: str | None) -> tuple[int, str | None]:
    """
    rest examples:
      "Граф"
      "2 Граф 2"
      "Мадс 4"
      "5 Нахом Кевин"
      "5 шт"
      "4 карты"
      ""  (then use cur_card)
    """
    rest = (rest or "").strip()
    if not rest:
        return (1, cur_card)

    # вычленяем количество
    qty = None

    # "5 шт" / "5 карт" / "5 карты" / "5 карта"
    m = re.match(r"^(\d+)\s*(шт\.?|штук|карта|карты|карт)?\b(.*)$", rest, flags=re.I)
    if m and m.group(1):
        qty = int(m.group(1))
        rest2 = (m.group(3) or "").strip()
    else:
        rest2 = rest

    # если не нашли qty в начале: пробуем в конце "Граф 2" / "Виктор 5 карт"
    if qty is None:
        m2 = re.match(r"^(.*?)(?:\s+(\d+))\s*(шт\.?|штук|карта|карты|карт)?\s*$", rest2, flags=re.I)
        if m2 and m2.group(2):
            qty = int(m2.group(2))
            rest2 = (m2.group(1) or "").strip()

    if qty is None:
        qty = 1

    # иногда пишут "2 Граф 2" -> уберем хвостовую цифру, если осталась
    tokens = rest2.split()
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    card = " ".join(tokens).strip()

    # чистим мусорные слова, если остались
    card = re.sub(r"\b(шт\.?|штук|карта|карты|карт)\b", "", card, flags=re.I).strip()
    card = re.sub(r"\s+", " ", card).strip()

    if not card:
        card = cur_card

    return qty, card

def _parse_expected_from_text(text: str) -> dict[tuple[str, str], int]:
    """
    returns {(username_lower, card_norm): expected_qty}
    """
    expected: dict[tuple[str, str], int] = defaultdict(int)
    cur_card: str | None = None
    cur_default_qty = 1

    for raw in (text or "").splitlines():
        line = (raw or "").strip()
        if not line:
            continue

        # пропускаем "Имя, [04.02.2026 19:04:08]"
        if _AUTHOR_TS_RE.match(line):
            continue

        low = line.lower()

        # групповый заголовок, не карта
        if _norm(line) in {"золото 18к"}:
            cur_card = None
            cur_default_qty = 1
            continue

        # заголовки вида "Каин и Авель, по одной карте"
        if "по одной" in low:
            card_title = line.split(",")[0].strip()
            if card_title:
                cur_card = card_title
                cur_default_qty = 1
            continue

        # заголовки вида "Джон (с белкой) 21 карта" / "Лилиан 19 карт"
        if not line.startswith("@"):
            hdr = line.rstrip(":").strip()
            hdr = re.sub(r"\s+\d+\s*карт\w*\s*$", "", hdr, flags=re.I).strip()
            # если это выглядит как название карты (короткая строка) - ставим контекст
            if hdr and len(hdr) <= 60:
                cur_card = hdr
                cur_default_qty = 1
            continue

        # строки вида "@user ...."
        m = _USER_LINE_RE.match(line)
        if not m:
            continue
        uname = _norm(m.group(1))
        rest = (m.group(2) or "").strip()

        qty, card = _parse_qty_and_card(rest, cur_card)
        if card is None:
            continue

        # если в rest вообще нет названия карты (например "@yaaziyaa"), берем cur_card
        # qty по умолчанию 1, но если cur_card задан и в rest пусто, ок
        card_norm = _norm(card)

        # если rest пустой, но у нас стоит cur_default_qty (редко нужно), применим
        if not rest and cur_card:
            qty = cur_default_qty

        expected[(uname, card_norm)] += int(qty)

    return dict(expected)

def _chunk(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines = text.splitlines()
    out, cur, size = [], [], 0
    for ln in lines:
        add = len(ln) + 1
        if cur and size + add > limit:
            out.append("\n".join(cur))
            cur, size = [ln], add
        else:
            cur.append(ln)
            size += add
    if cur:
        out.append("\n".join(cur))
    return out

# Public feature contracts. Private names remain temporary local aliases.
cards_preview = _cards_preview
chunk = _chunk
chunk_lines = _chunk_lines
exchange_mode_label = _ex_mode_label
extract_usernames_from_text = _extract_usernames_from_text
parse_batch_ids = _parse_batch_ids
parse_expected_from_text = _parse_expected_from_text
