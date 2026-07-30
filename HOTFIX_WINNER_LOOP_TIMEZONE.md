# Hotfix: winner loop timezone and duplicate logs

## Ошибка

`auction_winner_loop` сравнивал `datetime.now()` без часового пояса с
`auctions.end_time`, который после перехода схемы на `timestamptz` возвращается
`asyncpg` как timezone-aware `datetime`. Python запрещает такое сравнение и
выбрасывает `TypeError: can't compare offset-naive and offset-aware datetimes`.

## Исправление

- цикл использует `bot.core.time.utc_now()`;
- `end_time` нормализуется через `ensure_utc()`;
- legacy naive timestamps по-прежнему трактуются как московское время;
- добавлена тестируемая функция `winner_deadline_reached()`;
- `setup_logging()` очищает обработчики, добавленные legacy-модулями во время
  импорта, поэтому записи больше не дублируются.
