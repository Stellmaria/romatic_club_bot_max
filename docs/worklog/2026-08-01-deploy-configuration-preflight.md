# 2026-08-01 — Deployment configuration preflight

## Инцидент

После merge PR #59 production update до `059d6e174c7c9565872a24bc36e3401018d641a5` снова завершился rollback. Новый bot-контейнер переходил в `restarting`, после чего старый deployment script пытался выполнить smoke через `docker compose exec` и получал ошибку Docker daemon.

Дополнительно старый smoke импортировал удалённый в PR #58 module-level singleton `bot.core.settings.settings`. Даже при стабильном контейнере этот smoke был несовместим с новой process-scoped конфигурацией.

## Исправление

- target images строятся до изменения runtime;
- строгая конфигурация bot и userbot проверяется в одноразовых `docker compose run --rm --no-deps` контейнерах;
- работающие production-контейнеры заменяются только после успешного preflight;
- rollback различает смену Git-кода и фактическую замену runtime;
- при ошибке preflight текущие контейнеры остаются нетронутыми;
- финальный smoke использует `BotProcessSettings.from_env`, а не удалённый singleton;
- добавлен regression-контракт порядка операций.

## Отдельная эксплуатационная причина

Supervisor token был создан bootstrap-командой с правами `0600 velvet:velvet`, тогда как production bot запускается под UID/GID `10001`. До следующего deploy необходимо отдельно исправить доступ контейнера к file-based secret без публикации токена и без `chmod 644`.
