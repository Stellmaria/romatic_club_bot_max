# Карточный домик · Telegram Mini App

Telegram Mini App для существующего бота и канала «Карточный домик».
Целевой production URL: `https://app.kartochny-domik.ru`.

## Что входит

- `aiohttp` HTTP-процесс без новых Python runtime-зависимостей;
- серверная проверка `Telegram.WebApp.initData` и давности `auth_date`;
- `GET /api/webapp/me` с синхронизацией Telegram-профиля;
- `GET /api/webapp/auctions` с публичными запланированными и активными лотами;
- mobile-first frontend на Telegram theme CSS variables;
- профиль пользователя и read-only экран аукционов;
- `GET /healthz`;
- hardened `Dockerfile.webapp` на том же базовом образе и SSL runtime, что основной bot image;
- Compose overlay `compose.webapp.yaml`;
- отдельный image smoke + Trivy workflow для webapp;
- автоматическая настройка Telegram menu button при старте бота.

## Переменные окружения web-процесса

Создайте `.env.webapp` по `.env.webapp.example`. Процесс использует существующие
`BOT_TOKEN`, `DATABASE_URL` и DB-настройки проекта. Дополнительно доступны:

- `WEBAPP_HOST` - по умолчанию `0.0.0.0`;
- `WEBAPP_PORT` - по умолчанию `8080`;
- `WEBAPP_AUTH_MAX_AGE_SECONDS` - максимальный возраст `initData`, по умолчанию `3600`.

Для production используется отдельный `.env.webapp`, а не весь `.env.bot`.

## Подключение к Telegram-боту

После того как HTTPS endpoint реально доступен, в `.env.bot` задайте:

```dotenv
WEBAPP_PUBLIC_URL=https://app.kartochny-domik.ru
```

Если переменная отсутствует или пуста, бот работает как раньше и Telegram menu
button не меняется. Если URL задан, он должен быть абсолютным HTTPS URL без
логина/пароля и URL fragment.

При старте бот вызывает `setChatMenuButton` и публикует кнопку `Карточный домик`
для приватных чатов. Временная ошибка Telegram API при настройке этой кнопки
логируется, но не останавливает основной bot process.

## Локальный запуск

```bash
python -m webapi
```

Обычный браузер не содержит Telegram `initData`, поэтому интерфейс покажет
диагностическое сообщение. Для настоящей авторизации Mini App должна быть открыта
Telegram-клиентом по HTTPS URL.

## Запуск через Docker Compose

Скопируйте `.env.webapp.example` в `.env.webapp`, заполните `BOT_TOKEN` и
`DATABASE_URL`, затем запустите opt-in webapp deployment:

```bash
./deploy/server/deploy-webapp.sh
```

Скрипт валидирует основной Compose + overlay, собирает webapp, запускает сервис и
ждёт успешный healthcheck. Он намеренно отделён от основного production deploy до
первого Telegram smoke-test, чтобы новый web surface не менял проверенный
rollback-контур бота и userbot.

Эквивалентная ручная команда:

```bash
docker compose -f compose.yaml -f compose.webapp.yaml up -d --build webapp
```

По умолчанию web-процесс публикуется только на loopback хоста:
`127.0.0.1:8080`. HTTPS reverse proxy должен обслуживать
`app.kartochny-domik.ru` и проксировать трафик на этот адрес.

При необходимости host port можно изменить через `WEBAPP_PUBLISH_PORT`, а bind
address через `WEBAPP_BIND_ADDRESS`.

## Маршруты

- `/` - Mini App UI;
- `/healthz` - liveness;
- `/api/webapp/me` - профиль текущего Telegram-пользователя;
- `/api/webapp/auctions` - публичные лоты со статусами `scheduled`, `publishing`, `active`.

API-запросы Mini App ожидают заголовок:

```text
Authorization: tma <Telegram.WebApp.initData>
```

Сервер не принимает `initDataUnsafe` как доверенную авторизацию.

## Что нужно настроить вручную после HTTPS-деплоя

1. Создать DNS-запись `app.kartochny-domik.ru` на production-сервер.
2. Настроить HTTPS reverse proxy на `127.0.0.1:8080`.
3. Проверить `https://app.kartochny-domik.ru/healthz`.
4. Записать `WEBAPP_PUBLIC_URL=https://app.kartochny-domik.ru` в `.env.bot` и
   перезапустить bot service. Menu button будет настроен автоматически через Bot API.
5. В `@BotFather` настроить **Main Mini App** на тот же URL.
6. Открыть приложение именно из Telegram и проверить профиль и раздел аукционов
   на реальном `initData`.

## Следующий срез

Следующий функциональный этап - персональная коллекция и карточные изображения.
Его нужно строить после явного определения DB-контракта владения картами и способа
безопасной выдачи Telegram media в браузер. Telegram `file_id` нельзя просто
подставлять в HTML как публичный URL.
