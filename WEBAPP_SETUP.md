# Карточный домик · Telegram Mini App

Telegram Mini App для существующего бота и канала «Карточный домик».
Целевой production URL: `https://app.kartochny-domik.ru`.

## Текущий продуктовый срез

Mini App сейчас содержит одну рабочую страницу: **Аукционы**.

На ней реализованы:

- Telegram-профиль в шапке без какого-либо баланса;
- статус `Luxury 1`, `Luxury 2` или кнопка `Купить Luxury`;
- определение Luxury 2 и Luxury 1 по членству в соответствующих Telegram-чатах;
- совместимый fallback на старый `users.is_luxury` как Luxury 1, если Telegram-проверка недоступна или чаты не настроены;
- Luxury-only календарь с серверной проверкой доступа;
- Luxury-only свободные слоты с серверной проверкой доступа;
- активный аукцион, текущая ставка, обратный отсчёт и переход в опубликованный Telegram-лот;
- список ближайших аукционов выбранного дня;
- обновление сегодняшнего экрана polling-запросом раз в 15 секунд;
- защищённая выдача карточного изображения через backend без раскрытия bot token;
- нижнее меню как визуальный каркас. Остальные разделы пока не реализуются.

## Техническая основа

- `aiohttp` HTTP-процесс без новых Python runtime-зависимостей;
- серверная проверка `Telegram.WebApp.initData` и давности `auth_date`;
- `GET /api/webapp/me` с синхронизацией Telegram-профиля;
- агрегированный read model для Auction Home;
- `Cache-Control: no-store` для авторизованных API-ответов;
- `GET /healthz`;
- hardened `Dockerfile.webapp` на том же базовом образе и SSL runtime, что основной bot image;
- Compose overlay `compose.webapp.yaml`;
- отдельный image smoke + Trivy workflow для webapp;
- автоматическая настройка Telegram menu button при старте бота.

## Переменные окружения web-процесса

Создайте `.env.webapp` по `.env.webapp.example`. Процесс использует существующие
`BOT_TOKEN`, `DATABASE_URL` и DB-настройки проекта. Дополнительно используются:

- `LUXURY_CHAT_ID` - Telegram chat id для Luxury 1;
- `LUXURY_CHAT_ID_LVL2` - Telegram chat id для Luxury 2;
- `AUCTION_CHANNEL_USERNAME` - username публичного аукционного канала без обязательного `@`;
- `WEBAPP_LUXURY_CONTACT_URL` - HTTPS-ссылка для кнопки `Купить Luxury`, по умолчанию `https://t.me/velassya`;
- `WEBAPP_HOST` - по умолчанию `0.0.0.0`;
- `WEBAPP_PORT` - по умолчанию `8080`;
- `WEBAPP_AUTH_MAX_AGE_SECONDS` - максимальный возраст `initData`, по умолчанию `3600`.

Для production используется отдельный `.env.webapp`, а не весь `.env.bot`.

## Контракт Luxury

Уровень вычисляется в таком порядке:

1. член Luxury 2 chat -> `Luxury 2`;
2. иначе член Luxury 1 chat -> `Luxury 1`;
3. если Telegram membership lookup недоступен или Luxury chats не настроены, старый `users.is_luxury=true` -> `Luxury 1`;
4. иначе Luxury отсутствует.

Клиентская блокировка календаря и свободных слотов является только UX. Backend повторно проверяет Luxury и отвечает `403 luxury_required`, поэтому доступ нельзя получить подменой JavaScript-запроса.

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

Скопируйте `.env.webapp.example` в `.env.webapp`, заполните необходимые значения,
затем запустите opt-in webapp deployment:

```bash
./deploy/server/deploy-webapp.sh
```

Скрипт валидирует основной Compose + overlay, собирает webapp, запускает сервис и
ждёт успешный healthcheck. Он намеренно отделён от основного production deploy до
первого реального Telegram smoke-test, чтобы новый web surface не менял проверенный
rollback-контур бота и userbot.

Эквивалентная ручная команда:

```bash
docker compose -f compose.yaml -f compose.webapp.yaml up -d --build webapp
```

По умолчанию web-процесс публикуется только на loopback хоста:
`127.0.0.1:8080`. HTTPS reverse proxy должен обслуживать
`app.kartochny-domik.ru` и проксировать трафик на этот адрес.

## Маршруты

- `/` - единственная текущая Mini App страница аукционов;
- `/healthz` - liveness;
- `/api/webapp/me` - Telegram-профиль текущего пользователя;
- `/api/webapp/auction-home` - активный лот и ближайшие лоты сегодня;
- `/api/webapp/auction-home?date=YYYY-MM-DD` - выбранный календарный день, только Luxury для дат кроме сегодня;
- `/api/webapp/free-slots?date=YYYY-MM-DD` - свободные слоты, только Luxury;
- `/api/webapp/cards/{card_id}/image` - авторизованная выдача карточного изображения.

API-запросы Mini App ожидают заголовок:

```text
Authorization: tma <Telegram.WebApp.initData>
```

Сервер не принимает `initDataUnsafe` как доверенную авторизацию.

## Что нужно настроить вручную после HTTPS-деплоя

1. Создать DNS-запись `app.kartochny-domik.ru` на production-сервер.
2. Настроить HTTPS reverse proxy на `127.0.0.1:8080`.
3. Проверить `https://app.kartochny-domik.ru/healthz`.
4. Заполнить `.env.webapp`, включая Luxury chats и `AUCTION_CHANNEL_USERNAME`.
5. Записать `WEBAPP_PUBLIC_URL=https://app.kartochny-domik.ru` в `.env.bot` и перезапустить bot service.
6. В `@BotFather` настроить **Main Mini App** на тот же URL.
7. Открыть Mini App из Telegram и проверить на реальных пользователях: без Luxury, Luxury 1 и Luxury 2.
8. Проверить активный лот, текущую ставку, Telegram-переход, календарь и свободные слоты.

До выполнения этих infrastructure-шагов домен в репозитории является целевым именем, а не подтверждением, что DNS/HTTPS уже настроены.
