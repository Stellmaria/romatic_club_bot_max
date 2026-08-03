# Runbook: production observability alerts

Все команды выполняются на сервере из каталога deployment. Не публикуйте дампы метрик и логи с идентификаторами пользователей в открытых каналах.

## BotCoreLatencyP95High

**Первые действия:** подтвердить рост p95 на панели Core, проверить `/readyz`, состояние PostgreSQL и рестарты контейнера bot.

**Типовые причины:** медленные запросы, внешний Telegram API, блокировка event loop, перегруженный worker.

**Диагностика:** сопоставить `correlation_id` медленных операций с логами, проверить активные запросы PostgreSQL и `worker_ready`/`worker_restarts`.

**Эскалация:** если p95 выше 2 секунд более 20 минут или растёт error rate, остановить тяжёлые фоновые операции и эскалировать владельцу core.

## UserbotLatencyP95High

**Первые действия:** проверить панель Userbot, состояние сессии Telethon, доступность Telegram и рестарты userbot.

**Типовые причины:** flood wait, сетевые задержки, длинная очередь, деградация Telegram API.

**Диагностика:** проверить operation-level логи, queue depth и частоту flood-wait ошибок.

**Эскалация:** при устойчивом превышении 20 минут ограничить потребителей очереди и эскалировать владельцу userbot.

## BotCoreErrorRateHigh

**Первые действия:** определить доминирующий `error_type`, проверить последние изменения и readiness.

**Типовые причины:** ошибка handler, недоступная БД, несовместимые callback payload, внешняя ошибка Telegram.

**Диагностика:** сгруппировать structured logs по `operation_id` и `correlation_id`, проверить миграции и PostgreSQL.

**Эскалация:** при error rate выше 10% или потере критичного flow выполнить rollback на проверенный SHA.

## UserbotErrorRateHigh

**Первые действия:** проверить типы ошибок userbot, авторизацию сессии и связь с Telegram.

**Типовые причины:** отозванная сессия, flood wait, malformed task, ошибка middleware.

**Диагностика:** проверить structured logs и соответствующий queue item без публикации содержимого сообщения.

**Эскалация:** остановить потребление повреждённой очереди и эскалировать владельцу userbot.

## BotSchedulerLagHigh

**Первые действия:** проверить scheduler worker, event loop, PostgreSQL locks и время контейнера.

**Типовые причины:** долгий task, блокировка БД, CPU starvation, массовый backlog.

**Диагностика:** сравнить `scheduler_lag_seconds`, worker heartbeat, активные запросы и нагрузку контейнера.

**Эскалация:** при lag выше 60 секунд более 10 минут отключить некритичные jobs и эскалировать владельцу scheduler.

## UserbotQueueDepthHigh

**Первые действия:** проверить, растёт ли очередь, живы ли consumers и нет ли flood wait.

**Типовые причины:** остановленный consumer, Telegram rate limit, всплеск входящих задач, poison message.

**Диагностика:** сравнить queue depth с throughput/error rate, проверить последний успешно обработанный item и рестарты.

**Эскалация:** при очереди выше 500 или непрерывном росте 15 минут ограничить producers, изолировать poison item и эскалировать владельцу userbot.

## Проверка после восстановления

После rollback или restore дождитесь трёх стабильных health polls, проверьте `/readyz`, обязательные серии `/metrics` и отсутствие активных alert expressions. Deployment smoke выполняет эту проверку автоматически.
