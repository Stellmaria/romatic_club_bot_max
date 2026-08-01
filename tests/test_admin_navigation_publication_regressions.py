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


def test_exchange_navigation_exposes_pending_and_approved_flows() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")
    catalog = _source("bot/handlers/auction/exchange/catalog.py")

    assert 'F.text == "🛒 Биржа"' in navigation
    assert 'callback_data="admreq|pending|exchange"' in navigation
    assert 'F.data == "admreq|pending|exchange"' in navigation
    assert 'F.data.startswith("expend_mode|")' in navigation
    assert "show_pending_exchange_requests(call.message)" in navigation
    assert "show_pending_exchange_requests_all(call.message)" in navigation
    assert 'F.data == "ex_appr:root"' in navigation
    assert "kb_exchange_approved_root()" in navigation
    assert "exinv|" not in navigation
    assert '@router.callback_query(F.data == "ex_appr:decks")' in catalog
    assert '@router.callback_query(F.data.startswith("ex_appr:list:all:"))' in catalog
    assert '@router.callback_query(F.data.startswith("ex_appr:lot:"))' in catalog


def test_exchange_queue_uses_supported_pending_total_query() -> None:
    exchange_queue = _source("bot/handlers/admin/presentation/exchange_queue.py")

    assert "await queries.pending_total()" in exchange_queue
    assert "await queries.pending_count()" not in exchange_queue


def test_exchange_moderation_uses_supported_pending_total_query() -> None:
    exchange_moderation = _source("bot/handlers/auction/exchange_moderation.py")

    assert "await queries.pending_total()" in exchange_moderation
    assert "await queries.pending_count()" not in exchange_moderation


def test_schedule_navigation_acks_and_chunks_grouped_preview() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")

    assert 'F.text == "📅 Расписание"' in navigation
    assert "start_preview_schedule(message, state)" in navigation
    assert "start_edit_schedule" not in navigation
    assert 'F.data.startswith("preview_schedule|")' in navigation
    assert 'period="day"' in navigation
    assert 'prefix="preview_schedule"' in navigation
    assert 'await call.answer("Загружаю расписание…")' in navigation
    assert "get_auctions_by_date_with_owners(selected_date)" in navigation
    assert "_grouped_schedule_lines(auctions)" in navigation
    assert "_schedule_message_chunks(selected_date, lines)" in navigation
    assert 'auction.get("owners_json")' in navigation
    assert "build_grouped_schedule_lines_with_prefixes(" not in navigation


def test_per_lot_schedule_editor_remains_separate() -> None:
    schedule = _source("bot/handlers/admin/admin_panel_schedule.py")

    assert 'F.text == "📝 Редактировать расписание"' in schedule
    assert "start_edit_schedule(message, state)" in schedule
    assert "for lot in auctions:" in schedule
    assert 'F.data.startswith("edit_schedule_lot|")' in schedule


def test_publisher_receives_both_channel_addresses() -> None:
    application = _source("bot/application.py")
    workers = _source("bot/bootstrap/workers.py")
    publication = _source("bot/handlers/auction/publication.py")

    assert "auction_channel_id=bot_settings.auction_channel_id" in application
    assert "auction_channel_username=bot_settings.auction_channel_username" in application
    assert "channel_id=auction_channel_id" in workers
    assert "channel_username=auction_channel_username" in workers
    assert "def _publication_targets(" in publication
    assert "for target in _publication_targets(channel_id, resolved_channel_username):" in publication
