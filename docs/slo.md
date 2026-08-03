# SLO наблюдаемости Romatic Club

Этот документ задаёт единый контракт для production-мониторинга core-бота и userbot. Окно оценки для оперативных алертов — 5 минут, для SLO-отчётности — 30 дней.

## Core latency

- SLI: `telegram_update_latency_seconds`.
- Цель: p95 не выше 2 секунд для основных Telegram flow.
- Алерт: `BotCoreLatencyP95High`, если порог превышен 10 минут.

## Userbot latency

- SLI: `userbot_operation_latency_seconds`.
- Цель: p95 не выше 5 секунд.
- Алерт: `UserbotLatencyP95High`, если порог превышен 10 минут.

## Error rate

- Core: отношение `telegram_update_errors_total` к `telegram_updates_total`.
- Userbot: отношение `userbot_operation_errors_total` к `userbot_operations_total`.
- Цель: error rate не выше 5% в каждом компоненте.
- Алерты: `BotCoreErrorRateHigh`, `UserbotErrorRateHigh` после 5 минут превышения.

## Scheduler lag

- SLI: `scheduler_lag_seconds{component="core"}`.
- Цель: lag не выше 30 секунд.
- Алерт: `BotSchedulerLagHigh` после 5 минут превышения.

## Userbot queue depth

- SLI: `userbot_queue_depth`.
- Цель: глубина очереди не выше 100 элементов.
- Алерт: `UserbotQueueDepthHigh` после 10 минут превышения.

## Доступность probes

- `/healthz` подтверждает, что процесс жив.
- `/readyz` возвращает готовность PostgreSQL и критичных background workers.
- `/metrics` публикует Prometheus exposition без пользовательских текстов и секретов.

Пороговые значения меняются только вместе с dashboard, alert rules, runbook и тестом `scripts/validate_monitoring.py`.
