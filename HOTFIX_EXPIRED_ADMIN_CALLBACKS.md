# Hotfix: expired admin callback queries

## Symptom

Immediately after startup, old Telegram button updates were replayed and
`admin_panel.edit_lot_menu()` failed on `call.answer()` with:

`Bad Request: query is too old and response timeout expired or query ID is invalid`

## Root cause

The project already contained `ExpiredCallbackMiddleware` and
`safe_callback_answer()`, but the legacy `main.py` used for `python main.py` did
not register the middleware and did not drop pending Telegram updates.

## Changes

- registered `ExpiredCallbackMiddleware` in the real `main.py` entry point;
- call `delete_webhook(drop_pending_updates=...)` before workers and polling;
- default `DROP_PENDING_UPDATES` to enabled;
- use `safe_callback_answer()` in `edit_lot_menu`;
- use the safe helper in the shared admin access decorator.

Expired callback answer errors are now ignored, while unrelated Telegram errors
continue to propagate.
