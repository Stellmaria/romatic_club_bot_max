# 2026-08-01 — Hermes incident orchestration для Max

- Дата: `2026-08-01`
- ID: `hermes-incident-orchestration`
- Линия/фаза: `server operations`
- Статус: `готово к merge`
- Ветка: `agent/hermes-incident-orchestration-v2`
- База проверки: актуальный `main` через pull-request merge ref

## Цель

Добавить read-only мониторинг production-сервисов `bot` и `userbot`, который передаёт очищенные инциденты главному `@VelvetHermesBot`. Главный Hermes маршрутизирует вероятные дефекты только к `@romatic_max_coder_bot`, дожидается результата, проверяет PR/CI и уведомляет владельца. Merge и изменение production остаются только по явному разрешению владельца.

## Реализовано

- отдельный read-only monitor для `bot` и `userbot`;
- обнаружение stop, роста Docker `RestartCount` и подтверждённого `unhealthy`;
- bounded redaction логов, URL и credentials;
- Runs API submit/poll к основному Hermes через VPS loopback;
- фиксированная инструкция `coderctl submit max`;
- Telegram-уведомления о начале и terminal-результате разбора;
- persistent state, cooldown и защита от параллельного спама;
- восстановление ожидания активного Hermes run после restart monitor;
- sandboxed `romatic-hermes-incident-monitor.service`;
- отдельный installer с проверкой shared loopback credentials;
- CI regression contracts для detection, redaction, cooldown, read-only команд и systemd sandbox.

## Безопасность

- monitor выполняет только `docker compose ps`, `docker compose logs` и `docker inspect`;
- отсутствуют restart, update, rollback, deploy и mutating systemd routes;
- Hermes и coder не получают Max `.env`, Docker socket или Supervisor token;
- transport errors логируются без URL и credentials;
- coder может подготовить ветку и PR, но не может merge или менять production;
- shared Hermes API key читается только из `/srv/hermes-operator-control/incident.env` и не печатается.

## Совместимость

SQL и production data не изменяются. Existing Server Supervisor, bot/userbot Compose services и secrets сохраняются. Monitor использует внутренний Runs API основного Hermes через loopback и не публикует новые порты.

Связанный orchestration PR `Stellmaria/Velvet#534` слит до финализации этого PR. Production installation выполняется после merge Max PR в порядке Velvet → Max.

## Проверки

- `python -m py_compile scripts/hermes_incident_monitor.py`;
- `bash -n deploy/server/install-hermes-incident-monitor.sh`;
- focused pytest monitor contracts;
- полный GitHub Actions CI на актуальном pull-request merge ref.

## PR

- PR: `#55` — `Добавить автоматическую передачу инцидентов Max в Hermes`;
- ветка: `agent/hermes-incident-orchestration-v2`;
- после зелёного CI PR переводится из Draft и сливается в `main`.

## После merge

Production install и health smoke остаются отдельным эксплуатационным действием и не выполняются кодером автоматически.
