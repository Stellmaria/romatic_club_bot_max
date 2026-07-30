# Настраиваемые медиа аукциона

Медиа колод, карт, отдельных аукционов, редкостей и услуг теперь можно менять через админ-команды. Добавлять новый `deck_id` в словари Python больше не требуется.

## Быстрая команда для колоды

```text
/deck_media DECK_ID FILE_ID [MEDIA_TYPE]
```

Пример с видео:

```text
/deck_media 22 BAACAgIAAxkBAAERAAE1aldpUk9K4DEcKgWm3hXyf7IwoCcAAsKhAAL3JrlKJlfJWuoy7Ow9BA video
```

`video` можно не писать: для `file_id`, начинающегося с `BAAC`, команда определит видео автоматически.

Команду также можно:

- написать в подписи к загруженному видео: `/deck_media 22`;
- отправить ответом на сообщение с видео: `/deck_media 22`.

## Универсальная команда

```text
/set_media TARGET KEY FILE_ID [MEDIA_TYPE]
```

Поддерживаемые цели:

| TARGET | KEY | Назначение |
|---|---|---|
| `deck` | `deck_id` | заставка всей колоды |
| `card` | `card_id` | медиа конкретной карты |
| `auction` | `auction_id` | медиа конкретного аукциона |
| `rarity` | `bronze/silver/gold/diamond/any` | медиа варианта «любая карта редкости» |
| `service` | ключ услуги | подписки, Друзья+, слоты прогресса и другие услуги |
| `spins` | количество | кручения, например `10`, `50`, `100` |
| `default` | `card` или `deck` | общее медиа по умолчанию |

Примеры:

```text
/set_media card 145 BAAC... video
/set_media auction 301 AgAC... photo
/set_media rarity gold BAAC... video
/set_media service friends_plus BAAC... video
/set_media spins 50 BAAC... video
/set_media default card BAAC... video
```

## Просмотр и удаление

```text
/get_media deck 22
/delete_media deck 22
/media_list
/media_list deck
```

Удаление настройки возвращает старый fallback, если он ещё присутствует в коде. Для новой колоды без fallback нужно просто назначить другое медиа.

## Хранение

Настройки сохраняются в таблице `public.auction_media_assets`, создаваемой миграцией:

```text
migrations/008_auction_media_registry.sql
```

Уникальная запись определяется парой `target_kind + target_key`. Повторная команда обновляет существующую запись, а не создаёт дубль.

Для `card` и `auction` команда дополнительно синхронизирует прежние поля `cards.image_id` и `auctions.image_id`, чтобы старые пути публикации продолжили работать.
