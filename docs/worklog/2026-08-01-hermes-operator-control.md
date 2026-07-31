# 2026-08-01 — Доступ главного Hermes к Max Supervisor

- Дата: `2026-08-01`
- ID: `hermes-operator-control`
- Линия/фаза: `server operations`
- Статус: `частично`
- Ветка: `agent/hermes-operator-control`
- Базовый commit: `220d608b2e98ac9aff227a43b9fd2aab8bec2c08`

## Перед началом

### Цель

Разрешить главному `@VelvetHermesBot` безопасно проверять и управлять Max `bot` и `userbot` через существующий Server Supervisor, не выдавая эти права репозиторному кодеру `@romatic_max_coder_bot`.

### Исходный контекст

Max-coder работает в изолированном `/workspace` без production `.env`, Docker daemon, systemd и server checkout. Поэтому запрос на запуск bot и userbot из coder-контейнера закономерно завершился отказом. Сам Max Server Supervisor уже поддерживает фиксированные операции status, logs, restart bot, restart userbot, update и rollback, но его proxy был доступен только внутри обычной Compose-сети Max.

### Планируемый объём

- подключить только `supervisor-proxy` к общей internal control network;
- не подключать туда bot, userbot, PostgreSQL или coder;
- добавить стабильный alias `romatic-supervisor`;
- сохранить отсутствие host port и Docker socket;
- добавить regression contract и обновить runbook.

### Критерии готовности

- Hermes operator gateway видит Max proxy по alias `romatic-supervisor`;
- control network имеет `internal: true` и `attachable: true`;
- bot, userbot и postgres остаются только в обычной сети проекта;
- Max-coder не получает новую сеть или runtime credentials;
- обязательный CI проходит.

### Риски и ограничения

Control network создаётся Compose-проектом Max и используется только для связи двух минимальных proxy-контейнеров. Удаление Max Compose network временно разорвёт связь с operator gateway до следующего запуска Max stack.

## После завершения

### Фактически сделано

- `supervisor-proxy` подключён к `hermes-supervisor-control`;
- добавлен alias `romatic-supervisor`;
- сеть объявлена internal и attachable;
- bot, userbot и postgres не изменены;
- добавлен `tests/test_hermes_operator_control_contract.py`;
- `docs/SERVER_SUPERVISOR_RUNBOOK.md` дополнен границами доступа и проверкой сети;
- открыт draft PR `#15`, связанный с `Stellmaria/Velvet#529`;
- ветка пересобрана поверх актуального `main` после изменения runtime-мигратора базы.

### Миграции и совместимость

SQL-миграций нет. API Supervisor и Telegram-системное меню не меняются. После deployment пересоздаётся только `supervisor-proxy`; bot и userbot не требуют перезапуска из-за этой сетевой правки.

### Проверки

Добавлен contract test Compose-сети. Предыдущий CI был зелёным; после синхронизации с актуальным `main` требуется повторный CI и server smoke.

### PR и commit

- Max PR: `#15`;
- Velvet PR: `Stellmaria/Velvet#529`;
- ветка: `agent/hermes-operator-control`.

### Незавершённое

- дождаться зелёного CI обоих PR;
- слить Max до Velvet installer deployment;
- обновить server checkout;
- проверить internal network и read-only status bot/userbot через главный Hermes.

### Следующий шаг

После зелёного CI слить Max PR, пересоздать только `supervisor-proxy`, затем установить Hermes operator gateway из `/srv/velvet` и проверить status bot/userbot без изменяющих действий.
