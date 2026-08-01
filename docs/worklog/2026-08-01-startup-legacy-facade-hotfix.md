# 2026-08-01 — Startup legacy facade hotfix

## Перед началом

- **Линия:** production hotfix Max.
- **Базовый commit:** `34c6b955620be18e714f9885829b4bf90bbaf3a7`.
- **Ветка:** `hotfix/startup-settings-import`.
- **Инцидент:** production update завершился автоматическим rollback. Рабочий runtime остался на `68becc157a0ea9b65d7384385be8f5be4a550062`.
- **Первичный симптом:** `ImportError: cannot import name 'settings' from 'bot.core.settings'`.
- **Цель:** загрузить полный production import graph bot/userbot, восстановить потерянные публичные DB-контракты без возврата удалённого singleton или `db/legacy_impl.py`, сохранить rollback-safe deployment.
- **Критерии готовности:** полный CI, загрузка `bot.application`, `bot.bootstrap.routers` и `userbot.application`, отсутствие импортов удалённого `bot.core.settings.settings`, сохранение PR #64 `pending_total()`.
- **Ограничения:** production, Supervisor и PostgreSQL не изменяются этим PR; merge и deployment выполняются отдельно после разрешения владельца.

## Найденная корневая причина

После удаления `db/legacy_impl.py` модульный facade не экспортировал часть API, которые продолжали импортировать production handlers. Прежний CI собирал image, но не загружал полный router/import graph, поэтому дефект дошёл до deployment.

Последовательный startup regression выявил отсутствующие контракты:

- `get_last_nonempty_card_deck_id`;
- `get_bid_auction_by_discussion_id`;
- `add_bid`;
- `update_auction_status`;
- `update_lot_field`;
- `mark_user_private_chat_opened`;
- `mark_user_private_chat_closed`;
- `count_missing_auction_ids`;
- `get_missing_auction_ids`;
- `reserve_first_missing_auction_id_for_stats`;
- compatibility `logger`.

Три функции свободных auction ID использовались интерфейсом PR #62, но не имели реализации в DB facade.

## Реализация

- добавлен полный startup/import regression;
- восстановлены узкие владельцы compatibility API вместо возврата legacy-монолита;
- `update_lot_field` ограничен фактически используемыми колонками и больше не принимает произвольное имя SQL-поля;
- резервирование свободного auction ID выполняется транзакционно, под advisory lock и с `ON CONFLICT DO NOTHING`;
- PM delivery markers сохраняют прежнюю SQL-семантику;
- facade contract проверяет все импорты из `db.db` и `db.legacy`.

## После завершения

- **Статус:** частично, ожидается полный CI текущего head.
- **PR:** #65.
- **Production:** не изменялся; остаётся на стабильном `68becc157a0ea9b65d7384385be8f5be4a550062`.
- **Следующий шаг:** получить зелёный CI, обновить итоговые проверки и подготовить PR к review без merge/deploy.
