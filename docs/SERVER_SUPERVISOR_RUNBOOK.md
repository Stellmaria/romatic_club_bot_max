# Romatic Club Server Supervisor

Server Supervisor предоставляет владельцу фиксированные операции управления production-сервисами после переноса на Ubuntu и Docker Compose.

## Возможности

- состояние host-side Supervisor, основного bot, userbot и Git checkout;
- раздельный restart `bot` и `userbot`;
- обновление `origin/main` через deploy gate;
- PostgreSQL backup и проверка через `pg_restore -l`;
- пересборка production images и smoke-проверка;
- rollback на предыдущий сохранённый commit.

Произвольных shell-команд в API нет.

## Граница доступа

Telegram-системное меню доступно только ID из `ADMINS_OWNERS`. Обычный `ADMINS` не даёт доступа к Supervisor.

Control plane состоит из трёх частей:

1. host-side `romatic-server-supervisor.service`;
2. минимальный `supervisor-proxy` без Docker socket и checkout;
3. основной контейнер `bot` либо фиксированный Hermes control adapter.

`userbot`, PostgreSQL и coder-контейнеры не являются control clients.

## Сети

Основной bot обращается к proxy только через internal-сеть:

```text
romatic-supervisor-control
```

Hermes operator gateway обращается к тому же proxy через отдельную internal-сеть:

```text
hermes-supervisor-control
```

`supervisor-proxy` не подключается к общей Compose-сети. `userbot` подключён только к default-сети и не может разрешить или открыть `supervisor-proxy:8765`.

Проверка:

```bash
docker network inspect romatic-supervisor-control \
  --format '{{.Internal}} {{json .Containers}}'

docker network inspect hermes-supervisor-control \
  --format '{{.Internal}} {{json .Containers}}'
```

В первой сети допустимы только основной bot и `supervisor-proxy`. Во второй допустимы `supervisor-proxy` и фиксированный Hermes gateway. `userbot`, PostgreSQL и coder-контейнеры там отсутствуют.

## Секреты

Supervisor token не хранится как рабочее значение в общем `.env`.

Installer создаёт:

```text
server-data/runtime/supervisor/token
server-data/runtime/supervisor/supervisor.env
```

Оба файла имеют режим `0640` и группу `romatic-supervisor` с GID `10001` по умолчанию.

- `token` монтируется Docker secret только в основной `bot`;
- `supervisor.env` загружается только host-side systemd service;
- `userbot` получает `SUPERVISOR_ENABLED=false`, пустые `SUPERVISOR_TOKEN`, `SUPERVISOR_TOKEN_FILE` и `SUPERVISOR_BASE_URL`;
- proxy токен не получает и только переносит TCP в Unix socket.

## Unix socket

Host API слушает:

```text
server-data/runtime/supervisor/romatic-server-supervisor.sock
```

Требования:

- mode `0660`;
- group `romatic-supervisor`;
- parent directory mode `0770`;
- systemd `UMask=0007`;
- отсутствует доступ для `other`.

Проверка:

```bash
stat -c '%a %U %G %n' \
  server-data/runtime/supervisor/romatic-server-supervisor.sock
```

Ожидаемый mode: `660`.

## Аудит, request ID и повторные запросы

Каждый API-запрос получает `X-Request-ID` и `X-Actor`. Новый bot client создаёт уникальный request ID и использует один и тот же ID при сетевом retry.

Host Supervisor:

- возвращает request ID в заголовке и JSON;
- сохраняет actor, request ID, method, path, outcome, HTTP status и operation ID;
- повторный POST с тем же request ID возвращает сохранённый ответ и не запускает вторую операцию;
- допускает только allowlist routes;
- ограничивает mutating operations по actor, по умолчанию 6 запросов за 60 секунд;
- одновременно выполняет не более одной системной операции.

Audit log:

```text
server-data/runtime/supervisor/operations.log
```

State:

```text
server-data/runtime/supervisor/state.json
```

## Threat model

### Компрометация userbot

Ожидаемый результат: атакующий не получает Supervisor token и не имеет сетевого маршрута к proxy. Даже знание имени сервиса не позволяет открыть TCP-соединение.

### Компрометация основного bot

Bot является разрешённым control client и поэтому считается более доверенным. Риск ограничивается:

- owner-only Telegram authorization;
- фиксированным allowlist API;
- request ID и idempotency;
- actor rate limit;
- отсутствием Docker socket в контейнере;
- host audit trail.

Компрометация bot остаётся критичным инцидентом и требует немедленной ротации токена и отключения control plane.

### Компрометация proxy

Proxy не имеет token, checkout, Docker socket, systemd или host port. Он видит Unix socket через group-limited volume и доступен только в двух internal-сетях.

### Компрометация Hermes/coder

Hermes использует фиксированный gateway. Coder не получает Supervisor token, Docker socket и production checkout. Merge, deploy, restart и rollback не выполняются coder-агентом автоматически.

### Повтор или дублирование запроса

Одинаковый request ID не создаёт повторную операцию. Новый request ID проходит rate limit и проверку единственной активной операции.

## Установка

```bash
cd /srv/romatic-club
sudo env \
  ROMATIC_APP_DIR=/srv/romatic-club \
  ROMATIC_ENV_FILE=/srv/romatic-club/.env \
  ROMATIC_COMPOSE_FILE=/srv/romatic-club/compose.yaml \
  ROMATIC_SERVICE_USER=velvet \
  ROMATIC_SUPERVISOR_GROUP=romatic-supervisor \
  ROMATIC_SUPERVISOR_GID=10001 \
  bash deploy/server/install-server-supervisor.sh
```

Installer:

- создаёт dedicated group;
- генерирует file-backed token;
- рендерит systemd units;
- запускает Supervisor и Compose;
- проверяет socket mode/group;
- проверяет пустые credentials в `userbot`;
- негативно проверяет, что `userbot` не достигает proxy.

После установки Supervisor общий Hermes gateway устанавливается из checkout Velvet.

## Обновление после изменения Supervisor

Host-side Supervisor — долгоживущий Python-процесс. Он вычисляет Git blob-хеш
собственного `scripts/server_supervisor.py` при старте и перед запуском deploy
сравнивает его с файлом в целевом commit. Поэтому уже запущенный старый
Supervisor не может сам себя обновить: он отклонит update до вызова
`deploy/server/deploy.sh`.

После merge этой защиты требуется ровно один доверенный restart на host:

```bash
sudo systemctl restart romatic-server-supervisor.service
```

Затем update можно повторить обычным разрешённым способом. Этот restart не
выполняется кодом приложения или API и не является частью production-изменений
данной ветки; его должен выполнить оператор с доступом к host/systemd.

## Ротация токена

1. Остановить основной bot и host Supervisor:

```bash
cd /srv/romatic-club
docker compose --env-file .env -f compose.yaml stop bot
sudo systemctl stop romatic-server-supervisor.service
```

2. Создать новый token без вывода в терминал:

```bash
sudo python3 - <<'PY'
import secrets
from pathlib import Path

root = Path('/srv/romatic-club/server-data/runtime/supervisor')
token = secrets.token_urlsafe(48)
(root / 'token').write_text(token + '\n', encoding='utf-8')
(root / 'supervisor.env').write_text(
    'SUPERVISOR_TOKEN=' + token + '\n'
    'SUPERVISOR_COMMAND_TIMEOUT_SECONDS=1800\n'
    'SUPERVISOR_RATE_LIMIT=6\n'
    'SUPERVISOR_RATE_WINDOW_SECONDS=60\n',
    encoding='utf-8',
)
PY
sudo chown velvet:romatic-supervisor \
  server-data/runtime/supervisor/token \
  server-data/runtime/supervisor/supervisor.env
sudo chmod 0640 \
  server-data/runtime/supervisor/token \
  server-data/runtime/supervisor/supervisor.env
```

3. Перезапустить host service и пересоздать bot, чтобы Docker secret был перечитан:

```bash
sudo systemctl restart romatic-server-supervisor.service
docker compose --env-file .env -f compose.yaml up -d --force-recreate bot
```

4. Проверить `/supervisor` и audit log.

## Аварийное отключение control plane

При подозрении на компрометацию:

```bash
cd /srv/romatic-club
sudo systemctl disable --now romatic-server-supervisor.service
docker compose --env-file .env -f compose.yaml stop supervisor-proxy
chmod 000 server-data/runtime/supervisor/token
```

После этого Telegram restart/update/rollback недоступны, но bot и userbot могут продолжать основную работу. Для восстановления нужно расследование, ротация token, повторный запуск installer и health/security smoke.

## Проверка после установки

```bash
sudo systemctl is-active romatic-server-supervisor.service
sudo systemctl status romatic-server-supervisor.service --no-pager

cd /srv/romatic-club
docker compose --env-file .env -f compose.yaml ps
stat -c '%a %U %G %n' \
  server-data/runtime/supervisor/romatic-server-supervisor.sock

docker compose --env-file .env -f compose.yaml exec -T userbot \
  sh -ec 'test -z "${SUPERVISOR_TOKEN:-}"; test -z "${SUPERVISOR_TOKEN_FILE:-}"'
```

При restart основного bot изменяется только `StartedAt` сервиса `bot`. При restart userbot изменяется только `StartedAt` сервиса `userbot`. PostgreSQL и второй процесс сохраняют прежнее время запуска.

## Incident orchestration

Read-only monitor Max отслеживает stop, рост `RestartCount` и подтверждённый `unhealthy`, очищает bounded logs и передаёт инцидент главному Hermes. Monitor и coder не получают право merge, deploy, restart, update или rollback.
