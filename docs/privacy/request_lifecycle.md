# Privacy request lifecycle

## Scope

The bot accepts authenticated self-service anonymization requests in private chat. The workflow removes optional account data and replaces the original Telegram user identifier in retained business or security records with a request-specific negative surrogate. It does not erase facts that remain under a documented business, moderation, delivery, or security hold.

The canonical policy remains `docs/privacy/data_inventory.json`. Its jurisdiction status is deliberately `legal-retention-periods-not-approved`; this workflow does not silently approve retention periods that still require an owner or legal decision.

## User commands

- `/privacy_delete_request` creates one active reviewed request for the authenticated Telegram user.
- `/privacy_delete_status` shows the latest request state and retained hold codes.
- `/privacy_delete_cancel` cancels a request only while it is still pending review.
- `/privacy_export` remains the read-only self-service export path.

## Review and execution

Operator actions use `python -m scripts.privacy_requests` and emit aggregate JSON without raw personal values.

1. `plan REQUEST_ID` recomputes active blockers, retained holds, action counts, the policy hash, and a deterministic plan hash.
2. `approve REQUEST_ID --plan-sha256 HASH` stores the exact reviewed plan.
3. `apply REQUEST_ID --plan-sha256 HASH --confirm APPLY:REQUEST_ID:FIRST_12_HASH_CHARS` recomputes the plan in a serializable transaction and refuses changed or blocked requests.

Approval and execution require different operator identities. Request and audit evidence becomes immutable after a terminal state.

## Blocking holds

Execution is blocked for an owner/admin role, an active auction or active auction participation, an active market listing, pending exchange moderation, an open appeal, unresolved UID verification, an active account or UID ban, or pending/unreviewed Telegram delivery.

## Retained holds

Completed requests may retain pseudonymized business history, security history, and a minimal keyed UID digest. Retained rows reference a request-specific negative surrogate instead of the original Telegram user identifier. Optional preferences, subscriptions, thanks records, trusted username membership, Telegram media references, verification ciphertext, and terminal delivery payloads are deleted or minimized.

## Backups

Primary-data mutation does not rewrite historical encrypted backups. The inventory currently records 14-day local and 90-day offsite backup retention. Removed primary values may therefore remain recoverable only until the corresponding encrypted backup expires. The offsite control is tracked separately in `Stellmaria/Velvet#614`.
