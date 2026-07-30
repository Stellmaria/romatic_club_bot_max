import html
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Mapping, Optional, Tuple, List, Dict, Iterable
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.handlers.admin.helper.admin_constants import (
    CURRENCY_EMOJI,
    RARITY_EMOJI,
    ACTION_LABELS, RARITY_RU,
)
from bot.handlers.admin.helper.new.utils import auction_kind_label
from bot.domain.auctions import currency_choices_label
from bot.handlers.admin.logs_admin import send_admin_log
from db.db import get_lot_by_id, get_lot_owners

DT_FMT = "%d.%m.%Y %H:%M:%S"
D_FMT = "%d.%m.%Y"
T_FMT = "%H:%M"

ID_PATTERNS = (
    r"(?i)auction\s*id[:\s]*([0-9]+)",
    r"(?i)лот\s*№\s*([0-9]+)",
    r"№\s*([0-9]+)",
)

UTC = timezone.utc
MSK_TZ = ZoneInfo("Europe/Moscow")


def _dt_to_msk(dt: Any) -> datetime | None:
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(MSK_TZ)
    except Exception:
        return dt


def _human_wait(delta: timedelta) -> str:
    sec = int(delta.total_seconds())
    if sec < 0:
        sec = 0
    days, sec = divmod(sec, 86400)
    hours, sec = divmod(sec, 3600)
    mins, _ = divmod(sec, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


def extract_auction_id(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    for pat in ID_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                pass
    return None


def owners_to_html(owners_any: Any) -> str:
    if owners_any is None:
        return "—"

    # Нормализуем к итерации
    if isinstance(owners_any, str):
        items: Iterable[Any] = [owners_any]
    elif isinstance(owners_any, Mapping):
        items = [owners_any]
    elif isinstance(owners_any, (list, tuple)):
        # Случай «один владелец» как [uid, username, full_name]
        if owners_any and not isinstance(owners_any[0], (Mapping, list, tuple, str)):
            items = [owners_any]
        else:
            items = owners_any
    else:
        items = [owners_any]

    out: list[str] = []
    for it in items:
        uid, username, full_name = _owner_parts(it)

        if username:
            label = f"@{username}"
            link = f'<a href="https://t.me/{username}">{html.escape(label)}</a>'
            if full_name:
                link += f" ({html.escape(full_name)})"
            out.append(link)
        else:
            label = html.escape(full_name or (f"id{uid}" if uid is not None else "—"))
            if uid:
                out.append(f"{label}: {_uid_extra_links(int(uid))}")
            else:
                out.append(label)

    return ", ".join(out) if out else "—"


def _owner_parts(item: Any) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    uid = username = full_name = None
    if isinstance(item, Mapping):
        uid = item.get("user_id")
        username = item.get("username")
        full_name = item.get("full_name")
    elif isinstance(item, (list, tuple)):
        if len(item) > 0: uid = item[0]
        if len(item) > 1: username = (str(item[1]).lstrip("@") if item[1] is not None else None)
        if len(item) > 2: full_name = item[2]
    elif isinstance(item, str):
        full_name = item
    else:
        uid = getattr(item, "user_id", None)
        username = getattr(item, "username", None)
        full_name = getattr(item, "full_name", None)

    if isinstance(uid, str):
        try:
            uid = int(uid)
        except Exception:
            uid = None
    if isinstance(username, str):
        username = username.lstrip("@") or None
    if isinstance(full_name, str):
        full_name = full_name or None
    return uid, username, full_name


def _uid_extra_links(uid: int) -> str:
    tme = f"https://t.me/{uid}"
    tg1 = f'<a href="tg://user?id={uid}">tg://user?id={uid}</a>'
    tg2 = f'<a href="tg://openmessage?user_id={uid}">tg://openmessage?user_id={uid}</a>'
    return f"{tme} {tg1} {tg2}"


def format_field_change_block(field_title: str, old_value: Any, new_value: Any) -> str:
    return (
        "\n\n🧩 <b>Изменение поля</b>"
        f"\n📝 <b>Поле:</b> {html.escape(_as_str(field_title, '-'))}"
        f"\n📎 <b>Было:</b> {html.escape(_as_str(old_value, '—'))}"
        f"\n✅ <b>Стало:</b> {html.escape(_as_str(new_value, '—'))}"
    )


def format_admin_action_log(
        action: str,
        admin: Optional[Dict[str, Any]] = None,
        target: Optional[Dict[str, Any]] = None,
        lot: Optional[Dict[str, Any]] = None,
        owners_text: Optional[str] = None,
        recipients: Optional[int] = None,
        message_text: Optional[str] = None,
        reason: Optional[str] = None,
        discussion_chat_id: Optional[int] = None,
        discussion_message_id: Optional[int] = None,
) -> str:
    now_str = datetime.now().strftime(DT_FMT)
    action_label = ACTION_LABELS.get(action, action)
    msg: List[str] = [action_label, f"🕒 {now_str} (МСК)"]

    # ✅ Админ кликабельный всегда
    if admin is not None:
        msg.append(_user_link_html(admin, label_prefix="👤 Админ"))

    if action == "add_deck" and lot is not None:
        deck_name = html.escape(_as_str(lot.get("deck_name"), "-"))
        msg.append(f"📚 Название колоды: {deck_name}")
    elif lot is not None:
        msg.extend(format_lot_main_info(lot, owners_text))
        msg += format_lot_block(lot, discussion_chat_id, discussion_message_id)

    if action == "request_delete_lot" and reason:
        msg.append(f"❗️ Причина удаления: {html.escape(_as_str(reason, '-'))}")

    # ✅ Target тоже кликабельный
    if action in {"give_trusted", "remove_trusted", "add_admin", "remove_admin"}:
        if target is not None:
            msg.append(_user_link_html(target, label_prefix="🙍‍♂️ Пользователь"))

    if action == "broadcast":
        if message_text:
            msg.append(f"💬 Текст рассылки: {html.escape(_as_str(message_text, ''))}")
        if recipients is not None:
            msg.append(f"📬 Получателей: {recipients}")

    msg.append(f"Действие: {html.escape(action)} через бота.")
    return "\n".join(msg)


def _as_str(v: object, default: str = "") -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return default
    return str(v)


def _coerce_int(v: object, default: int = 0) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except ValueError:
                return default
    return default


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return many
    tail = n % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _gift_line(card: Mapping[str, Any]) -> str:
    # новый формат: obtain_type + obtain_amount -> 🎁 +X {emoji}
    try:
        ot = str(card.get("obtain_type") or "").strip().lower()
        amt = int(card.get("obtain_amount") or 0)
        if ot and amt > 0:
            em = {"diamonds": "💎", "cups": "🍵", "treasures": "🪙"}.get(ot, "💰")
            return f"При получении в подарок даёт: 🎁 +{amt} {em}"
    except Exception:
        pass

    # fallback на старые поля (если где-то ещё живут)
    try:
        dia = int(card.get("gift_diamonds") or 0)
        if dia > 0:
            return f"При получении в подарок даёт: 🎁 +{dia} 💎"
    except Exception:
        pass

    try:
        cups = int(card.get("gift_cups") or 0)
        if cups > 0:
            return f"При получении в подарок даёт: 🎁 +{cups} 🍵"
    except Exception:
        pass

    return "При получении в подарок даёт: 🎁 —"


def _try_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def format_user(
        user: Dict[str, Any],
        html_mode: bool = True,
        crown: bool = True,
        plain: bool = False,
) -> str:
    is_luxury = user.get("is_luxury", False)
    crown_emoji = "👑 " if crown and is_luxury else ""
    uname = (
        f"@{user.get('username')}"
        if user.get("username")
        else user.get("full_name") or f"id:{user['user_id']}"
    )
    if html_mode and not plain:
        return (
            f"{crown_emoji}<a href='tg://user?id={user['user_id']}'>"
            f"{html.escape(str(uname))}</a>"
        )
    return f"{crown_emoji}{uname}"


def format_owners(
        users: List[Dict[str, Any]],
        html_mode: bool = True,
        crown: bool = True,
        plain: bool = False,
) -> str:
    return ", ".join(
        format_user(u, html_mode=html_mode, crown=crown, plain=plain) for u in users
    )


def get_currency_emoji(currency: Optional[str]) -> str:
    cur = str(currency or "").lower()
    return CURRENCY_EMOJI.get(cur, CURRENCY_EMOJI.get("алмазы", "💎"))


def _user_link_html(u: Optional[Dict[str, Any]], *, label_prefix: str) -> str:
    """
    Делает кликабельную ссылку на пользователя:
    - если есть user_id -> tg://user?id=...
    - иначе если есть username -> https://t.me/username
    - иначе просто текст
    """
    if not u:
        return f"{label_prefix}: —"

    uid = u.get("user_id") or u.get("id")
    username = _as_str(u.get("username"), "").strip().lstrip("@")
    full_name = _as_str(u.get("full_name"), "").strip()

    # что показываем как текст ссылки
    if username:
        label = f"@{username}"
    elif full_name:
        label = full_name
    elif uid:
        label = f"id{uid}"
    else:
        label = "—"

    safe_label = html.escape(label)

    # кликабельность по id
    if uid:
        try:
            uid_int = int(uid)
            return (
                f"{label_prefix}: "
                f"<a href='tg://user?id={uid_int}'>{safe_label}</a> "
                f"(id: <code>{uid_int}</code>)"
            )
        except Exception:
            pass

    # кликабельность по username
    if username:
        safe_un = html.escape(username)
        return f"{label_prefix}: <a href='https://t.me/{safe_un}'>@{safe_un}</a>"

    return f"{label_prefix}: {safe_label}"


def format_lot_main_info(lot: Dict[str, Any], owners_text: Optional[str] = None) -> List[str]:
    kind = auction_kind_label(lot.get("auction_kind"))

    craft_val = lot.get("craft_uid_possible")
    if craft_val is True:
        craft_txt = "✅ Да"
    elif craft_val is False:
        craft_txt = "❌ Нет"
    else:
        craft_txt = "—"

    hero = (lot.get("hero_name") or "").strip()
    deck_id = lot.get("deck_id")

    lines: List[str] = [
        (
            "🎴 Лот №"
            f"{html.escape(str(lot.get('auction_id', '-')))}: "
            f"{html.escape(str(lot.get('card_name', '-')))}"
        ),
    ]

    # 👤 Герой (если есть)
    if hero:
        lines.append(f"👤 Герой: <b>{html.escape(hero)}</b>")

    # 🗂 Колода (если есть)
    if deck_id is not None and str(deck_id).strip() != "":
        lines.append(f"🗂 Колода: <b>{html.escape(str(deck_id))}</b>")

    # ⚙️ остальное
    lines.extend([
        f"⚙️ Тип: {html.escape(kind)}",
        f"🙍‍♂️ Владелец(ы): {owners_text or '-'}",
    ])
    kind_key = str(lot.get("auction_kind") or "standard").strip().lower()
    accepted_label = html.escape(
        currency_choices_label(
            lot.get("accepted_currencies"),
            fallback=lot.get("currency"),
            custom_terms=lot.get("custom_offer_terms"),
        )
    )
    if kind_key == "reverse":
        lines.append(f"💱 Валюта ставок: {accepted_label}")
        lines.append("📉 Побеждает минимальная ставка")
    elif kind_key == "free":
        lines.append(f"💱 Принимаются предложения: {accepted_label}")
    else:
        lines.append(
            "💰 Старт: "
            f"{html.escape(str(lot.get('start_price', '-')))} "
            f"{get_currency_emoji(lot.get('currency'))}"
        )
    lines.append(f"🆔 Крафт на UID: {craft_txt}")

    return lines


def format_card_caption(card: Dict[str, Any]) -> str:
    rarity = card.get("rarity", "-")
    rarity_str = (
        f"{RARITY_EMOJI.get(str(rarity).lower(), '')} {rarity}" if rarity != "-" else "-"
    )
    return (
        f"<b>{html.escape(str(card.get('card_name', '-')))}</b>\n"
        f"Герой: {html.escape(str(card.get('hero_name', '-')))}\n"
        f"Номер: {html.escape(str(card.get('num', '-')))}\n"
        f"Редкость: {html.escape(str(rarity_str))}\n"
        f"История: {html.escape(str(card.get('story', '-')))}\n"
        f"Цитата: {html.escape(str(card.get('quote', '-')))}\n"
        f"{_gift_line(card)}"
    )


def _infer_any_rarity_from_title(title: str) -> str | None:
    t = (title or "").strip().lower()
    if "бронз" in t:
        return "bronze"
    if "сереб" in t:
        return "silver"
    if "золот" in t:
        return "gold"
    if "алмаз" in t or "эпик" in t:
        return "diamond"
    return None


def _compress_deck_ids(deck_ids: list[int]) -> str:
    nums = sorted({int(x) for x in deck_ids if x})
    if not nums:
        return "—"

    ranges: list[tuple[int, int]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))

    parts: list[str] = []
    for a, b in ranges:
        if a == b:
            parts.append(str(a))
        elif b == a + 1:
            parts.append(f"{a},{b}")
        else:
            parts.append(f"{a}–{b}")

    return "№" + ", ".join(parts)


def _any_card_label(title: str, any_rarity: str | None) -> str:
    t = (title or "").strip().lower()
    if "любая колода" in t:
        return "Любая колода"
    if "любая карта" in t:
        return "Любая карта"
    if any_rarity == "bronze":
        return "Любая бронза"
    if any_rarity == "silver":
        return "Любое серебро"
    if any_rarity == "gold":
        return "Любое золото"
    if any_rarity == "diamond":
        return "Любой эпик"
    return title or "—"


def format_pending_lot(lot: Mapping[str, Any], owners: Any) -> str:
    # Валюта
    cur = _as_str(lot.get("currency"), "").strip().lower()
    emoji = get_currency_emoji(cur)

    price_raw = lot.get("start_price")
    price_str = f"— {emoji}" if price_raw is None else f"{_coerce_int(price_raw, 0)} {emoji}"

    # Основное
    auction_id = html.escape(str(lot.get("auction_id", "-")))
    status = html.escape(_as_str(lot.get("status"), "-"))
    title_raw = _as_str(lot.get("card_name") or lot.get("hero_name"), "-")
    title = html.escape(title_raw)
    comment = html.escape(_as_str(lot.get("comment"), "-"))
    created_msk = _dt_to_msk(lot.get("created_at"))
    created_block = ""
    if created_msk:
        sent_str = created_msk.strftime("%d.%m.%Y %H:%M")
        wait_str = _human_wait(datetime.now(MSK_TZ) - created_msk)
        created_block = (
            f"<b>Отправлено:</b> {html.escape(sent_str)} (МСК)\n"
            f"<b>На модерации:</b> {html.escape(wait_str)}\n"
        )

    kind_key = _as_str(lot.get("auction_kind"), "standard").strip().lower()
    kind = html.escape(auction_kind_label(kind_key))
    accepted_label = html.escape(
        currency_choices_label(
            lot.get("accepted_currencies"),
            fallback=lot.get("currency"),
            custom_terms=lot.get("custom_offer_terms"),
        )
    )
    if kind_key == "reverse":
        price_line = (
            f"<b>Валюта ставок:</b> {accepted_label}\n"
            "<b>Побеждает:</b> минимальная ставка\n"
        )
    elif kind_key == "free":
        price_line = f"<b>Принимаются предложения:</b> {accepted_label}\n"
    else:
        price_line = f"<b>Стартовая цена:</b> {price_str}\n"

    # Карточная мета (из JOIN)
    deck_id = lot.get("deck_id")
    deck_name = _as_str(lot.get("deck_name"), "").strip()
    rarity = _as_str(lot.get("rarity"), "").strip()
    card_id = lot.get("card_id")
    card_num = lot.get("card_num")
    hero = html.escape(_as_str(lot.get("hero_name"), "-"))

    # --- "ЛЮБАЯ ..." лоты: тянем подсказки ---
    possible_deck_ids = lot.get("possible_deck_ids") or []
    any_rarity = _as_str(lot.get("any_rarity"), "").strip() or _infer_any_rarity_from_title(title_raw)

    # Колода
    deck_line = "—"
    if deck_id:
        if deck_name:
            deck_line = f"№{html.escape(str(deck_id))} — {html.escape(deck_name)}"
        else:
            deck_line = f"№{html.escape(str(deck_id))}"
    elif possible_deck_ids:
        deck_line = html.escape(_compress_deck_ids(list(possible_deck_ids)))

    # Редкость
    if (not rarity) and any_rarity:
        rarity = any_rarity

    rarity_line = "—"
    if rarity:
        r_key = rarity.lower()
        rarity_emoji = RARITY_EMOJI.get(r_key, "")
        rarity_ru = RARITY_RU.get(r_key, rarity)
        rarity_line = html.escape(f"{rarity_emoji} {rarity_ru}".strip())

    # Карта
    card_meta_line = "—"
    if card_id:
        if card_num is not None:
            card_meta_line = f"id={html.escape(str(card_id))} / №{html.escape(str(card_num))}"
        else:
            card_meta_line = f"id={html.escape(str(card_id))}"
    else:
        # для "Любая бронза" и т.п.
        label = _any_card_label(title_raw, any_rarity or None)
        if possible_deck_ids:
            deck_nums = _compress_deck_ids(list(possible_deck_ids)).lstrip("№")
            card_meta_line = html.escape(f"{label} (колоды {deck_nums})")
        else:
            card_meta_line = html.escape(label)

    # Крафт
    craft_val = lot.get("craft_uid_possible")
    if craft_val is True:
        craft_line = "✅ Да"
    elif craft_val is False:
        craft_line = "❌ Нет"
    else:
        craft_line = "—"

    owners_html = owners_to_html_with_status(owners)

    return (
        "📝 <b>Заявка</b>\n"
        f"<b>ID лота:</b> {auction_id}\n"
        f"<b>Лот:</b> {title}\n"
        f"{created_block}"
        f"<b>Тип:</b> {kind}\n"
        f"<b>Колода:</b> {deck_line}\n"
        f"<b>Герой:</b> {hero}\n"
        f"<b>Редкость:</b> {rarity_line}\n"
        f"<b>Карта:</b> {card_meta_line}\n"
        f"<b>Статус заявки:</b> {status}\n"
        f"<b>Крафт на UID возможен:</b> {craft_line}\n"
        f"<b>Валюта:</b> {emoji}\n"
        f"{price_line}"
        f"<b>Комментарий:</b> {comment}\n"
        f"<b>Владелец(ы):</b> {owners_html}"
    )


def format_datetime_block(st: Optional[Any], et: Optional[Any]) -> List[str]:
    if not st or not et:
        return []
    try:
        if isinstance(st, str):
            st = datetime.fromisoformat(st)
        if isinstance(et, str):
            et = datetime.fromisoformat(et)
        if not isinstance(st, datetime) or not isinstance(et, datetime):
            return []
        return [
            f"📅 Дата выхода: {st.strftime(D_FMT)}",
            f"⏰ Время: {st.strftime(T_FMT)}–{et.strftime(T_FMT)} (МСК)",
        ]
    except (TypeError, ValueError):
        return []


def format_lot_block(
        lot: Dict[str, Any],
        discussion_chat_id: Optional[int] = None,
        discussion_message_id: Optional[int] = None,
) -> List[str]:
    result = [
        f"💬 Комментарий: {html.escape(str(lot.get('comment', '-') or '-'))}"
    ]
    st, et = lot.get("start_time"), lot.get("end_time")
    result += format_datetime_block(st, et)
    link_block = format_discussion_link(discussion_chat_id, discussion_message_id)
    if link_block:
        result.append(link_block)
    return result


def make_telegram_link(chat_id: int, msg_id: int) -> str:
    chat_id_num = str(chat_id)
    if chat_id_num.startswith("-100"):
        chat_id_num = chat_id_num[4:]
    elif chat_id_num.startswith("-"):
        chat_id_num = chat_id_num[1:]
    return f"https://t.me/c/{chat_id_num}/{msg_id}"


def format_discussion_link(
        discussion_chat_id: Optional[int],
        discussion_message_id: Optional[int],
        label: str = "сообщение",
) -> str:
    if discussion_chat_id is not None and discussion_message_id is not None:
        url = make_telegram_link(discussion_chat_id, discussion_message_id)
        return f"<a href='{url}'>{html.escape(label)}</a>"
    return ""


def format_deleted_bid_log(
        username: str, user_id: int, amount: Any, auction_id: int, msg_id: int
) -> str:
    return (
        "⚠️ Выдано предупреждение за удаление ставки\n"
        f"Пользователь: @{html.escape(str(username))} (id: {user_id})\n"
        f"Аукцион: {auction_id}\n"
        f"Сумма: {amount}\n"
        f"msg_id: {msg_id}"
    )


def format_lot_log_telegram(
        lot: Dict[str, Any],
        owners_text: Optional[str] = None,
        admin: Optional[Dict[str, Any]] = None,
        action: str = "approve_lot",
        discussion_chat_id: Optional[int] = None,
        discussion_message_id: Optional[int] = None,
) -> str:
    now_str = datetime.now().strftime(DT_FMT)
    action_label = ACTION_LABELS.get(action, action)
    msg = [action_label, f"🕒 {now_str} (МСК)"]
    if admin:
        msg.append(
            f"👤 Админ: @{html.escape(str(admin.get('username', '-')))}"
        )
    msg.extend(format_lot_main_info(lot, owners_text))
    return "\n".join(msg)


def format_missing_forward_log(
        discussion_chat_id: int,
        discussion_message_id: int,
        auction_id: Optional[int] = None,
) -> str:
    now_str = datetime.now().strftime(DT_FMT)
    link_block = format_discussion_link(
        discussion_chat_id, discussion_message_id, label="перейти к сообщению"
    )
    a_id = auction_id if auction_id else "не найден"
    return (
        "‼️ В обсуждении найден аукционный пост БЕЗ пересылки!\n"
        f"🕒 {now_str} (МСК)\n"
        f"Обсуждение: {link_block} (ID {discussion_message_id})\n"
        f"Лот, который должен был выйти: №{a_id}\n"
        "Для привязки используйте пересылку поста из канала или команду /bind_lot."
    )


def format_admins_list(admins: List[Dict[str, Any]]) -> str:
    if not admins:
        return "Список админов пуст."
    lines: List[str] = []
    for admin in admins:
        username = admin.get("username")
        user_id = admin.get("user_id")
        if username:
            lines.append(
                "• "
                f"<a href='tg://user?id={user_id}'>@{username}</a> "
                f"(id: <code>{user_id}</code>)"
            )
        else:
            lines.append(f"• id: <code>{user_id}</code>")
    return "\n".join(lines)


def format_auction_log(event_type: str, data: dict) -> str:
    now_str = datetime.now().strftime(DT_FMT)

    if event_type == "bind_success":
        return (
            "🔗 Пост успешно привязан\n"
            f"🕒 {now_str} (МСК)\n"
            f"Канал message_id: <code>{data['channel_msg_id']}</code>\n"
            "Обсуждение discussion_msg_id: "
            f"<code>{data['discussion_msg_id']}</code>\n"
            f"Лот (auction_id): <b>{data.get('auction_id', 'не найден')}</b>"
        )

    if event_type == "bind_by_template":
        return (
            "🔗 Пост привязан по шаблону\n"
            f"🕒 {now_str} (МСК)\n"
            f"Канал message_id: <code>{data['channel_msg_id']}</code>\n"
            "Обсуждение discussion_msg_id: "
            f"<code>{data['discussion_msg_id']}</code>\n"
            f"Лот (auction_id): <b>{data.get('auction_id', 'не найден')}</b>"
        )

    if event_type == "missing_forward":
        return (
            "‼️ В обсуждении найден аукционный пост БЕЗ пересылки!\n"
            f"🕒 {now_str} (МСК)\n"
            "Обсуждение discussion_msg_id: "
            f"<code>{data['discussion_msg_id']}</code>\n"
            "Лот, который должен был выйти: "
            f"<b>{data.get('auction_id', 'не найден')}</b>\n"
            "Для привязки используйте пересылку поста из канала или команду /bind_lot."
        )

    return f"ℹ️ {event_type}: {data}"


async def log_delete_request(bot: Optional[Bot], req: Mapping[str, Any]) -> None:
    raw_snapshot = req.get("snapshot")
    snapshot: Mapping[str, Any] = (
        raw_snapshot if isinstance(raw_snapshot, Mapping) else {}
    )

    candidates = [
        req.get("auction_id"),
        req.get("lot_id"),
        snapshot.get("auction_id"),
        snapshot.get("lot_id"),
    ]
    auction_id: Optional[int] = next(
        (x for x in (_try_int(c) for c in candidates) if x is not None),
        None,
    )

    if auction_id is None:
        source_text = _as_str(req.get("source_text"), "")
        caption_text = _as_str(req.get("caption"), "")
        auction_id = extract_auction_id(source_text or caption_text)

    if not auction_id:
        await send_admin_log(
            bot,
            "❗️ Некорректный идентификатор лота в заявке.\n"
            "Действие: request_delete_lot через бота.",
        )
        return

    lot: Optional[Mapping[str, Any]] = await get_lot_by_id(auction_id)
    src: Mapping[str, Any] = lot if lot else snapshot

    card_name = _as_str(src.get("card_name"), "-")
    owners_txt = _as_str(src.get("owners_text"), "-")
    start_price = _as_str(src.get("start_price"), "-")
    currency = _as_str(src.get("currency"), "-")

    start = src.get("start_time")
    end = src.get("end_time")

    def _fmt_date_time(st: object, et: object) -> Tuple[str, str]:
        st_dt: Optional[datetime] = st if isinstance(st, datetime) else None
        et_dt: Optional[datetime] = et if isinstance(et, datetime) else None

        if st_dt is not None:
            dt_date = st_dt.strftime(D_FMT)
        else:
            dt_date = _as_str(src.get("date"), "-")

        if st_dt is not None and et_dt is not None:
            dt_time = f"{st_dt.strftime(T_FMT)}–{et_dt.strftime(T_FMT)}"
        else:
            dt_time = _as_str(src.get("time"), "-")

        return dt_date, dt_time

    date_str, time_str = _fmt_date_time(start, end)
    warn = "" if lot else (
        "\n⚠️ Лот не найден в текущем расписании "
        "(возможно перенесён/удалён)."
    )

    text = (
        "🗑️ Запрос на удаление лота\n"
        f"🎴 Лот №{auction_id}: {html.escape(card_name)}\n"
        f"🙍‍♂️ Владелец(ы): {html.escape(owners_txt)}\n"
        f"💰 Старт: {html.escape(start_price)} {html.escape(currency)}\n"
        f"📅 Дата выхода: {html.escape(date_str)}\n"
        f"⏰ Время: {html.escape(time_str)} (МСК)\n"
        f"❗️ Причина удаления: "
        f"{html.escape(_as_str(req.get('reason'), '-'))}\n"
        "Действие: request_delete_lot через бота."
        f"{warn}"
    )

    await send_admin_log(bot, text)


def owners_to_html_with_status(owners_any: Any) -> str:
    if owners_any is None:
        return "—"

    if isinstance(owners_any, str):
        items: Iterable[Any] = [owners_any]
    elif isinstance(owners_any, Mapping):
        items = [owners_any]
    elif isinstance(owners_any, (list, tuple)):
        if owners_any and not isinstance(owners_any[0], (Mapping, list, tuple, str)):
            items = [owners_any]
        else:
            items = owners_any
    else:
        items = [owners_any]

    out: list[str] = []

    for it in items:
        uid, username, full_name = _owner_parts(it)

        # статусы (если есть)
        badges: list[str] = []
        if isinstance(it, Mapping):
            lvl = 0
            try:
                lvl = int(it.get("luxury_level") or 0)
            except Exception:
                lvl = 0

            if lvl >= 2:
                badges.append("👑 Лакшери 2")
            elif lvl == 1 or it.get("is_luxury"):
                badges.append("👑 Лакшери 1")
            if it.get("is_trusted"):
                badges.append("✅ Проверенный")

        badge_str = f" <i>({' · '.join(badges)})</i>" if badges else ""

        if username:
            label = f"@{username}"
            link = f'<a href="https://t.me/{username}">{html.escape(label)}</a>'
            if full_name:
                link += f" ({html.escape(str(full_name))})"
            out.append(link + badge_str)
        else:
            label = html.escape(str(full_name or (f"id{uid}" if uid is not None else "—")))
            if uid:
                out.append(f"{label}{badge_str}: {_uid_extra_links(int(uid))}")
            else:
                out.append(label + badge_str)

    return ", ".join(out) if out else "—"


async def get_lot_owners_with_levels(bot: Bot, auction_id: int) -> list[dict]:
    from bot.handlers.auctions import get_user_luxury_level  # локальный импорт, чтоб не словить цикл

    owners = await get_lot_owners(int(auction_id))
    for o in owners:
        uid = int(o.get("user_id") or 0)
        if uid:
            try:
                o["luxury_level"] = await get_user_luxury_level(bot, uid)
            except Exception:
                o["luxury_level"] = 1 if o.get("is_luxury") else 0
    return owners


def format_exchange_admin_log(
        *,
        batch_id: int,
        deck_id: int,
        mode: str,
        items: int,
        price: int,
        currency: str,
        username: str | None,
        user_id: int,
) -> str:
    mode_label = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Вся колода (карты отдельно)",
    }.get(mode, mode)

    cur_emoji = "💎" if currency == "алмазы" else "🍵"
    user_tag = f"@{username}" if username else f"id{user_id}"

    return (
        "🛒 <b>Биржа — новая заявка</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{batch_id}</code>\n"
        f"📚 <b>Колода:</b> <code>{deck_id}</code>\n"
        f"🧩 <b>Формат:</b> {mode_label}\n"
        f"🃏 <b>Количество позиций:</b> {items}\n"
        f"💰 <b>Цена:</b> {price} {cur_emoji}\n"
        f"👤 <b>Отправитель:</b> {user_tag} (<code>{user_id}</code>)"
    )
