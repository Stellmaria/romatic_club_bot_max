"""Canonical button-driven menu for private users."""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.time import to_moscow, utc_now
from bot.handlers.admin.helper.admin_keyboards import months_keyboard
from bot.handlers.auction.exchange.common import (
    exchange_deck_keyboard,
    get_exchange_deck_ids,
    get_exchange_decks_for_menu,
)
from bot.handlers.auction.schedule import (
    LUX_END,
    LUX_START,
    REG_END,
    REG_START,
    WORK_END,
    WORK_START,
    _format_blocks,
    _format_slots,
    _slot_iter_range,
)
from bot.handlers.constants import USER_MESSAGES
from bot.handlers.helper.appeals import appeal_start
from bot.handlers.helper.helpers_users import (
    check_luxury,
    format_today_lots_fancy,
    register_user,
)
from bot.handlers.users import my_lots_cmd
from bot.keyboards.keyboards import (
    USER_MENU_ADD_LOT,
    USER_MENU_EXCHANGE,
    USER_MENU_HELP,
    USER_MENU_HOME,
    USER_MENU_LUXURY,
    USER_MENU_MY_LOTS,
    USER_MENU_NOTIFICATIONS,
    USER_MENU_PROFILE,
    USER_MENU_SCHEDULE,
    USER_MENU_SUBSCRIPTIONS,
    USER_MENU_SUPPORT,
    build_user_main_keyboard,
)
from bot.legacy_fsm import ExchangeFSM, LuxScheduleFSM, UIDVerificationFSM
from bot.services.exchange_catalog import ExchangeCatalogService
from bot.services.handler_persistence import (
    get_auctions_by_date,
    get_settings,
    get_user_verified_uid,
    is_subscribed,
    log_admin_action,
    mark_user_private_chat_closed,
    mark_user_private_chat_opened,
    set_settings,
    set_subscription,
    sync_trusted_status,
)
from bot.telegram.boundary import escape_html
from bot.telegram.user_entrypoints import launch_add_lot, launch_card_subscription
from db.legacy import (
    get_auctions_by_card_ref,
    get_auctions_in_range,
    get_verified_uid_for_user,
    is_admin,
    is_luxury_user,
)


router = Router(name="user-menu")
logger = logging.getLogger(__name__)

_SCHEDULE_PAGE_SIZE = 8
_WEEKDAYS_RU = (
    "Пн",
    "Вт",
    "Ср",
    "Чт",
    "Пт",
    "Сб",
    "Вс",
)

_NOTIFY_DEFAULTS = {
    "notify_auction_start": True,
    "notify_bid_reminder": True,
    "notify_auction_end": True,
    "notify_daily_today": False,
}
_NOTIFY_MAP = {
    "notify_toggle_start": (
        "notify_auction_start",
        "notify_start_on",
        "notify_start_off",
    ),
    "notify_toggle_remind": (
        "notify_bid_reminder",
        "notify_remind_on",
        "notify_remind_off",
    ),
    "notify_toggle_end": (
        "notify_auction_end",
        "notify_end_on",
        "notify_end_off",
    ),
    "notify_toggle_today": (
        "notify_daily_today",
        "notify_today_on",
        "notify_today_off",
    ),
}


class UserMenuFSM(StatesGroup):
    waiting_for_card_search = State()


def _today_msk() -> date:
    return to_moscow(utc_now()).date()


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "да", "on"}
    return default


def user_main_text(
    *,
    full_name: str | None = None,
    status_line: str | None = None,
) -> str:
    parts = ["✨ <b>Клуб Романтики • Аукционы</b>"]
    if full_name:
        parts.append(f"Привет, <b>{html.escape(full_name)}</b>!")
    if status_line:
        parts.append(status_line.strip())
    parts.extend(
        [
            "",
            "Здесь всё работает через кнопки. Выберите нужный раздел ниже.",
            "Кнопка «🏠 Меню» в любой момент отменит текущий сценарий и вернёт на главный экран.",
        ]
    )
    return "\n".join(parts)


async def send_user_main_menu(
    message: Message,
    state: FSMContext,
    *,
    status_line: str | None = None,
) -> None:
    await state.clear()
    full_name = message.from_user.full_name if message.from_user else None
    await message.answer(
        user_main_text(full_name=full_name, status_line=status_line),
        parse_mode="HTML",
        reply_markup=build_user_main_keyboard(),
    )


def _home_inline_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 Главное меню", callback_data="user_menu|home")


def build_schedule_keyboard(*, page: int = 0) -> InlineKeyboardMarkup:
    page = max(0, int(page))
    today = _today_msk()
    start = today + timedelta(days=page * _SCHEDULE_PAGE_SIZE)

    builder = InlineKeyboardBuilder()
    buttons: list[InlineKeyboardButton] = []
    for offset in range(_SCHEDULE_PAGE_SIZE):
        selected = start + timedelta(days=offset)
        if selected == today:
            label = f"Сегодня • {selected:%d.%m}"
        elif selected == today + timedelta(days=1):
            label = f"Завтра • {selected:%d.%m}"
        else:
            label = f"{_WEEKDAYS_RU[selected.weekday()]} • {selected:%d.%m}"
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"user_day|{selected.isoformat()}",
            )
        )

    for index in range(0, len(buttons), 2):
        builder.row(*buttons[index:index + 2])

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ Раньше",
                callback_data=f"user_schedule|{page - 1}",
            )
        )
    navigation.append(
        InlineKeyboardButton(
            text="Позже ➡️",
            callback_data=f"user_schedule|{page + 1}",
        )
    )
    builder.row(*navigation)
    builder.row(_home_inline_button())
    return builder.as_markup()


async def _edit_or_answer(
    message: Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


async def show_schedule_menu(message: Message, *, page: int = 0) -> None:
    start = _today_msk() + timedelta(days=max(0, page) * _SCHEDULE_PAGE_SIZE)
    end = start + timedelta(days=_SCHEDULE_PAGE_SIZE - 1)
    await _edit_or_answer(
        message,
        text=(
            "📅 <b>Расписание аукционов</b>\n\n"
            f"Период: <b>{start:%d.%m.%Y}</b> – <b>{end:%d.%m.%Y}</b>\n"
            "Выберите день:"
        ),
        reply_markup=build_schedule_keyboard(page=page),
    )


async def show_day_schedule(message: Message, target_day: date) -> None:
    lots = await get_auctions_by_date(target_day)
    if not lots:
        await message.answer(
            f"📅 На <b>{target_day:%d.%m.%Y}</b> аукционов пока нет.",
            parse_mode="HTML",
            reply_markup=build_user_main_keyboard(),
        )
        return

    text = await format_today_lots_fancy(target_day, lots)
    lines = text.splitlines()
    header = f"🛜АНОНС НА {target_day:%d.%m.%Y}🛜"
    text = "\n".join([header, *lines[1:]]) if lines else header
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_user_main_keyboard(),
        disable_web_page_preview=True,
    )


def build_exchange_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Выставить на биржу",
            callback_data="user_exchange|create",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔎 Смотреть предложения",
            callback_data="ex_view:decks",
        )
    )
    builder.row(_home_inline_button())
    return builder.as_markup()


async def show_exchange_menu(message: Message) -> None:
    await _edit_or_answer(
        message,
        text=(
            "🛍 <b>Биржа карт</b>\n\n"
            "Выберите действие: выставить карту или посмотреть уже принятые предложения."
        ),
        reply_markup=build_exchange_keyboard(),
    )


async def start_exchange_submission(message: Message, state: FSMContext) -> None:
    await state.clear()
    decks = await get_exchange_decks_for_menu()
    allowed_ids = await get_exchange_deck_ids(decks)
    if not decks or not allowed_ids:
        await message.answer(
            "На бирже сейчас нет доступных колод.",
            reply_markup=build_user_main_keyboard(),
        )
        return

    await state.set_state(ExchangeFSM.waiting_for_deck)
    await message.answer(
        "🛍 <b>Новая заявка на биржу</b>\n\nВыберите колоду:",
        parse_mode="HTML",
        reply_markup=exchange_deck_keyboard(decks, allowed_ids),
    )


async def show_exchange_browser(message: Message) -> None:
    allowed_ids = await get_exchange_deck_ids()
    service = await ExchangeCatalogService.create()
    deck_ids = await service.decks_with_approved(allowed_ids)

    builder = InlineKeyboardBuilder()
    for deck_id in deck_ids:
        builder.button(
            text=f"📚 Колода {int(deck_id)}",
            callback_data=f"ex_view:deck:{int(deck_id)}",
        )
    builder.adjust(2 if len(deck_ids) > 1 else 1)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к бирже",
            callback_data="user_exchange|root",
        )
    )

    text = (
        "🛍 <b>Биржа карт</b>\n\nВыберите колоду:"
        if deck_ids
        else "🛍 <b>Биржа карт</b>\n\nПринятых предложений пока нет."
    )
    await _edit_or_answer(message, text=text, reply_markup=builder.as_markup())


def build_notifications_keyboard(
    settings: dict,
    *,
    subscribed: bool,
) -> InlineKeyboardMarkup:
    enabled = lambda key: _as_bool(
        settings.get(key),
        _NOTIFY_DEFAULTS[key],
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"📨 Общие уведомления {'✅' if subscribed else '❌'}",
            callback_data="user_notify|global",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=(
                "🔔 О начале аукциона "
                f"{'✅' if enabled('notify_auction_start') else '❌'}"
            ),
            callback_data="notify_toggle_start",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=(
                "⏰ За минуту до конца "
                f"{'✅' if enabled('notify_bid_reminder') else '❌'}"
            ),
            callback_data="notify_toggle_remind",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=(
                "🏁 О завершении "
                f"{'✅' if enabled('notify_auction_end') else '❌'}"
            ),
            callback_data="notify_toggle_end",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=(
                "📅 Анонс дня в 00:00 "
                f"{'✅' if enabled('notify_daily_today') else '❌'}"
            ),
            callback_data="notify_toggle_today",
        )
    )
    builder.row(_home_inline_button())
    return builder.as_markup()


async def show_notifications_menu(
    message: Message,
    *,
    user_id: int,
) -> None:
    settings = await get_settings(user_id) or {}
    subscribed = await is_subscribed(user_id)
    await _edit_or_answer(
        message,
        text=(
            "🔔 <b>Уведомления</b>\n\n"
            f"Общие уведомления: <b>{'включены' if subscribed else 'выключены'}</b>\n"
            "Нажимайте на пункты, чтобы изменить настройки."
        ),
        reply_markup=build_notifications_keyboard(
            settings,
            subscribed=subscribed,
        ),
    )


def build_profile_keyboard(*, verified: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not verified:
        builder.row(
            InlineKeyboardButton(
                text="🆔 Пройти UID-верификацию",
                callback_data="user_profile|verify_uid",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔔 Настроить уведомления",
            callback_data="user_profile|notifications",
        )
    )
    builder.row(_home_inline_button())
    return builder.as_markup()


async def show_profile(message: Message, *, user: types.User) -> None:
    subscribed = await is_subscribed(user.id)
    try:
        uid = await get_user_verified_uid(user.id)
    except Exception:
        uid = None

    verification = "✅ UID верифицирован" if uid else "❌ UID не верифицирован"
    uid_line = ""
    if uid:
        value = str(uid)
        uid_line = f"\nUID: <code>{value[:3]}***{value[-3:]}</code>"

    await message.answer(
        "<b>👤 Профиль</b>\n"
        f"Имя: {escape_html(user.full_name)}\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"Общие уведомления: {'✅ включены' if subscribed else '❌ выключены'}\n"
        f"Верификация: {verification}"
        f"{uid_line}",
        parse_mode="HTML",
        reply_markup=build_profile_keyboard(verified=bool(uid)),
    )


def build_luxury_keyboard(*, is_luxury: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить статус",
            callback_data="user_luxury|refresh",
        )
    )
    if is_luxury:
        builder.row(
            InlineKeyboardButton(
                text="📅 VIP-расписание",
                callback_data="user_luxury|schedule",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🕳 Свободные слоты",
                callback_data="user_luxury|gaps",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🔎 Найти карту в расписании",
                callback_data="user_luxury|when",
            )
        )
    builder.row(_home_inline_button())
    return builder.as_markup()


async def show_luxury_menu(
    message: Message,
    *,
    user_id: int,
    bot: Bot,
) -> None:
    is_luxury = await check_luxury(user_id, bot)
    text = (
        "👑 <b>Лакшери-раздел</b>\n\n"
        + (
            "Статус подтверждён. Доступны расширенное расписание, "
            "свободные слоты и поиск карт."
            if is_luxury
            else "Лакшери-статус не найден. Оформить доступ можно у @velassya."
        )
    )
    await _edit_or_answer(
        message,
        text=text,
        reply_markup=build_luxury_keyboard(is_luxury=is_luxury),
    )


def build_gap_days_keyboard(*, page: int = 0) -> InlineKeyboardMarkup:
    page = max(0, int(page))
    today = _today_msk()
    start = today + timedelta(days=page * _SCHEDULE_PAGE_SIZE)
    builder = InlineKeyboardBuilder()

    buttons = [
        InlineKeyboardButton(
            text=f"{_WEEKDAYS_RU[day.weekday()]} • {day:%d.%m}",
            callback_data=f"user_gap_day|{day.isoformat()}",
        )
        for day in (
            start + timedelta(days=offset)
            for offset in range(_SCHEDULE_PAGE_SIZE)
        )
    ]
    for index in range(0, len(buttons), 2):
        builder.row(*buttons[index:index + 2])

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ Раньше",
                callback_data=f"user_gaps_page|{page - 1}",
            )
        )
    navigation.append(
        InlineKeyboardButton(
            text="Позже ➡️",
            callback_data=f"user_gaps_page|{page + 1}",
        )
    )
    builder.row(*navigation)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="user_luxury|root",
        )
    )
    return builder.as_markup()


async def show_gap_days(message: Message, *, page: int = 0) -> None:
    await _edit_or_answer(
        message,
        text="🕳 <b>Свободные слоты</b>\n\nВыберите день:",
        reply_markup=build_gap_days_keyboard(page=page),
    )


async def show_day_gaps(message: Message, *, user_id: int, target_day: date) -> None:
    if not (await is_admin(user_id) or await is_luxury_user(user_id)):
        await message.answer("Эта функция доступна только Лакшери-пользователям.")
        return

    range_start = datetime.combine(target_day, time())
    rows = await get_auctions_in_range(
        range_start,
        range_start + timedelta(days=1),
        statuses=["scheduled", "active"],
    )
    busy: set[time] = set()
    for auction in rows:
        start = to_moscow(auction["start_time"]).replace(tzinfo=None)
        rounded_minute = 0 if start.minute < 30 else 30
        busy.add(time(start.hour, rounded_minute))

    now = to_moscow(utc_now()).replace(tzinfo=None)

    def free_slots(start: time, end: time) -> list[datetime]:
        slots = _slot_iter_range(target_day, start, end)
        if target_day == _today_msk():
            slots = [slot for slot in slots if slot >= now]
        return [slot for slot in slots if slot.time() not in busy]

    show_free = free_slots(WORK_START, WORK_END)
    luxury_free = free_slots(LUX_START, LUX_END)
    regular_free = free_slots(REG_START, REG_END)
    blocks = _format_blocks(show_free)

    await message.answer(
        f"🕳 Свободные слоты на <b>{target_day:%d.%m.%Y}</b>\n\n"
        f"<b>Показ:</b> {', '.join(blocks) if blocks else '—'}\n"
        f"<b>Лакшери:</b> {_format_slots(luxury_free)}\n"
        f"<b>Обычные:</b> {_format_slots(regular_free)}\n\n"
        f"Всего свободных стартов: <b>{len(show_free)}</b>",
        parse_mode="HTML",
        protect_content=True,
        reply_markup=build_user_main_keyboard(),
    )


async def show_card_schedule_search(
    message: Message,
    *,
    user_id: int,
    query: str,
) -> None:
    if not (await is_admin(user_id) or await is_luxury_user(user_id)):
        await message.answer("Эта функция доступна только Лакшери-пользователям.")
        return

    lots = await get_auctions_by_card_ref(
        query,
        statuses=["pending", "scheduled", "active"],
    )
    if not lots:
        await message.answer(
            "По этой карте или герою ничего не найдено.",
            reply_markup=build_user_main_keyboard(),
        )
        return

    by_day: dict[date, list[dict]] = defaultdict(list)
    for lot in lots:
        by_day[to_moscow(lot["start_time"]).date()].append(lot)

    lines = [f"🔎 <b>Расписание по запросу «{html.escape(query)}»</b>", ""]
    for selected_day in sorted(by_day):
        lines.append(f"<b>{selected_day:%d.%m.%Y}</b>")
        for lot in sorted(
            by_day[selected_day],
            key=lambda item: to_moscow(item["start_time"]),
        ):
            start = to_moscow(lot["start_time"]).strftime("%H:%M")
            hero = html.escape(str(lot.get("hero_name") or "—"))
            card = html.escape(str(lot.get("card_name") or "—"))
            lines.append(f"• {start} • {hero} — {card}")
        lines.append("")

    await message.answer(
        "\n".join(lines).strip(),
        parse_mode="HTML",
        protect_content=True,
        reply_markup=build_user_main_keyboard(),
    )


def help_text() -> str:
    return (
        "ℹ️ <b>Как пользоваться ботом</b>\n\n"
        "🎴 <b>Подать лот</b> — запускает пошаговое оформление заявки.\n"
        "📦 <b>Мои лоты</b> — актуальные, завершённые, выплаты и архив.\n"
        "📅 <b>Расписание</b> — выбор дня кнопками.\n"
        "🛍 <b>Биржа</b> — выставление и просмотр предложений.\n"
        "🔔 <b>Уведомления</b> — все переключатели в одном месте.\n"
        "🃏 <b>Подписки</b> — подписки на карты, колоды и пресеты.\n"
        "👤 <b>Профиль</b> — статус уведомлений и UID-верификация.\n"
        "👑 <b>Лакшери</b> — расширенное расписание и поиск.\n"
        "🆘 <b>Поддержка</b> — обращение администрации с вложениями.\n\n"
        "Кнопка «🏠 Меню» отменяет текущий ввод и возвращает на главный экран."
    )


@router.message(Command("start"), F.chat.type == "private")
async def user_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
    command: CommandObject,
) -> None:
    try:
        await mark_user_private_chat_opened(message.from_user.id)
    except Exception:
        pass

    argument = (command.args or "").strip().lower()
    if argument == "addlot":
        await launch_add_lot(message, state, bot)
        return
    if argument == "subs":
        await launch_card_subscription(message, state)
        return

    try:
        await sync_trusted_status(
            message.from_user.id,
            message.from_user.username,
        )
        is_luxury, full_name = await register_user(message.from_user, bot)
        status = (
            "👑 Лакшери-статус подтверждён."
            if is_luxury
            else "✅ Профиль участника зарегистрирован."
        )
        await state.clear()
        await message.answer(
            user_main_text(full_name=full_name, status_line=status),
            parse_mode="HTML",
            reply_markup=build_user_main_keyboard(),
        )
    except TelegramForbiddenError:
        try:
            await mark_user_private_chat_closed(message.from_user.id)
        except Exception:
            pass
    except Exception:
        logger.exception("Could not open the user menu")
        await message.answer(
            "Не удалось загрузить профиль. Попробуйте открыть меню ещё раз.",
            reply_markup=build_user_main_keyboard(),
        )


@router.message(F.text == USER_MENU_HOME, F.chat.type == "private")
async def user_home(message: Message, state: FSMContext) -> None:
    await send_user_main_menu(message, state)


@router.message(F.text == USER_MENU_ADD_LOT, F.chat.type == "private")
async def user_add_lot(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    await launch_add_lot(message, state, bot)


@router.message(F.text == USER_MENU_MY_LOTS, F.chat.type == "private")
async def user_my_lots(message: Message) -> None:
    await my_lots_cmd(message)


@router.message(F.text == USER_MENU_SCHEDULE, F.chat.type == "private")
async def user_schedule(message: Message) -> None:
    await show_schedule_menu(message)


@router.message(F.text == USER_MENU_EXCHANGE, F.chat.type == "private")
async def user_exchange(message: Message) -> None:
    await show_exchange_menu(message)


@router.message(F.text == USER_MENU_NOTIFICATIONS, F.chat.type == "private")
async def user_notifications(message: Message) -> None:
    await show_notifications_menu(message, user_id=message.from_user.id)


@router.message(F.text == USER_MENU_SUBSCRIPTIONS, F.chat.type == "private")
async def user_subscriptions(message: Message, state: FSMContext) -> None:
    await launch_card_subscription(message, state)


@router.message(F.text == USER_MENU_PROFILE, F.chat.type == "private")
async def user_profile(message: Message) -> None:
    await show_profile(message, user=message.from_user)


@router.message(F.text == USER_MENU_LUXURY, F.chat.type == "private")
async def user_luxury(
    message: Message,
    bot: Bot,
) -> None:
    await show_luxury_menu(
        message,
        user_id=message.from_user.id,
        bot=bot,
    )


@router.message(F.text == USER_MENU_SUPPORT, F.chat.type == "private")
async def user_support(message: Message, state: FSMContext) -> None:
    await appeal_start(message, state)


@router.message(F.text == USER_MENU_HELP, F.chat.type == "private")
async def user_help(message: Message) -> None:
    await message.answer(
        help_text(),
        parse_mode="HTML",
        reply_markup=build_user_main_keyboard(),
    )


@router.callback_query(F.data == "user_menu|home")
async def user_home_callback(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await call.answer()
    await send_user_main_menu(call.message, state)


@router.callback_query(F.data.startswith("user_schedule|"))
async def user_schedule_page(call: CallbackQuery) -> None:
    try:
        page = int((call.data or "").split("|", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return
    await call.answer()
    await show_schedule_menu(call.message, page=page)


@router.callback_query(F.data.startswith("user_day|"))
async def user_schedule_day(call: CallbackQuery) -> None:
    try:
        target_day = date.fromisoformat((call.data or "").split("|", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная дата.", show_alert=True)
        return
    await call.answer("Открываю расписание")
    await show_day_schedule(call.message, target_day)


@router.callback_query(F.data == "user_exchange|root")
async def user_exchange_root(call: CallbackQuery) -> None:
    await call.answer()
    await show_exchange_menu(call.message)


@router.callback_query(F.data == "user_exchange|create")
async def user_exchange_create(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await call.answer("Открываю оформление")
    await start_exchange_submission(call.message, state)


@router.callback_query(F.data == "ex_view:decks")
async def user_exchange_decks(call: CallbackQuery) -> None:
    if await is_admin(call.from_user.id):
        raise SkipHandler
    await call.answer()
    await show_exchange_browser(call.message)


@router.callback_query(F.data == "user_notify|global")
async def user_toggle_global_notifications(call: CallbackQuery) -> None:
    current = await is_subscribed(call.from_user.id)
    new_value = not current
    await set_subscription(call.from_user.id, new_value)
    try:
        await log_admin_action(
            call.from_user.id,
            "subscribe" if new_value else "unsubscribe",
            None,
            (
                "Пользователь включил общие уведомления через меню"
                if new_value
                else "Пользователь выключил общие уведомления через меню"
            ),
        )
    except Exception:
        pass
    await call.answer(
        "Общие уведомления включены"
        if new_value
        else "Общие уведомления выключены"
    )
    await show_notifications_menu(call.message, user_id=call.from_user.id)


@router.callback_query(
    F.data.startswith("notify_toggle_")
    | (F.data == "toggle_notify_daily_today")
)
async def user_toggle_notification(call: CallbackQuery) -> None:
    callback = call.data or ""
    if callback == "toggle_notify_daily_today":
        callback = "notify_toggle_today"
    if callback not in _NOTIFY_MAP:
        await call.answer("Неизвестная настройка.", show_alert=True)
        return

    field, enabled_key, disabled_key = _NOTIFY_MAP[callback]
    settings = await get_settings(call.from_user.id) or {}
    current = _as_bool(settings.get(field), _NOTIFY_DEFAULTS[field])
    new_value = not current
    await set_settings(call.from_user.id, **{field: new_value})
    await call.answer(
        USER_MESSAGES.get(enabled_key if new_value else disabled_key)
        or "Настройка обновлена"
    )
    await show_notifications_menu(call.message, user_id=call.from_user.id)


@router.callback_query(F.data == "user_profile|notifications")
async def user_profile_notifications(call: CallbackQuery) -> None:
    await call.answer()
    await show_notifications_menu(call.message, user_id=call.from_user.id)


@router.callback_query(F.data == "user_profile|verify_uid")
async def user_profile_verify_uid(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    existing = await get_verified_uid_for_user(call.from_user.id)
    if existing:
        await call.answer("UID уже верифицирован.", show_alert=True)
        return
    await state.clear()
    await state.set_state(UIDVerificationFSM.waiting_for_uid)
    await call.answer()
    await call.message.answer(
        "🆔 <b>UID-верификация</b>\n\nПришлите UID из 24 символов (0–9, a–f).",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "user_luxury|root")
async def user_luxury_root(
    call: CallbackQuery,
    bot: Bot,
) -> None:
    await call.answer()
    await show_luxury_menu(
        call.message,
        user_id=call.from_user.id,
        bot=bot,
    )


@router.callback_query(F.data == "user_luxury|refresh")
async def user_luxury_refresh(
    call: CallbackQuery,
    bot: Bot,
) -> None:
    await call.answer("Статус обновлён")
    await show_luxury_menu(
        call.message,
        user_id=call.from_user.id,
        bot=bot,
    )


@router.callback_query(F.data == "user_luxury|schedule")
async def user_luxury_schedule(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await is_luxury_user(call.from_user.id):
        await call.answer("Доступно только Лакшери-пользователям.", show_alert=True)
        return
    await state.clear()
    await state.set_state(LuxScheduleFSM.choosing_month)
    await call.answer()
    await call.message.answer(
        "📅 Выберите месяц:",
        reply_markup=months_keyboard(
            prefix="luxsched",
            auction_id=None,
        ),
        protect_content=True,
    )


@router.callback_query(F.data == "user_luxury|gaps")
async def user_luxury_gaps(call: CallbackQuery) -> None:
    if not await is_luxury_user(call.from_user.id):
        await call.answer("Доступно только Лакшери-пользователям.", show_alert=True)
        return
    await call.answer()
    await show_gap_days(call.message)


@router.callback_query(F.data.startswith("user_gaps_page|"))
async def user_luxury_gaps_page(call: CallbackQuery) -> None:
    try:
        page = int((call.data or "").split("|", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return
    await call.answer()
    await show_gap_days(call.message, page=page)


@router.callback_query(F.data.startswith("user_gap_day|"))
async def user_luxury_gap_day(call: CallbackQuery) -> None:
    try:
        target_day = date.fromisoformat((call.data or "").split("|", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная дата.", show_alert=True)
        return
    await call.answer("Считаю свободные слоты")
    await show_day_gaps(
        call.message,
        user_id=call.from_user.id,
        target_day=target_day,
    )


@router.callback_query(F.data == "user_luxury|when")
async def user_luxury_when(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await is_luxury_user(call.from_user.id):
        await call.answer("Доступно только Лакшери-пользователям.", show_alert=True)
        return
    await state.clear()
    await state.set_state(UserMenuFSM.waiting_for_card_search)
    await call.answer()
    await call.message.answer(
        "🔎 Напишите название карты, имя героя или ID карты.\n"
        "Кнопка «🏠 Меню» отменит поиск."
    )


@router.message(
    StateFilter(UserMenuFSM.waiting_for_card_search),
    F.chat.type == "private",
)
async def user_luxury_when_query(
    message: Message,
    state: FSMContext,
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите название карты, героя или ID.")
        return
    await state.clear()
    await show_card_schedule_search(
        message,
        user_id=message.from_user.id,
        query=query,
    )


__all__ = [
    "UserMenuFSM",
    "build_exchange_keyboard",
    "build_notifications_keyboard",
    "build_schedule_keyboard",
    "help_text",
    "router",
    "send_user_main_menu",
    "show_day_schedule",
    "show_exchange_menu",
    "show_notifications_menu",
    "show_schedule_menu",
    "user_main_text",
]
