"""CLIK order workflow.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

import html
from aiogram.filters import Command
from aiogram import (
    F,
    Router,
    types,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.state import (
    State,
    StatesGroup,
)
from aiogram.exceptions import TelegramBadRequest
from bot.services.admin_logging import send_admin_log


from bot.telegram.callback_parser import split_callback_data

CLIK_ROOT_TEXT = (
    "🐸 <b>Жабий помощник</b>\n\n"
    "Привет! Я бот — твой помощник здесь.\n"
    "Смотри прайс-лист, изучай памятки и смело оформляй заказ.\n"
    "Если что-то не ясно — задай вопрос, я помогу."
)

CLIK_PRICE_TEXT = (
    "💸 <b>Прайс-лист</b>\n"
    "#price_list\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)

CLIK_INSTRUCTION_TEXT = (
    "📌 <b>Памятки / Советы</b>\n"
    "#instruction\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)

CLIK_ASK_TEXT = (
    "❓ <b>Задать вопрос</b>\n"
    "#ask_me\n\n"
    "Напиши вопрос одним сообщением.\n"
    "Я отправлю его админам."
)

CLIK_ORDER_PICK_PAY_TEXT = (
    "🛒 <b>Оформить заказ</b>\n"
    "#make_an_order\n\n"
    "Выбери вариант оплаты:"
)

CLIK_CB = "clik"

CLIK_STORY_COVER_PVT = "AgACAgQAAxkBAAELwn9ppGTmTAABJV5-fL_avtoXg1oHo-IAAioOaxtHzCBRAut-JCPq_mIBAAMCAANtAAM6BA"

CLIK_PVT_LI = [
    ("seb", "Себастьян", "AgACAgQAAxkBAAELwoFppGUelLt-CdGhoOkrqOTM490lGAACFQ5rG-beIFFkrLymWF1WEQEAAwIAA20AAzoE"),
    ("wil", "Вильям", "AgACAgQAAxkBAAELwo1ppGVI6pJ2q5wuNzNXhAka1GakSAACKw5rG0fMIFHW7G34DQqgUAEAAwIAA20AAzoE"),
    ("kri", "Кристина", "AgACAgQAAxkBAAELwo9ppGV02pUgY_2Sy9-ZTO0d4Eo2SAACLA5rG0fMIFFwCeb8kSdCQwEAAwIAA20AAzoE"),
    ("jac", "Джеки", "AgACAgQAAxkBAAELwpZppGWD_2ppkKYejBEyWzx9R-oUFAACLQ5rG0fMIFGP0i2Y6VUTcgEAAwIAA20AAzoE"),
    ("jor", "Хорхе", "AgACAgQAAxkBAAELwrRppGYH8dpcZunjNaNLtz3oH2CyuQACMA5rG0fMIFGcwl05-liqJQEAAwIAA20AAzoE"),
    ("cli", "Клайв", "AgACAgQAAxkBAAELwrhppGYVzWcSl7SYIN-xEDlGsvUeMwACMQ5rG0fMIFGo_o6Kjxw0vgEAAwIAA20AAzoE"),
    ("die", "Диего", "AgACAgQAAxkBAAELwr9ppGYt2gue1lPtrVO_FoQ1PJ-VuQACMg5rG0fMIFE0SHwnZRXkSwEAAwIAA20AAzoE"),
    ("kai", "Кай", "AgACAgQAAxkBAAELwqRppGXQJF3BXJ7b7GFjg9gJU1HwtAACLw5rG0fMIFFzVcOPF9D3BgEAAwIAA20AAzoE"),
    ("lor", "Лоренза", "AgACAgQAAxkBAAELwqJppGWrDdb6ipQYfmnIvChEkF0-1AACLg5rG0fMIFGoyJNIma67CQEAAwIAA20AAzoE"),
]

CLIK_PVT_LI_MAP = {k: {"name": n, "photo": p} for k, n, p in CLIK_PVT_LI}

CLIK_STORIES = [
    "Паруса в Тумане",
    "Рожденная Луной",
    "Моя Голивудская История",
    "Королева за 30 дней",
    "Тени Сентфора",
    "Высокий Прибой",
    "В Ритме Страсти",
    "Я Охочусь на Тебя",
    "Секрет Небес",
    "Легенда Ивы",
    "Дракула. История любви",
    "Путь валькирии",
    "Ярость Титанов",
    "Десять Желаний Софи",
    "Грешный Лондон",
    "По Тонкому Льду",
    "Арканум",
    "Хроники Гладиаторов",
    "Сердце Треспии",
    "Кали: Зов Тьмы",
    "Цветок из Огня Тиамат",
    "Теодора",
    "Сквозь Бурю и Пламя",
    "Идеал",
    "Пси",
    "Покоряя Версаль",
    "Роза Пустыни",
    "Секрет Небес 2",
    "W: Ловчая Времени",
    "Эдемов Сад",
    "Идеал. Том 2",
    "Разбитое Сердце Астреи",
    "Секрет Небес Реквием",
    "Семь Братьев",
    "И Поглотит Нас Морок",
    "Бюро Паралельных Миров. Том 1",
    "Te amo: Том 1. Залив Надежды",
]

CLIK_STORIES_PER_PAGE = 10

class ClikFSM(StatesGroup):
    waiting_question = State()

    order_story = State()
    order_tasks = State()
    order_ach_mode = State()
    order_love_mode = State()          # для НЕ-ПВТ (1/2/3/все)
    order_love_select = State()        # для ПВТ (выбор персонажей)
    waiting_other_text = State()
    order_cups_source = State()

    waiting_order = State()            # логин/пароль

def _clik_mark(v: bool) -> str:
    return "✅" if v else "⬜️"

def _clik_story_key(title: str) -> str:
    return "pvt" if title.strip() == "Паруса в Тумане" else "other"

def _clik_pages(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)

def _kb_clik_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Прайс-лист", callback_data=f"{CLIK_CB}:price"),
            InlineKeyboardButton(text="📌 Памятки", callback_data=f"{CLIK_CB}:instruction"),
        ],
        [
            InlineKeyboardButton(text="🛒 Оформить заказ", callback_data=f"{CLIK_CB}:order"),
            InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"{CLIK_CB}:ask"),
        ],
    ])

def _kb_clik_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")]
    ])

def _kb_clik_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍵 Чашки", callback_data=f"{CLIK_CB}:pay:cups"),
            InlineKeyboardButton(text="💎 Алмазы", callback_data=f"{CLIK_CB}:pay:diamonds"),
            InlineKeyboardButton(text="₽ Рубли", callback_data=f"{CLIK_CB}:pay:rub"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")],
    ])

def _kb_clik_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")]
    ])

def _kb_clik_stories(page: int) -> InlineKeyboardMarkup:
    total = len(CLIK_STORIES)
    pages = _clik_pages(total, CLIK_STORIES_PER_PAGE)
    page = max(0, min(int(page), pages - 1))

    start = page * CLIK_STORIES_PER_PAGE
    chunk = CLIK_STORIES[start:start + CLIK_STORIES_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for i, title in enumerate(chunk, start=start):
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CLIK_CB}:s:pick:{i}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CLIK_CB}:s:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"{CLIK_CB}:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CLIK_CB}:s:page:{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="⬅️ Назад к оплате", callback_data=f"{CLIK_CB}:order")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _clik_task_flags(data: dict) -> dict:
    return {
        "play": bool(data.get("clik_t_play")),
        "ach": bool(data.get("clik_t_ach")),
        "love": bool(data.get("clik_t_love")),
        "wardrobe": bool(data.get("clik_t_wardrobe")),
        "other": bool(data.get("clik_t_other")),
    }

def _clik_order_intro(data: dict, step_text: str) -> str:
    pay = html.escape(str(data.get("clik_pay") or "—"))
    story = html.escape(str(data.get("clik_story") or "—"))
    return (
        "✅ <b>Сообщение для заказа</b>\n\n"
        f"💳 <b>Оплата:</b> {pay}\n"
        f"📚 <b>История:</b> {story}\n\n"
        f"{step_text}"
    )

def _clik_preview_summary(data: dict) -> str:
    flags = _clik_task_flags(data)

    # ачивки (заглушка)
    ach_mode = str(data.get("clik_ach_mode") or "").strip()  # all | story

    # любовные линии
    story_key = str(data.get("clik_story_key") or "other")
    love_mode = str(data.get("clik_love_mode") or "").strip()  # all | 1 | 2 | 3
    love_selected = data.get("clik_love_selected") or []

    other_text = (data.get("clik_other_text") or "").strip()

    lines = ["<b>Что нужно сделать:</b>"]
    any_task = False

    if flags["play"]:
        lines.append("• пройти историю;")
        any_task = True

    if flags["ach"]:
        any_task = True
        if ach_mode == "all":
            lines.append("• собрать ачивки (все) — <i>список позже</i>;")
        elif ach_mode == "story":
            lines.append("• собрать ачивки (по истории) — <i>список позже</i>;")
        else:
            lines.append("• собрать ачивки — <i>режим не выбран</i>;")

    if flags["wardrobe"]:
        lines.append("• собрать гардероб (зеркало);")
        any_task = True

    if flags["love"]:
        any_task = True
        if story_key == "pvt":
            if love_selected:
                names = [CLIK_PVT_LI_MAP.get(k, {}).get("name", k) for k in love_selected]
                lines.append("• любовные линии: " + ", ".join(html.escape(n) for n in names) + ";")
            else:
                lines.append("• любовные линии — <i>не выбраны</i>;")
        else:
            if love_mode == "all":
                lines.append("• достичь 100% по любовным линиям (все);")
            elif love_mode in {"1", "2", "3"}:
                lines.append(f"• достичь 100% по любовным линиям ({love_mode});")
            else:
                lines.append("• достичь 100% по любовным линиям — <i>кол-во не выбрано</i>;")

    if flags["other"]:
        any_task = True
        if other_text:
            lines.append(f"• другое: <code>{html.escape(other_text)}</code>;")
        else:
            lines.append("• другое: <i>описание не задано</i>;")

    if not any_task:
        lines.append("• —")

    # чашки (если выбран pay=cups)
    if str(data.get("clik_pay_key") or "") == "cups":
        src = str(data.get("clik_cups_source") or "").strip()
        src_txt = "—"
        if src == "account":
            src_txt = "есть чашки на аккаунте"
        elif src == "daily":
            src_txt = "проходим на ежедневных"
        lines.append("")
        lines.append(f"🍵 <b>Чашки:</b> {html.escape(src_txt)}")

    return "\n".join(lines)

def _kb_clik_tasks(data: dict) -> InlineKeyboardMarkup:
    flags = _clik_task_flags(data)

    ach_mode = str(data.get("clik_ach_mode") or "").strip()
    ach_suffix = ""
    if flags["ach"]:
        ach_suffix = " (все)" if ach_mode == "all" else (" (по истории)" if ach_mode == "story" else "")

    story_key = str(data.get("clik_story_key") or "other")
    love_suffix = ""
    if flags["love"]:
        if story_key == "pvt":
            selected = data.get("clik_love_selected") or []
            love_suffix = f" ({len(selected)})" if selected else ""
        else:
            lm = str(data.get("clik_love_mode") or "").strip()
            love_suffix = " (все)" if lm == "all" else (f" ({lm})" if lm in {"1", "2", "3"} else "")

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📖 Пройти историю {_clik_mark(flags['play'])}", callback_data=f"{CLIK_CB}:t:toggle:play")],
        [InlineKeyboardButton(text=f"🏆 Ачивки{ach_suffix} {_clik_mark(flags['ach'])}", callback_data=f"{CLIK_CB}:t:ach")],
        [InlineKeyboardButton(text=f"💞 Любовные линии{love_suffix} {_clik_mark(flags['love'])}", callback_data=f"{CLIK_CB}:t:love")],
        [InlineKeyboardButton(text=f"🪞 Гардероб (Зеркало) {_clik_mark(flags['wardrobe'])}", callback_data=f"{CLIK_CB}:t:toggle:wardrobe")],
        [InlineKeyboardButton(text=f"✍️ Другое {_clik_mark(flags['other'])}", callback_data=f"{CLIK_CB}:t:other")],
        [InlineKeyboardButton(text="➡️ Далее", callback_data=f"{CLIK_CB}:t:next")],
        [InlineKeyboardButton(text="⬅️ Назад к историям", callback_data=f"{CLIK_CB}:t:back_stories")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_clik_ach_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все ачивки", callback_data=f"{CLIK_CB}:ach:set:all")],
        [InlineKeyboardButton(text="✅ Только по выбранной истории", callback_data=f"{CLIK_CB}:ach:set:story")],
        [InlineKeyboardButton(text="❌ Выключить ачивки", callback_data=f"{CLIK_CB}:ach:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:ach:back")],
    ])

def _kb_clik_love_mode_generic() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все", callback_data=f"{CLIK_CB}:love:set:all")],
        [
            InlineKeyboardButton(text="1", callback_data=f"{CLIK_CB}:love:set:1"),
            InlineKeyboardButton(text="2", callback_data=f"{CLIK_CB}:love:set:2"),
            InlineKeyboardButton(text="3", callback_data=f"{CLIK_CB}:love:set:3"),
        ],
        [InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:love:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:love:back")],
    ])

def _kb_clik_love_pvt(data: dict) -> InlineKeyboardMarkup:
    selected = set(data.get("clik_love_selected") or [])
    rows: list[list[InlineKeyboardButton]] = []

    # кнопки персонажей (в 2 колонки)
    btns: list[InlineKeyboardButton] = []
    for k, n, _p in CLIK_PVT_LI:
        mark = "✅" if k in selected else "⬜️"
        btns.append(InlineKeyboardButton(text=f"{mark} {n}", callback_data=f"{CLIK_CB}:lpvt:toggle:{k}"))

    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"{CLIK_CB}:lpvt:done")])
    rows.append([InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:lpvt:off")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:lpvt:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_clik_cups_source() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍵 Есть чашки на аккаунте", callback_data=f"{CLIK_CB}:cups:account")],
        [InlineKeyboardButton(text="📅 Проходим на ежедневных", callback_data=f"{CLIK_CB}:cups:daily")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:cups:back")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])

def _kb_clik_final_back(data: dict) -> InlineKeyboardMarkup:
    if str(data.get("clik_pay_key") or "") == "cups":
        back_cb = f"{CLIK_CB}:final:back_cups"
    else:
        back_cb = f"{CLIK_CB}:final:back_tasks"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])

async def _ui_edit(call: types.CallbackQuery, *, text: str, kb: InlineKeyboardMarkup, photo_id: str | None = None):
    """
    Универсально обновляет UI.
    - Если photo_id указан: стараемся показать фото + caption (с заменой текст->фото при необходимости).
    - Если photo_id не указан: показываем текст (с заменой фото->текст при необходимости).
    """
    msg = call.message
    if not msg:
        return

    chat_id = msg.chat.id
    try:
        if photo_id:
            if msg.photo:
                media = types.InputMediaPhoto(media=photo_id, caption=text, parse_mode="HTML")
                await msg.edit_media(media=media, reply_markup=kb)
            else:
                new_msg = await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
        else:
            if msg.photo:
                new_msg = await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
            else:
                await msg.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest:
        # Фоллбек: просто отправляем новое
        if photo_id:
            await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

def _user_tag(u: types.User) -> str:
    uname = (u.username or "").strip()
    if uname:
        esc = html.escape(uname.lstrip("@"))
        return f'<a href="https://t.me/{esc}">@{esc}</a>'
    return f'<a href="tg://user?id={u.id}">id{u.id}</a>'

router = Router(name=__name__)


@router.message(Command("clik"), F.chat.type == "private")
async def clik_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(CLIK_ROOT_TEXT, reply_markup=_kb_clik_root(), parse_mode="HTML")


@router.callback_query(F.data == f"{CLIK_CB}:noop")
async def clik_noop(call: types.CallbackQuery):
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:root")
async def clik_root(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _ui_edit(call, text=CLIK_ROOT_TEXT, kb=_kb_clik_root(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:price")
async def clik_price(call: types.CallbackQuery):
    await _ui_edit(call, text=CLIK_PRICE_TEXT, kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:instruction")
async def clik_instruction(call: types.CallbackQuery):
    await _ui_edit(call, text=CLIK_INSTRUCTION_TEXT, kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.callback_query(F.data == f"{CLIK_CB}:ask")
async def clik_ask(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ClikFSM.waiting_question)
    await _ui_edit(call, text=CLIK_ASK_TEXT + "\n\n<b>Отмена:</b> напиши «Отмена».", kb=_kb_clik_back(), photo_id=None)
    await call.answer()


@router.message(ClikFSM.waiting_question, F.chat.type == "private")
async def clik_got_question(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    user = message.from_user
    body = html.escape(txt)
    log_text = (
        "❓ <b>Вопрос от пользователя</b>\n"
        f"👤 {(_user_tag(user) if user else '—')} (id: <code>{user.id if user else 0}</code>)\n\n"
        f"{body}"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer("✅ Вопрос отправлен админам.", reply_markup=_kb_clik_root(), parse_mode="HTML")


@router.callback_query(F.data == f"{CLIK_CB}:order")
async def clik_order(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _ui_edit(call, text=CLIK_ORDER_PICK_PAY_TEXT, kb=_kb_clik_pay(), photo_id=None)
    await call.answer()


@router.callback_query(F.data.startswith(f"{CLIK_CB}:pay:"))
async def clik_pay(call: types.CallbackQuery, state: FSMContext):
    parts = split_callback_data(call.data or "", ":")
    if len(parts) < 3:
        await call.answer("Кривая кнопка.", show_alert=True)
        return

    pay_key = parts[2].strip()
    pay_label = {"cups": "Чашки", "diamonds": "Алмазы", "rub": "Рубли (₽)"}.get(pay_key, pay_key)

    await state.clear()
    await state.update_data(
        clik_pay=pay_label,
        clik_pay_key=pay_key,
        clik_story=None,
        clik_story_key=None,
        clik_story_page=0,

        clik_t_play=False,
        clik_t_ach=False,
        clik_ach_mode=None,
        clik_t_love=False,
        clik_love_mode=None,
        clik_love_selected=[],

        clik_t_wardrobe=False,
        clik_t_other=False,
        clik_other_text=None,

        clik_cups_source=None,
    )
    await state.set_state(ClikFSM.order_story)

    data = await state.get_data()
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(0), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_story, F.data.startswith(f"{CLIK_CB}:s:page:"))
async def clik_story_page(call: types.CallbackQuery, state: FSMContext):
    try:
        page = int(split_callback_data(call.data or "", ":")[-1])
    except Exception:
        page = 0

    await state.update_data(clik_story_page=page)
    data = await state.get_data()
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(page), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_story, F.data.startswith(f"{CLIK_CB}:s:pick:"))
async def clik_story_pick(call: types.CallbackQuery, state: FSMContext):
    try:
        idx = int(split_callback_data(call.data or "", ":")[-1])
    except Exception:
        await call.answer("Не понял историю.", show_alert=True)
        return

    if idx < 0 or idx >= len(CLIK_STORIES):
        await call.answer("История вне списка.", show_alert=True)
        return

    story = CLIK_STORIES[idx]
    story_key = _clik_story_key(story)

    await state.update_data(clik_story=story, clik_story_key=story_key)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    # ПВТ: показываем обложку истории
    if story_key == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:back_stories")
async def clik_back_to_stories(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("clik_story_page") or 0)

    await state.set_state(ClikFSM.order_story)
    text = _clik_order_intro(data, "1) <b>Выбери историю кнопкой ниже:</b>")
    await _ui_edit(call, text=text, kb=_kb_clik_stories(page), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data.startswith(f"{CLIK_CB}:t:toggle:"))
async def clik_task_toggle(call: types.CallbackQuery, state: FSMContext):
    key = split_callback_data(call.data or "", ":")[-1].strip()

    data = await state.get_data()
    if key == "play":
        await state.update_data(clik_t_play=not bool(data.get("clik_t_play")))
    elif key == "wardrobe":
        await state.update_data(clik_t_wardrobe=not bool(data.get("clik_t_wardrobe")))
    else:
        await call.answer()
        return

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:ach")
async def clik_ach_open(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_ach=True)
    await state.set_state(ClikFSM.order_ach_mode)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Ачивки</b>\nВыбери режим (пока без списка достижений, это заглушка):"
    )
    # не обязательно менять фото, оставим что было
    await _ui_edit(call, text=text, kb=_kb_clik_ach_mode(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data.startswith(f"{CLIK_CB}:ach:set:"))
async def clik_ach_set(call: types.CallbackQuery, state: FSMContext):
    mode = split_callback_data(call.data or "", ":")[-1].strip()  # all | story
    if mode not in {"all", "story"}:
        await call.answer("Кривой режим.", show_alert=True)
        return

    await state.update_data(clik_t_ach=True, clik_ach_mode=mode)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data == f"{CLIK_CB}:ach:off")
async def clik_ach_off(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_ach=False, clik_ach_mode=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_ach_mode, F.data == f"{CLIK_CB}:ach:back")
async def clik_ach_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:love")
async def clik_love_open(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    story_key = str(data.get("clik_story_key") or "other")

    await state.update_data(clik_t_love=True)

    if story_key == "pvt":
        # ПВТ: выбор персонажей с картинками
        await state.set_state(ClikFSM.order_love_select)

        # дефолт: показываем первого (Себастьян)
        default_key = "seb"
        await state.update_data(clik_love_last=default_key)

        data = await state.get_data()
        text = _clik_order_intro(
            data,
            "2) <b>Любовные линии (ПВТ)</b>\n"
            "Нажимай на персонажей ниже. Сверху будет меняться картинка выбранной линии.\n"
            "Можно выбрать несколько."
        )
        await _ui_edit(call, text=text, kb=_kb_clik_love_pvt(data), photo_id=CLIK_PVT_LI_MAP[default_key]["photo"])
        await call.answer()
        return

    # НЕ-ПВТ: пока заглушка по количеству
    await state.set_state(ClikFSM.order_love_mode)
    text = _clik_order_intro(
        data,
        "2) <b>Любовные линии</b>\nВыбери сколько линий нужно закрыть (пока без имён героев):"
    )
    await _ui_edit(call, text=text, kb=_kb_clik_love_mode_generic(), photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data.startswith(f"{CLIK_CB}:love:set:"))
async def clik_love_set_generic(call: types.CallbackQuery, state: FSMContext):
    mode = split_callback_data(call.data or "", ":")[-1].strip()  # all | 1 | 2 | 3
    if mode not in {"all", "1", "2", "3"}:
        await call.answer("Кривой режим.", show_alert=True)
        return

    await state.update_data(clik_t_love=True, clik_love_mode=mode)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data == f"{CLIK_CB}:love:off")
async def clik_love_off_generic(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_love=False, clik_love_mode=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_mode, F.data == f"{CLIK_CB}:love:back")
async def clik_love_back_generic(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=None)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data.startswith(f"{CLIK_CB}:lpvt:toggle:"))
async def clik_love_pvt_toggle(call: types.CallbackQuery, state: FSMContext):
    key = split_callback_data(call.data or "", ":")[-1].strip()
    if key not in CLIK_PVT_LI_MAP:
        await call.answer()
        return

    data = await state.get_data()
    selected = set(data.get("clik_love_selected") or [])

    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)

    await state.update_data(clik_love_selected=list(selected), clik_love_last=key, clik_t_love=True)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Любовные линии (ПВТ)</b>\n"
        "Нажимай на персонажей ниже. Сверху будет меняться картинка выбранной линии.\n"
        "Можно выбрать несколько."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_love_pvt(data), photo_id=CLIK_PVT_LI_MAP[key]["photo"])
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:done")
async def clik_love_pvt_done(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:off")
async def clik_love_pvt_off(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_love=False, clik_love_selected=[], clik_love_last=None)
    await state.set_state(ClikFSM.order_tasks)

    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_love_select, F.data == f"{CLIK_CB}:lpvt:back")
async def clik_love_pvt_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)
    await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    await call.answer()


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:other")
async def clik_other_open(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(clik_t_other=True)
    await state.set_state(ClikFSM.waiting_other_text)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "2) <b>Другое</b>\n"
        "Напиши одним сообщением, что именно нужно.\n"
        "Если хочешь выключить «Другое» — пришли <code>-</code>.\n\n"
        "<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_cancel(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.message(ClikFSM.waiting_other_text, F.chat.type == "private")
async def clik_other_text(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    if txt == "-":
        await state.update_data(clik_t_other=False, clik_other_text=None)
    else:
        await state.update_data(clik_t_other=True, clik_other_text=txt)

    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await message.answer_photo(CLIK_STORY_COVER_PVT, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(ClikFSM.order_tasks, F.data == f"{CLIK_CB}:t:next")
async def clik_tasks_next(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pay_key = str(data.get("clik_pay_key") or "").strip()

    # если чашки — спрашиваем источник чашек
    if pay_key == "cups":
        await state.set_state(ClikFSM.order_cups_source)
        text = _clik_order_intro(
            data,
            "3) <b>Чашки</b>\nВыбери, есть ли чашки на аккаунте или проходим на ежедневных:"
        )
        await _ui_edit(call, text=text, kb=_kb_clik_cups_source(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
        await call.answer()
        return

    # иначе сразу просим логин/пароль
    await state.set_state(ClikFSM.waiting_order)
    data = await state.get_data()
    text = _clik_order_intro(
        data,
        _clik_preview_summary(data)
        + "\n\n4) <b>Пришли логин и пароль</b> одним сообщением.\n\n<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_final_back(data), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.order_cups_source, F.data == f"{CLIK_CB}:cups:back")
async def clik_cups_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.callback_query(ClikFSM.order_cups_source, F.data.startswith(f"{CLIK_CB}:cups:"))
async def clik_cups_set(call: types.CallbackQuery, state: FSMContext):
    tail = split_callback_data(call.data or "", ":")[-1].strip()
    if tail not in {"account", "daily"}:
        await call.answer()
        return

    await state.update_data(clik_cups_source=tail)
    await state.set_state(ClikFSM.waiting_order)

    data = await state.get_data()
    text = _clik_order_intro(
        data,
        _clik_preview_summary(data)
        + "\n\n4) <b>Пришли логин и пароль</b> одним сообщением.\n\n<b>Отмена:</b> напиши «Отмена»."
    )
    await _ui_edit(call, text=text, kb=_kb_clik_final_back(data), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.waiting_order, F.data == f"{CLIK_CB}:final:back_cups")
async def clik_final_back_cups(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_cups_source)
    data = await state.get_data()
    text = _clik_order_intro(
        data,
        "3) <b>Чашки</b>\nВыбери, есть ли чашки на аккаунте или проходим на ежедневных:"
    )
    await _ui_edit(call, text=text, kb=_kb_clik_cups_source(), photo_id=(CLIK_STORY_COVER_PVT if str(data.get("clik_story_key")) == "pvt" else None))
    await call.answer()


@router.callback_query(ClikFSM.waiting_order, F.data == f"{CLIK_CB}:final:back_tasks")
async def clik_final_back_tasks(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClikFSM.order_tasks)
    data = await state.get_data()
    text = _clik_order_intro(data, _clik_preview_summary(data))
    kb = _kb_clik_tasks(data)

    if str(data.get("clik_story_key") or "") == "pvt":
        await _ui_edit(call, text=text, kb=kb, photo_id=CLIK_STORY_COVER_PVT)
    else:
        await _ui_edit(call, text=text, kb=kb, photo_id=None)

    await call.answer()


@router.message(ClikFSM.waiting_order, F.chat.type == "private")
async def clik_got_order(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=_kb_clik_root(), parse_mode="HTML")
        return

    data = await state.get_data()
    pay_label = str(data.get("clik_pay") or "—")

    user = message.from_user
    creds = html.escape(txt)

    order_text = (
        "🛒 <b>Новый заказ</b>\n"
        f"💳 Оплата: <b>{html.escape(pay_label)}</b>\n"
        f"👤 {(_user_tag(user) if user else '—')} (id: <code>{user.id if user else 0}</code>)\n\n"
        f"{_clik_order_intro(data, _clik_preview_summary(data))}\n\n"
        "🔐 <b>Логин и пароль:</b>\n"
        f"<code>{creds}</code>"
    )
    await send_admin_log(message.bot, order_text)

    await state.clear()
    await message.answer("✅ Заказ отправлен админам. Ожидай ответа.", reply_markup=_kb_clik_root(), parse_mode="HTML")


__all__ = [
    "router",
    "clik_cmd",
    "clik_noop",
    "clik_root",
    "clik_price",
    "clik_instruction",
    "clik_ask",
    "clik_got_question",
    "clik_order",
    "clik_pay",
    "clik_story_page",
    "clik_story_pick",
    "clik_back_to_stories",
    "clik_task_toggle",
    "clik_ach_open",
    "clik_ach_set",
    "clik_ach_off",
    "clik_ach_back",
    "clik_love_open",
    "clik_love_set_generic",
    "clik_love_off_generic",
    "clik_love_back_generic",
    "clik_love_pvt_toggle",
    "clik_love_pvt_done",
    "clik_love_pvt_off",
    "clik_love_pvt_back",
    "clik_other_open",
    "clik_other_text",
    "clik_tasks_next",
    "clik_cups_back",
    "clik_cups_set",
    "clik_final_back_cups",
    "clik_final_back_tasks",
    "clik_got_order",
]
