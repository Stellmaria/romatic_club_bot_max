from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_priority_admin_navigation_precedes_conflicting_routers() -> None:
    source = _source("bot/bootstrap/routers.py")

    priority = source.index("dispatcher.include_router(admin_navigation_router)")
    for later_router in (
        "dispatcher.include_router(admin_panel_system_router)",
        "dispatcher.include_router(users_router)",
        "dispatcher.include_router(auctions_router)",
        "dispatcher.include_router(auction_exchange_router)",
        "dispatcher.include_router(admin_panel_router)",
    ):
        assert priority < source.index(later_router)


def test_complete_admin_menu_exposes_schedule_exchange_and_legacy_sections() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")
    menu = _source("bot/handlers/admin/admin_menu.py")

    assert '@router.message(Command("admin"), F.chat.type == "private")' in navigation
    assert '@router.message(Command("admin_panel"), F.chat.type == "private")' in navigation
    assert '["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"]' in menu
    assert '["📊 Статистика", "📣 Рассылка", "🚫 Логи"]' in menu
    assert '["📅 Расписание", "🛒 Биржа"]' in menu
    assert 'rows.append(["🖥 Система"])' in menu
    assert (
        'F.text.lower().in_(["назад", "⬅️ назад", "отмена", "❌ отмена", "cancel"])'
        in navigation
    )
    assert '"admin_back"' in navigation
    assert '"universal_cancel"' in navigation


def test_exchange_navigation_uses_supported_catalog_callbacks() -> None:
    source = _source("bot/handlers/admin/admin_navigation.py")

    assert 'F.text == "🛒 Биржа"' in source
    assert 'F.data == "ex_appr:root"' in source
    assert "_kb_exchange_approved_root()" in source
    assert "exinv|" not in source


def test_schedule_navigation_is_read_only_and_clears_previous_fsm() -> None:
    source = _source("bot/handlers/admin/admin_navigation.py")

    assert 'F.text == "📅 Расписание"' in source
    assert "start_preview_schedule(message, state)" in source
    assert "start_edit_schedule" not in source


def test_publisher_receives_both_channel_addresses() -> None:
    application = _source("bot/application.py")
    workers = _source("bot/bootstrap/workers.py")
    publication = _source("bot/handlers/auction/publication.py")

    assert "auction_channel_id=app_settings.auction_channel_id" in application
    assert "auction_channel_username=app_settings.auction_channel_username" in application
    assert "channel_id=auction_channel_id" in workers
    assert "channel_username=auction_channel_username" in workers
    assert "def _publication_targets(" in publication
    assert "for target in _publication_targets(channel_id, channel_username):" in publication
