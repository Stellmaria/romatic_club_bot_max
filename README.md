# Romatic Club Bot Max

Проект поддерживает только Python 3.13.

## Быстрый запуск

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -m db.migrator
python main.py
```

Перед запуском создай пустую PostgreSQL-базу и заполни `.env`.

Полная инструкция по чистой установке, резервному копированию и переносу базы находится в [DATABASE_SETUP.md](DATABASE_SETUP.md).

## Server Supervisor

Для Ubuntu/Docker Compose доступен отдельный host-side Supervisor с Telegram-меню `/supervisor`, безопасным перезапуском только основного бота, обновлением `origin/main`, проверяемым backup PostgreSQL и rollback.

Установка и эксплуатация описаны в [docs/SERVER_SUPERVISOR_RUNBOOK.md](docs/SERVER_SUPERVISOR_RUNBOOK.md).

## Миграции

SQL-файлы лежат в `db/migrations`. Бот применяет их автоматически при старте, если `DB_AUTO_MIGRATE=1`.

Не редактируй уже применённые миграции. Для каждого следующего изменения создавай новый файл с очередным номером.
