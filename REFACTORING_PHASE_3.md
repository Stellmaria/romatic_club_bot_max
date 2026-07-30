# Refactoring Phase 3: auction bidding and autobids

## Scope

This phase extracts the highest-risk bid placement paths from the Telegram monolith while preserving the existing bot and userbot entrypoints.

## New architecture

- `bot/domain/auctions/`
  - normalized auction statuses and currencies;
  - bid parsing and step/minimum rules;
  - typed auction, bid and autobid models;
  - domain exceptions for expected failures.
- `bot/repositories/auction_bids.py`
  - transaction-scoped auction locking;
  - atomic max-bid validation and insertion;
  - duplicate Telegram message protection;
  - bid revision and cancellation primitives.
- `bot/services/auction_bids.py`
  - shared placement flow for Bot API and Telethon;
  - active-window and ban checks;
  - `/oops` revision window enforcement.
- `bot/repositories/auction_autobids.py` and `bot/services/auction_autobids.py`
  - autobid configuration, validation, listing and disabling.
- `bot/handlers/auction/`
  - thin Bot API adapters for bids and autobid commands.

## Fixed defects

1. Bot API and userbot no longer use conflicting bid parsers and currency steps.
2. `10k` and `10к` are consistently parsed as 10,000.
3. Bid placement locks the auction row and reads the maximum bid inside the same database transaction.
4. One Telegram message ID can create at most one bid.
5. Existing duplicate bid rows are archived before migration cleanup.
6. The userbot reuses the application database pool instead of opening a second pool.
7. The unfinished `/oops` branch now cancels or revises the bidder's own bid within 60 seconds.
8. Edited bids are removed from accounting in Bot API moderation mode.
9. The bidding router uses `SkipHandler` when disabled so it does not swallow unrelated discussion messages.
10. Autobid commands were removed from the 11k-line auction handler and moved to a dedicated router/service/repository.
11. The public default autobid password `2069` was removed. An optional environment password is still supported.
12. Duplicate auction/exchange handlers and duplicate warning-cleanup handlers were removed.
13. Legacy direct `add_bid`, `upsert_autobid` and `disable_autobid` functions were removed from `db/db.py`.
14. The tracked IDE module file was removed and `*.iml` is now ignored.

## Database migration

`migrations/003_auction_bid_integrity.sql`:

- archives duplicate rows in `bid_duplicate_archive`;
- removes duplicate rows after archiving;
- adds a partial unique index on `bids.discussion_message_id`;
- adds winner-order and active-discussion indexes.

Test this migration on a database copy before production deployment.

## Validation

- `python -m compileall`: passed;
- regression/unit tests: 23 passed;
- no duplicate top-level functions remain in the two auction handler monoliths;
- patch was verified against a clean Phase 2 copy;
- release archive excludes secrets, sessions, caches, virtual environments and IDE metadata.

## Next phase

The next safe extraction target is auction publication and moderation, followed by exchange workflows. The remaining monoliths are still large, but bid writes are now behind a stable service boundary, which is the part where concurrent users previously had the most leverage to create inconsistent state.

## Rebase на Phase 2 fixed_v2

Эта сборка Phase 3 перенесена поверх `refactored_project_phase2_fixed_v2` и сохраняет дополнительные исправления базовой версии:

- PostgreSQL-интервалы строятся через типизированный `make_interval(...)`, без несовместимой передачи integer в `$1::text`;
- миграция 002 сохраняет рабочий legacy-статус `publishing` и заранее перечисляет неизвестные статусы;
- `db/db.py` не настраивает logging самостоятельно, конфигурация остаётся ответственностью entrypoint;
- дополнительные регрессионные тесты Phase 2 fixed_v2 сохранены без замены.
