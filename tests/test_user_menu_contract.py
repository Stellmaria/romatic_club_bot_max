from pathlib import Path

from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_user_keyboard_exposes_public_sections_only() -> None:
    source = _source("bot/keyboards/keyboards.py")

    for label in (
        "🎴 Подать лот",
        "📦 Мои лоты",
        "📅 Сегодня",
        "🛍 Биржа",
        "🔔 Уведомления",
        "🃏 Подписки",
        "👤 Профиль",
        "👑 Лакшери",
        "🆘 Поддержка",
        "ℹ️ Помощь",
        "🏠 Меню",
    ):
        assert label in source

    layout = source[source.index("USER_MENU_LAYOUT") : source.index("def build_user_main_keyboard")]
    assert "USER_MENU_TODAY" in layout
    assert "USER_MENU_EXCHANGE" in layout
    assert "USER_MENU_SCHEDULE" not in layout

    # The schedule constant remains so stale Telegram keyboards can be rejected safely.
    assert 'USER_MENU_SCHEDULE = "📆 Расписание"' in source
    assert 'USER_MENU_EXCHANGE = "🛍 Биржа"' in source
    assert "is_persistent=True" in source
    assert "return build_user_main_keyboard()" in source
    assert 'KeyboardButton(text="/"' not in source


def test_user_menu_routes_public_buttons_to_existing_flows() -> None:
    source = _source("bot/handlers/user_menu.py")

    assert '@router.message(Command("start"), F.chat.type == "private")' in source
    assert "launch_add_lot(message, state, bot)" in source
    assert "my_lots_cmd(message)" in source
    assert "launch_card_subscription(message, state)" in source
    assert "appeal_start(message, state)" in source
    assert "UIDVerificationFSM.waiting_for_uid" in source
    assert "LuxScheduleFSM.choosing_month" in source
    assert 'F.data.startswith("notify_toggle_")' in source
    assert 'USER_MESSAGES["commands_info"]' not in source


def test_today_is_public_while_other_schedule_entries_are_guarded() -> None:
    source = _source("bot/handlers/user_access_control.py")

    assert 'Command("today")' in source
    assert "F.text == USER_MENU_TODAY" in source
    assert "show_day_schedule(message, to_moscow(utc_now()).date())" in source
    assert 'Command("day")' in source
    assert 'Command("day", "today")' not in source
    assert "F.text == USER_MENU_SCHEDULE" in source
    assert 'F.data.startswith("user_schedule|")' in source
    assert 'F.data.startswith("user_day|")' in source


def test_exchange_entry_points_are_public() -> None:
    access = _source("bot/handlers/user_access_control.py")
    menu = _source("bot/handlers/user_menu.py")

    assert "USER_MENU_EXCHANGE" not in access
    assert "ExchangeFSM" not in access
    assert "Биржа доступна только администраторам" not in access

    assert "F.text == USER_MENU_EXCHANGE" in menu
    assert 'F.data == "user_exchange|root"' in menu
    assert 'F.data == "user_exchange|create"' in menu
    assert 'F.data == "ex_view:decks"' in menu
    assert "start_exchange_submission(call.message, state)" in menu


def test_luxury_schedule_access_remains_available() -> None:
    schedule = _source("bot/handlers/auction/schedule.py")
    menu = _source("bot/handlers/user_menu.py")
    access = _source("bot/handlers/user_access_control.py")

    assert "return await is_admin(user_id) or await is_luxury_user(user_id)" in schedule
    assert 'F.data == "user_luxury|schedule"' in menu
    assert "Расписание для Лакшери-пользователей открывается через раздел" in access
    assert "👑 <b>Лакшери</b> — расписание, свободные слоты и поиск карт." in access


def test_user_menu_has_universal_home_and_corrected_button_help() -> None:
    menu = _source("bot/handlers/user_menu.py")
    access = _source("bot/handlers/user_access_control.py")

    assert 'callback_data="user_menu|home"' in menu
    assert "await state.clear()" in menu
    assert "user=call.from_user" in menu
    assert "Здесь всё работает через кнопки" in menu
    assert "Как пользоваться ботом" in access
    assert "📅 <b>Сегодня</b> — аукционы, которые идут в течение текущего дня." in access
    assert "🛍 <b>Биржа</b> — выставление карт и просмотр принятых предложений." in access
    assert "Расписание по другим дням доступно администраторам и Лакшери-пользователям" in access
    assert "Биржа является административной функцией" not in access
    assert "Кнопка «🏠 Меню»" in access


def test_user_schedule_label_does_not_collide_with_priority_admin_router() -> None:
    keyboard = _source("bot/keyboards/keyboards.py")
    admin_navigation = _source("bot/handlers/admin/admin_navigation.py")

    assert 'USER_MENU_SCHEDULE = "📆 Расписание"' in keyboard
    assert 'F.text == "📅 Расписание"' in admin_navigation


def test_access_control_precedes_schedule_and_user_routers() -> None:
    names = [feature.name for feature in get_router_registry().ordered_features]

    access = names.index("users.access-control")
    assert access < names.index("auctions.schedule")
    assert access < names.index("users.menu")
    assert access < names.index("users.core")
    assert access < names.index("exchange.catalog")

    menu = names.index("users.menu")
    assert menu < names.index("users.profile")
    assert menu < names.index("users.core")
    assert menu < names.index("auctions.core")
    assert menu < names.index("exchange.catalog")
