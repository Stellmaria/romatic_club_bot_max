# 2026-08-01 — Hermes incident orchestration для Max

- Дата: `2026-08-01`
- ID: `hermes-incident-orchestration`
- Линия/фаза: `server operations`
- Статус: `частично`
- Ветка: `agent/hermes-incident-orchestration`
- Базовый commit: текущий `main` на момент создания ветки

## Перед началом

### Цель

Добавить read-only мониторинг production-сервисов `bot` и `userbot`, который передаёт очищенные инциденты главному `@VelvetHermesBot`. Главный Hermes должен маршрутизировать вероятные дефекты к `@romatic_max_coder_bot`, дождаться результата, проверить PR/CI и уведомить владельца. Merge и изменение production остаются только по явному разрешению владельца.

### Исходный контекст

Max Server Supervisor уже предоставляет главному Hermes фиксированные `status/logs/restart/update/rollback`, а coder Max изолирован в своём Git workspace. Отсутствовал автоматический watcher stop/auto-restart/unhealthy и транспорт инцидента в основной Hermes Runs API.

### Планируемый объём

- отдельный read-only monitor для `bot` и `userbot`;
- bounded redaction логов и секретов;
- Runs API submit/poll к основному Hermes через VPS loopback;
- project instruction `coderctl submit max`;
- Telegram-уведомление о начале и завершении разбора;
- persistent state и cooldown;
- systemd sandbox и отдельный installer;
- CI regression contracts.

### Критерии готовности

- monitor не выполняет restart/update/rollback или mutating Docker actions;
- оба сервиса отслеживаются раздельно;
- healthy recreation не создаёт ложный инцидент;
- auto-restart, stop и подтверждённый unhealthy создают bounded incident;
- одинаковый incident не спамит во время cooldown;
- terminal Hermes result отправляется владельцу;
- CI полностью зелёный.

### Риски и ограничения

- monitor зависит от уже установленного Velvet orchestration и `/srv/hermes-operator-control/incident.env`;
- Hermes/coder output не является разрешением на merge или deployment;
- Telegram destination берётся из log chat или owner/admin IDs;
- production smoke выполняется только после merge обоих связанных PR.

## После завершения

Статус: `частично`.

### Фактически сделано

- добавлен `scripts/hermes_incident_monitor.py`;
- добавлен sandboxed `romatic-hermes-incident-monitor.service`;
- добавлен отдельный installer с проверкой shared loopback credentials;
- добавлены tests detection/redaction/read-only/systemd contract;
- CI расширен compile/bash/pytest проверками monitor.

### Миграции и совместимость

SQL и production data не изменяются. Existing Server Supervisor, bot/userbot Compose services и secrets сохраняются. Monitor использует общий внутренний API основного Hermes через loopback и не публикует новые порты.

### Проверки

- `python -m py_compile scripts/hermes_incident_monitor.py`;
- `bash -n deploy/server/install-hermes-incident-monitor.sh`;
- focused pytest monitor contracts;
- полный CI draft PR.

### PR и commit

- PR будет создан из `agent/hermes-incident-orchestration`;
- commits публикуются последовательно в той же ветке.

### Незавершённое

- обновить Server Supervisor runbook;
- открыть draft PR и исправить CI;
- после merge выполнить VPS install и health smoke.

### Следующий шаг

Открыть связанный draft PR и довести проверки до зелёного состояния без установки на production до merge.
