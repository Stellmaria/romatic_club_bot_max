# Time policy

## Contract

- Domain and persistence **instants** are timezone-aware `datetime` values normalized to UTC.
- PostgreSQL stores instants as `timestamptz`. Migration `018_utc_timestamp_policy.sql` converts remaining historical `timestamp without time zone` columns by interpreting their old values as `Europe/Moscow` wall time.
- User-facing schedules and business dates use `Europe/Moscow` explicitly through `bot.core.time`. A date derived from an instant must use `business_today`, `moscow_date`, or another helper with an explicit timezone.
- Naive datetimes are rejected at application, domain, and repository boundaries by `require_aware`. `to_moscow_wall` and `schedule_slot_key` are presentation-only values and must never be persisted.
- Runtime code uses the `Clock` port. `SystemClock` is the production implementation; tests inject a deterministic clock.

## Serialization

General timestamps are serialized as second-precision UTC ISO-8601, for example `2026-08-03T13:45:00Z`.

Telegram callback timestamps use the compact versioned form `u1:<unix-seconds>`. Parsing and serialization are centralized in `bot.core.time` and re-exported by `bot.telegram.callback_parser`.

### Legacy callback compatibility

Callbacks and FSM state written before this policy may contain a naive ISO timestamp such as `2026-08-03T16:45:00`. The compatibility parser interprets that value as Moscow wall time and converts it to UTC.

The compatibility branch has a declared sunset date of **2026-11-01** (`LEGACY_CALLBACK_COMPATIBILITY_SUNSET`). Removal requires a release note and confirmation that no long-lived callback/state payloads from the old format remain. New code must not emit the legacy form.

## Enforcement

`scripts/check_time_policy.py` scans runtime Python code for direct `datetime.now()`, `datetime.utcnow()`, `datetime.today()`, `date.today()`, `pytz`, and `dateutil.tz` usage. Existing legacy occurrences are recorded in `quality/time-policy-baseline.json`.

The baseline is a one-way ratchet: entries may be removed as code is migrated, but new entries and increased counts fail CI. This allows the remaining large legacy adapters to be migrated incrementally without permitting any further spread of ambiguous time handling.

## Operational notes

- The application does not depend on the PostgreSQL session timezone for business dates.
- `timestamptz` values are instants; `AT TIME ZONE 'Europe/Moscow'` is applied only when deriving Moscow wall-clock dates or display values.
- Backups taken before migration 018 contain historical naive Moscow values. Restore procedures must apply all migrations before starting application workers.
