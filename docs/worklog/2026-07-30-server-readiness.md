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
