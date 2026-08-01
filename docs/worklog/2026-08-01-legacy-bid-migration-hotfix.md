# 2026-08-01 — Legacy bid migration hotfix

## Инцидент

Production update до `a755d77e3fe206ea92219f56b36a9214a0821baf` перевёл `bot` и `userbot` в restart-loop при применении `012_bid_currency_and_deadline_contract.sql`.

Backfill `bids.currency` заставлял PostgreSQL повторно проверить существующий cross-table CHECK `chk_bids_step_and_min_by_currency`. Историческая ставка, допустимая при прежних условиях лота, больше не удовлетворяла текущей функции `is_valid_bid`, поэтому весь startup завершался с `CheckViolationError`.

## Исправление

- migration 012 временно снимает существующий CHECK перед backfill;
- определение constraint считывается из PostgreSQL и восстанавливается без изменения бизнес-правила;
- constraint возвращается как `NOT VALID`, поэтому исторические строки сохраняются, а новые inserts/updates продолжают проверяться;
- deployment smoke отклоняет `restarting`, любой рост `RestartCount` и требует несколько последовательных стабильных опросов;
- добавлен реальный PostgreSQL regression с legacy-ставкой и повторным запуском migration;
- CI получил disposable PostgreSQL service для выполнения regression.

## Production

Production после инцидента оставлен на стабильном commit `68becc157a0ea9b65d7384385be8f5be4a550062`. База вручную не редактировалась и из dump не восстанавливалась. Incident monitor остаётся выключенным до отдельного исправления дедупликации.
