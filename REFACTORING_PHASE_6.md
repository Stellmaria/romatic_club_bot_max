# Рефакторинг — фаза 6

## Итог

Фаза 6 завершает разбор двух крупнейших handler-монолитов и расширяет
transactional outbox до пользовательских ежедневных уведомлений и
административных рассылок.

Основные результаты:

1. `bot/handlers/auctions.py` уменьшен с 10 443 до 4 021 строки;
2. `bot/handlers/auction_comments.py` уменьшен с 3 481 до 221 строки;
3. расписание, предупреждения, winner/print-flow и exchange вынесены в
   самостоятельные роутеры;
4. card-day, daily announcement и admin broadcast переведены на устойчивую
   очередь с дедупликацией;
5. для неопределённого результата доставки введён безопасный ручной review,
   исключающий автоматические дубли;
6. добавлены opt-in интеграционные проверки outbox на настоящем PostgreSQL.

## 1. Декомпозиция handler-слоя

Новая структура аукционного домена:

| Модуль | Ответственность |
|---|---|
| `bot/handlers/auction/schedule.py` | `/when`, `/gaps`, разбор и отображение расписания в МСК |
| `bot/handlers/auction/warnings.py` | предупреждения, ban/unban, очистка предупреждений |
| `bot/handlers/auction/winner.py` | определение победителя, уведомления, print-win/print-ex flow |
| `bot/handlers/auction/exchange.py` | exchange UI, заявки, модерация и диагностика обменов |
| `bot/handlers/auction_comments.py` | небольшой compatibility/misc-роутер и legacy Flask bridge |

Все новые роутеры явно регистрируются в `main.py`. Связи с публикацией,
модерацией и appeals переведены на новые модули; прежние копии функций удалены.
Дополнительно устранена повторная отправка owner-уведомления в winner-flow.

`exchange.py` теперь изолирован как отдельный домен, но остаётся крупным. Его
внутренняя декомпозиция запланирована на фазу 7.

## 2. Расширение transactional outbox

Outbox теперь поддерживает два Telegram-метода:

- `send_message` — текстовые уведомления;
- `copy_message` — административные рассылки с сохранением типа исходного
  сообщения и вложений.

В устойчивую очередь переведены:

- уведомления о старте, последней минуте и окончании аукциона из фазы 5;
- уведомление подписчика о дне карточки;
- ежедневный анонс;
- массовая административная рассылка.

Card-day marker и запись outbox создаются в одной транзакции. Если транзакция
откатывается, не остаётся ложной отметки «уже отправлено». Daily и broadcast
получили стабильные `dedupe_key`; повторный запуск процесса не создаёт второй
набор тех же команд.

Worker захватывает записи через `FOR UPDATE SKIP LOCKED` по одной команде. Это
позволяет нескольким экземплярам безопасно разбирать очередь и ограничивает
зону неопределённости одной текущей доставкой на worker.

## 3. Контроль результата доставки

Миграция `006_outbox_delivery_control.sql` добавляет `topic`, состояние
доставки и поля административной проверки.

Состояния `delivery_state`:

| Состояние | Значение | Разрешённое действие |
|---|---|---|
| `not_attempted` | запрос ещё точно не доставлялся | автоматическая обработка |
| `confirmed_sent` | доставка подтверждена | ничего не повторять |
| `confirmed_not_sent` | Telegram точно отклонил запрос | разрешён безопасный replay |
| `unknown` | запрос мог дойти до Telegram до сбоя | только ручная проверка |

Сетевая ошибка, отмена worker после начала запроса или истёкший lease не
переотправляются автоматически. Это намеренная защита от двойных сообщений.

Добавлены административные команды:

- `/outbox_status` — сводка очереди и самое старое pending-сообщение;
- `/outbox_failed [limit]` — последние ошибки и их certainty;
- `/outbox_retry ID [комментарий]` — повтор только для
  `confirmed_not_sent`;
- `/outbox_confirm ID [комментарий]` — фиксация вручную подтверждённой
  доставки `unknown` без повторной отправки.

## 4. Проверки

Локально выполнены:

- `compileall` и AST-разбор 132 Python-файлов;
- 60/60 доменных и статических регрессионных тестов;
- проверка top-level символов: повторных функций/классов нет;
- проверка неразрешённых глобальных имён после разделения handlers;
- проверка регистрации вынесенных роутеров;
- проверка отсутствия старых функций в прежних монолитах;
- сохранены все 151 handler-декоратор исходных двух модулей;
- проверка транзакционной связки card-day marker + outbox;
- проверка запрета replay для `unknown`;
- проверка admin broadcast через `copy_message` outbox.

Добавлены пять opt-in тестов в `tests/integration/test_outbox_postgres.py`:

1. конкурентная постановка auction event;
2. гонка при создании card-day marker;
3. непересекающиеся `SKIP LOCKED` batch;
4. дедупликация `copy_message` broadcast;
5. crash/expired lease с запретом повторной доставки `unknown`.

Они намеренно запускаются только при наличии disposable PostgreSQL с `test` в
имени базы и явного `OUTBOX_INTEGRATION_CONFIRM=1`. В локальном окружении без
тестовой БД пять тестов корректно пропущены. Реальные Telegram-запросы также не
выполнялись.

Пример запуска:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/auction_test \
OUTBOX_INTEGRATION_CONFIRM=1 \
python -m unittest tests.integration.test_outbox_postgres -v
```

## Развёртывание

1. Сделать проверенный backup PostgreSQL.
2. Остановить все bot/userbot worker старой версии.
3. Развернуть код и запустить один экземпляр для последовательного применения
   миграции 006.
4. Проверить constraint метода и распределение состояний:

   ```sql
   SELECT conname, pg_get_constraintdef(oid)
   FROM pg_constraint
   WHERE conrelid = 'public.telegram_outbox'::regclass
     AND conname IN (
       'chk_telegram_outbox_method',
       'chk_telegram_outbox_delivery_state'
     );

   SELECT status, delivery_state, count(*)
   FROM public.telegram_outbox
   GROUP BY status, delivery_state
   ORDER BY status, delivery_state;
   ```

5. Выполнить `/outbox_status` из приватного чата администратора.
6. Запустить остальные экземпляры и наблюдать `failed/unknown`.
7. Не выполнять SQL-replay вручную: использовать `/outbox_retry` только для
   `confirmed_not_sent`. Для `unknown` сначала проверить факт доставки.

## План фазы 7

1. Разделить `auction/exchange.py` на submission, catalog, moderation и
   diagnostics.
2. Перенести оставшиеся winner/exchange SQL-операции из handlers и `db/db.py`
   в тематические repositories/services.
3. Расширить outbox на ручные winner-медиа, pin/unpin и другие критические
   Telegram-команды, где возможна идемпотентность.
4. Подключить disposable PostgreSQL в CI и добавить fake Telegram transport для
   воспроизводимых delivery/crash тестов.
5. Продолжить сокращение `db/db.py` и зафиксировать границы handler → service →
   repository автоматической архитектурной проверкой.
