"""Auction guides, FAQ navigation and gratitude callbacks."""

import html
import re

from aiogram import Router, F
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.auction.kinds import auction_kind_keyboard
from bot.services.guides import GuideThanksService
from bot.telegram.states import UserAddLotFSM

router = Router(name=__name__)


async def _send_long_message(
    message: types.Message, lines: list[str], *, chunk_limit: int = 3500
) -> None:
    """
    Безопасно отправляет большой текст частями, чтобы не ловить
    TelegramBadRequest: message is too long.
    Разбиваем по строкам, не рвём HTML-теги.
    """
    buf: list[str] = []
    cur_len = 0

    async def _flush():
        nonlocal buf, cur_len
        if buf:
            await message.answer("\n".join(buf), parse_mode="HTML")
            buf = []
            cur_len = 0

    for line in lines:
        # +1 за перевод строки
        add_len = len(line) + (1 if buf else 0)
        if cur_len + add_len > chunk_limit:
            await _flush()
        buf.append(line)
        cur_len += add_len

    await _flush()


def _tg_username_key(username: str | None) -> str:
    """Ключ для склейки: без @, lower."""
    if not username:
        return ""
    return username.strip().lstrip("@").lower()


def _tg_username_clean(username: str | None) -> str:
    """Для отображения/ссылки: без @, как есть."""
    if not username:
        return ""
    return username.strip().lstrip("@")


def admin_thanks_text(page: int, items: list, total_pages: int) -> str:
    # Совместимость: если кто-то вызвал как admin_thanks_text(items, page, total_pages)
    if isinstance(page, (list, tuple)) and isinstance(items, int):
        page, items = items, page

    try:
        page = int(page)
    except Exception:
        page = 0

    if not isinstance(items, (list, tuple)):
        items = []

    lines = [
        "🏆 <b>Рейтинг админских “Спасибо”</b>",
        f"📖 Страница: <b>{page + 1}/{int(total_pages) if total_pages else 1}</b>",
        "",
    ]

    if not items:
        lines.append("Пока тут пусто. Люди ещё не научились благодарить.")
        return "\n".join(lines)

    def _clean_username(u: str | None) -> str:
        return (u or "").strip().lstrip("@")

    def _key(u: str | None) -> str:
        return _clean_username(u).lower()

    # Склеиваем дубли (Nick vs @Nick)
    merged: dict[str, dict] = {}
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            author, total, users = row[0], row[1], row[2]
        else:
            # asyncpg.Record / dict
            try:
                author = row["author"]
                total = row["thanks_total"] if "thanks_total" in row else row.get("total")
                users = row["users_total"] if "users_total" in row else row.get("users")
            except Exception:
                continue

        author_clean = _clean_username(str(author or ""))
        if not author_clean:
            continue

        k = author_clean.lower()
        rec = merged.get(k)
        if not rec:
            rec = {"author": author_clean, "total": 0, "users": 0}
            merged[k] = rec

        rec["total"] += int(total or 0)
        # users корректно склеить можно только по user_id-сетам; тут безопасно берём max
        rec["users"] = max(rec["users"], int(users or 0))

    rows = sorted(merged.values(), key=lambda x: (-x["total"], -x["users"], x["author"].lower()))

    base = page * ADMIN_THANKS_PAGE_SIZE
    for i, r in enumerate(rows, start=1):
        place = base + i
        author = html.escape(r["author"])
        link = f'<a href="https://t.me/{author}">@{author}</a>'
        lines.append(f"{place}. {link} — <b>{r['total']}</b> 🙏 | 👥 <b>{r['users']}</b>")

    return "\n".join(lines)


def admin_thanks_kb(page: int, total_pages: int, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"auk_admin_thanks:page:{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}", callback_data="auk_admin_thanks:noop"
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(text="➡️", callback_data=f"auk_admin_thanks:page:{page + 1}")
        )
    kb.row(*nav)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))

    # та же глобальная кнопка спасибо (чтобы была “везде”)
    kb.row(
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data="auk_guides_thanks:menu_root",
        )
    )
    return kb.as_markup()


@router.callback_query(
    UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guide_menu:thanks_top"
)
async def auk_admin_thanks_open(call: types.CallbackQuery) -> None:
    await call.answer()
    total, users = await _get_guides_thanks_totals()
    items, total_pages = await _get_admin_thanks_page(0)

    await call.message.edit_text(
        admin_thanks_text(0, items, total_pages),
        parse_mode="HTML",
        reply_markup=admin_thanks_kb(0, total_pages, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(
    UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_admin_thanks:page:")
)
async def auk_admin_thanks_page(call: types.CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":")[-1])
    total, users = await _get_guides_thanks_totals()
    items, total_pages = await _get_admin_thanks_page(page)

    await call.message.edit_text(
        admin_thanks_text(page, items, total_pages),
        parse_mode="HTML",
        reply_markup=admin_thanks_kb(page, total_pages, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_admin_thanks:noop")
async def auk_admin_thanks_noop(call: types.CallbackQuery) -> None:
    await call.answer()


# =======================
# 📚 GUIDES (content)
# =======================
GUIDE_AUTHOR_USERNAME = "Dear_Davidik"
GUIDE_AUTHOR_LINK = f'<a href="https://t.me/{GUIDE_AUTHOR_USERNAME}">@{GUIDE_AUTHOR_USERNAME}</a>'

GUIDE_CREDIT = f"\n\n✍️ <b>Написал и оформил:</b> {GUIDE_AUTHOR_LINK}"
DAVID_SIGN = f"\n\n✍️ <b>Ответ от:</b> {GUIDE_AUTHOR_LINK}"

# 🆔 UID craft guide authors
GUIDE_UID_AUTHOR_USERNAME = "skamto"
GUIDE_UID_AUTHOR_LINK = (
    f'<a href="https://t.me/{GUIDE_UID_AUTHOR_USERNAME}">@{GUIDE_UID_AUTHOR_USERNAME}</a>'
)

GUIDE_UID_CREDIT = (
    f"\n\n✍️ <b>Автор:</b> Анонимный автор\n✍️ <b>Написал и оформил:</b> {GUIDE_AUTHOR_LINK}"
)

GUIDE_TREASURES_PHOTO_ID = (
    "AgACAgQAAxkBAAEH03RpY9cqYlBZOvrwI4gLmb-YGcw7JAACDQtrGw6yIVNMfJZvRLF9cQEAAwIAA3gAAzgE"
)

GUIDE_TREASURES_TEXT = (
    "🪙 <b>Как оплачивать сокровищами?</b>\n\n"
    "🧩 Сокровища — ресурс игры: даётся при разбиве карт, а также падает из колеса 🎡.\n"
    "Мы используем 🪙 для покупки колод.\n\n"
    "✅ <b>Для оплаты сокровищами нужно:</b>\n"
    "🎁 Подарочные карты в нужном количестве, которые при получении дадут столько 🪙, сколько нужно заплатить.\n\n"
    "🃏 <b>Карты делятся на номинал:</b>\n"
    "🥉 Бронза — <b>10</b> 🪙\n"
    "🥈 Серебро — <b>20</b> 🪙\n"
    "🥇 Золото — <b>40</b> 🪙\n"
    "💎 Эпик — <b>60</b> 🪙\n\n"
    "💖☀️💧 <i>(При разбиве сокровища всегда дают рандомное количество по виду: сердца, солнца, капли)</i>\n\n"
    "❗️❗️❗️ <b>ПОЖАЛУЙСТА, СЧИТАЙТЕ ВНИМАТЕЛЬНЕЕ, КАКОЕ КОЛИЧЕСТВО СОКРОВИЩ ПОЛУЧИТ ЧЕЛОВЕК "
    "ПРИ ВАШЕЙ ОТПРАВКЕ КАРТ НА РАЗБИВ</b> ❗️❗️❗️"
) + GUIDE_CREDIT

GUIDE_CUPS_PHOTOS = [
    "AgACAgQAAxkBAAEH0_lpY9pRWQU0QDAy8rwxsG3LV0546wACDgtrGw6yIVM6nEZHpvpR3QEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH0_tpY9pjdTtu4SnivV3D2Oe1VP0M4gACDwtrGw6yIVMayvfycNczuAEAAwIAA3gAAzgE",
    "AgACAgQAAxkBAAEH0_1pY9pxvKD_m4858Rj9DKI-J756vQACEAtrGw6yIVPxj6wzv_XKZQEAAwIAA3gAAzgE",
    "AgACAgQAAxkBAAEH0_9pY9qDysXqkdw9HUEfCdZdLQ2duwACEQtrGw6yIVPSRSmOwBZgJAEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH1AFpY9qYF3rdjlAOnltdqeQbD6m-2QACEgtrGw6yIVOyMTjvifnyRgEAAwIAA3kAAzgE",
]
GUIDE_TYPE_ANY_CARD_TEXT = (
    "🃏 <b>«Любая карта» — что это значит и как работает?</b>\n\n"
    "Лот «Любая карта» означает: не важно, бронза/серебро/золото/эпик.\n"
    "Но он <b>обязывает продавца</b> иметь у себя в наличии <b>все карты</b> выбранной категории на момент выставления лота.\n\n"
    "👑 <b>Кто выбирает карту?</b>\n"
    "✅ <b>Победитель</b>. Именно он говорит, какую карту из категории он хочет.\n\n"
    "⚠️ <b>Важное предупреждение</b>\n"
    "Если победитель выбрал карту, а у вас её нет, это считается махинациями.\n"
    "За такое можно получить <b>варн/бан</b>.\n\n"
    "📦 <b>Что нужно иметь в наличии</b>\n"
    "• 🥉 «Бронза» — все бронзы\n"
    "• 🥈 «Серебро» — все серебра\n"
    "• 🥇 «Золото» — все золота\n"
    "• 💎 «Эпик» — все эпики\n"
    "• 🃏 «Любая карта» — <b>все карты</b> на момент выставления аукциона\n\n"
    "🙏 Пожалуйста, рассчитывайте свои возможности и желания заранее.\n"
) + GUIDE_CREDIT
GUIDE_CUPS_TEXT = (
    "🍵 <b>Как оплачивать чашками?</b>\n\n"
    "📌 Чашки — один из основных ресурсов игры для прохождения историй.\n"
    "🎁 Их можно получить, когда вам дарят карту, что даёт чашки: <b>2/4/6/8/12</b>.\n\n"
    "✅ <b>Для оплаты чашками нужно:</b>\n"
    "• 🎁 Подарочные карты, которые при получении дают нужное количество 🍵.\n"
    "🛒 <i>Их можно приобрести, покупая колоды.</i>\n\n"
    "🔎 <b>Как понять, что карта чашечная?</b>\n"
    "1️⃣ Выберите карту и нажмите «создать подарочную» — покажет, что даст карта при отправке.\n"
    "2️⃣ Нажмите ➕ в анкете рядом с коллекционными картами. Там всегда последняя колода, которую можно купить. "
    "В правом верхнем углу будет указан номинал.\n"
    "3️⃣ Посмотреть трекер-лист по картам, что вышли за всё время.\n\n"
    "❗️ <b>БУДЬТЕ ВНИМАТЕЛЬНЫ: СЧИТАЙТЕ, СКОЛЬКО ТОЧНО КАРТ С ЧАШКАМИ У ВАС ЕСТЬ ДЛЯ ОПЛАТЫ</b> ❗️"
) + GUIDE_CREDIT

GUIDE_DIAMONDS_PHOTOS = [
    "AgACAgQAAxkBAAEH1SppY93FVqn3FpG6Rn-c3cmdoQAB6NUAAhYLaxsOsiFTFl026WXL68oBAAMCAAN5AAM4BA",
    "AgACAgQAAxkBAAEH1SxpY93YYm0pJfP0TlVyxfxSodaeBwACFwtrGw6yIVOXMMQvv6B0DwEAAwIAA20AAzgE",
    "AgACAgQAAxkBAAEH1S5pY93oO_CT7o3Rs1shMyJ_OoQmhwACGAtrGw6yIVMRnEDNTnX3AAEBAAMCAANtAAM4BA",
]

GUIDE_DIAMONDS_TEXT = (
    "💎 <b>Как оплачивать алмазами?</b>\n\n"
    "💠 Алмазы — основная валюта игры: на них совершают покупки в сериях и берут удвоение на колесо 🎡.\n\n"
    "✅ <b>У вас должно быть для оплаты алмазами:</b>\n\n"
    "1️⃣ <b>Нужное количество алмазов</b> и точный расчёт выплат за месяц (если нет твинов).\n"
    "📝 <i>Примечание:</i>\n"
    "• Не рекомендуется превышать лимит, если стоимость вышла в <b>900</b> 💎 за месяц (оплата по 30 💎 в сутки)\n"
    "• и <b>3000</b> 💎 в месяц (по 100 💎 в сутки с функцией <b>+Друзья</b>)\n\n"
    "⚠️ <i>P.S.</i> Мы не рекомендуем превышать лимиты и не несём ответственность за ваши подсчёты.\n\n"
    "2️⃣ <b>Ферма</b> в игре, благодаря которой вы будете кидать большое количество 💎.\n"
    "• <i>Фермы</i> — это специальные дополнительные аккаунты в приложениях или программах (пример в фото).\n\n"
    "3️⃣ 🎁 Вы также можете оплачивать картами, что дают 💎 при получении.\n"
    "• Но в этом варианте их обычно нужно слишком много.\n\n"
    "4️⃣ 🔁 Возможен другой источник оплаты, если нет ферм.\n"
    "Например: вы выставили лот и заработали 15к 💎, но вы также купили карту за 10к 💎. "
    "Вы можете попросить человека оплатить ваш долг.\n"
    "📣 Пожалуйста, поставьте в известность обоих участников сделки.\n\n"
    "❗️ <b>БУДЬТЕ БДИТЕЛЬНЫ И РАССЧИТЫВАЙТЕ СВОИ АЛМАЗЫ ПРИ ПОКУПКЕ КАРТ</b> ❗️"
) + GUIDE_CREDIT

GUIDE_UID_CRAFT_PHOTO_ID = (
    "AgACAgQAAxkBAAEIU7FpaOoQaDSe9h1-4ziJzuFSSJAUWwACSwtrG-bLSFPUmi3RIn1HpQEAAwIAA3kAAzgE"
)

GUIDE_UID_CRAFT_TEXT = (
    "🆕 <b>«Крафт по UID»</b>\n\n"
    "✨ <b>Крафт по UID</b> — это когда продавец покупает на официальном сайте за реальные деньги право крафта, "
    "но оформляет его на UID покупателя.\n"
    "💎 Покупатель платит только алмазами/чашками/сокровищами, реальные деньги тратит продавец.\n\n"
    "🎁 <b>На официальном сайте по UID можно закрафтить:</b>\n"
    "• 🃏 подарочный дубль карты: бронза / серебро / золото / эпик\n"
    "• 🤝 друзей\n"
    "• 🧩 дополнительные слоты\n"
    "• 🎰 крутки: 10 / 50 / 100\n\n"
    "🔧 <b>Как это работает в аукционе</b>\n"
    "1️⃣ Продавец создаёт лот через бота и нажимает кнопку «ДА» на вопрос о возможности <b>Крафта по UID</b>.\n"
    "2️⃣ Проходит аукцион, бот определяет победителя.\n"
    "3️⃣ Продавцу передаётся UID победителя.\n"
    "4️⃣ Продавец:\n"
    "   • заходит на официальный сайт Клуба Романтики,\n"
    "   • покупает нужный крафт за реальные деньги,\n"
    "   • вводит UID победителя.\n"
    "✅ Победитель в игре получает <b>право крафта</b> (карта/друзья/слоты/крутки — в зависимости от лота).\n\n"
    "⚠️ <b>Важно помнить</b>\n"
    "• «Крафт по UID» — это передача права на крафт, а не просто «скинуть карту».\n"
    "• 💰 Деньги за крафт платит продавец лота на официальном сайте.\n"
    "• 🔎 Очень внимательно проверяйте UID победителя — крафт уйдёт именно на тот аккаунт, который вы введёте.\n"
    "• 🃏 Если вы хотите выставить карту, а не крафт — выбирайте «НЕТ» на вопрос о возможности «Крафта по UID».\n"
    "📌 Все остальные правила аукциона и работы бота Макса остаются прежними"
) + GUIDE_UID_CREDIT

GUIDE_AUTOBID_PHOTO_ID = (
    "AgACAgQAAxkBAAEJIBxpenbWkrqL-xVl_scLsl-vrpKHFQAC5gxrGxTH2FNrxa7zQVGDMgEAAwIAA3kAAzgE"
)

GUIDE_VENOM_RULES_TEXT = (
    "🕷️ <b>Гайд: как Веном реагирует на ставки</b>\n\n"
    "За соблюдение правил во время аукционов следит бот «Веном». "
    "Собрали примеры его ответов, чтобы вы понимали, что будет происходить 👇\n\n"
    "1️⃣ <b>Нормальная ставка</b>\n"
    "Пользователь: <code>300</code>\n"
    "✅ Ставка записана.\n\n"
    "2️⃣ <b>Ставка ниже минималки</b>\n"
    "Пользователь: <code>280</code>\n"
    "Веном: ⚠️ Ставка не принята. Минимум сейчас: <b>290</b> чай.\n"
    "📝 Сообщение остаётся.\n\n"
    "3️⃣ <b>Не ставка (текст вместо числа)</b>\n"
    "Пользователь: <code>две сотни</code>\n"
    "Веном: ❌ Сообщение удаляется.\n"
    "❗ Пиши числом или с <b>K/К</b> (например <code>10к</code>).\n\n"
    "4️⃣ <b>Неправильный шаг валюты</b>\n"
    "Пользователь: <code>291</code> (чай, должно быть чётное)\n"
    "Веном: ❌ Ставка удалена, мут <b>1 мин</b>.\n\n"
    "5️⃣ <b>Флуд (не ответ на пост лота)</b>\n"
    "Пользователь: <code>поздравляю</code>\n"
    "Веном: ❌ Сообщение удаляется, мут <b>1 мин</b>.\n\n"
    "6️⃣ <b>Исправление ставки (/oops)</b>\n"
    "Пользователь: <code>2000</code> → <code>oops 200</code>\n"
    "Веном: ✅ Ставка исправлена.\n\n"
    "7️⃣ <b>Поздний /oops (больше 60 сек)</b>\n"
    "Веном: ❌ Мут на <b>1 мин</b>, ставка не исправлена.\n\n"
    "8️⃣ <b>Удаление ставки вручную</b>\n"
    "Веном: ⚠️ Предупреждение за удаление ставки.\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "👑 <b>Для админов</b>\n"
    "• Админский флуд/не-ставка игнорируется.\n"
    "• Ставки ниже минимума и неправильный шаг валюты: бот пишет, но <b>не удаляет</b>.\n"
    "• Админские ставки можно редактировать без ограничений.\n"
) + GUIDE_CREDIT
GUIDE_TYPE_EXCHANGE_TEXT = (
    "🛒 <b>Биржа</b>\n\n"
    "Биржа — масштабный аукцион, в котором участвует много людей и выставляется огромное количество карт.\n"
    "Здесь карты уходят <b>по фиксированной цене</b>.\n"
    "Вы боретесь не за цену, а за <b>количество</b>.\n\n"
    "⏱️ <b>Суть биржи</b>\n"
    "Биржа — это гонка на время. Самое важное — успеть урвать нужные карты!\n\n"
    "📝 <b>Кто может подать заявки</b>\n"
    "Подать заявку/ки на биржу может каждый желающий, лимит не ограничен.\n\n"
    "🧍‍♀️ <b>Кто может покупать</b>\n"
    "Участвовать может любой, но лимиты на покупку разные:\n"
    "• 🍵 Чайные карты — до <b>1 шт</b> одному человеку\n"
    "• 💎 Алмазные карты — до <b>3 шт</b>\n"
    "• 🃏 Колода — <b>одна в одни руки</b>\n\n"
    "🕒 <b>Время проведения</b>\n"
    "В течение суток, пока карты не будут распроданы.\n\n"
    "💳 <b>Оплата</b>\n"
    "Алмазы 💎\n\n"
    "✅ <b>Как забрать карту</b>\n"
    "Нужно написать в комментариях: <code>Беру</code>\n"
    "Если больше одной: <code>Беру 3</code>\n"
) + GUIDE_CREDIT
GUIDE_TYPE_STANDARD_TEXT = (
    "⭐️ <b>Стандартный аукцион</b>\n\n"
    "Это основной формат аукциона: лот публикуется в канале, а ставки делаются <b>только в комментариях</b> под постом.\n\n"
    "📌 <b>Где проходит</b>\n"
    "• Пост лота в канале\n"
    "• Комментарии под постом (там же бот принимает ставки)\n\n"
    "🧾 <b>Как выставить лот</b>\n"
    "1️⃣ /addlot → выбери <b>⭐️ Стандартный</b>\n"
    "2️⃣ Выбери <b>колоду</b> из списка (1–20) или «Свой вариант / пресеты»\n"
    "3️⃣ Заполни данные карты и стартовую цену\n"
    "4️⃣ Дождись модерации, затем лот выйдет по расписанию\n\n"
    "🕷️ <b>Как принимаются ставки</b>\n"
    "• Нормальная ставка числом: ✅ записывается\n"
    "• Текст вместо числа: ❌ удаляется\n"
    "• Шаг валюты/ошибки: могут быть ❌ удаление/мут (зависит от ситуации)\n"
    "• Есть исправление ставки через <code>/oops</code> (ограничено по времени)\n\n"
    "⚠️ <b>Важно</b>\n"
    "• Учитываются только сообщения-ставки в комментариях под лотом.\n"
    "• Следить за правилами помогает бот «Веном».\n"
) + GUIDE_CREDIT
GUIDE_AUTOBID_TEXT = (
    "🚀 <b>«Автоставки от Макса»</b> 🤖 (обновлённая механика)\n\n"
    "🎯 <b>Автоставка теперь работает как снайпер</b>: бот не торгуется шагами бесконечно, "
    "а делает <b>одну финальную ставку</b> под конец аукциона.\n\n"
    "⚙️ <b>Как это работает</b>\n"
    "1️⃣ Ты выбираешь лот и задаёшь <b>максимальную сумму</b> (лимит).\n"
    "2️⃣ Бот ждёт почти до самого конца.\n"
    "3️⃣ В финальные секунды бот делает <b>одну ставку</b> в комментариях под лотом.\n"
    "✅ Если твой лимит выше текущей ставки, бот постарается перехватить лидерство.\n\n"
    "⏰ <b>Когда бот ставит</b>\n"
    "• Обычно примерно за <b>2 секунды</b> до конца.\n"
    "• Часто это выглядит как ставка в районе <code>:58</code> перед завершением.\n"
    "• Перед ставкой может появиться <i>typing…</i>, чтобы это не выглядело как вмешательство инопланетян.\n\n"
    "💱 <b>Особенности по валютам</b>\n\n"
    "💎 <b>Алмазы</b>\n"
    "• Ставки учитываются кратно <b>30</b>.\n"
    "• Если текущая ставка ниже твоего лимита: бот повышает по правилам, стараясь выйти на <b>лимит</b>.\n"
    "• Если тебя уже догнали до лимита: бот может сделать <b>одну попытку оверкапа</b> <b>+90💎</b> (если это имеет смысл).\n\n"
    "☕️ <b>Чай / чашки</b>\n"
    "• Если текущая ставка ниже твоего лимита: бот ставит сразу <b>лимит</b>.\n"
    "• Если тебя догнали до лимита: может сделать <b>одну попытку оверкапа</b> <b>+2☕️</b>.\n\n"
    "🪙 <b>Другое (монеты и т.п.)</b>\n"
    "• Если текущая ставка ниже лимита: бот ставит сразу <b>лимит</b>.\n"
    "• Если тебя догнали: может сделать <b>одну попытку оверкапа</b> <b>+10</b>.\n\n"
    "⚠️ <b>Важно</b>\n"
    "• Это не «автоторги каждую минуту». Это <b>один финальный выстрел</b>.\n"
    "• Если ты и так лидер, бот <b>не перебивает сам себя</b>.\n"
    "• Работает только в <b>комментариях</b> под постом лота (как и обычные ставки).\n"
    "• Функция <b>платная</b> и включается вручную админами.\n"
    "• Привязка идёт к <b>конкретному лоту</b> и <b>конкретному пользователю</b>.\n\n"
    "📩 <b>Как подключить</b>\n"
    "Напиши админам:\n"
    "• 🆔 ID лота\n"
    "• 👤 твой @username\n"
    "• 🔢 лимит (максимальная сумма)\n"
) + GUIDE_CREDIT

# =======================
# 📝 GUIDE: application (how to submit)
# =======================
GUIDE_REPORT_SCAM_PHOTOS = [
    # 1
    "AgACAgQAAxkBAAELXiFpmg8yRzgwu-gRxXaBVQh3KN2kqQACzw1rG9-W0FAPDX9nx1qwQAEAAwIAA3kAAzoE",
    # 2
    "AgACAgQAAxkBAAELXiNpmg9Ec8PSoI6evB8l9DkZ4tEQ2AAC0A1rG9-W0FCkfb_oB8FRKwEAAwIAA3kAAzoE",
    # 3
    "AgACAgQAAxkBAAELXiVpmg9VQoVSUKt-mAKJrEbsm5NsGAAC0Q1rG9-W0FBKbiAy9_9HlwEAAwIAA3kAAzoE",
    # 4
    "AgACAgQAAxkBAAELXidpmg99i9fPTqxwr-a32L7zIjbb7wAC0g1rG9-W0FCXdaOICCT6ywEAAwIAA3kAAzoE",
    # 5
    "AgACAgQAAxkBAAELXixpmg-QpPZlyZfTeKEw25_luiUthQAC0w1rG9-W0FDLfAABVmUt6csBAAMCAAN5AAM6BA",
]
GUIDE_APPLY_PHOTOS = [
    "AgACAgQAAxkBAAEH2E5pY_DClQV03UhjYeZCXEl2BUpfiQACRAtrGw6yIVPgkaiIOnb8QQEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FNpY_GSq8Vr0_99IXQTCv04eXbuHgACRQtrGw6yIVNCfLlJztdRpQEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FVpY_KNg3NTdlVhvD3bz9d1ZWA7mQACRgtrGw6yIVNTA3zvEp4JYwEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FdpY_Kjh7nHdjkrL__D0HtOP8f2ugACRwtrGw6yIVNO2h2xVzuhQwEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FppY_K-PgiH6JRj_XovfUsKrVatFAACSAtrGw6yIVNaTRoNljdP2AEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2F5pY_LPibtWmDEJVVXdEke00KTMFQACSQtrGw6yIVMlcJ6Upw7olAEAAwIAA3kAAzgE",
]

GUIDE_APPLY_TEXT = (
    "📝 <b>Как подать заявку на аукцион?</b>\n\n"
    "1️⃣ 🤖 Зайдите в бот Макс (<code>@RomanticClubBot</code>) и нажмите <b>Старт</b>.\n\n"
    "2️⃣ 📱 В левом нижнем углу откройте меню и выберите <b>«Подать заявку на аукцион»</b>.\n\n"
    "3️⃣ 🏷️ Выберите вид аукциона.\n\n"
    "4️⃣ 🗂️ Найдите ту колоду и карту, что у вас есть.\n\n"
    "5️⃣ 💰 Выберите номинал (🍵 чай / 💎 алмазы / 🪙 сокровища), а также стартовую ставку из предложенных.\n"
    "При желании добавьте комментарий.\n\n"
    "6️⃣ 📸 Если вы <b>не ЛАКШЕРИ</b> (человек с подпиской), то вы обязаны отправить фото подтверждения подарочной карты в наличии.\n"
    "❗️<b>Отправлять только 1 скрин</b>❗️\n\n"
    "7️⃣ ⏳ Через некоторое время вам придёт подтверждение на добавление вашего Лота на аукцион "
    "(в течение <b>2 суток</b>, всё зависит от загруженности бота).\n\n"
    "📸 Примеры скринов можно открыть кнопкой «Примеры (скрины)»."
) + GUIDE_CREDIT
GUIDE_REPORT_SCAM_TEXT = (
    "🛡️ <b>Гайд: как подать жалобу на мошенника?</b>\n\n"
    "Сейчас есть 2 рабочих способа написать в официальную поддержку КР 📩\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌐 <b>Вариант 1: через официальный сайт (где вы делаете покупки)</b>\n"
    "1️⃣ Перейдите на официальный сайт КР (тот, где оформляете покупки).\n"
    "2️⃣ Справа внизу нажмите фиолетовый значок со знаком вопросика ❔\n"
    "3️⃣ Он перенаправит вас в поддержку.\n\n"
    "🖼️ <i>(Изображение 1)</i>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎮 <b>Вариант 2: через приложение игры</b>\n"
    "1️⃣ Зайдите в игру.\n"
    "2️⃣ Нажмите ⚙️ настройки в правом верхнем углу.\n\n"
    "🖼️ <i>(Изображение 2)</i>\n\n"
    "3️⃣ Вас перекинет в основное меню, выберите «Поддержка» 🆘\n\n"
    "🖼️ <i>(Изображение 3)</i>\n\n"
    "4️⃣ Далее нажмите на нужную почту (контакт поддержки) 📧\n"
    "5️⃣ Откроется почта, и там уже пишете жалобу + прикладываете доказательства.\n\n"
    "🖼️ <i>(Изображение 4–5)</i>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📎 <b>Что лучше указать в жалобе (чтобы не «потеряли»)</b>\n"
    "✅ ID игрока (UID)\n"
    "✅ Суть обмана (что обещал/что получил/что не сделал)\n"
    "✅ Дата/примерное время\n"
    "✅ Скриншоты переписки/договорённости/пруфы передачи карт\n\n"
    "➡️ Готовые тексты жалоб откройте кнопкой ниже."
) + GUIDE_CREDIT

GUIDE_LUXURY_PERKS_TEXT = (
    "👑 <b>Лакшери-плюшки в боте Максе и как ими пользоваться</b>\n\n"
    "📌 Чтобы всё заработало после покупки Лакшери у админа, сначала обновите статус в боте.\n"
    "💳 Стоимость: <b>199/299₽</b> в месяц (в зависимости от уровня, возможна оплата другой валютой).\n\n"
    "🔄 <b>1) Обновить статус Лакшери</b>\n"
    "• Откройте меню и нажмите «Проверить Лакшери статус»\n"
    "• Команда: <code>/luxury_check</code>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📒 <b>2) Журнал самых ожидаемых карт</b>\n"
    "Показывает рейтинг карт, на которые подписаны люди.\n"
    "Это помогает понять:\n"
    "• какие карты выгоднее выставлять\n"
    "• с каких можно получить больше прибыли\n"
    "Команда: <code>/lux_top</code>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🕳 <b>3) Свободные места на аукционе (дыры в расписании)</b>\n"
    "Показывает свободное время, куда можно попросить админа поставить ваш лот.\n"
    "Команда: <code>/gaps</code>\n\n"
    "🗓 <b>Форматы для /gaps</b>\n"
    "✅ <b>Месяц</b> (покажет свободные слоты по дням):\n"
    "• <code>/gaps 2026-02</code>\n"
    "• <code>/gaps 02.2026</code>\n"
    "• <code>/gaps февраль</code> / <code>/gaps фев</code>\n"
    "• <code>/gaps 2</code>\n\n"
    "✅ <b>Один день</b> (раздельный вывод по «Показ/Лакшери/Обычные»):\n"
    "• <code>/gaps 2026-01-15</code>\n"
    "• <code>/gaps 15.01</code>\n"
    "• <code>/gaps 15.01.2026</code>\n"
    "• <code>/gaps сегодня</code> / <code>/gaps завтра</code>\n\n"
    "ℹ️ Если год не указан, бот подставит текущий, а если дата/месяц уже прошли, возьмёт следующий год (для планирования).\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📅 <b>4) Расписание аукционов на любую дату</b>\n"
    "Доступ к расписанию на ближайшие 3 месяца: можно смотреть, когда и какие лоты будут.\n"
    "Команда: <code>/vip_schedule</code>\n"
) + GUIDE_CREDIT
GUIDE_TYPE_STANDARD_TEXT = (
    "⭐️ <b>Стандартный аукцион</b>\n\n"
    "Это основной формат: лот публикуется в канале, ставки делаются <b>только в комментариях</b> под постом.\n\n"
    "📌 <b>Как участвовать</b>\n"
    "• Открываете пост лота\n"
    "• Пишите ставку числом в комментариях\n\n"
    "⚠️ <b>Важно</b>\n"
    "• Считаются только ставки в комментариях под постом\n"
    "• За правилами следит «Веном» (удаление/мут/предупреждения по ситуации)\n"
) + GUIDE_CREDIT

GUIDE_REPORT_SCAM_TEMPLATES_TEXT = (
    "📨 <b>Шаблоны жалоб</b> (копируй-вставляй)\n\n"
    "⚠️ Совет: добавьте 1–2 строки от себя (дата/обстоятельства) и прикрепите пруфы 📎\n"
    "ID мошенника: <code>5ce16c00e4b0aed72208dee5</code>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "1) Здравствуйте, хочу проинформировать о мошеннических действиях со стороны пользователя с ID: 5ce16c00e4b0aed72208dee5. "
    "Согласно договорённости, этот игрок обязался отправить свои карты взамен отправленных мною карт, но так и не выполнил своё обязательство.\n"
    "Прошу принять меры и учесть данное нарушение.\n\n"
    "2) Здравствуйте,\n"
    "Хочу уведомить о мошенничестве со стороны пользователя 5ce16c00e4b0aed72208dee5. Она предлагает купить карты за карты, после выплаты не отправляет свои.\n\n"
    "3) Добрый день,\n"
    "Прошу обратить внимание на мошеннические действия пользователя с ID 5ce16c00e4b0aed72208dee5. Она обманула очень многих.\n\n"
    "4) Приветствую,\n"
    "Сообщаю о нарушении условий сделки со стороны игрока 5ce16c00e4b0aed72208dee5. Он получил карты по договору, но не исполнил свою часть обязательств обмена картами.\n\n"
    "5) Здравствуйте,\n"
    "Информирую вас о ситуации с мошенничеством от пользователя 5ce16c00e4b0aed72208dee5. В тематических группах этот пользователь разводит других пользователей на карты!\n\n"
    "6) Добрый день,\n"
    "Заявляю о том, что пользователь 5ce16c00e4b0aed72208dee5 нарушил условия сделки: по договору он должен был прислать карты за переданные карты, но так этого не сделал.\n\n"
    "7) Здравствуйте,\n"
    "Прошу обратить внимание на действия игрока 5ce16c00e4b0aed72208dee5, который, получив карты, не исполнил обязательство по выплате, нарушив соглашение.\n\n"
    "8) Добрый день,\n"
    "Обращаюсь с жалобой на мошенничество со стороны пользователя 5ce16c00e4b0aed72208dee5. Девушка так и не отправила мне карты в ответ, несмотря на договорённость.\n\n"
    "9) Здравствуйте,\n"
    "Информирую о том, что игрок с ID 5ce16c00e4b0aed72208dee5 нарушил условия сделки. После получения карт он не произвёл оплату картами в ответ, как было обещано в соглашении.\n\n"
    "10) Добрый день,\n"
    "Сообщаю о мошеннических действиях от пользователя с ID 5ce16c00e4b0aed72208dee5. Он обязался выслать карты за полученные карты, но не выполнил своё обязательство.\n\n"
    "11) Здравствуйте,\n"
    "Уведомляю вас о том, что игрок 5ce16c00e4b0aed72208dee5 не выполняет условия соглашения: после получения карт он не отправляет желаемые карты и перестает выходить на связь."
) + GUIDE_CREDIT
# =======================
# 🙏 THANKS (global)
# =======================


async def _ensure_guides_thanks_table() -> None:
    """Compatibility wrapper for callers of the former handler helper."""

    service = await GuideThanksService.create()
    await service.ensure_schema()


async def _get_guides_thanks_totals() -> tuple[int, int]:
    service = await GuideThanksService.create()
    return await service.totals()


async def _inc_guides_thanks(user_id: int, author: str | None = None) -> tuple[int, int]:
    service = await GuideThanksService.create()
    return await service.increment(user_id=int(user_id), author=author)


async def _reset_guides_thanks() -> None:
    """Полное обнуление глобального счётчика 'Спасибо' для гайдов."""

    service = await GuideThanksService.create()
    await service.reset()


# =======================
# 📚 GUIDES (menus + kb)
# =======================

GUIDES_MENU_TEXT: dict[str, str] = {
    "menu_root": ("📚 <b>Гайды</b>\nВыберите раздел:"),
    "menu_payment": ("💳 <b>Оплата</b>\nВыберите способ оплаты:"),
    "menu_types": ("🗂️ <b>Типы аукционов</b>\nВыберите тип:"),
}


def guides_kb(page: str, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # 1) Кнопки страницы
    if page == "menu_root":
        kb.button(text="💳 Оплата", callback_data="auk_guide_menu:payment")
        kb.button(text="📝 Оформление заявки", callback_data="auk_guide:apply")
        kb.button(text="🆔 Крафт по UID", callback_data="auk_guide:uid_craft")
        kb.button(text="🤖 Автоставки", callback_data="auk_guide:autobid")
        kb.button(text="🕷️ Веном: правила ставок", callback_data="auk_guide:venom_rules")
        kb.button(text="👑 Лакшери: плюшки", callback_data="auk_guide:luxury_perks")

        # ✅ НОВОЕ
        kb.button(text="🛡️ Жалоба на мошенника", callback_data="auk_guide:report_scam")

        kb.button(text="🗂️ Типы аукционов", callback_data="auk_guide_menu:types")
        kb.adjust(1)

    elif page == "menu_payment":
        kb.button(text="🪙 Оплата сокровищами", callback_data="auk_guide:treasures")
        kb.button(text="🍵 Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="💎 Оплата алмазами", callback_data="auk_guide:diamonds")
        kb.button(text="⬅️ Назад", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "menu_types":
        kb.button(text="⭐️ Стандартный", callback_data="auk_guide:type_standard")
        kb.button(text="🛒 Биржа", callback_data="auk_guide:type_exchange")
        kb.button(text="🃏 Любая карта", callback_data="auk_guide:type_any_card")
        kb.button(text="⬅️ Назад", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "type_standard":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)

    elif page == "type_exchange":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)

    elif page == "type_any_card":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)
    elif page == "treasures":
        kb.button(text="➡️ Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)
    elif page == "luxury_perks":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)
    elif page == "cups":
        kb.button(text="➡️ Оплата алмазами", callback_data="auk_guide:diamonds")
        kb.button(text="⬅️ Оплата сокровищами", callback_data="auk_guide:treasures")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)

    elif page == "diamonds":
        kb.button(text="⬅️ Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)

    elif page == "apply":
        kb.button(text="📸 Примеры (скрины)", callback_data="auk_guide:apply_photos")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "report_scam":
        kb.button(text="📨 Тексты жалоб", callback_data="auk_guide:report_scam_texts")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "report_scam_texts":
        kb.button(text="⬅️ Назад к гайду", callback_data="auk_guide:report_scam")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "uid_craft":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "autobid":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    # 2) Назад к выбору аукциона (всегда)
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))

    # 3) Общая кнопка "Спасибо" (всегда внизу)
    kb.row(
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"auk_guides_thanks:{page}",
        )
    )

    return kb.as_markup()


# =======================
# 📚 GUIDES (send content)
# =======================
async def _send_guide_luxury_perks(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_LUXURY_PERKS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("luxury_perks", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_standard(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_STANDARD_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_standard", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_exchange(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_EXCHANGE_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_exchange", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_any_card(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_ANY_CARD_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_any_card", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_autobid(message: types.Message) -> None:
    try:
        await message.answer_photo(
            (GUIDE_AUTOBID_PHOTO_ID or "").strip(),
            caption="🤖 <b>Гайд</b>: автоставки",
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        # если фото сломалось — не валим апдейт, просто пропускаем картинку
        pass

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_AUTOBID_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("autobid", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_treasures(message: types.Message) -> None:
    await message.answer_photo(
        GUIDE_TREASURES_PHOTO_ID,
        caption="🪙 <b>Гайд</b>: оплата сокровищами",
        parse_mode="HTML",
    )

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TREASURES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("treasures", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_cups(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_CUPS_PHOTOS):
        if i == 0:
            media.append(
                types.InputMediaPhoto(
                    media=fid,
                    caption="🍵 <b>Гайд</b>: оплата чашками",
                    parse_mode="HTML",
                )
            )
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_CUPS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("cups", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_diamonds(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_DIAMONDS_PHOTOS):
        if i == 0:
            media.append(
                types.InputMediaPhoto(
                    media=fid,
                    caption="💎 <b>Гайд</b>: оплата алмазами",
                    parse_mode="HTML",
                )
            )
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_DIAMONDS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("diamonds", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_venom_rules(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_VENOM_RULES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("venom_rules", total, users),
        disable_web_page_preview=True,
    )


# Removed unreachable duplicate handler: _send_guide_type_standard.
async def _send_guide_apply(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_APPLY_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("apply", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_report_scam(message: types.Message) -> None:
    if GUIDE_REPORT_SCAM_PHOTOS:
        media: list[types.InputMediaPhoto] = []
        for i, fid in enumerate(GUIDE_REPORT_SCAM_PHOTOS):
            if i == 0:
                media.append(
                    types.InputMediaPhoto(
                        media=fid,
                        caption="🛡️ <b>Гайд</b>: жалоба на мошенника",
                        parse_mode="HTML",
                    )
                )
            else:
                media.append(types.InputMediaPhoto(media=fid))
        await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_REPORT_SCAM_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("report_scam", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_report_scam_texts(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_REPORT_SCAM_TEMPLATES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("report_scam_texts", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_apply_photos(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_APPLY_PHOTOS):
        if i == 0:
            media.append(
                types.InputMediaPhoto(
                    media=fid,
                    caption="📝 <b>Оформление заявки</b>: примеры (скрины)",
                    parse_mode="HTML",
                )
            )
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    # Клавиатуру к альбому прикрепить нельзя, поэтому кидаем отдельным сообщением.
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        "⬆️ Примеры скринов отправлены.",
        parse_mode="HTML",
        reply_markup=guides_kb("apply", total, users),
    )


async def _send_guide_uid_craft(message: types.Message) -> None:
    await message.answer_photo(
        GUIDE_UID_CRAFT_PHOTO_ID,
        caption="🆔 <b>Гайд</b>: крафт по UID",
        parse_mode="HTML",
    )

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_UID_CRAFT_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("uid_craft", total, users),
        disable_web_page_preview=True,
    )


# =======================
# 📚 GUIDES (handlers)
# =======================


async def _send_guides_menu(message: types.Message, page: str) -> None:
    total, users = await _get_guides_thanks_totals()
    text = GUIDES_MENU_TEXT.get(page, "📚 <b>Гайды</b>")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=guides_kb(page, total, users),
        disable_web_page_preview=True,
    )


# =======================
# 💬 DAVID ANSWERS (content)
# =======================

DAVID_ANSWERS: dict[str, dict[str, str]] = {
    "заявка": {
        "title": "Заявка не принята",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я отправил заявку в бот на аукцион, а её до сих пор не приняли, что делать?</b>\n\n"
            "😌 Не волнуйтесь: администрация КД видит вашу заявку, поэтому наберитесь терпения и немного подождите.\n"
            "⏳ Посты обрабатываются в норме в течение <b>24–48 часов</b>.\n\n"
            "📅 Если времени прошло больше, значит на ближайшие даты нет свободных мест.\n"
            "✅ Заявка будет принята, но чуть позже.\n\n"
            "⚙️ Помните: всё зависит от загруженности бота и количества поступивших заявок.\n\n"
            "🔑 <b>Код:</b> <code>заявка</code>"
        ),
    },
    "конец": {
        "title": "Когда пришлют итоги",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я выиграл на аукционе / мой аукцион прошёл. Когда мне отправят данные другого человека для сделки?</b>\n\n"
            "📩 Итоги отправляются через бота или вам в ЛС одним из админов в течение <b>24 часов</b> "
            "с момента завершения аукциона.\n\n"
            "🧠 Пожалуйста, подождите: чисто физически мы не можем сидеть весь рабочий день "
            "и скидывать итоги через 5 минут после завершения.\n\n"
            "⚙️ Всё зависит от нагрузки бота, количества аукционов и других нюансов.\n\n"
            "🆘 Если вам не отправили итоги в течение суток, пожалуйста, напишите Давиду, указав свой аукцион.\n\n"
            "🔑 <b>Код:</b> <code>конец</code>"
        ),
    },
    "отклон": {
        "title": "Почему отклонили заявку",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Мою заявку отклонили в боте на аукцион, почему?</b>\n\n"
            "✍🏻 Заявки отклоняют чаще всего в трёх случаях:\n"
            "1) <b>Неправильное доказательство</b> — вы отправили не тот скриншот (другое фото/изображение, не связанное с картой).\n"
            "2) <b>На скрине нет подарочной карты</b> — на доказательстве должна быть видна сама подарочная карточка.\n"
            "3) <b>Не совпадает выбор в боте и доказательство</b> — выбрали одно, а на скрине другое.\n\n"
            "📌 <b>Дополнение:</b>\n"
            "Комментарий в стиле «2 карты в одном лоте» тоже может быть причиной отказа, потому что обычным участникам "
            "разрешено выставлять только <b>1 карту</b> за раз.\n\n"
            "📷 <b>Важно:</b> доказательство отправляем <b>одним скрином</b> (в одном экземпляре), без «пачки фоток».\n\n"
            "👑 <b>Лакшери/VIP:</b> правило про 1 карту и строгость доказательства касается только участников без лакшери. "
            "С VIP статусом подтверждение не требуется.\n\n"
            "🔑 <b>Код:</b> <code>отклон</code>"
        ),
    },
    "другие": {
        "title": "Как подать на другие аукционы",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я хочу подать заявку на другие аукционы, как это сделать?</b>\n\n"
            "ℹ️ Если вы <b>без специальной подписки</b>, то подавать заявки на дополнительные виды аукционов у вас "
            "возможности нет.\n\n"
            "✅ Всем доступны:\n"
            "• <b>Стандартный аукцион</b>\n"
            "• <b>Биржа</b>\n\n"
            "🌑 Дополнительные аукционы (только для лакшери):\n"
            "• <b>Чёрный</b>\n"
            "• <b>Обратный</b>\n"
            "• <b>Быстрый</b>\n"
            "• <b>Свободный</b>\n\n"
            "👑 Подавать заявки туда могут только <b>Лакшери</b>.\n"
            "💳 Подписка на месяц: <b>199/299₽</b> (цена зависит от того, покупали ли вы подписку раньше).\n"
            "🔁 Возможна оплата и другой валютой.\n\n"
            "📩 Подробнее в ЛС: <b>@velassya</b>\n\n"
            "🔑 <b>Код:</b> <code>другие</code>"
        ),
    },
    "мошенники": {
        "title": "Просят отдать заранее",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>У меня прошёл аукцион, и победитель просит карту/другие ресурсы вперёд. Могу ли я отдать их?</b>\n\n"
            "⚠️ Администрация КД просит вас заранее оценивать, <b>с кем вы ведёте сделку</b>. "
            "Мы <b>не несём ответственности</b> за вашу карту или ресурсы (крутки, слоты и т.д.) и за ваши личные решения.\n\n"
            "✅ Рекомендуемое правило:\n"
            "• <b>Не отдавайте</b> карту/ресурсы до оплаты, если только это не ваш знакомый или человек с хорошей репутацией.\n\n"
            "🚫 Никто не застрахован от <b>мошенников</b>. В случае обмана вернуть ресурсы чаще всего <b>невозможно</b>.\n\n"
            "📌 Запомните простую формулу: <b>сначала оплата, потом товар</b>.\n\n"
            "🔑 <b>Код:</b> <code>мошенники</code>"
        ),
    },
    "отмена": {
        "title": "Отмена лота",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я хочу убрать карту с аукциона. В каких случаях мне могут отказать?</b>\n\n"
            "✅ Если вы отправили запрос на отмену лота по своим личным причинам <b>за сутки и раньше</b>, то без проблем, мы уберём его.\n\n"
            "⛔️ Но если до выхода анонса осталось <b>меньше суток</b> или анонс-пост с вашей картой уже опубликован — отмена невозможна. Ваша заявка будет отклонена.\n"
            "В этом случае нужно либо участвовать в аукционе, как запланировали, либо получить бан в КД за отказ продавать карту.\n\n"
            "📌 Это правило касается также <b>бирж</b>, но не других аукционов (свободный, быстрый, обратный, чёрный), так как они идут не по расписанию.\n\n"
            "🧠 Пожалуйста, планируйте продажи заранее и трезво оценивайте свои желания и возможности.\n\n"
            "🔑 <b>Код:</b> <code>отмена</code>"
        ),
    },
}

DAVID_PAGE_SIZE = 5


def _david_codes() -> list[str]:
    # порядок можно расширять: новые коды добавляй в dict, список сам подхватит
    order = ["заявка", "конец", "отклон", "другие", "мошенники", "отмена"]
    rest = [c for c in DAVID_ANSWERS.keys() if c not in order]
    return order + sorted(rest)


def _david_pages_total() -> int:
    n = len(_david_codes())
    return max(1, (n + DAVID_PAGE_SIZE - 1) // DAVID_PAGE_SIZE)


def david_list_text(page: int) -> str:
    pages = _david_pages_total()
    return (
        "💬 <b>Ответы от Давида</b>\n"
        "Выберите вопрос кнопкой ниже.\n\n"
        "🧾 Быстрый вызов в чате:\n"
        "• <code>Макс ответ заявка</code>\n"
        "• <code>Макс ответ конец</code>\n"
        "• <code>Макс ответ отклон</code>\n"
        "• <code>Макс ответ другие</code>\n"
        "• <code>Макс ответ отмена</code>\n"
        "• <code>Макс ответ мошенники</code>\n\n"
        f"📖 Страница: <b>{page + 1}/{pages}</b>"
    )


def david_list_kb(page: int, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    codes = _david_codes()
    pages = _david_pages_total()
    page = max(0, min(page, pages - 1))

    start = page * DAVID_PAGE_SIZE
    chunk = codes[start : start + DAVID_PAGE_SIZE]

    for code in chunk:
        title = DAVID_ANSWERS[code]["title"]
        kb.button(text=f"💬 {title}", callback_data=f"auk_david:show:{code}")
    kb.adjust(1)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"auk_david:page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="auk_david:noop"))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"auk_david:page:{page + 1}"))
    if nav_row:
        kb.row(*nav_row)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))
    kb.row(
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"auk_david_thanks:list:{page}",
        )
    )
    return kb.as_markup()


def david_answer_kb(code: str, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К списку ответов", callback_data="auk_david:page:0")
    kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
    kb.adjust(1)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))
    kb.row(
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"auk_david_thanks:show:{code}",
        )
    )
    return kb.as_markup()


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guide_menu:david")
async def auk_guides_david_open(call: types.CallbackQuery) -> None:
    await call.answer()
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = david_list_text(0)
    await call.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=david_list_kb(0, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david:page:"))
async def auk_david_page(call: types.CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":")[-1])
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = david_list_text(page)

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=david_list_kb(page, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=david_list_kb(page, total, users),
            disable_web_page_preview=True,
        )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david:show:"))
async def auk_david_show(call: types.CallbackQuery) -> None:
    await call.answer()
    code = call.data.split(":")[-1].strip().lower()

    item = DAVID_ANSWERS.get(code)
    if not item:
        await call.answer("Неизвестный код 🤔", show_alert=True)
        return

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = item["text"] + DAVID_SIGN

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=david_answer_kb(code, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=david_answer_kb(code, total, users),
            disable_web_page_preview=True,
        )


async def _ensure_admin_thanks_tables() -> None:
    """Compatibility wrapper for the former runtime-DDL helper."""

    service = await GuideThanksService.create()
    await service.ensure_schema()


async def _inc_admin_thanks(author: str, user_id: int) -> None:
    """+1 спасибо автору, и +1 уникальному юзеру (если первый раз)."""

    service = await GuideThanksService.create()
    await service.increment_admin(author=author, user_id=int(user_id))


ADMIN_THANKS_PAGE_SIZE = 10


async def _get_admin_thanks_page(page: int) -> tuple[list[tuple[str, int, int]], int]:
    """Возвращает [(author, thanks_total, users_total)], total_pages — уже БЕЗ дублей."""

    service = await GuideThanksService.create()
    return await service.admin_page(page, page_size=ADMIN_THANKS_PAGE_SIZE)


DAVID_CALL_RE = re.compile(r"(?i)^\s*(?:макс|max)\s+ответ\s+(?P<code>[\wа-яё]+)\s*$")


@router.message(F.text.regexp(r"(?i)^\s*(?:макс|max)\s+ответ\s+[\wа-яё]+\s*$"))
async def msg_david_answer_call(message: types.Message) -> None:
    m = DAVID_CALL_RE.match(message.text or "")
    if not m:
        return

    code = (m.group("code") or "").strip().lower()

    if code == "аукцион":
        await message.reply(
            GUIDE_APPLY_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    item = DAVID_ANSWERS.get(code)
    if not item:
        return

    await message.reply(
        item["text"] + DAVID_SIGN,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_david:noop")
async def auk_david_noop(call: types.CallbackQuery) -> None:
    await call.answer()


@router.callback_query(
    UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david_thanks:")
)
async def auk_david_thanks(call: types.CallbackQuery) -> None:
    # auk_david_thanks:list:<page>  или  auk_david_thanks:show:<code>
    parts = call.data.split(":")
    mode = parts[1]
    tail = parts[2] if len(parts) > 2 else ""

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)

    try:
        if mode == "list":
            page = int(tail or "0")
            await call.message.edit_reply_markup(reply_markup=david_list_kb(page, total, users))
        elif mode == "show":
            code = (tail or "").strip().lower()
            await call.message.edit_reply_markup(reply_markup=david_answer_kb(code, total, users))
    except Exception:
        pass

    await call.answer("🙏 +1")


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guides")
async def auk_guides_open(call: types.CallbackQuery) -> None:
    await call.answer()
    await _send_guides_menu(call.message, "menu_root")


@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guide_menu:"))
async def auk_guides_menu(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    dest = call.data.split(":", 1)[1].strip()

    if dest == "root":
        page = "menu_root"
    elif dest == "payment":
        page = "menu_payment"
    elif dest == "types":
        page = "menu_types"
    else:
        page = "menu_root"

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = GUIDES_MENU_TEXT.get(page, "📚 <b>Гайды</b>")

    # ✅ Ключевое: из выбора колоды НЕ редактируем сообщение (чтобы не исчез список колод)
    current_state = await state.get_state()
    if current_state == UserAddLotFSM.waiting_for_deck.state:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )
        return

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )


# Removed unreachable duplicate handler: _send_guide_type_exchange.


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_auk_types")
async def cb_user_auk_types_from_decks(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _send_guides_menu(call.message, "menu_types")


@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guide:"))
async def auk_guide_open(call: types.CallbackQuery) -> None:
    await call.answer()
    page = call.data.split(":", 1)[1].strip()
    if page == "type_standard":
        await _send_guide_type_standard(call.message)
        return
    if page == "luxury_perks":
        service = await GuideThanksService.create()
        if not await service.is_luxury_user(call.from_user.id):
            await call.answer(
                "👑 Доступно только для Лакшери.\n\n"
                "Если вы уже купили Лакшери — обновите статус:\n"
                "/luxury_check",
                show_alert=True,
            )
            return

        await _send_guide_luxury_perks(call.message)
        return

    if page == "type_standard":
        await _send_guide_type_standard(call.message)
        return

    if page == "type_exchange":
        await _send_guide_type_exchange(call.message)
        return

    if page == "type_any_card":
        await _send_guide_type_any_card(call.message)
        return
    if page == "treasures":
        await _send_guide_treasures(call.message)
        return
    if page == "cups":
        await _send_guide_cups(call.message)
        return
    if page == "diamonds":
        await _send_guide_diamonds(call.message)
        return

    if page == "autobid":
        await _send_guide_autobid(call.message)
        return
    if page == "type_exchange":
        await _send_guide_type_exchange(call.message)
        return
    if page == "apply":
        await _send_guide_apply(call.message)
        return

    if page == "uid_craft":
        await _send_guide_uid_craft(call.message)
        return

    if page == "apply_photos":
        await _send_guide_apply_photos(call.message)
        return
    if page == "report_scam":
        await _send_guide_report_scam(call.message)
        return
    if page == "venom_rules":
        await _send_guide_venom_rules(call.message)
        return
    if page == "report_scam_texts":
        await _send_guide_report_scam_texts(call.message)
        return
    await call.answer("Неизвестный гайд 🤔", show_alert=True)


@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guides_thanks:"))
async def auk_guides_thanks(call: types.CallbackQuery) -> None:
    page = call.data.split(":", 1)[1].strip()
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)

    try:
        await call.message.edit_reply_markup(reply_markup=guides_kb(page, total, users))
    except Exception:
        pass

    await call.answer("🙏 +1")


@router.callback_query(StateFilter(UserAddLotFSM), F.data == "auk_guides_back")
async def auk_guides_back(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)

    await state.update_data(auction_kind=None)
    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)

    await call.message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )
