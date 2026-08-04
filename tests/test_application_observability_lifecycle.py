from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bot.application as application
from bot.application import _run_polling_with_worker_monitor


class _PollingCompletesDispatcher:
    async def start_polling(self, _bot: object) -> None:
        await asyncio.sleep(0)


class _WaitingTaskManager:
    def __init__(self) -> None:
        self.cancelled = False

    async def wait_for_failure(self) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def test_worker_monitor_is_cancelled_after_polling_completes() -> None:
    manager = _WaitingTaskManager()

    await _run_polling_with_worker_monitor(
        _PollingCompletesDispatcher(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        manager,  # type: ignore[arg-type]
    )

    assert manager.cancelled is True


class _WaitingDispatcher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def start_polling(self, _bot: object) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class _FailingTaskManager:
    async def wait_for_failure(self) -> Any:
        await asyncio.sleep(0)
        raise RuntimeError("critical worker failed")


async def test_worker_failure_cancels_polling_and_is_reraised() -> None:
    dispatcher = _WaitingDispatcher()

    with pytest.raises(RuntimeError, match="critical worker failed"):
        await _run_polling_with_worker_monitor(
            dispatcher,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            _FailingTaskManager(),  # type: ignore[arg-type]
        )

    assert dispatcher.started.is_set()
    assert dispatcher.cancelled is True


def _bot_config() -> SimpleNamespace:
    sample_bot_token = ":".join(("123456789", "A" * 35))
    return SimpleNamespace(
        bot=SimpleNamespace(
            log_level="INFO",
            aiogram_debug=False,
            uid_hash_key="test-hash-key",
            uid_enc_key="test-current-key",
            uid_enc_key_previous="test-previous-key",
            bot_token=sample_bot_token,
            debug_middleware=True,
            drop_pending_updates=True,
            auction_channel_id=-1001234567890,
            auction_channel_username="@auction_channel",
        ),
        supervisor=object(),
        database=object(),
        runtime_dir=Path("auction-bot-test"),
    )


def _install_run_bot_fakes(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
    *,
    polling_error: BaseException | None = None,
    failing_cleanup: str | None = None,
) -> dict[str, Any]:
    import bot.bootstrap as bootstrap
    import bot.bootstrap.container as container_module
    import bot.core.legacy_config as legacy_config
    import bot.core.observability as observability
    import bot.telegram.protection as protection
    import bot.uid_crypto as uid_crypto
    import db.lifecycle as lifecycle
    import db.pool as pool_module

    state: dict[str, Any] = {"events": [], "metric_calls": []}
    events: list[str] = state["events"]

    class FakePool:
        def get_size(self) -> int:
            return 8

        def get_idle_size(self) -> int:
            return 5

        def get_min_size(self) -> int:
            return 2

        def get_max_size(self) -> int:
            return 12

    class FakeDatabaseRuntime:
        def __init__(self, _settings: object) -> None:
            self.pool: FakePool | None = FakePool()
            self.started = False
            state["database_runtime"] = self

        def require_pool(self) -> FakePool:
            assert self.pool is not None
            return self.pool

    class FakeMetricsRegistry:
        def __init__(self) -> None:
            state["metrics"] = self

        def increment(self, name: str, **labels: str) -> None:
            state["metric_calls"].append((name, labels))

    class FakeSupervisor:
        async def start(self) -> None:
            events.append("supervisor.start")

        async def close(self) -> None:
            events.append("supervisor.close")
            if failing_cleanup == "supervisor":
                raise RuntimeError("supervisor cleanup failed")

    supervisor = FakeSupervisor()

    class FakeBotSession:
        async def close(self) -> None:
            events.append("bot.close")
            if failing_cleanup == "bot":
                raise RuntimeError("bot cleanup failed")

    class FakeBot:
        def __init__(self, *, token: str) -> None:
            state["bot_token"] = token
            self.session = FakeBotSession()

        async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
            state["drop_pending_updates"] = drop_pending_updates
            events.append("bot.delete_webhook")

    class FakeUpdateObserver:
        def outer_middleware(self, middleware: object) -> None:
            state["middleware"] = middleware
            events.append("dispatcher.middleware")

    class FakeDispatcher:
        def __init__(self, **kwargs: object) -> None:
            state["dispatcher_kwargs"] = kwargs
            self.update = FakeUpdateObserver()

    class FakeTaskManager:
        def __init__(self) -> None:
            state["task_manager"] = self

        def start(self, specs: object) -> None:
            state["task_specs"] = specs
            events.append("tasks.start")

        async def stop(self) -> None:
            events.append("tasks.stop")
            if failing_cleanup == "tasks":
                raise RuntimeError("task cleanup failed")

    class FakeHealthProbeServer:
        def __init__(
            self,
            *,
            database_ready: Any,
            task_manager: Any,
            metrics: object,
            database_metrics: Any,
            port: int,
        ) -> None:
            state["health_port"] = port
            state["health_metrics_registry"] = metrics
            state["database_ready"] = database_ready
            state["task_manager_provider"] = task_manager
            state["database_metrics_provider"] = database_metrics

        async def start(self) -> None:
            assert state["database_ready"]() is True
            assert state["task_manager_provider"]() is state["task_manager"]
            state["database_metrics"] = state["database_metrics_provider"]()
            events.append("health.start")

        async def close(self) -> None:
            events.append("health.close")
            if failing_cleanup == "health":
                raise RuntimeError("health cleanup failed")

    class FakeMiddleware:
        pass

    async def fake_init_db(runtime: FakeDatabaseRuntime) -> None:
        runtime.started = True
        events.append("database.start")

    async def fake_close_db(runtime: FakeDatabaseRuntime) -> None:
        runtime.started = False
        events.append("database.close")
        if failing_cleanup == "database":
            raise RuntimeError("database cleanup failed")

    async def fake_polling(
        _dispatcher: object,
        _bot: object,
        _task_manager: object,
    ) -> None:
        events.append("polling.run")
        if polling_error is not None:
            raise polling_error

    monkeypatch.setattr(
        application,
        "configure_logging",
        lambda level, **kwargs: state.update(logging_configuration=(level, kwargs)),
    )
    monkeypatch.setattr(
        application.SupervisorClient,
        "from_settings",
        staticmethod(lambda _settings: supervisor),
    )
    monkeypatch.setattr(application, "Bot", FakeBot)
    monkeypatch.setattr(application, "Dispatcher", FakeDispatcher)
    monkeypatch.setattr(application, "BackgroundTaskManager", FakeTaskManager)
    monkeypatch.setattr(application, "ObservabilityMiddleware", FakeMiddleware)
    monkeypatch.setattr(application, "_run_polling_with_worker_monitor", fake_polling)
    monkeypatch.setattr(
        legacy_config,
        "configure_legacy_config",
        lambda config: state.update(legacy_config=config),
    )
    monkeypatch.setattr(
        uid_crypto,
        "configure_uid_crypto",
        lambda *args: state.update(uid_crypto_args=args),
    )
    monkeypatch.setattr(
        bootstrap,
        "build_background_task_specs",
        lambda *args, **kwargs: (args, kwargs),
    )
    monkeypatch.setattr(
        bootstrap,
        "register_all_routers",
        lambda dispatcher, **kwargs: state.update(
            registered_dispatcher=dispatcher,
            router_kwargs=kwargs,
        ),
    )
    monkeypatch.setattr(
        container_module.ApplicationContainer,
        "build",
        staticmethod(lambda **kwargs: state.update(container_kwargs=kwargs) or object()),
    )
    monkeypatch.setattr(observability, "MetricsRegistry", FakeMetricsRegistry)
    monkeypatch.setattr(observability, "HealthProbeServer", FakeHealthProbeServer)
    monkeypatch.setattr(
        protection,
        "patch_bot_protect_content",
        lambda bot, **kwargs: state.update(protected_bot=bot, protection_kwargs=kwargs),
    )
    monkeypatch.setattr(lifecycle, "init_db", fake_init_db)
    monkeypatch.setattr(lifecycle, "close_db", fake_close_db)
    monkeypatch.setattr(pool_module, "DatabaseRuntime", FakeDatabaseRuntime)
    return state


async def test_run_bot_starts_observability_and_closes_every_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _install_run_bot_fakes(monkeypatch)
    config = _bot_config()

    await application.run_bot(config)  # type: ignore[arg-type]

    assert state["logging_configuration"] == (
        "INFO",
        {"aiogram_debug": False, "structured": True},
    )
    assert state["drop_pending_updates"] is True
    assert state["health_port"] == 8081
    assert state["database_metrics"] == {
        "database_pool_size": 8.0,
        "database_pool_idle": 5.0,
        "database_pool_min_size": 2.0,
        "database_pool_max_size": 12.0,
    }
    assert state["metric_calls"] == [("application_starts_total", {"process": "bot"})]
    assert state["events"] == [
        "database.start",
        "supervisor.start",
        "dispatcher.middleware",
        "bot.delete_webhook",
        "tasks.start",
        "health.start",
        "polling.run",
        "health.close",
        "tasks.stop",
        "bot.close",
        "supervisor.close",
        "database.close",
    ]


async def test_run_bot_records_failure_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _install_run_bot_fakes(
        monkeypatch,
        polling_error=RuntimeError("polling exploded"),
        failing_cleanup="health",
    )

    with pytest.raises(RuntimeError, match="polling exploded"):
        await application.run_bot(_bot_config())  # type: ignore[arg-type]

    assert state["metric_calls"] == [
        ("application_starts_total", {"process": "bot"}),
        ("application_failures_total", {"process": "bot"}),
    ]
    assert state["events"][-5:] == [
        "health.close",
        "tasks.stop",
        "bot.close",
        "supervisor.close",
        "database.close",
    ]


async def test_run_bot_raises_cleanup_error_after_clean_polling_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _install_run_bot_fakes(monkeypatch, failing_cleanup="tasks")

    with pytest.raises(RuntimeError, match="task cleanup failed"):
        await application.run_bot(_bot_config())  # type: ignore[arg-type]

    assert state["events"][-5:] == [
        "health.close",
        "tasks.stop",
        "bot.close",
        "supervisor.close",
        "database.close",
    ]
