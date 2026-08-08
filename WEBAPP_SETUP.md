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
- отдельный `Dockerfile.webapp`;
- Compose overlay `compose.webapp.yaml`;
- автоматическая настройка Telegram menu button при старте бота.

## Переменные окружения web-процесса

Создайте `.env.webapp` по `.env.webapp.example`. Процесс использует существующие
`BOT_TOKEN`, `DATABASE_URL` и DB-настройки проекта. Дополнительно доступны:

- `WEBAPP_HOST` - по умолчанию `0.0.0.0`;
- `WEBAPP_PORT` - по умолчанию `8080`;
- `WEBAPP_AUTH_MAX_AGE_SECONDS` - максимальный возраст `initData`, по умолчанию `3600`.

Для production рекомендуется отдельный `.env.webapp`, а не передача web-процессу
всего `.env.bot`.

## Подключение к Telegram-боту

В `.env.bot` задайте публичный адрес Mini App:

```dotenv
WEBAPP_PUBLIC_URL=https://app.example.com
```

Если переменная отсутствует или пуста, бот работает как раньше и Telegram menu
button не меняется. Если URL задан, он должен быть абсолютным HTTPS URL без
логина/пароля и URL fragment.

При старте бот вызывает `setChatMenuButton` и публикует кнопку
`Открыть приложение` для приватных чатов.

## Локальный запуск

```bash
python -m webapi
```

Обычный браузер не содержит Telegram `initData`, поэтому интерфейс покажет
диагностическое сообщение. Для настоящей авторизации Mini App должна быть открыта
Telegram-клиентом по HTTPS URL.

## Запуск через Docker Compose

Скопируйте `.env.webapp.example` в `.env.webapp`, заполните `BOT_TOKEN` и
`DATABASE_URL`, затем запустите overlay вместе с основным Compose-файлом:

```bash
docker compose -f compose.yaml -f compose.webapp.yaml up -d --build webapp
```

По умолчанию web-процесс публикуется только на loopback хоста:
`127.0.0.1:8080`. Перед ним нужен HTTPS reverse proxy (Nginx, Caddy или другой
используемый на сервере proxy), который обслуживает ваш публичный домен.

При необходимости host port можно изменить через `WEBAPP_PUBLISH_PORT`, а bind
address через `WEBAPP_BIND_ADDRESS`.

## Маршруты

- `/` - Mini App UI;
- `/healthz` - liveness;
- `/api/webapp/me` - профиль текущего Telegram-пользователя.

`/api/webapp/me` ожидает заголовок:

```text
Authorization: tma <Telegram.WebApp.initData>
```

## Что нужно настроить вручную после HTTPS-деплоя

1. Убедиться, что публичный `WEBAPP_PUBLIC_URL` открывается по HTTPS.
2. Записать тот же URL в `.env.bot` и перезапустить bot service. Menu button
   будет настроен автоматически через Bot API.
3. В `@BotFather` открыть настройки бота и настроить **Main Mini App** на тот же
   URL. Это добавит отдельную Launch/Open App кнопку в профиле бота и позволит
   использовать `startapp` deep links.
4. Открыть приложение именно из Telegram и проверить `/api/webapp/me` на реальном
   `initData`.

## Следующий срез

После реального Telegram smoke-test можно добавлять API и UI коллекции, затем
аукцион. На этом этапе визуальный макет уже даст пользу: появляются карточки,
фильтры, состояния лотов и действия пользователя.
