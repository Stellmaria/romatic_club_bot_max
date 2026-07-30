# Серверное развёртывание

Этот контур запускает три независимых процесса: PostgreSQL, Telegram bot и
Telethon userbot. Оба Python-процесса применяют миграции при старте; мигратор
берёт PostgreSQL advisory lock, поэтому параллельный старт безопасен.

## До миграции

1. На старой машине остановить оба процесса бота, чтобы получить согласованный
   снимок базы.
2. Сделать custom-format backup PostgreSQL: `scripts/backup_database.ps1` или
   эквивалентный `pg_dump --format=custom`.
3. Сохранить отдельно и безопасно: `.env`, `userbot_session.session` и значения
   `UID_HASH_KEY`/`UID_ENC_KEY`. Новые UID-ключи не расшифруют исторические UID.
4. Не копировать эти файлы в Git и не отправлять в Docker build context.

## Первый запуск на Linux

На сервере нужны Docker Engine и Compose plugin. В каталоге репозитория:

```bash
cp .env.example .env
mkdir -p var backups
chmod 700 var backups
```

Заполни `.env`. Для Compose PostgreSQL укажи `POSTGRES_DB`, `POSTGRES_USER` и
`POSTGRES_PASSWORD`; `DATABASE_URL` должен использовать хост `postgres`, а не
`localhost`, например `postgresql://auction_bot:...@postgres:5432/auction_bot`.
Укажи `RUNTIME_DIR=/app/var` и `USERBOT_SESSION=/app/var/userbot_session`.

Перед запуском проверка не раскрывает значения и не открывает сетевые соединения:

```bash
docker compose run --rm bot python -m scripts.server_preflight --userbot
```

После успешной проверки поднять только PostgreSQL:

```bash
docker compose up -d postgres
```

Восстановить custom dump в контейнер PostgreSQL, затем проверить миграции:

```bash
docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backups/auction_bot.dump
docker compose run --rm bot python -m db.migrator
```

Наконец запустить оба процесса и наблюдать логи:

```bash
docker compose up -d --build bot userbot
docker compose logs -f --tail=100 bot userbot
```

## Откат и эксплуатация

- Перед любым обновлением сделай новый custom dump и проверь, что он читается
  `pg_restore --list`.
- Для отката кода: `git checkout <known-good-commit>` и `docker compose up -d --build`.
- Миграции не откатываются автоматически. Если новая миграция уже применилась,
  восстанавливай проверенный дамп только при остановленных bot и userbot.
- Telegram session хранится в `var/`; её утрата потребует повторной
  интерактивной авторизации userbot. Не переносить session через Git.

## Резервные копии

На Linux используй `scripts/backup_database.sh`: он создаёт custom dump,
проверяет его через `pg_restore --list` и удаляет файлы старше 14 дней. Каталог
`backups/` должен находиться на отдельном persistent disk или синхронизироваться
в защищённое внешнее хранилище: один диск сервера не является backup-стратегией.

Ежедневный запуск в 03:15 UTC через crontab:

```cron
15 3 * * * cd /opt/romatic-club-bot && BACKUP_KEEP_DAYS=14 ./scripts/backup_database.sh >> /var/log/romatic-club-backup.log 2>&1
```

Не включай одновременно bot/userbot с восстановлением. После restore сначала
запусти preflight и мигратор, затем процессы ботов.
