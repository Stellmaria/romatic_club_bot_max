from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _top_level_functions(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_schedule_and_warning_routers_are_extracted_and_registered() -> None:
    auctions = _top_level_functions("bot/handlers/auctions.py")
    schedule = _top_level_functions("bot/handlers/auction/schedule.py")
    comments = _top_level_functions("bot/handlers/auction_comments.py")
    warnings = _top_level_functions("bot/handlers/auction/warnings.py")
    winner = _top_level_functions("bot/handlers/auction/winner.py")
    exchange = set().union(*(
        _top_level_functions(str(path.relative_to(ROOT)))
        for path in sorted((ROOT / "bot/handlers/auction/exchange").glob("*.py"))
    ))
    main = _source("main.py")

    assert {"cmd_when", "cmd_gaps"} <= schedule
    assert not ({"cmd_when", "cmd_gaps"} & auctions)
    assert {"admin_unmute", "admin_ban_user", "cmd_prune_warnings"} <= warnings
    assert not ({"admin_unmute", "admin_ban_user", "cmd_prune_warns"} & comments)
    assert {"announce_winner", "cmd_print_win"} <= winner
    assert {"exchange_deck_keyboard", "show_pending_exchange_requests"} <= exchange
    assert "dp.include_router(auction_schedule_router)" in main
    assert "dp.include_router(auction_warnings_router)" in main
    assert "dp.include_router(auction_winner_router)" in main
    assert "dp.include_router(auction_exchange_router)" in main


def test_card_day_claim_and_enqueue_are_one_transaction() -> None:
    repository = _source("bot/repositories/outbox.py")
    notifications = _source("bot/auction_notify.py")

    marker = "async def enqueue_card_day_notification"
    body = repository[repository.index(marker):]
    assert "async with conn.transaction()" in body
    assert "INSERT INTO public.card_day_notifications" in body
    assert "await self._insert_messages" in body
    assert "notify_users" not in notifications
    assert "mark_card_day_notified" not in notifications
    assert "enqueue_card_day_notification" in notifications


def test_daily_announcement_uses_deduplicated_outbox() -> None:
    notifications = _source("bot/auction_notify.py")
    assert 'topic="daily"' in notifications
    assert "today.isoformat()" in notifications
    assert "_telegram_text_chunks(msg)" in notifications
    send_daily = notifications[
        notifications.index("async def send_daily_announce"):
        notifications.index("async def _sleep_until")
    ]
    assert "bot.send_message" not in send_daily


def test_notification_time_parse_failure_does_not_leave_unbound_values() -> None:
    notifications = _source("bot/auction_notify.py")
    assert "st_dt: Optional[datetime] = None" in notifications
    assert "et_dt: Optional[datetime] = None" in notifications


def test_delivery_certainty_blocks_unknown_replay() -> None:
    repository = _source("bot/repositories/outbox.py")
    worker = _source("bot/telegram/outbox.py")
    migration = _source("migrations/006_outbox_delivery_control.sql")

    assert "delivery_state = 'confirmed_not_sent'" in repository
    assert "AND delivery_state = 'confirmed_not_sent'" in repository
    assert "AND delivery_state = 'unknown'" in repository
    assert 'delivery_state="confirmed_not_sent"' in worker
    assert 'delivery_state="unknown"' in worker
    assert "chk_telegram_outbox_delivery_state" in migration


def test_outbox_admin_commands_use_service_boundary() -> None:
    handler = _source("bot/handlers/admin/outbox.py")
    main = _source("main.py")
    assert 'Command("outbox_status")' in handler
    assert 'Command("outbox_failed")' in handler
    assert 'Command("outbox_retry")' in handler
    assert 'Command("outbox_confirm")' in handler
    assert "TelegramOutboxService.create()" in handler
    assert "SELECT " not in handler
    assert "UPDATE " not in handler
    assert "dp.include_router(outbox_admin_router)" in main


def test_admin_broadcast_is_queued_as_copy_message() -> None:
    broadcast = _source("bot/handlers/admin/broadcast.py")
    repository = _source("bot/repositories/outbox.py")
    worker = _source("bot/telegram/outbox.py")
    migration = _source("migrations/006_outbox_delivery_control.sql")
    assert "enqueue_copy_message_broadcast" in broadcast
    assert "message.bot.copy_message" not in broadcast
    assert "'copy_message'" in repository
    assert "bot.copy_message" in worker
    assert "'send_message', 'copy_message'" in migration


def test_phase6_monoliths_are_smaller_than_phase5_baseline() -> None:
    assert len(_source("bot/handlers/auctions.py").splitlines()) < 4_500
    assert len(_source("bot/handlers/auction_comments.py").splitlines()) < 300


def test_winner_owner_notification_is_not_sent_twice() -> None:
    winner = _source("bot/handlers/auction/winner_components/announcement.py")
    block = winner[
        winner.index("async def send_notifications"):
        winner.index("@router.callback_query", winner.index("async def send_notifications"))
    ]
    assert "await bot.send_message(user_id, common_text" not in block
    assert "owner_text = common_text" in block
    assert "owner_text," in block


def test_phase6_handler_split_has_no_unresolved_globals() -> None:
    paths = (
        "bot/handlers/auctions.py",
        "bot/handlers/auction_comments.py",
        "bot/handlers/auction/schedule.py",
        "bot/handlers/auction/warnings.py",
        "bot/handlers/auction/winner.py",
        "bot/handlers/auction/exchange/common.py",
        "bot/handlers/auction/exchange/notifications.py",
        "bot/handlers/auction/exchange/submission.py",
        "bot/handlers/auction/exchange/moderation.py",
        "bot/handlers/auction/exchange/catalog.py",
        "bot/handlers/auction/exchange/diagnostics/__init__.py",
        "bot/handlers/auction/exchange/diagnostics/common.py",
        "bot/handlers/auction/exchange/diagnostics/media.py",
        "bot/handlers/auction/exchange/diagnostics/delivery.py",
        "bot/handlers/auction/exchange/diagnostics/reports.py",
        "bot/handlers/auction/exchange/diagnostics/reconciliation.py",
    )
    known = set(dir(builtins)) | {"__doc__", "__file__", "__name__", "__package__"}

    for relative in paths:
        table = symtable.symtable(_source(relative), relative, "exec")
        defined = {
            name
            for name in table.get_identifiers()
            if (
                table.lookup(name).is_assigned()
                or table.lookup(name).is_imported()
                or table.lookup(name).is_namespace()
            )
        }
        referenced_globals: set[str] = set()
        pending = [table]
        while pending:
            current = pending.pop()
            referenced_globals.update(
                name
                for name in current.get_identifiers()
                if current.lookup(name).is_global() and current.lookup(name).is_referenced()
            )
            pending.extend(current.get_children())

        assert not (referenced_globals - defined - known), relative

def test_market_sales_card_rewards_do_not_compare_enum_to_invalid_literals() -> None:
    market_flow = _source("bot/handlers/admin/services/market_add_flow.py")
    block = market_flow[
        market_flow.index("async def _my_sales_render"):
        market_flow.index("async def _my_sales_enter", market_flow.index("async def _my_sales_render") + 1)
        if "async def _my_sales_enter" in market_flow[market_flow.index("async def _my_sales_render") + 1:]
        else len(market_flow)
    ]
    assert "c.obtain_type::text" in block
    assert "c.obtain_type = 'cups'" not in block
    assert "c.obtain_type = 'treasures'" not in block
    assert "('tea', 'cups', 'cup')" in block




def test_stale_callback_updates_are_dropped_and_safely_ignored() -> None:
    main = _source("main.py")
    middleware = _source("bot/middlewares/expired_callback.py")
    callback_utils = _source("bot/telegram/callbacks.py")

    assert 'os.getenv("DROP_PENDING_UPDATES", "1")' in main
    assert "await bot.delete_webhook(drop_pending_updates=drop_pending_updates)" in main
    assert main.index("await bot.delete_webhook") < main.index("task_manager = BackgroundTaskManager()")
    assert "dp.update.outer_middleware(ExpiredCallbackMiddleware())" in main
    assert '"query is too old"' in callback_utils
    assert '"response timeout expired"' in callback_utils
    assert '"query id is invalid"' in callback_utils
    assert "is_expired_callback_error(error)" in middleware


def test_subscription_unsubscribe_callbacks_use_safe_answer() -> None:
    card_subscribe = _source("bot/handlers/card_subscribe.py")
    card_economy = _source("bot/handlers/admin/helper/new/card_economy.py")

    assert 'await safe_call_answer(call, "Подписка удалена")' in card_subscribe
    assert 'await call.answer("Подписка удалена")' not in card_subscribe
    assert 'await safe_callback_answer(call, "Отписано")' in card_economy
    assert 'await safe_callback_answer(call, "Уже отписан")' in card_economy
