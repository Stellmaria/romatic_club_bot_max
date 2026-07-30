# Auction Bot

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

## Миграции

SQL-файлы лежат в `db/migrations`. Бот применяет их автоматически при старте, если `DB_AUTO_MIGRATE=1`.

Не редактируй уже применённые миграции. Для каждого следующего изменения создавай новый файл с очередным номером.
