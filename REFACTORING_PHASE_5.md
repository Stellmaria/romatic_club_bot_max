# Рефакторинг — фаза 5

## Итог

Фаза 5 закрывает три системных риска, оставшихся после фазы 4:

1. уведомления Telegram теперь ставятся в транзакционный outbox вместе с флагом события;
2. критические времена аукционов и ставок переведены с неявного локального времени на `timestamptz`/UTC;
3. административный lifecycle вынесен из `auction_comments.py` в отдельный роутер и application-слой.

Дополнительно исправлен дефект старого цикла: `notified_end` выставлялся в `TRUE`, хотя сообщение о завершении аукциона не отправлялось.

## 1. Transactional Telegram outbox

Новые компоненты:

- `migrations/005_transactional_outbox_and_utc.sql` — таблица `telegram_outbox`;
- `bot/repositories/outbox.py` — атомарная постановка и конкурентный захват команд;
- `bot/services/outbox.py` — валидация payload и API для auction-событий;
- `bot/telegram/outbox.py` — доставка команд в Telegram;
- фоновая задача `telegram-outbox` в `main.py`.

Для событий `start`, `one_minute` и `end` репозиторий в одной транзакции:

1. атомарно меняет соответствующий `notified_*` только из `FALSE` в `TRUE`;
2. создаёт по одной команде на получателя;
3. защищает каждую команду уникальным `dedupe_key`.

Конкурентные экземпляры бота используют `FOR UPDATE SKIP LOCKED`. Повторная постановка одного события блокируется и флагом аукциона, и уникальным ключом outbox.

### Политика ошибок доставки

- `TelegramRetryAfter` возвращает команду в `pending` с указанной Telegram задержкой;
- `TelegramForbiddenError` и `TelegramBadRequest` переводят команду в `failed`;
- сетевая/неизвестная ошибка после начала запроса переводит команду в `failed` для ручной проверки;
- просроченный lease также переводится в `failed` и не переотправляется автоматически.

Это намеренная защита от дублей: после сбоя между фактической отправкой и записью `sent` нельзя безопасно доказать, что Telegram не принял сообщение.

Outbox захватывает команды по одной. При остановке процесса только текущая доставка может оказаться в неопределённом состоянии; ещё не начатые команды остаются `pending`.

## 2. UTC и `timestamptz`

Миграция 005 преобразует:

- `auctions.start_time`;
- `auctions.end_time`;
- `auctions.created_at`;
- `bids.placed_at`;
- `bids.created_at`.

Исторические значения `timestamp without time zone` трактуются как локальное время `Europe/Moscow`, затем переводятся в `timestamptz`. PostgreSQL хранит такие значения как абсолютный момент времени; application-слой передаёт aware UTC, а пользовательский интерфейс форматирует их в МСК.

Общий модуль `bot/core/time.py` содержит:

- `utc_now()`;
- `ensure_utc()`;
- `to_moscow()`;
- `moscow_date()` и `moscow_time()`.

На границах workflow нормализованы модерация, перенос, ручной restart/stop, публикация, финализация и операции со ставками. Дневные SQL-фильтры теперь явно используют календарную дату `Europe/Moscow`, поэтому не зависят от `TimeZone` PostgreSQL-сессии.

## 3. Декомпозиция административного lifecycle

Из `bot/handlers/auction_comments.py` вынесены:

- `/lot_owner`;
- `/activate_lot`;
- `/user_lots`;
- `макс удалить`;
- `макс старт`;
- `макс стоп`;
- общий resolver лота по reply-цепочке.

Новый роутер: `bot/handlers/auction/admin_lifecycle.py`.

Удаление ставки больше не выполняет `DELETE` из handler. `AuctionAdminService` и `AuctionAdminRepository` одной транзакцией удаляют ставку, создают предупреждение и увеличивают счётчик пользователя.

`auction_comments.py` уменьшен с 3828 до 3481 строки. Это первый безопасный срез большого файла; winner/print/warnings-подсистемы остаются кандидатами на следующую фазу.

## 4. Дополнительная надёжность

- удалён общий runtime-метод `db.db.update_lot_field` с динамическим именем колонки;
- исправлен winner lookup для обратного аукциона: минимальная ставка выбирается первой;
- миграции сериализованы session advisory lock, поэтому несколько реплик не применяют один SQL-файл одновременно;
- `init_db.sql` синхронизирован с `timestamptz`-схемой;
- expression-index дня аукциона пересоздаётся с явным `Europe/Moscow`.

## 5. Проверки

В локальном окружении выполнены:

- `compileall` и AST-разбор всех Python-файлов;
- 50/50 доменных и статических регрессионных тестов;
- проверка 104 модулей `bot`: нет повторных top-level символов;
- нет top-level import cycles;
- services/repositories не импортируют handlers;
- старый `update_lot_field` отсутствует в runtime-коде;
- вынесенные lifecycle-функции отсутствуют в `auction_comments.py` и зарегистрированы отдельным роутером.

Реальная миграция PostgreSQL и доставка через Telegram в локальном окружении не выполнялись: для них нужны рабочая копия БД, токены и сетевой integration-стенд.

## Развёртывание

Миграция 005 меняет типы колонок и может удерживать блокировку таблиц. Рекомендуемый порядок:

1. сделать проверенный backup PostgreSQL;
2. остановить bot/userbot и убедиться, что старые процессы не пишут в БД;
3. развернуть новую версию и запустить один экземпляр для применения миграций;
4. проверить типы колонок:

   ```sql
   SELECT table_name, column_name, data_type
   FROM information_schema.columns
   WHERE table_schema = 'public'
     AND (
       (table_name = 'auctions' AND column_name IN ('start_time', 'end_time', 'created_at'))
       OR (table_name = 'bids' AND column_name IN ('placed_at', 'created_at'))
     )
   ORDER BY table_name, column_name;
   ```

5. проверить несколько известных лотов: время в интерфейсе должно остаться тем же МСК-временем;
6. проверить outbox:

   ```sql
   SELECT status, count(*)
   FROM public.telegram_outbox
   GROUP BY status
   ORDER BY status;
   ```

7. после проверки запустить остальные реплики и userbot;
8. наблюдать `failed` и `processing` дольше 15 минут; такие записи требуют ручной проверки, а не слепого replay.

## План фазы 6

1. Разделить `handlers/auctions.py`: создание заявок, расписание/диагностика и exchange UI.
2. Разделить оставшийся `auction_comments.py`: winner flow, print-win, warnings/ban и служебные команды.
3. Расширить outbox на daily/card-subscription и административные рассылки.
4. Добавить integration-тесты с настоящим PostgreSQL для миграций, row locks, outbox race и crash-after-send сценария.
5. Добавить административный экран/команды для просмотра `failed`, подтверждения факта доставки и безопасного ручного replay.
6. Продолжить сокращение `db/db.py`: перенос read models и оставшихся тематических операций в repositories.
