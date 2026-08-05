# Approved temporary-data cleanup

The automated privacy cleanup is intentionally limited to one reviewed rule:

- dataset: `schedule_operator_state`
- table: `schedule_setup_sessions`
- policy: `temporary_7d`
- eligibility: inactive rows whose `updated_at` is before the UTC day boundary seven days ago
- maximum batch: 1,000 rows per worker run

## Safety controls

The service refuses to run unless the inventory keeps both the retention class and
the rule explicitly approved and destructively enabled. Every plan contains the
inventory SHA-256, a fixed cutoff, the eligible row count, a batch limit, and a
canonical plan SHA-256.

Apply runs in a serializable PostgreSQL transaction. It obtains a global advisory
transaction lock, recounts eligible rows, aborts on drift, deletes a bounded batch
with `FOR UPDATE SKIP LOCKED`, and writes a pseudonymous `privacy.cleanup.applied`
audit record. Existing database triggers prevent privacy audit rows from being
updated or deleted.

Active schedule workflows are never eligible, even when their timestamps are old.

## Operation

The bot worker runs the approved rule once per day and exposes aggregate metrics.
It never logs row payloads or Telegram user identifiers.

Operators can inspect the current plan:

```bash
python scripts/privacy_cleanup.py plan --pretty
```

The plan prints an exact `confirmation_token`. Manual execution requires that
unchanged token on the same UTC day:

```bash
python scripts/privacy_cleanup.py apply --confirm 'APPLY:<plan_sha256>' --pretty
```

The inventory entry for `schedule_setup_deck_scopes` was removed because no such
table exists in the canonical migration history or reference schema. No cleanup
job is permitted for proposed or legally undecided retention classes.

Deleted primary rows can remain in encrypted backups until the documented backup
retention window expires; backups are not rewritten by this job.
