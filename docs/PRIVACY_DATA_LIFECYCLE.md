# Personal-data lifecycle

Status: engineering baseline for issue #45. This document is not a legal retention opinion.

## Safety boundary

The application currently has no destructive personal-data lifecycle command. The first
implementation is deliberately fail-closed:

- the machine-readable inventory is the source of truth;
- cleanup only has `validate` and read-only `plan` modes;
- every retention class and cleanup rule keeps `destructive_enabled=false`;
- a plan emits counts and policy metadata, never row values;
- export, anonymization and deletion require a later reviewed implementation and
  production acceptance.

A lot-deletion request is not a personal-data deletion request. Existing auction and
exchange deletion controls must not be reused for privacy requests.

## Inventory and sensitivity

The canonical registry is `docs/privacy/data_inventory.json`. It groups the current
privacy-relevant schema into these domains:

1. Telegram identity profiles and privileged roles.
2. UID identity, encrypted UID material, verification proofs and abuse-prevention records.
3. Auction owners, bids, autobids, winners and publication recovery.
4. Exchange submissions, proofs, moderators and manual winners.
5. Market listings and Telegram media identifiers.
6. Notification preferences and delivery suppression.
7. Appeals, bans, warnings and moderation requests.
8. Operator audit history.
9. Telegram outbox payloads and delivery errors.
10. Temporary schedule-setup and publication-review state.

The registry records purpose, sensitivity, permitted access roles, retention class,
backup presence, deletion action and exceptions for each domain.

## Retention matrix

| Class | Period | Current status | Automated mutation |
| --- | ---: | --- | --- |
| `temporary_7d` | 7 days | approved engineering policy | disabled |
| `operational_30d` | 30 days | proposed | disabled |
| `security_365d` | 365 days | proposed | disabled |
| `business_hold` | not fixed | owner/legal decision required | disabled |
| `account_lifetime` | not fixed | owner/legal decision required | disabled |

Only temporary schedule workflow state has approved cleanup rules. Even those rules are
plan-only until a separate change introduces an audited apply path.

## Backup semantics

Primary deletion cannot promise immediate disappearance from backups. The current recovery
policy retains local backup artifacts for 14 days and targets 90 days for encrypted
off-host archives. Off-host provisioning is tracked in `Stellmaria/Velvet#614`.

When deletion is eventually implemented, the audit response must state that expired backup
copies disappear through scheduled retention, not by unsafe surgery on immutable archives.

## Log redaction

Operational structured logs pass through `bot.core.privacy.redact`. The contract masks:

- tokens, passwords, secrets and authenticated DSN passwords;
- phone numbers and 24-character UID values;
- Telegram usernames and labelled Telegram/user/chat identifiers in free text;
- mapping fields that contain user, actor, owner, winner, bidder, moderator, admin,
  username, UID, session or Telegram file identifiers;
- the same material inside exception tracebacks and structured extras.

Correlation IDs, operation IDs, auction IDs and message IDs remain available unless they
are nested under a field explicitly classified as personal.

Application user-facing messages and restricted moderator screens are not operational
logs. Their access rules are reviewed separately.

## Cleanup planner

Validate the policy:

```bash
python -m scripts.privacy_cleanup validate
```

Produce an offline contract plan:

```bash
python -m scripts.privacy_cleanup plan --offline --pretty
```

Produce a production-like read-only count plan using the existing `DATABASE_URL`:

```bash
python -m scripts.privacy_cleanup plan --pretty
```

The planner sets PostgreSQL read-only mode, applies statement and lock timeouts, and emits
only aggregate counts. There is intentionally no `apply` command.

## Required successor work

Issue #45 remains open after this foundation slice. The next reviewed slices must add:

1. authenticated user export with field-level access controls;
2. a transactional anonymization/delete plan that respects business and security holds;
3. immutable audit evidence containing no personal values;
4. integration tests against disposable PostgreSQL;
5. owner/legal approval for proposed retention classes;
6. production scheduling only after dry-run evidence and rollback review.
