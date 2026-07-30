# Hotfix: IMMUTABLE expression index

## Ошибка

PostgreSQL останавливал `002_initial_schema.sql` с сообщением:

```text
functions in index expression must be marked IMMUTABLE
```

Причина: в старой базе `auctions.start_time` уже имеет тип `timestamptz`.
Выражение `start_time::date` зависит от параметра `TimeZone` текущей сессии и
поэтому не может использоваться в expression index.

## Исправление

Миграция теперь определяет фактический тип столбца через `pg_attribute`:

- для `timestamptz` индексируется дата после явного перевода в
  `Europe/Moscow`;
- для legacy `timestamp` сохраняется прежний индекс по `column::date`.

Такая же защита добавлена для `auction_posts_backfill.post_date_msk`.

Дополнительно из `db/db.py` удалена локальная настройка `StreamHandler`, из-за
которой сообщения базы и мигратора печатались два раза.

## Повторный запуск

Неудачная миграция выполнялась внутри транзакции и не записалась в
`schema_migrations`, поэтому после замены файлов достаточно снова запустить:

```text
python main.py
```

или:

```text
python find_discussion_id.py
```
