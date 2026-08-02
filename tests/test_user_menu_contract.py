from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_user_keyboard_exposes_all_primary_sections() -> None:
    source = _source("bot/keyboards/keyboards.py")

    for label in (
        "🎴 Подать лот",
        "📦 Мои лоты",
        "📆 Расписание",
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

    assert "is_persistent=True" in source
    assert "return build_user_main_keyboard()" in source
    assert 'KeyboardButton(text="/"' not in source


def test_user_menu_routes_buttons_to_existing_flows() -> None:
    source = _source("bot/handlers/user_menu.py")

    assert '@router.message(Command("start"), F.chat.type == "private")' in source
    assert "launch_add_lot(message, state, bot)" in source
    assert "my_lots_cmd(message)" in source
    assert "launch_card_subscription(message, state)" in source
    assert "appeal_start(message, state)" in source
    assert "ExchangeFSM.waiting_for_deck" in source
    assert "UIDVerificationFSM.waiting_for_uid" in source
    assert "LuxScheduleFSM.choosing_month" in source
    assert 'F.data.startswith("user_day|")' in source
    assert 'F.data == "ex_view:decks"' in source
    assert 'F.data.startswith("notify_toggle_")' in source
    assert "USER_MESSAGES[\"commands_info\"]" not in source


def test_user_menu_has_universal_home_and_button_help() -> None:
    source = _source("bot/handlers/user_menu.py")

    assert 'callback_data="user_menu|home"' in source
    assert "await state.clear()" in source
    assert "user=call.from_user" in source
    assert "Здесь всё работает через кнопки" in source
    assert "Как пользоваться ботом" in source
    assert "Кнопка «🏠 Меню»" in source


def test_user_schedule_label_does_not_collide_with_priority_admin_router() -> None:
    keyboard = _source("bot/keyboards/keyboards.py")
    admin_navigation = _source("bot/handlers/admin/admin_navigation.py")

    assert 'USER_MENU_SCHEDULE = "📆 Расписание"' in keyboard
    assert 'F.text == "📅 Расписание"' in admin_navigation


def test_user_menu_precedes_legacy_user_routers() -> None:
    source = _source("bot/bootstrap/routers.py")

    menu = source.index("dispatcher.include_router(user_menu_router)")
    assert menu < source.index("dispatcher.include_router(profile_router)")
    assert menu < source.index("dispatcher.include_router(users_router)")
    assert menu < source.index("dispatcher.include_router(auctions_router)")
    assert menu < source.index("dispatcher.include_router(auction_exchange_router)")
