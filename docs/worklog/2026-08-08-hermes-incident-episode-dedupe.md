# 2026-08-08 — Hermes incident episode dedupe для Max

- Дата: `2026-08-08`
- ID: `hermes-incident-episode-dedupe`
- Линия/фаза: `server operations`
- Статус: `готово к merge`
- Ветка: `fix/hermes-incident-episode-dedupe`
- База: `fb28f05e18ac3ff912b2db616a4b2a43fb63bccf`

## Цель

Не допускать повторных Hermes-разборов одного непрерывного production outage, когда состояние сервиса последовательно меняется между `container-auto-restarted`, `container-unhealthy` и `container-not-running`.

## Контекст

До исправления cooldown был привязан к полному `event_key`. Изменение причины или состояния контейнера меняло ключ и позволяло отправить новый Hermes run сразу после завершения предыдущего, хотя физически это оставался тот же инцидент.

## Реализовано

- состояние `incident_episode_open` сохраняется в persistent monitor state;
- после успешной постановки Hermes run эпизод помечается открытым;
- пока эпизод открыт, новые event keys не создают дополнительные Hermes runs;
- эпизод закрывается только после подтверждённого восстановления всех наблюдаемых сервисов `bot` и `userbot` до `running=true` и `health=healthy`;
- существующая защита от параллельного run и cooldown одного event key сохранена;
- старые state-файлы без нового поля совместимы и трактуются как закрытый эпизод.

## Проверки

Добавлены regression tests для блокировки follow-up state changes, сохранения прежнего cooldown и строгого определения healthy recovery. Полный CI выполняется на pull request.

## Безопасность и production

Изменение не добавляет mutating runtime-команд и не меняет БД. Production deployment/restart monitor остаются отдельным эксплуатационным действием после merge.