# Fix v4: expired Telegram callback queries

## Symptom

After restarting the bot, Telegram delivered callback updates accumulated while the bot was offline. The handlers changed data and then failed on `callback_query.answer()` with:

`Bad Request: query is too old and response timeout expired or query ID is invalid`

## Changes

- Added `DROP_PENDING_UPDATES=1` to `.env.example`.
- `main.py` now calls `delete_webhook(drop_pending_updates=...)` before background jobs and polling begin.
- Added `ExpiredCallbackMiddleware`, which suppresses only the known expired-callback `TelegramBadRequest` variants.
- Added the shared `safe_callback_answer()` helper.
- Updated both subscription-removal handlers from the traceback to use the safe helper.
- Added regression tests.

## Operational note

With `DROP_PENDING_UPDATES=1`, messages and button presses accumulated while the bot was offline are discarded during startup. This is intentional: replaying stale callback actions can mutate data unexpectedly.

Set `DROP_PENDING_UPDATES=0` only when preserving offline updates is more important than rejecting stale button presses.
