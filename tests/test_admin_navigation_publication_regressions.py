from pathlib import Path

from bot.bootstrap.routers import get_router_registry


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_priority_admin_navigation_precedes_conflicting_routers() -> None:
    names = [feature.name for feature in get_router_registry().ordered_features]

    priority = names.index("admin.navigation")
    for later_feature in (
        "admin.system",
        "users.core",
        "auctions.core",
        "exchange.catalog",
        "admin.panel",
    ):
        assert priority < names.index(later_feature)


def test_admin_root_keeps_schedule_inside_moderation() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")
    menu = _source("bot/handlers/admin/admin_menu.py")
    requests = _source("bot/handlers/admin/admin_panel_requests.py")

    assert '@router.message(Command("admin"), F.chat.type == "private")' in navigation
    assert '@router.message(Command("admin_panel"), F.chat.type == "private")' in navigation
    assert '["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"]' in menu
    assert '["📊 Статистика", "📣 Рассылка", "🚫 Логи"]' in menu
    assert '["🛒 Биржа"]' in menu
    assert '["📅 Расписание", "🛒 Биржа"]' not in menu
    assert '["📅 Расписание", "🛒 Биржа"]' in requests
    assert 'rows.append(["🖥 Система"])' in menu
    assert (
        'F.text.lower().in_(["назад", "⬅️ назад", "отмена", "❌ отмена", "cancel"])'
        in navigation
    )
    assert '"admin_back"' in navigation
    assert '"universal_cancel"' in navigation


def test_exchange_navigation_exposes_pending_and_approved_flows() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")
    pending_view = _source(
        "bot/handlers/admin/presentation/exchange_pending_view.py"
    )
    catalog = _source("bot/handlers/auction/exchange/catalog.py")

    assert 'F.text == "🛒 Биржа"' in navigation
    assert 'callback_data="admreq|pending|exchange"' in navigation
    assert 'F.data == "admreq|pending|exchange"' in navigation
    assert "show_pending_exchange_mode_picker(call.message)" in navigation
    assert 'F.data.startswith("expend_mode|")' in navigation
    assert "show_pending_exchange_request_one(call.message, state, page=0)" in navigation
    assert "show_pending_exchange_requests_all(call.message, limit=200)" in navigation
    assert 'F.data.startswith("expend_page|")' in navigation
    assert "show_pending_exchange_request_one(call.message, state, page=page)" in navigation
    assert "page намеренно игнорируем" not in pending_view
    assert "pending_batches(include_luxury=True)" in pending_view
    assert 'callback_data="expend_mode|one"' in pending_view
    assert 'callback_data="expend_mode|all"' in pending_view
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


def test_schedule_navigation_uses_detailed_moderation_renderer() -> None:
    navigation = _source("bot/handlers/admin/admin_navigation.py")
    schedule = _source("bot/handlers/admin/moderation_schedule.py")

    assert 'F.text == "📅 Расписание"' in navigation
    assert "start_preview_schedule(message, state)" in navigation
    assert "start_edit_schedule" not in navigation
    assert 'F.data.startswith("preview_schedule|")' not in navigation
    assert "_grouped_schedule_lines" not in navigation
    assert "_schedule_message_chunks" not in navigation

    assert (
        '@router.callback_query(PreviewScheduleFSM.choosing_month, '
        'F.data.startswith("preview_schedule|"))'
        in schedule
    )
    assert (
        '@router.callback_query(PreviewScheduleFSM.choosing_day, '
        'F.data.startswith("preview_schedule|"))'
        in schedule
    )
    assert "await call.answer()" in schedule
    assert "await get_auctions_by_date_with_owners(selected_date)" in schedule
    assert "Актуальное расписание" in schedule
    assert "Обновлено:" in schedule
    assert "Auction ID:" in schedule
    assert "Герой:" in schedule
    assert "Владелец(ы):" in schedule
    assert "Дата заявки:" in schedule
    assert "Свободное время для записи:" in schedule
    assert "split_message_by_blocks(blocks)" in schedule


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
