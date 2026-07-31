# Premium-анонсы расписания

Userbot публикует в `AUCTION_CHANNEL_ID` компактное расписание на следующий день. Проверка выполняется каждые 30 секунд, а публикация начинается в `23:00` по Москве. Дата уже опубликованного расписания сохраняется в `var/schedule_announcements.json`, поэтому перезапуск процесса не создаёт повторный пост.

## Первичная настройка эмодзи

1. Откройте личный чат с Premium-аккаунтом, под которым запущен userbot.
2. Отправьте сообщение-шаблон, используя нужные кастомные эмодзи справа:

   ```text
   header = 🦋
   card = 🎴
   diamond = 💎
   tea = ☕
   ```

3. Ответьте на шаблон командой `/schedule_emojis`.
4. Проверьте результат командой `/schedule_preview`.
5. Состояние worker показывается командой `/schedule_status`.

Обязательные ключи: `header`, `card`, `diamond`, `tea`. Пока они не сохранены, worker не публикует расписание обычными эмодзи вместо Premium-эмодзи.

Для отдельного героя или карты можно добавить строку:

```text
hero:Сонхва = 🧑
card:Название карты = 🎴
```

Приоритет значка строки: `hero:<имя героя>`, затем `card:<название карты>`, затем общий `card`.

## Настройки окружения

```dotenv
SCHEDULE_ANNOUNCEMENTS_ENABLED=true
SCHEDULE_ANNOUNCEMENTS_HOUR=23
SCHEDULE_ANNOUNCEMENTS_MINUTE=0
SCHEDULE_ANNOUNCEMENTS_REQUIRE_CUSTOM_EMOJI=true
SCHEDULE_ANNOUNCEMENT_STATE_FILE=var/schedule_announcements.json
```

Premium-аккаунт должен быть администратором канала с правом публикации. Сессия userbot и файл состояния находятся в каталоге `var`, который монтируется в контейнер и не должен попадать в Git.
