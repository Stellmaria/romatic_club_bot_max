# Hotfix: immutable migration history

## Симптом

При запуске `main.py` или `find_discussion_id.py` мигратор останавливался с ошибкой:

```text
Уже применённая миграция была изменена: 002_initial_schema.sql
```

## Причина

В уже применённую миграцию `002_initial_schema.sql` позднее добавили колонку
`accepted_currencies`. Это изменило SHA-256 файла и корректно было отвергнуто
мигратором.

## Исправление

- `002_initial_schema.sql` восстановлена байт-в-байт до прежней версии.
- Колонка `accepted_currencies` создаётся только новой миграцией
  `009_auction_type_and_free_currencies.sql`.
- Записи `public.schema_migrations` вручную менять не требуется.
