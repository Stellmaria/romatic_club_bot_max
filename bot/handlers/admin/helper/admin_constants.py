import html
from typing import Set

from bot.handlers.admin.helper.new.utils import auction_kind_label
from bot.domain.auctions import currency_choices_label
from bot.handlers.helper.helpers_users import emoji_by_currency
from bot.presentation.warnings import WARN_TEXTS
from bot.services.admin_auctions import AdminAuctionContextService

CANCEL_TEXT = ["назад", "отмена", "⬅️ назад"]
NO_ACCESS_MSG = "Нет доступа."
CANCEL_MSG = "Действие отменено."
FIELD_CHANGED_MSG = "Поле успешно обновлено."
PHOTO_CHANGED_MSG = "Фото успешно обновлено."
DELETE_CANCELLED_MSG = "Удаление отменено."
EDIT_CANCELLED_MSG = "Редактирование отменено."
ADD_CANCELLED_MSG = "Добавление карты отменено."
INVALID_DATE_FORMAT = "❌ Неверный формат даты. Используй YYYY-MM-DD."
ERROR_FETCH_LOGS = "Ошибка при получении логов. Сообщите разработчику."

ADD_CARD_FIELDS = [
    ("card_name", "Введите название карты:"),
    ("num", "Введите номер карты:"),
    ("hero_name", "Введите имя героя:"),
    ("image", "Пришлите изображение карты:"),
    ("rarity", "Выберите редкость карты:"),
    ("story", "Введите название истории:"),
    ("quote", "Введите цитату с карты (или напишите «-»):"),
]

SYSTEM_MESSAGES = {
    "no_access": NO_ACCESS_MSG,
    "user_not_found": "Пользователь не найден.",
    "user_not_found_id": "Пользователь не найден по этому идентификатору.",
    "invalid_password": "❌ Неверный пароль.",
    "syntax_error": "Используйте: {example}",
    "field_edit_error": "Ошибка: поле недоступно для редактирования.",
    "invalid_currency": "Валюта должна быть 'алмазы' или 'чашки'.",
    "cannot_delete_self": "Нельзя удалить самого себя.",
    "operation_failed": "Что-то пошло не так. Попробуйте позже.",
    "invalid_command": "Неизвестная команда.",
    "set_luxury_usage": "Используйте: /set_luxury @username",
    "unset_luxury_usage": "Используйте: /unset_luxury @username",
}

ADMIN_ERRORS = {
    "user_not_found": "Пользователь не найден.",
    "need_password": "Требуется пароль. Используй: /admins пароль",
    "wrong_password": "❌ Неверный пароль.",
    "cant_remove_owner": "Нельзя удалить владельца из списка админов.",
    "not_private": "Эта команда работает только в личных сообщениях боту.",
    "too_many_requests": "⏱ Пожалуйста, не спамьте командами. Подождите немного.",
    "parse_error": "Неверный формат команды.",
}

ADMIN_MESSAGES = {
    "admin_panel_greeting": "Добро пожаловать в админ-панель! Выберите раздел:",
    "no_access": NO_ACCESS_MSG,
    "field_changed": FIELD_CHANGED_MSG,
    "photo_changed": PHOTO_CHANGED_MSG,
    "field_edit_error": "Ошибка: поле недоступно для редактирования.",
    "enter_new_value": "Введите новое значение:",
    "num_must_be_int": "Номер должен быть числом.",
    "no_pending_lots": "Нет новых заявок.",
    "choose_month": "Выберите месяц для выхода лота:",
    "choose_day": "Выберите день публикации лота:",
    "choose_time": "Выберите свободное время для аукциона:",
    "lot_not_found": "Лот не найден. Возможно, он уже удалён.",
    "date_parse_error": "Ошибка разбора даты: {error}",
    "no_free_slots": "Свободных слотов нет на эту дату.",
    "lot_rejected": "Лот отклонён и скрыт из расписания.",
    "reject_cancelled": "Отмена отказа. Лот не отклонён.",
    "enter_rejection_reason": "Введите причину отказа для пользователя:",
    "lot_deleted": "Лот успешно удалён.",
    "delete_confirm": "Удалить этот лот?",
    "broadcast_enter_text": "Введите текст рассылки (или 'отмена' для отмены):",
    "broadcast_cancelled": "Рассылка отменена.",
    "broadcast_done": "Рассылка завершена. Сообщение доставлено {count} пользователям.",
    "broadcast_error": "Во время рассылки произошла ошибка.",
    "delete_cancelled": DELETE_CANCELLED_MSG,
    "lot_scheduled": "✅ Лот успешно запланирован!",
    "format_date_prompt": "Формат даты: ГГГГ-ММ-ДД\nИли напишите \"Назад\".",
    "no_free_slots_date": "Нет свободных слотов на эту дату.",
    "choose_free_time": "Выберите свободное время:",
    "what_to_edit": "Что редактировать?",
    "invalid_date_format": INVALID_DATE_FORMAT,
    "error_fetch_logs": ERROR_FETCH_LOGS,
    "send_new_photo": "Пришлите новое фото карты или 'Назад'.",
    "send_photo_or_cancel": "Пришлите фото или напишите «Отмена/Назад».",
    "new_time_template": "Новое время: <b>{start}</b> — <b>{end}</b>\nЛот обновлён.",
    "choose_month_for_preview": "Выберите месяц для просмотра расписания:",
    "choose_day_for_preview": "Выберите день для просмотра расписания:",
    "invalid_day_data": "Ошибка данных дня.",
    "delete_card_confirm": "Удалить эту карту?",
    "card_deleted": "Карта удалена из базы.",
    "delete_card_cancelled": DELETE_CANCELLED_MSG,
    "choose_deck": "Выбери колоду для просмотра:",
    "no_cards_in_deck": "В этой колоде пока нет карт.",
    "choose_another_deck": "Посмотреть другую колоду?",
    "edit_cancelled": EDIT_CANCELLED_MSG,
    "card_field_updated": FIELD_CHANGED_MSG,
    "card_photo_updated": PHOTO_CHANGED_MSG,
    "set_luxury": "Статус Лакшери выдан {username}.",
    "unset_luxury": "Статус Лакшери снят с {username}.",
    "user_now_admin": "Пользователь <code>{user_id}</code> теперь админ.",
    "user_removed_admin": "Пользователь {user_id} больше не админ.",
    "no_admins": "Список админов пуст.",
    "user_not_found_id": "Пользователь не найден по этому идентификатору.",
    "logs_empty": "Логи пусты.",
    "too_many_logs": "Слишком много логов, сократите диапазон (лимит).",
    "addcard_cancelled": ADD_CANCELLED_MSG,
    "card_added": "Карта успешно добавлена в базу!",
    "choose_deck_for_add": "Выбери колоду:",
    "last_admin_actions_header": "<b>Последние действия админов:</b>\n",
    "owner_access_granted": "Владелец, доступ разрешён без пароля.",
    "admin_access_granted": "Пароль принят! Доступ разрешён.",
    "enter_admin_password": "Введите пароль для доступа к админ-функциям:",
    "wrong_admin_password": "❌ Неверный пароль. Попробуйте снова или отмените команду.",
    "card_num_duplicate": "❌ Карта с таким номером уже есть! Введите другой номер.",
    "card_num_incorrect": "Введите корректный номер карты (целое число) или нажмите Назад.",
    "confirm": "Добавить",
    "cancel": "Отмена",
}

MSG_REASON_REJECT_ADD = "Напишите причину отклонения заявки на добавление лота:"
MSG_REASON_REJECT_DELETE = "Напишите причину отказа в удалении лота для пользователя:"
MSG_PHOTO_CONFIRM = "Фото для подтверждения наличия карты"
MSG_PHOTO_NOT_FOUND = "Фото подтверждения не найдено."
MSG_NO_FREE_SLOTS = "Нет свободных слотов в ближайшие 2 недели!"
MSG_REQUEST_NOT_FOUND = "Заявка не найдена!"
MSG_LOT_DELETED = "Заявка одобрена, лот удалён."
MSG_LOT_DELETED_USER = "Ваша заявка на удаление лота №{auction_id} одобрена. Лот удалён администрацией."
MSG_OWNER_REJECT_SENT = "Отказ отправлен владельцу."
MSG_WELCOME_PANEL = "Добро пожаловать в админ-панель! Выберите раздел:"

MSG_LOT_APPROVED_OWNER = (
    "✅ Ваш лот <b>{card_name}</b> одобрен и добавлен в расписание!\n"
    "⏰ <b>Дата:</b> {date}\n"
    "<b>Время:</b> {time} (МСК)\n"
    "Ожидайте публикации в канале аукциона!"
)
MSG_CHOOSE_ANOTHER_SLOT = (
    "⏩ <b>Выбрано недопустимое время.</b>\n"
    "Следующий свободный слот: <b>{slot}</b>.\n"
    "Выбрать это время?"
)
MSG_REQUEST_APPROVED = "Заявка одобрена, лот удалён."

CALLBACK_CONFIRM_LOT = "confirm_lot"
CALLBACK_REJECT_LOT = "reject_lot"
CALLBACK_CHOOSE_TIME_BACK = "choose_time_back"
CALLBACK_SHOW_PROOF = "show_proof"
CALLBACK_APPROVE_DELETE = "approve_delete"
CALLBACK_REJECT_DELETE = "reject_delete"
CALLBACK_BACK_TO_LOT = "back_to_lot"

REJECT_LOT_ADMIN_LOG = (
    "❌ <b>Отклонена заявка на добавление лота</b>\n"
    "🕒 {datetime} (МСК)\n"
    "👤 <b>Админ:</b> {admin_name} ({admin_id})\n"
    "🎴 <b>Лот №{auction_id}:</b> {card_name}\n"
    "🙍‍♂️ <b>Владелец(ы):</b> {owners_text}\n"
    "💬 <b>Комментарий модератора:</b> {reason}\n"
    "Действие: reject_lot через бота."
)
REJECT_LOT_USER_NOTIFY = (
    "❌ <b>Ваша заявка на добавление лота отклонена</b>\n"
    "🎴 <b>Лот:</b> {card_name}\n"
    "💬 <b>Причина:</b> {reason}\n"
    "Если есть вопросы — обратитесь к администрации."
)
REJECT_DELETE_ADMIN_LOG = (
    "❌ <b>Отклонена заявка на удаление лота №{auction_id}</b>\n"
    "<b>Владелец(ы):</b> {owners_text}\n"
    "<b>Причина заявки:</b> {delete_reason}\n"
    "<b>Комментарий модератора:</b> {reason}\n"
    "<b>Обработал:</b> {admin_name} ({admin_id})"
)
REJECT_DELETE_USER_NOTIFY = (
    "Ваша заявка на удаление лота №{auction_id} отклонена.\nПричина отказа: {reason}"
)
MSG_CONFIRM_PUBLICATION = (
    "<b>Лот:</b> {card_name}\n"
    "<b>Владельцы:</b> {owners}\n"
    "<b>Выбрано время:</b> {start}–{end}\n"
    "Подтвердить публикацию?"
)

BUTTONS = {
    "menu": "🏠 Меню",
    "admin_menu": "👑 Админ-меню",
    "admin": "🛠 Админ-панель",
    "stats": "📊 Статистика",
    "logs": "📜 Логи",
    "moderation": "⚙️ Модерация",
    "users": "🏠 Пользователи",
    "deck": "Колода",
    "custom": "Свой вариант",
    "approve": "✅ Одобрить",
    "reject": "❌ Отклонить",
    "edit": "✏️ Редактировать",
    "delete": "🗑️ Удалить",
    "my_lots": "🎴 Мои лоты",
    "add_lot": "➕ Добавить лот",
    "add": "Добавить",
    "cancel": "❌ Отмена",
    "subscribe": "🔔 Подписаться",
    "unsubscribe": "🔕 Отписаться",
    "back": "⬅️ Назад",
    "next": "➡️ Далее",
    "confirm": "✅ Подтвердить",
    "yes": "Да",
    "no": "Нет",
    "broadcast": "📣 Рассылка",
    "delete_requests": "🗑️ Удаления лотов",
    "back_months": "⬅️ К месяцам",
    "back_days": "⬅️ К датам",
}

CANCEL_TEXTS = {
    "addcard_cancel": ("Добавление карты отменено.", "🚫 <b>Админ отменил добавление карты</b>"),
    "removeluxury_cancel": ("Снятие лакшери отменено.", "🚫 <b>Админ отменил снятие Лакшери</b>"),
    "giveluxury_cancel": ("Выдача лакшери отменена.", "🚫 <b>Админ отменил выдачу Лакшери</b>"),
    "addadmin_cancel": ("Добавление админа отменено.", "🚫 <b>Админ отменил добавление админа</b>"),
    "removeadmin_cancel": ("Удаление админа отменено.", "🚫 <b>Админ отменил удаление админа</b>"),
    "givetrusted_cancel": ("Выдача доверия отменена.", ""),
    "removetrusted_cancel": ("Снятие доверия отменено.", ""),
}

RARITY_TREASURE = {
    "diamond": 60,
    "алмазная": 60,
    "gold": 40,
    "золотая": 40,
    "silver": 20,
    "серебряная": 20,
    "bronze": 10,
    "бронзовая": 10,
}

RARITY_EMOJI = {
    "diamond": "🔷",
    "алмазная": "🔷",
    "gold": "🟨",
    "золотая": "🟨",
    "silver": "🟦",
    "серебряная": "🟦",
    "bronze": "🟫",
    "бронзовая": "🟫",
}

RARITY_RU = {
    "diamond": "алмазная",
    "gold": "золотая",
    "silver": "серебряная",
    "bronze": "бронзовая",
}

CURRENCY_EMOJI = {
    "кристаллы": "💎 алмазы",
    "алмазы": "💎 алмазы",
    "чашки": "🍵 чай",
    "чай": "🍵 чай",
    "сокровища": "🪙 сокровища",
}

CAPTION_TEMPLATE = (
    "🏓АУКЦИОН 🏓\n\n"
    "Лот №{lot_num}\n"
    "⚙️ Тип: {auction_kind}\n\n"
    "{verified_line}"
    "{hero_line}"
    "{price_line}"
    "Принимаются ставки до\n"
    "{end_time_str} мск.\n"
    "Количество карт в лоте: {owners_count}\n"
    "{opt_meta}"
    "Оплата ставки в течение месяца.\n\n"
    "Комментарий: {comment}\n"
    "{rules_line}"
)

# Заглушки для «разбива» по редкости. Если есть явное поле в БД — используем его (см. _calc_salvage).
SALVAGE_BY_RARITY = {"bronze": 1, "silver": 3, "gold": 6, "diamond": 12}
SALVAGE_EMOJI = "🌿"


def _calc_salvage(card: dict) -> str | None:
    if not card:
        return None
    if card.get("salvage_amount"):
        try:
            return f"{int(card['salvage_amount'])} {SALVAGE_EMOJI}"
        except Exception:
            pass
    rarity = str(card.get("rarity") or "").strip().lower()
    if rarity in SALVAGE_BY_RARITY:
        return f"{SALVAGE_BY_RARITY[rarity]} {SALVAGE_EMOJI}"
    return None


def _get_effect_line(card: dict) -> str | None:
    if not card:
        return None
    # Если добавишь что-то одно из этого в таблицу cards — подхватится автоматом
    for key in ("effect", "bonus", "gives", "perk"):
        val = card.get(key)
        if val:
            s = str(val).strip()
            return s if len(s) <= 180 else s[:177] + "…"
    return None


from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def _fmt_time_hhmm_msk(dt) -> str:
    if not dt:
        return ""
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=MSK)
        else:
            dt = dt.astimezone(MSK)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


MAX_TG_CAPTION_LEN = 1024  # лимит подписи для фото в Telegram


def _one_line(s: str) -> str:
    """Склеивает переносы, чтобы caption не расползался на романы."""
    return " ".join(str(s).replace("\r", "\n").splitlines()).strip()


def _truncate(s: str, max_len: int) -> str:
    s = str(s)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def render_auction_caption(
        auction: dict,
        *,
        card: dict | None = None,
        deck: dict | None = None,
        owners_count: int | None = None,
        show_min_bid: bool = True,
) -> str:
    card = card or {}
    deck = deck or {}

    lot_num = auction.get("auction_id") or auction.get("lot_num") or ""

    hero_name = (auction.get("hero_name") or card.get("hero_name") or "") or ""
    card_name = (auction.get("card_name") or card.get("card_name") or "") or ""
    hero_name = str(hero_name).strip()
    card_name = str(card_name).strip()

    if hero_name and card_name and card_name.lower() != hero_name.lower():
        hero_line = f"{html.escape(hero_name)} — {html.escape(card_name)}\n"
    else:
        hero_line = f"{html.escape(hero_name or card_name)}\n"

    start_price = int(auction.get("start_price") or 0)
    emoji = emoji_by_currency(auction.get("currency"))
    end_time_str = auction.get("end_time_str") or _fmt_time_hhmm_msk(auction.get("end_time"))
    owners_count = int(owners_count or auction.get("owners_count") or auction.get("cards_count") or 1)

    kind_key = str(auction.get("auction_kind") or "standard").strip().lower()
    auction_kind = html.escape(auction_kind_label(kind_key))
    accepted_label = html.escape(
        currency_choices_label(
            auction.get("accepted_currencies"),
            fallback=auction.get("currency"),
            custom_terms=auction.get("custom_offer_terms"),
        )
    )
    if kind_key == "reverse":
        price_line = (
            f"Валюта ставок: {accepted_label}\n"
            f"Стартовый потолок: {start_price} {emoji}\n"
            "Ставки идут на понижение. Побеждает минимальная ставка.\n\n"
        )
        rules_line = (
            "Ставки указывайте суммой и валютой, если доступны и чай, и алмазы!"
        )
    elif kind_key == "free":
        price_line = f"Принимаются предложения: {accepted_label}\n\n"
        rules_line = "Оставляйте предложение и выбранную валюту в комментариях к этому посту!"
    else:
        suffix = " (мин ставка)" if show_min_bid else ""
        price_line = f"Цена старта: {start_price}{suffix} {emoji}\n\n"
        rules_line = "Ставки только цифрами в комментариях к этому посту!"

    sellers_total = int(auction.get("sellers_total") or 0)
    sellers_verified = int(auction.get("sellers_verified") or 0)
    is_verified = bool(auction.get("seller_verified"))
    if sellers_total:
        is_verified = (sellers_verified == sellers_total)

    verified_line = (
        "🔒 Лот от верифицированного продавца ✅\n"
        if is_verified
        else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌\n"
    )

    deck_id = (
            auction.get("deck_id")
            or card.get("deck_id")
            or deck.get("deck_id")
            or deck.get("id")
    )
    deck_name = (deck.get("name") or auction.get("deck_name") or "").strip()

    rarity = (card.get("rarity") or "").strip()
    effect = _get_effect_line(card)

    story = _one_line(card.get("story") or auction.get("story") or "")
    quote = _one_line(card.get("quote") or auction.get("quote") or "")

    gift_line = _gift_line_from_card(card)
    ex_line = _exchange_line_from_card(card)

    meta_lines: list[str] = []

    if deck_id and deck_name:
        dn = deck_name.strip()
        if str(deck_id).strip() in dn and "колода" in dn.lower():
            meta_lines.append(f"Колода: 🃏 {html.escape(str(deck_id))} колода")
        else:
            meta_lines.append(f"Колода: 🃏 {html.escape(str(deck_id))} колода — {html.escape(dn)}")
    elif deck_id:
        meta_lines.append(f"Колода: 🃏 {html.escape(str(deck_id))} колода")
    elif deck_name:
        meta_lines.append(f"Колода: 🃏 {html.escape(deck_name)}")

    if rarity:
        meta_lines.append(f"Редкость: 🏷️ {html.escape(rarity)}")

    craft = auction.get("craft_uid_possible")
    if craft is True:
        meta_lines.append("Крафт на UID: ✅ Да")
    elif craft is False:
        meta_lines.append("Крафт на UID: ❌ Нет")
    else:
        meta_lines.append("Крафт на UID: —")

    sold_count = auction.get("sold_count")
    if isinstance(sold_count, int) and sold_count >= 0:
        meta_lines.append(f"Продано ранее: 📊 {sold_count}")

    if effect:
        meta_lines.append(f"Что даёт: ✨ {html.escape(_one_line(effect))}")

    if gift_line:
        meta_lines.append(html.escape(_one_line(gift_line)))
    if ex_line:
        inner = html.escape(_one_line(ex_line))
        meta_lines.append(f"<tg-spoiler>{inner}</tg-spoiler>")

    if story:
        inner = html.escape(_truncate(story, 240))
        meta_lines.append(f"<tg-spoiler>История: 📜 {inner}</tg-spoiler>")
    if quote:
        inner = html.escape(_truncate(quote, 240))
        meta_lines.append(f"<tg-spoiler>Цитата: 💬 {inner}</tg-spoiler>")

    opt_meta = ("\n".join(meta_lines) + "\n") if meta_lines else ""

    comment = auction.get("comment")
    comment = "—" if comment in (None, "", "-") else str(comment).strip()

    text = CAPTION_TEMPLATE.format(
        lot_num=lot_num,
        auction_kind=auction_kind,
        verified_line=verified_line,
        hero_line=hero_line,
        price_line=price_line,
        end_time_str=end_time_str,
        owners_count=owners_count,
        opt_meta=opt_meta,
        comment=html.escape(comment),
        rules_line=rules_line,
    )

    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    if len(text) <= MAX_TG_CAPTION_LEN:
        return text

    if quote:
        meta_lines2 = [ln for ln in meta_lines if not ln.startswith("<tg-spoiler>Цитата:")]
        opt_meta2 = ("\n".join(meta_lines2) + "\n") if meta_lines2 else ""
        text2 = CAPTION_TEMPLATE.format(
            lot_num=lot_num,
            auction_kind=auction_kind,
            verified_line=verified_line,
            hero_line=hero_line,
            price_line=price_line,
            end_time_str=end_time_str,
            owners_count=owners_count,
            opt_meta=opt_meta2,
            comment=html.escape(comment),
            rules_line=rules_line,
        )
        while "\n\n\n" in text2:
            text2 = text2.replace("\n\n\n", "\n\n")
        if len(text2) <= MAX_TG_CAPTION_LEN:
            return text2
        text = text2

    if story:
        meta_lines3 = [ln for ln in meta_lines if not ln.startswith("<tg-spoiler>История:")]
        opt_meta3 = ("\n".join(meta_lines3) + "\n") if meta_lines3 else ""
        text3 = CAPTION_TEMPLATE.format(
            lot_num=lot_num,
            auction_kind=auction_kind,
            verified_line=verified_line,
            hero_line=hero_line,
            price_line=price_line,
            end_time_str=end_time_str,
            owners_count=owners_count,
            opt_meta=opt_meta3,
            comment=html.escape(comment),
            rules_line=rules_line,
        )
        while "\n\n\n" in text3:
            text3 = text3.replace("\n\n\n", "\n\n")
        if len(text3) <= MAX_TG_CAPTION_LEN:
            return text3
        text = text3

    return text[:MAX_TG_CAPTION_LEN]

def _exchange_line(auction: dict) -> str:
    parts = []
    t = auction.get("ex_gain_treasures") or 0
    tea = auction.get("ex_gain_tea") or 0
    dia = auction.get("ex_gain_diamonds") or 0

    if t:
        parts.append(f"+{t} 🪙")
    if tea:
        parts.append(f"+{tea} 🍵")
    if dia:
        parts.append(f"+{dia} 💎")

    return f"При разбиве даёт: {' '.join(parts)}\n" if parts else ""


def _norm_obtain_type(raw: object) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if s in {"diamonds", "diamond", "алмазы", "алмаз"}:
        return "diamonds"
    if s in {"cups", "cup", "tea", "чашки", "чашка", "чай"}:
        return "cups"
    if s in {"treasures", "treasure", "coins", "coin", "сокровища", "сокровище"}:
        return "treasures"
    return None


def _gift_line_from_card(card: dict | None) -> str:
    # Всегда возвращаем строку, чтобы “ничего не пишет” больше не существовало
    if not card:
        return "При получении в подарок даёт: 🎁 —"

    t = _norm_obtain_type(card.get("obtain_type"))
    amt = card.get("obtain_amount")

    try:
        amt_i = int(amt)
    except Exception:
        amt_i = 0

    if not t or amt_i <= 0:
        return "При получении в подарок даёт: 🎁 —"

    emoji = {"diamonds": "💎", "cups": "🍵", "treasures": "🪙"}[t]
    return f"При получении в подарок даёт: 🎁 +{amt_i} {emoji}"


def _exchange_line_from_card(card: dict) -> str | None:
    """Строка про разбив карты: бронза 10, серебро 20, золото 40, эпик 60 сокровищ."""
    if not card:
        return None

    rarity = str(card.get("rarity") or "").strip().lower()
    if not rarity:
        return None

    # нормализуем варианты, чтобы не зависеть от того, как в БД записали
    r = rarity

    # эпик
    if any(k in r for k in ("epic", "эпик")):
        amt = 60
    # золото
    elif any(k in r for k in ("gold", "золото")):
        amt = 40
    # серебро
    elif any(k in r for k in ("silver", "серебро")):
        amt = 20
    # бронза
    elif any(k in r for k in ("bronze", "бронза")):
        amt = 10
    else:
        # если редкость какая-то странная (алмаз и т.п.) — не показываем, чтобы не врать
        return None

    return f"При разбиве даёт: 🔨 +{amt} 🪙"


async def load_full_auction_ctx(auction_id: int) -> dict:
    service = await AdminAuctionContextService.create()
    return await service.load_full_context(auction_id)


AUCTION_FIELDS: Set[str] = {
    "status", "start_time", "end_time", "currency", "start_price", "comment", "discussion_message_id", "proof_photo_id"
}

CARD_FIELDS: Set[str] = {
    "card_name", "num", "hero_name", "image_id", "rarity", "deck_id", "story", "quote",
}

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
}

ACTION_LABELS.update({
    "move_lot": "⏱️ Перенос времени лота",
    "edit_pending": "✏️ Изменение заявки (модерация)",
    "edit_pending_field": "✏️ Изменение заявки (модерация)",
})

ADMIN_COMMANDS_INFO = (
    "<b>🛠️ Админ-панель: основные команды</b>\n\n"
    "<b>Главное:</b>\n"
    "<b>/admin</b> — открыть главное админ-меню\n"
    "<b>/adminhelp</b> <b>/admin_help</b> — это меню\n"
    "<b>/broadcast</b> — массовая рассылка всем пользователям\n\n"
    "<b>Статистика:</b>\n"
    "<b>/stats</b> — новые участники и заявки за сегодня\n\n"
    "<b>Модерация и лоты:</b>\n"
    "<b>/pendinglots</b> — заявки на модерацию (подтверждение, отклонение, редактирование)\n"
    "<code>/manage_lots @username</code> — лоты пользователя\n"
    "<b>/preview_schedule</b> — просмотр расписания лотов\n"
    "<b>/edit_schedule</b> — редактор расписания\n\n"
    "<b>Доверенные пользователи:</b>\n"
    "<code>/set_trusted @username</code>\n"
    "<code>/set_trusted 123456789</code>\n"
    "<code>/set_trusted @username 123456789</code>\n"
    "— выдать статус 'Доверенный' (работает по username, user_id или сразу обоим через пробел)\n"
    "<code>/unset_trusted @username</code>\n"
    "<code>/unset_trusted 123456789</code>\n"
    "<code>/unset_trusted @username 123456789</code>\n"
    "— снять статус 'Доверенный'\n\n"
    "<b>Удаление лотов:</b>\n"
    "<b>/delete_requests</b> — заявки на удаление лотов\n\n"
    "<b>Редактирование лотов:</b>\n"
    "• Все лоты можно <b>редактировать</b> и <b>удалять</b> через инлайн-клавиатуры\n"
    "• Подтверждение — пошаговый выбор даты и времени\n\n"
    "<b>Карты:</b>\n"
    "<b>/addcard</b> — добавить карту в базу\n"
    "<b>/cards [поиск]</b> — поиск/просмотр карт\n"
)
