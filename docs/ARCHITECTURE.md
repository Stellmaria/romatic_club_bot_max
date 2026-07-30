# Архитектурные границы

## Направление зависимостей

```text
Telegram / CLI adapters
        ↓
application services
        ↓
domain + repository protocols
        ↑
PostgreSQL / Telegram implementations
```

`bot/domain` не импортирует aiogram, asyncpg, handlers или конфигурацию.
Handlers преобразуют Telegram update в параметры use case и не должны открывать
транзакции. Services координируют правила и repositories. SQL принадлежит
repositories, а сетевые команды Telegram — transport-модулям.

## Composition root

`main.py` — только process entrypoint. `bot/application.py` владеет lifecycle
ресурсов, `bot/bootstrap/routers.py` — порядком aiogram routers, а
`bot/bootstrap/workers.py` — составом фоновых workers.
Модули не должны открывать соединения или создавать Telegram clients при
импорте. Фатальная ошибка startup/polling должна завершать процесс ненулевым
кодом, чтобы supervisor мог его перезапустить.

## Переходный legacy-слой

`db/db.py` остаётся тонким совместимым фасадом только для внешних legacy-
потребителей. Внутри production-кода импортов `db.db`, корневого `config` и
`fsm_states` больше нет. Реализации legacy-запросов физически разделены по
владельцам:

- `db/core.py` — общие query primitives и адаптер к `db/pool.py`;
- `db/users.py`, `db/admin.py`, `db/uid.py` — пользователи, администрирование и
  идентификация;
- `db/auctions.py`, `db/cards.py`, `db/subscriptions.py` — аукционы, каталог и
  уведомления;
- `db/market.py`, `db/exchange.py`, `db/posts.py` — маркет, обмены и статистика
  публикаций.

Services получают runtime pool через `db/pool.py`, а repositories принимают
его явно. При дальнейшем переносе bounded context:

1. создать тематический repository;
2. перенести SQL без изменения пользовательского контракта;
3. покрыть service behavior тестами;
4. перевести consumers;
5. удалить re-export из legacy-фасада.

Импорты `bot.services -> db.db`, `db -> bot.handlers` и SQL внутри handlers
запрещены архитектурными тестами. Глобальная AST-проверка требует ноль SQL-
строк во всём `bot/handlers/**`; persistence принадлежит repositories.

## Надёжность доставки

Критические идемпотентные сообщения ставятся в transactional outbox в одной
транзакции с domain state. Результат `unknown` никогда не replay-ится
автоматически: сначала администратор подтверждает факт доставки или её
отсутствие.

## Миграции

SQL-файлы `database/migrations/NNN_*.sql` неизменяемы после применения. Runner
сверяет SHA-256 и сериализует replicas advisory lock. Любое новое изменение
схемы — новый номер миграции; `database/bootstrap.sql` одновременно
поддерживается как bootstrap для чистого развёртывания. Источники DDL и
безопасный порядок восстановления описаны в `database/README.md`; приватные
per-table данные не являются частью исходного кода.
