# Рефакторинг — фаза 7

## Итог

Фаза 7 завершает архитектурный разбор исходного проекта и переводит его из
набора связанных монолитов в устанавливаемое приложение с явными границами,
управляемым lifecycle, проверяемыми миграциями и безопасной сборкой релиза.

При сверке использовались:

- исходный архив проекта `1.zip`;
- актуальный DDL-экспорт из pgAdmin `1.sql`;
- более полный исторический DDL-снимок;
- приватный per-table export только для структурной проверки валидатором.

Значения из приватных данных не выводились и не переносились в исходный код.
Дамп не является частью репозитория или релизных архивов.

## 1. Архитектура приложения

Запуск и владение ресурсами разделены следующим образом:

- `main.py` — минимальный process entrypoint;
- `bot/application.py` — composition root и lifecycle приложения;
- `bot/bootstrap/routers.py` — детерминированный порядок aiogram routers;
- `bot/bootstrap/workers.py` — состав и наблюдение фоновых workers;
- `bot/core/` — настройки, окружение, время и управление задачами;
- `bot/domain/` — правила предметной области без Telegram и PostgreSQL;
- `bot/services/` и `bot/features/` — прикладные сценарии;
- `bot/repositories/` — SQL и транзакционные операции;
- `bot/handlers/` — тонкие Telegram adapters;
- `bot/telegram/` — transport и transactional outbox;
- `userbot/` — отдельно запускаемый MTProto-компонент.

Архитектурные тесты запрещают SQL в `bot/handlers/**`, обратные импорты из
repositories/services в handlers, framework-типы в repositories и использование
корневых compatibility-модулей production-кодом.

## 2. Декомпозиция монолитов

Главный DB-модуль разделён по предметным областям:

- `db/users.py`, `db/admin.py`, `db/uid.py`;
- `db/auctions.py`, `db/cards.py`, `db/subscriptions.py`;
- `db/market.py`, `db/exchange.py`, `db/posts.py`;
- `db/core.py` и `db/pool.py` для общих primitives и lifecycle пула.

`db/db.py` сокращён до небольшого compatibility-фасада. Внутри приложения его
больше никто не импортирует.

Крупные Telegram-модули разделены на тематические routers и use cases:

- winner-flow — manual, exchange, print, resolution, presentation,
  notifications и feedback;
- exchange — submission, moderation, catalog и diagnostics;
- auctions — submission, guides и luxury administration;
- admin — panel, moderation, market и вспомогательные сценарии;
- UID, cards, comments, warnings, appeals и emojis получили собственные
  repositories/services.

Переходные фасады сохраняют прежние публичные импорты и порядок регистрации
handlers, поэтому внутреннее перемещение модулей не меняет пользовательские
команды и callback-контракты.

## 3. Конфигурация и lifecycle

Загрузка `.env` стала явной операцией entrypoint, а импорт модулей не меняет
окружение и не открывает соединения. Настройки типизированы и до запуска
проверяют обязательные Telegram, PostgreSQL и UID-параметры.

При ошибке polling или worker процесс завершается с ошибкой для supervisor.
При завершении отменяются дочерние задачи и закрываются workers, bot session и
DB pool. Ошибка cleanup не маскирует исходную причину сбоя.

Userbot получил отдельные application/entrypoint-модули. Пароль Telegram 2FA
вводится без отображения в терминале, а существующий session-файл остаётся
совместимым с прежним расположением.

## 4. PostgreSQL и миграции

Назначение SQL-источников зафиксировано в `database/README.md`:

- `database/pgadmin_schema.sql` — актуальный переданный DDL-снимок;
- `database/reference_schema.sql` — расширенный справочный DDL;
- `database/bootstrap.sql` — создание новой чистой базы;
- `database/migrations/001_*.sql` … `007_schema_alignment.sql` —
  последовательные неизменяемые обновления существующей базы.

Migration runner проверяет непрерывность номеров, обязательный набор файлов и
SHA-256 уже применённых миграций, а replicas сериализуются advisory lock. SQL
включён в wheel как package data и доступен после установки пакета.

Приватный per-table export проверяется `scripts/validate_private_dump.py` без
печати значений. Валидатор принимает только один целевой table на файл и форму
`INSERT ... VALUES`, отклоняет DDL, дополнительные statements, `INSERT SELECT`
и SQL-функции. Это защитный фильтр, а не замена изолированной staging-БД.

## 5. Надёжность и безопасность

- критические идемпотентные Telegram-команды используют transactional outbox;
- неопределённая доставка `unknown` не повторяется автоматически;
- content protection применяется ко всем поддержанным типам отправки и по
  умолчанию работает fail closed;
- административный секрет сравнивается constant-time и не может быть пустым;
- legacy HTTP bridge не стартует при импорте, подписывает timestamp, request-id
  и raw body через HMAC, отклоняет устаревшие и повторные запросы;
- `.env`, `*.session*`, дампы, CSV, `private/`, `var/` и виртуальное окружение
  исключаются из source release независимо от Git;
- release builder разрешает только документированные SQL-схемы и нумерованные
  миграции.

## 6. Зависимости и сборка

Проект оформлен через `pyproject.toml`, имеет консольные команды `auction-bot`
и `auction-userbot` и поддерживает только Python 3.14. Неиспользуемые runtime-
зависимости удалены; `pandas` оставлен только для maintenance tools.

Создаются два проверяемых артефакта:

- `dist/auction_telegram_bot-0.7.0-py3-none-any.whl`;
- `dist/auction-bot-source.zip`.

Оба архива проверяются на обязательные схемы и миграции, отсутствие закрытых
runtime-данных и корректность ZIP CRC.

## 7. Результаты проверок

На итоговом дереве выполнены:

- `pytest`: 206 тестов пройдено, 6 PostgreSQL integration tests пропущены;
- `ruff check .`: ошибок нет;
- `compileall`: все production, database и maintenance-модули компилируются;
- import smoke: 221 модуль импортирован без подключения к Telegram/PostgreSQL;
- `pip check`: конфликтов установленных зависимостей нет;
- граф production-импортов: top-level циклов нет;
- SQL в `bot/handlers/**`: 0;
- production-импорты `db.db`, корневых `config` и `fsm_states`: 0;
- приватный export: безопасно распознаны 45 table-файлов и 41 INSERT statement;
- wheel и source ZIP: обязательные ресурсы присутствуют, закрытые артефакты
  отсутствуют, CRC-проверка пройдена.

Пропущенные integration tests намеренно требуют явно подтверждённую disposable
PostgreSQL-базу. Реальные Telegram-запросы и восстановление приватного дампа в
production в рамках рефакторинга не выполнялись.

## 8. Порядок развёртывания

1. Сделать проверенный backup текущей PostgreSQL-базы.
2. Развернуть wheel или source release в staging с отдельными секретами.
3. Запустить integration tests только на явно подтверждённой disposable БД.
4. Проверить применение миграции 007 и основные bot/userbot сценарии.
5. Остановить старые workers и сначала запустить один новый экземпляр.
6. Проверить outbox, логи startup и административные smoke-сценарии.
7. Только после этого включать остальные replicas.

Старый дамп нельзя автоматически накатывать поверх рабочей базы: он остаётся
только источником для контролируемого восстановления в изолированном окружении.
