# Hotfix: `/day` and relative-date commands

## Symptoms

Commands such as `/day завтра` either returned a generic error, showed no lots for the expected Moscow date, or were consumed by an unfinished FSM form.

## Causes

1. `format_today_lots_fancy()` compared an offset-aware PostgreSQL datetime with `datetime.now()` without `tzinfo`.
2. The legacy `db.db.get_auctions_by_date()` facade filtered `DATE(start_time)` in the database session timezone instead of the Moscow auction calendar.
3. The stateful auction router was registered before the public command router, so broad FSM handlers could consume slash commands as form input.
4. The day parser used the host machine's local calendar date and existed twice in `users.py`.

## Fix

- Normalize auction deadlines with `ensure_utc()` and compare them with `utc_now()`.
- Render lot times through `to_moscow()`.
- Query `(start_time AT TIME ZONE 'Europe/Moscow')::date`.
- Register `/day`, `/today`, `/when`, and `/gaps` routers before the stateful auction monolith.
- Keep one Moscow-aware date parser with support for `сегодня`, `завтра`, and `послезавтра`.

## Verification

Targeted regression suite: 18 tests passed. Full project collection requires runtime dependencies (`aiogram`, `asyncpg`, and `telethon`) that are not installed in the packaging environment.
