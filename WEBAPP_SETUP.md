# Telegram Mini App - Phase 1

Первый рабочий срез Mini App для существующего бота.

## Что входит

- `aiohttp` HTTP-процесс без новых Python runtime-зависимостей;
- проверка `Telegram.WebApp.initData` на сервере;
- проверка давности `auth_date`;
- `GET /api/webapp/me`;
- синхронизация Telegram-профиля через существующий `db.profile_sync.sync_user_profile`;
- статический mobile-first frontend, использующий Telegram theme CSS variables;
- `GET /healthz`;
- отдельный `Dockerfile.webapp`.

## Переменные окружения

Процесс использует существующие `BOT_TOKEN`, `DATABASE_URL` и DB-настройки проекта.
Дополнительно доступны:

- `WEBAPP_HOST` - по умолчанию `0.0.0.0`;
- `WEBAPP_PORT` - по умолчанию `8080`;
- `WEBAPP_AUTH_MAX_AGE_SECONDS` - максимальный возраст `initData`, по умолчанию `3600`.

## Локальный запуск

После копирования файлов в корень репозитория:

```bash
python -m webapi
```

Обычный браузер не содержит Telegram `initData`, поэтому интерфейс покажет диагностическое сообщение. Для настоящей авторизации Mini App должна быть открыта Telegram-клиентом по HTTPS URL.

## Маршруты

- `/` - Mini App UI;
- `/healthz` - liveness;
- `/api/webapp/me` - профиль текущего Telegram-пользователя.

`/api/webapp/me` ожидает заголовок:

```text
Authorization: tma <Telegram.WebApp.initData>
```

## Следующий срез

После подключения URL в Telegram имеет смысл добавить API и UI коллекции, затем аукцион. Визуальный макет нужен именно на этом этапе, потому что тогда появляются реальные карточки, фильтры, состояния лотов и действия пользователя.
