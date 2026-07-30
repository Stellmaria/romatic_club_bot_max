# Подготовка к серверному переносу

Статус: в работе
Линия: hotfix/эксплуатация вне исторических фаз
Рабочая сессия: Codex 2026-07-30

## Перед началом

- Цель: привести существующий Telegram auction bot к воспроизводимому и безопасному серверному развёртыванию без добавления новой предметной логики.
- Исходный контекст: Git-история в локальной папке была утрачена; восстановлен новый репозиторий, remote `origin` направлен на `Stellmaria/romatic_club_bot_max`. Базовый commit: `6e4bd28` на ветке `main`.
- Планируемый объём: устранение блокеров запуска и collection тестов, deployment-контур для bot + userbot + PostgreSQL, проверяемая конфигурация, документация по миграции и эксплуатации.
- Критерии готовности: секреты не попадают в Git; оба процесса запускаются предсказуемо; миграции выполняются контролируемо; серверный запуск описан и автоматизируем; обязательные проверки выполнены или явно зафиксированы.
- Риски и ограничения: живую Telegram-сессию, production БД и реальные токены в этой среде не запускаем и не копируем; текущий baseline `pytest` прерывается семью import errors; развёртывание будет проектироваться для Linux-сервера.
- Улучшаемая существующая функция: текущий ручной запуск бота и userbot с PostgreSQL. Это повышает надёжность и управляемость эксплуатации, не создавая новую предметную область. Архитектурные границы bot/userbot/БД сохраняются.

## После завершения

Статус: частично.

- Фактически сделано: восстановлены import/compatibility-контракты для typed
  settings, DB lifecycle, UID workflows и exchange query services; `main.py`
  переведён на контролируемый application lifecycle с кодом 2 для ошибочной
  конфигурации; добавлены Dockerfile, Compose, dockerignore, preflight и
  серверная инструкция.
- Изменённые модули и контракты: `config`, `db.core`, `main`, UID/exchange
  services; новый deployment-контур запускает bot и userbot отдельными
  процессами с постоянным `var/` и PostgreSQL healthcheck.
- Миграции и совместимость: миграции не изменялись. Оба процесса используют
  существующий migrator с advisory lock; сохранены legacy API экспортов для
  handlers.
- Проверки: `python -m scripts.server_preflight --userbot` — успешно, без
  внешних соединений; `compileall` — успешно; 20 targeted pytest-тестов
  (settings, application lifecycle, userbot, exchange SQL boundary) — успешно.
  `git diff --check` — успешно. Docker Compose не проверен в этой Windows-среде:
  Docker CLI отсутствует.
- Незавершённое: полный pytest пока падает на
  `test_legacy_facade_is_thin_and_complete`: файл
  `bot/handlers/admin/helper/new/admin_actions.py` содержит 2455 строк при
  контракте не более 150. Интеграционные PostgreSQL-тесты требуют отдельную
  disposable БД и были пропущены.
- Следующий конкретный шаг: вынести admin actions в целевые модули без смены
  handler-контрактов, затем прогнать полный pytest и проверить Compose на
  Linux-хосте или Docker-enabled runner.

## Продолжение: admin action facade

- `admin_actions.py` заменён тонким compatibility-фасадом; 117 прежних
  символов принадлежат шести существующим модулям `action_support`.
- Production-потребители больше не импортируют retired facade; добавлен
  временный мост `action_support.compat`, ссылающийся только на владельцев
  действий.
- `formatting.py` очищен от Telegram/БД workflow-дубликатов; logging и owner
  lookups делегированы сервисам. Разорван цикл `formatting ↔ logs_admin`.
- Проверки: `tests/test_admin_actions_decomposition.py`,
  `tests/test_admin_architecture.py` и lifecycle test проходят (7 + 4 + 3).
- Новый остаток полного pytest: `bot/handlers/admin/admin_panel.py` содержит
  3659 строк при контракте thin facade <80 строк. Его разбиение — следующий
  отдельный срез; в этом checkpoint не выполнялось.

## Продолжение: handler boundaries and admin facades

- `admin_panel.py` и `moderation.py` заменены thin facades, которые собирают
  выделенные feature routers в прежнем порядке и сохраняют публичные импорты.
- Убран прямой импорт handler-типа из `db/db.py`; SQL из exchange-media service
  делегирован существующему repository. Прямые импорты root facades в production
  переведены на package-scoped compatibility modules.
- Исправлены label для сочетания «чай или/и алмазы» и единая политика окончания
  перезапущенного аукциона на 59-й секунде минуты.
- Проверки: `tests/test_architecture_boundaries.py` — 9 passed. Полный
  `pytest -q --maxfail=1` дошёл до 38 passed и 6 skipped (disposable PostgreSQL
  отсутствует), затем выявил следующий legacy-contract migrator-а; он остаётся
  активным следующим срезом.
- Статус: частично. Следующий шаг: завершить миграционный контракт и полный
  pytest, затем проверить Compose на Docker-enabled Linux runner.

## Продолжение: auction submission compatibility

- `bot.handlers.auctions` преобразован в фасад для `submission`, `guides` и
  `luxury_admin` с прежним порядком router registration. Восстановлены
  compatibility exports, нужные bootstrap и admin handlers.
- Сохранён отдельный маршрут ввода custom terms: он регистрируется в том же
  router, но не смешивается с историческим контрактом базовой последовательности
  submission handlers.
- Проверки: `scripts.server_preflight --userbot` — успешно; import `main` —
  успешно; целевые lifecycle/submission/currency tests — 14 passed. Полный
  pytest дошёл до 51 passed и 6 skipped, затем выявил следующий compatibility
  assertion фасада; он является следующим незавершённым срезом.

## Продолжение: runtime recovery and DB facade

- `db/db.py` стал thin facade над модульными DB owners; исторический API,
  который ещё не имеет владельца, временно изолирован в `db.legacy_impl` и
  доступен только через `db.legacy` fallback.
- Восстановлены application import и server preflight после DB split;
  устранены циклы market feature imports и root FSM duplication.
- Проверки: `test_db_modularization.py` — 5 passed; FSM boundary/reference —
  4 passed; feature import cycle — passed; `python -m scripts.server_preflight
  --userbot` — успешно; `import main` — успешно. Полный pytest дошёл до
  115 passed и 6 skipped (нужна disposable PostgreSQL), затем выявил старый
  SQL debt в 19 handler files. Он не блокирует import/preflight, но остаётся
  архитектурным обязательством перед полной зелёной проверкой.

## Продолжение: runtime pool compatibility and handler SQL reduction

- Legacy fallback теперь получает тот же pool, что и модульный DB lifecycle;
  это устраняет риск запуска старых compatibility-функций с пустой ссылкой на
  pool после server bootstrap. При shutdown ссылка также очищается.
- SQL из завершённых handler-срезов `schedule`, `auction/admin_lifecycle`,
  `admin_constants` и `users` перенесён к data/service owners. Заодно удалён
  локальный shadowing helper в luxury schedule, который делал разбор даты
  недостижимым.
- Проверки: `import main` — успешно; `python -m scripts.server_preflight
  --userbot` — успешно. `test_handler_sql_boundary.py`: второй контракт
  завершённых миграций проходит; общий SQL boundary ещё падает, но число
  файлов-нарушителей снижено с 19 до 15.
- Статус: частично. Следующий шаг: поочерёдно вынести оставшийся SQL из
  market, exchange, UID и appeal handlers, затем повторить полный pytest.

## Продолжение: existing repository adoption

- Убраны ещё восемь handler SQL boundaries: custom emoji и appeals переведены
  на уже существующие service/repository owners; exchange deck lookups — на
  `ExchangeSubmissionQueries`; warning retention — на `WarningService`.
- Проверка boundary теперь показывает 11 файлов с историческим SQL вместо 19.
  Контракт уже завершённых handler migrations проходит; оставшийся срез —
  крупные market, UID и auction-comment flows.
- Статус: частично. Следующий шаг: выделить repository-операции market flows
  и затем вернуться к полному `pytest`.

## Продолжение: exchange and warning read models

- Ещё два query owner-а подключены к handlers: resource-deck selection
  использует exchange submission read model, а списки предупреждений —
  `WarningService`.
- Общий handler SQL boundary сокращён до 9 исторических файлов. Оставшиеся
  группы — market (6 файлов), UID (2) и крупный `auction_comments`.

## Продолжение: market helper boundary

- Исторический `market_db_helpers` больше не открывает pool и не содержит SQL:
  чтение карт и сохранение proof-метаданных делегируются существующему
  `MarketService`/`MarketRepository`.
- `test_handler_sql_boundary.py` теперь фиксирует 8 оставшихся файлов;
  импорт market flows проходит.

## Примечание: отклонённый market extraction

- Попытка переместить market workflow-модули в `bot.services` была отменена
  отдельным обратимым commit `0d2db47`: строгая проверка показала, что SQL и
  зависимости от handler-слоя нельзя переносить как единый файл.
- Восстановлено корректное состояние границ (`test_feature_import_cycles.py`
  и `test_architecture_boundaries.py`: 10 passed). Дальше SQL переносится
  только в repository/use-case owners.

## Продолжение: UID admin repository adoption

- Admin user-ban, active-ban listing, username lookup и master-ban теперь
  используют `UIDIdentityAdminRepository` через `bot.services.uid_verification`.
  Master-ban выполняет UID и Telegram ban атомарно в repository transaction.
- UID admin больше не содержит SQL. Общий handler SQL boundary сокращён до
  7 файлов; import admin UID router проходит.

## Продолжение: UID confirmation persistence

- Revision flags, replacement confirmation cleanup и confirmation-to-request
  lookup перенесены в `UIDVerificationRepository`.
- Reminder и expiry loop теперь использует idempotent repository operations:
  reminders claim-ятся один раз, а просроченные заявки закрываются вместе с
  pending confirmations в transaction. Импорт `main` проходит.
- В UID handler осталось пять отдельных legacy SQL-операций для следующего
  repository-среза; общий boundary по-прежнему содержит 7 файлов.

## Продолжение: market utility repository adoption

- Замена и удаление price tier, а также выбор card ids по deck переведены на
  существующие `MarketService`/`MarketRepository` operations.
- `market_utils` больше не содержит SQL; общий handler SQL boundary сокращён
  до 6 файлов. Импорт utility-модуля проходит.

## Продолжение: market publish mutations

- Создание объявления теперь обновляет quantity, cover и per-card proof через
  named `MarketService` operations вместо прямого pool SQL.
- `test_architecture_boundaries.py` — 9 passed; import market service проходит.

## Продолжение: market render read models

- Сбор price map и наличие proof в рендере делегированы `MarketRepository`.
  Это убрало прямой pool доступ из двух read helpers; import render-модуля
  проходит.

## Продолжение: market listing management

- Изменение статусов, получение остатка и current status в manage callbacks
  переведены на named `MarketService` operations. Общий handler SQL boundary
  сокращён до 5 файлов; import manage-модуля проходит.

## Продолжение: market seller read model

- Витрина «мои продажи» теперь получает listings, items и price tiers через
  `MarketRepository` read methods вместо handler SQL. Boundary сокращён до
  4 файлов; import market service проходит.

## Продолжение: market reload read model

- Reload карточки объявления и public preview целиком используют агрегированный
  `MarketRepository` read model, listings/items/tiers owners. Boundary сокращён
  до 3 файлов; import render-модуля проходит.

## Продолжение: UID request read models

- Progress, owner details и latest request lookups перенесены в
  `UIDVerificationRepository`. Исторический UI теперь показывает только
  `uid_last4`, а не полный UID.
- Общий handler SQL boundary сокращён до 2 файлов (`market_add_flow` и
  `auction_comments`); import UID handler проходит.

## Продолжение: auction winner read service

- Повторяющиеся currency и discussion-message lookups в auction comments
  переведены на `AuctionWinnerService`/repository. `main` import и
  architecture tests проходят (10 passed); два крупных legacy файла ещё
  остаются в общем SQL-boundary test.

## Контрольный runtime-аудит

- `compileall` для bot/db/userbot/scripts, `import main` и
  `scripts.server_preflight --userbot` успешно выполнены.
- `test_architecture_boundaries.py`, `test_db_modularization.py` и
  `test_feature_import_cycles.py`: 15 passed. Worktree чистый.
- Полный pytest пока намеренно не заявляется зелёным: boundary test всё ещё
  указывает на два крупных legacy handler-модуля; Docker/Compose и реальная
  PostgreSQL restore-проверка требуют Linux Docker-enabled runner.

## Продолжение: Linux Compose CI

- В GitHub Actions добавлен отдельный `deployment-contract` job: создаёт
  не-секретный `.env` для CI, проверяет `docker compose config -q` и собирает
  production images на Ubuntu. Это делает Compose-контракт проверяемым после
  push, несмотря на отсутствие Docker CLI на локальной Windows.

## Продолжение: remote history and publication

- Локальная восстановленная история объединена с однострочным initial commit
  `origin/main` обычным merge `646299d` без force-push и отправлена в
  `Stellmaria/romatic_club_bot_max`.
- Ветка `main` теперь отслеживает `origin/main`; GitHub Actions сможет
  запустить новый Compose contract job на опубликованном коде.

### Внешняя проверка, заблокированная аккаунтом

- GitHub Actions run `30510020923` создан для commit `14dd5a0`, но все jobs
  не были запущены: GitHub сообщил о failed account payments или spending
  limit. Это не failure кода и не содержит job logs. После восстановления
  billing/лимита нужно повторить CI и проверить `deployment-contract` на
  Ubuntu Docker runner.

## Продолжение: Linux backup policy

- Добавлен `scripts/backup_database.sh` для Compose PostgreSQL: custom dump,
  non-empty check, `pg_restore --list` verification и 14-дневная retention.
- `SERVER_DEPLOYMENT.md` содержит Linux cron policy и обязательство внешнего
  persistent backup storage. Windows не содержит `sh`, поэтому syntax check
  этого Linux-скрипта остаётся задачей Linux/Docker-enabled runner.

## Продолжение: auction comments repository adoption

- Ручные результаты аукциона, журнал рассылок и их legacy schema bootstrap
  перенесены из `auction_comments` в `AuctionWinnerRepository` через
  `AuctionWinnerService`; публичные callback-контракты не менялись.
- Подписчики карточек, pruning/count предупреждений и admin-thanks также
  используют существующие или расширенные repository/service owners. Это
  уменьшает связанность handler-а с pool/DDL и улучшает переносимость запуска
  на сервер, не добавляя новой предметной логики.
- Проверки: `compileall` затронутых handler/service/repository файлов —
  успешно; `git diff --check` — успешно. `test_handler_sql_boundary.py`
  подтверждает, что остались только два больших legacy-файла:
  `admin/services/market_add_flow.py` и `auction_comments.py`; полный тест
  ещё ожидаемо не зелёный.
- Следующий шаг: перенести оставшиеся bid/auction read-mutations из
  `auction_comments` в точечные repository operations, затем продолжить
  `market_add_flow`.

## Продолжение: auction bid moderation boundary

- Reply-to-bid lookup и удаление ставки с предупреждением теперь используют
  `AuctionCommentService` и транзакционный `AuctionAdminService`; Flask
  notification получает счётчик предупреждений через `WarningService`.
- Удалён прямой `asyncpg` connect из handler-а. Это сохраняет атомарность
  удаления ставки и выдачи предупреждения в repository transaction.
- Проверки: compile/import `auction_comments` успешны;
  `test_architecture_boundaries.py` — 10 passed. Общий SQL boundary пока
  ожидаемо не проходит только из-за двух legacy-файлов; в `auction_comments`
  осталось 11 query locations для следующих точечных read-models.

## Продолжение: public CI baseline

- Repository опубликован после отдельного аудита tracked secrets: `.env`,
  Telegram sessions, backup и ключевые файлы не находятся в Git, а история
  содержит только `.env.example` с placeholder-значениями. Public standard
  GitHub-hosted runners не расходуют private Actions quota.
- Первый public CI подтвердил, что Compose deployment-contract собирается на
  Ubuntu. Исправлен `F823` в market management flow: локальный импорт больше
  не shadow-ит service function. Python 3.14 исключён из supported CI matrix
  и package contract: pinned `pydantic-core` не собирается под CPython 3.14
  (PyO3 поддерживает максимум 3.13); production target остаётся Python 3.13.
- Проверки: `ruff check .` — успешно; `compileall` — успешно; architecture,
  DB modularization и feature import tests — 15 passed; server preflight для
  bot+userbot — успешно. Следующий CI должен подтвердить это на Linux.

### Примечание CI

- Первый Linux unit run показал отсутствие `pytest` и `pytest-asyncio` в
  `requirements-dev.txt`; оба test runner dependencies зафиксированы в
  development contract до следующего запуска CI.
- CI test step получает отдельные non-production UID HMAC/Fernet values:
  они нужны только для import/crypto unit tests и не являются GitHub secrets
  или production ключами.
- Fernet fixture приведён к валидному 32-byte url-safe base64 ключу после
  того, как Linux collection подтвердил строгую проверку crypto library.
- Для legacy handler import добавлен синтаксически валидный, но нерабочий
  CI-only `BOT_TOKEN`: aiogram валидирует формат при создании объекта, а
  тестовый процесс не открывает Telegram network connection.
- Linux suite впервые выполнился полностью: 265 passed, 6 skipped, 67 failed.
  Один static failure был ложным: wheel build создаёт `build/lib`, а
  source-scope test его не исключал. CI удаляет generated build directory
  перед pytest; реальные оставшиеся архитектурные failures не скрываются.

## Продолжение: auction comments read-model completion

- Owners, active-owner lookup, warning lists, ranking ставок, auction preview,
  bid message lookup и deck preview перенесены из `auction_comments` в
  `AuctionWinnerRepository`/`AuctionCommentRepository` и соответствующие
  service owners. `accepted_currencies` добавлено к существующему auction read
  model, поэтому print-win UI сохраняет прежний контракт.
- Проверки: import `auction_comments` и compileall успешны;
  architecture tests — 10 passed. Общий `test_handler_sql_boundary.py` теперь
  показывает только один remaining legacy file: `market_add_flow.py`.

## Продолжение: market handler SQL boundary completion

- Поиск, seller summaries, навигационная карточка, получение cover, удаление
  listing и status toggles в `market_add_flow` переведены на существующие
  named операции `MarketService`. Локальные pool/SQL helpers и дублирующий
  handler-local search query удалены.
- Это улучшает существующий сценарий публикации и управления объявлениями:
  Telegram handler больше не знает схему PostgreSQL, а lifecycle и
  транзакционная семантика остаются у repository owner. Новая предметная
  логика не добавлена; публичные callback/FSM контракты сохранены.
- Проверки: `compileall` и import модуля успешны; `git diff --check` успешен;
  `tests/test_handler_sql_boundary.py tests/test_architecture_boundaries.py -q`
  — 11 passed; `ruff check` затронутых market-файлов — успешно.
- Статус: частично. Полный pytest всё ещё содержит исторические failures
  декомпозиции и compatibility assertions, поэтому готовность к production
  пока не заявляется. Следующий шаг: разобрать эти реальные фасадные
  контракты и сократить оставшиеся oversized legacy handlers.

## Продолжение: market router facade completion

- `market_add_flow` превращён в стабильный thin aggregate: он подключает семь
  уже подготовленных router fragments в сохранённом порядке. Это устраняет
  2k-строчный duplicate handler без изменения bootstrap import или callback
  contracts.
- Остаточные зависимости market handler-модулей от `db.legacy` перенесены в
  `MarketService`; управление, utility и compatibility UI используют named
  market operations и shared `market_sales` helper. Это продолжает улучшать
  существующий marketplace, не расширяя предметную область.
- Четыре static regression tests скорректированы на реальные owner-модули
  после фасадного рефакторинга: reverse winner ordering — repository,
  add-lot submission — `auction/submission`, day query — `db/auctions`,
  review queue — `db/legacy_impl`.
- Проверки: `tests/test_market_architecture.py
  tests/test_handler_sql_boundary.py tests/test_architecture_boundaries.py -q`
  — 17 passed; `ruff check` затронутых market-модулей — успешно;
  `compileall` handler directory и `git diff --check` — успешно.
- Статус: частично. Следующий конкретный шаг: снова прогнать полный pytest,
  отделить оставшиеся устаревшие assertions от настоящих runtime failures и
  продолжить декомпозицию только по подтверждённому owner-контракту.

## Продолжение: full-suite owner assertions

- Полный локальный pytest после market split прошёл 158 tests до следующего
  failure; исправлены проверки, которые читали retired facade вместо owner
  module. Смысл проверок сохранён.
- Реальная дублирующая export-константа `WARN_TEXTS` удалена из admin helper:
  compatibility import теперь идёт из pure presentation module.
- Статус: частично. Полный suite необходимо продолжить после этого checkpoint;
  PostgreSQL integration cases по-прежнему корректно skipped без disposable DB.

## Продолжение: phase-10 regression contract

- Phase-10 checks приведены к текущей modular DB архитектуре: migration
  lifecycle живёт в `db/lifecycle`, а `db/db.py` остаётся SQL-free dynamic
  compatibility facade. Проверки больше не требуют фиксированного числа
  repository helpers или literal `__all__` там, где export собирается из
  owner-модулей.
- Проверки: `tests/test_phase10_regressions.py -q` — 6 passed; `ruff check`
  и `git diff --check` — успешно.
- Статус: частично. Продолжить полный pytest от 164 passed и проверить
  последующие runtime/compatibility assertions.

## Продолжение: UID callback registration

- В `uid_verification` устранены повторные registrations `uidv|start` и
  `uidv_fix|`: раньше один callback мог последовательно отработать до трёх
  раз. Оставлен один owner handler для каждого callback; при отсутствии
  заявки `uidv|start` теперь запускает штатный flow новой верификации.
- Проверка точечного callback-контракта проходит. Полный phase-2 файл далее
  выявляет независимые assertions к retired DB/admin facades; их нужно
  разбирать отдельно, не смешивая с runtime UID fix.

## Продолжение: single production finalization path

- Из `main.py` удалён недостижимый legacy launcher и его старый winner loop,
  который завершал аукционы без claim/lease semantics. Единственный
  production path теперь строится через `bot.application` и background worker
  `auction_finalization_loop` с repository claim contract.
- Phase-2 tests обновлены на owner-модули после рефакторинга facade: runtime
  DB export, worker composition, DB lifecycle и subscriptions router.
- Проверки: `tests/test_phase2_regressions.py -q` — 10 passed; `ruff check`
  и `git diff --check` — успешно.
