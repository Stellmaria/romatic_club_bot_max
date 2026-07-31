# Romatic Club Server Supervisor

Server Supervisor возвращает владельцу управление основным ботом и userbot из Telegram после переноса на Ubuntu/Docker Compose.

## Что умеет

- показывает состояние host-side Supervisor, основного bot, userbot и Git checkout;
- отдельно перезапускает Compose service `bot`;
- отдельно перезапускает Compose service `userbot`;
- обновляет `origin/main` через проверяемый deploy gate;
- перед обновлением создаёт PostgreSQL custom-format dump и проверяет его через `pg_restore -l`;
- пересобирает `bot`, `userbot` и `supervisor-proxy`;
- выполняет smoke-проверку PostgreSQL и конфигурации;
- при ошибке возвращает код и контейнеры к предыдущему commit;
- позволяет откатиться на предыдущий сохранённый commit.

## Доступ

- системное меню доступно только Telegram ID из `ADMINS_OWNERS`;
- обычные администраторы не видят кнопку `🖥 Система` в админ-панели;
- команды `/system`, `/supervisor`, `/restart` и `/restart_userbot`, а также все callback системного меню повторно проверяют `ADMINS_OWNERS`;
- наличие пользователя в обычном списке `ADMINS` не даёт доступ к Supervisor.

## Безопасность

- bot и proxy не получают Docker socket;
- proxy не получает checkout, systemd, `.env` или host port;
- host runtime работает непривилегированным пользователем;
- API доступен через Unix socket и bearer token;
- поддерживаются только фиксированные действия status, logs, restart bot, restart userbot, update и rollback;
- произвольная shell-команда отсутствует.

## Установка

Рекомендуемый checkout:

```bash
sudo mkdir -p /srv/romatic-club-max
sudo chown velvet:velvet /srv/romatic-club-max
```

После клонирования репозитория и заполнения `/srv/romatic-club-max/.env`:

```bash
cd /srv/romatic-club-max
sudo bash deploy/server/install-server-supervisor.sh
```

Для другого каталога или пользователя:

```bash
sudo env \
  ROMATIC_APP_DIR=/srv/your-path \
  ROMATIC_SERVICE_USER=your-user \
  bash deploy/server/install-server-supervisor.sh
```

## Telegram

В личном чате владельца:

```text
/supervisor
```

Меню содержит:

- обновить статус;
- перезапустить основной bot;
- перезапустить userbot;
- обновить `main`;
- посмотреть логи;
- откатить предыдущий deploy.

Отдельные команды:

```text
/restart
/restart_userbot
```

## Ручной deploy

```bash
cd /srv/romatic-club-max
ROMATIC_APP_DIR=/srv/romatic-club-max \
ROMATIC_ENV_FILE=.env \
ROMATIC_COMPOSE_FILE=compose.yaml \
bash deploy/server/deploy.sh
```

## Проверка

```bash
sudo systemctl is-active romatic-server-supervisor.service
sudo systemctl status romatic-server-supervisor.service --no-pager

cd /srv/romatic-club-max
docker compose --env-file .env -f compose.yaml ps
```

При restart основного bot должен измениться только `StartedAt` контейнера `bot`.
При restart userbot должен измениться только `StartedAt` контейнера `userbot`.
PostgreSQL и второй процесс в каждом случае должны сохранить прежнее время запуска.
