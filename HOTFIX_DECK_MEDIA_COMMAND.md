# Hotfix: `/deck_media`

Исправлено подключение административного роутера медиа в `main.py`.
Без регистрации `media_assets_router` команды `/deck_media`, `/set_media`,
`/get_media`, `/delete_media` и `/media_list` существовали в коде, но Dispatcher
их не видел.

Также старый монолитный сценарий создания лота и обмена переведён на чтение
медиа колоды из `public.auction_media_assets`. Жёстко заданный словарь остаётся
только fallback для ещё не перенесённых колод.

Пример:

```text
/deck_media 26 BAACAgIAAxkBAAERAAE1aldpUk9K4DEcKgWm3hXyf7IwoCcAAsKhAAL3JrlKJlfJWuoy7Ow9BA
```

Префикс `BAAC` автоматически распознаётся как `video`.
Команда доступна администратору в личном чате с ботом.
