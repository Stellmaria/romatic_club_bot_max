# Инвентаризация legacy API базы данных — фаза 10

Файл сформирован автоматически по статическим импортам `from db.db import ...`. Динамические импорты и обращения через модуль учитываются отдельно при следующих фазах.

| Репозиторий | Функций | Всё ещё импортируются через facade |
|---|---:|---:|
| `db.repositories.admin` | 24 | 21 |
| `db.repositories.auctions` | 25 | 16 |
| `db.repositories.autobids` | 4 | 4 |
| `db.repositories.bids` | 7 | 4 |
| `db.repositories.cards` | 36 | 23 |
| `db.repositories.exchanges` | 25 | 14 |
| `db.repositories.market` | 20 | 12 |
| `db.repositories.post_stats` | 10 | 9 |
| `db.repositories.stats` | 2 | 0 |
| `db.repositories.subscriptions` | 25 | 17 |
| `db.repositories.uid` | 40 | 23 |
| `db.repositories.users` | 17 | 12 |

## Самые связанные функции legacy-facade

| Функция | Репозиторий | Файлов-импортёров |
|---|---|---:|
| `is_luxury_user` | `db.repositories.users` | 11 |
| `get_all_decks` | `db.repositories.cards` | 10 |
| `get_user_by_username` | `db.repositories.users` | 9 |
| `get_lot_by_id` | `db.repositories.auctions` | 8 |
| `get_user` | `db.repositories.users` | 8 |
| `is_admin` | `db.repositories.admin` | 8 |
| `log_audit_action` | `db.repositories.admin` | 8 |
| `get_lot_owners` | `db.repositories.auctions` | 7 |
| `get_card_by_id` | `db.repositories.cards` | 6 |
| `add_user` | `db.repositories.users` | 4 |
| `get_auctions_by_date_with_owners` | `db.repositories.auctions` | 4 |
| `get_cards_by_deck` | `db.repositories.cards` | 4 |
| `get_deck_by_id` | `db.repositories.cards` | 4 |
| `log_admin_action` | `db.repositories.admin` | 4 |
| `set_luxury_status` | `db.repositories.users` | 4 |
| `count_sold_by_card_id` | `db.repositories.auctions` | 3 |
| `count_sold_same_card` | `db.repositories.auctions` | 3 |
| `get_auctions_by_date` | `db.repositories.auctions` | 3 |
| `get_user_basic_info_by_username` | `db.repositories.users` | 3 |
| `get_user_id_by_username` | `db.repositories.users` | 3 |
| `get_user_verified_uid` | `db.repositories.uid` | 3 |
| `market_add_rate_tiers` | `db.repositories.market` | 3 |
| `add_deck` | `db.repositories.cards` | 2 |
| `add_warning` | `db.repositories.admin` | 2 |
| `get_all_users` | `db.repositories.users` | 2 |
| `get_auction_winner` | `db.repositories.bids` | 2 |
| `get_exchange_batch_by_id` | `db.repositories.exchanges` | 2 |
| `get_exchange_cards_for_deck` | `db.repositories.exchanges` | 2 |
| `get_exchange_items_by_batch_id` | `db.repositories.exchanges` | 2 |
| `get_lots_by_owner` | `db.repositories.auctions` | 2 |
| `get_pending_auctions` | `db.repositories.auctions` | 2 |
| `get_settings` | `db.repositories.subscriptions` | 2 |
| `get_uid_profile_binding` | `db.repositories.uid` | 2 |
| `get_uid_verification_request` | `db.repositories.uid` | 2 |
| `get_user_id_by_uid_any` | `db.repositories.uid` | 2 |
| `get_warnings_count` | `db.repositories.admin` | 2 |
| `get_whois_admin_payload` | `db.repositories.users` | 2 |
| `is_user_banned` | `db.repositories.admin` | 2 |
| `list_auctions` | `db.repositories.auctions` | 2 |
| `list_user_card_subs` | `db.repositories.subscriptions` | 2 |

Полная машинная карта находится в `db/legacy_api_inventory.json`.
