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

## Продолжение: Linux backup policy

- Добавлен `scripts/backup_database.sh` для Compose PostgreSQL: custom dump,
  non-empty check, `pg_restore --list` verification и 14-дневная retention.
- `SERVER_DEPLOYMENT.md` содержит Linux cron policy и обязательство внешнего
  persistent backup storage. Windows не содержит `sh`, поэтому syntax check
  этого Linux-скрипта остаётся задачей Linux/Docker-enabled runner.
