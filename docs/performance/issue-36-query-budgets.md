# Issue #36: database performance contracts

## Runtime behavior

Administrative lists use forward keyset pagination. Each page performs one SQL
round trip and requests `page_size + 1` rows to decide whether a next cursor is
needed. The handler never loads the complete `users`, `admins`, or trusted-name
sets and performs no per-row lookup.

`UserSyncMiddleware` keeps a bounded profile fingerprint cache. An unchanged
Telegram profile does not schedule PostgreSQL work. A changed profile is
written in a background task with a 750 ms timeout; the SQL `ON CONFLICT ...
WHERE ... IS DISTINCT FROM ...` guard also avoids a physical update after a
cold cache or process restart.

Critical page and profile operations are measured by `db.performance`:

- round trips and failures;
- rolling p50, p95, and maximum latency;
- slow-query count (250 ms default threshold);
- maximum observed asyncpg pool utilization;
- explicit saturation log events when no idle connection remains.

The snapshot deliberately contains operation names and timings only. SQL text,
Telegram identifiers, usernames, and bind values are not logged.

## Representative CI data set

`tests/integration/test_query_performance_postgres.py` creates 50,000 users,
1,000 administrators, and 1,000 manual trusted usernames on PostgreSQL 17. It
runs `ANALYZE`, captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and checks
both the selected indexes and the execution budgets.

| Query | Required access path | CI budget |
|---|---|---:|
| Users after `user_id` cursor | `users_pkey` | < 250 ms |
| Case-insensitive username lookup | `ix_users_username_ci` | < 250 ms |
| Trusted users after composite cursor | `ix_users_trusted_username_ci` | < 250 ms |
| Admin/users/trusted page | one measured round trip per page | 1 round trip |

The 250 ms ceiling is intentionally generous for shared GitHub runners. The
rolling runtime metric uses the same value as the slow-query threshold, so a
production regression becomes visible before it exhausts the pool.

## Before/after update-path measurement

The unit performance contract models the former middleware with a fixed 4 ms
DB wait on every update and compares it with the new background/debounced path.
For 30 unchanged updates it requires:

- legacy round trips: 30;
- optimized round trips: 1;
- optimized handler p95: less than 25% of legacy handler p95.

This is a controlled regression test, not a claim about production network
latency. Production values are available through
`database_performance_snapshot()` and should be exported by the observability
work in issue #42.

## Index audit

Migration `017_query_performance_indexes.sql` adds or verifies:

- case-insensitive user/trusted-name lookup and trusted keyset order;
- auction owner, bid ranking, pending-delete, card/deck, exchange, and market
  access paths;
- Moscow business-date schedule lookup;
- auction publication/finalization due queues;
- Telegram outbox pending/processing queues.

Indexes are additive and idempotent because runtime migrations execute inside a
transaction. Existing names from migrations 014–016 are retained so upgraded
installations and clean installations converge on the same query contracts.

## N+1 audit

The audited administrative page queries join usernames/roles in the page SQL;
there is no follow-up lookup per rendered row. Existing schedule and ownership
paths already use set-based SQL: `get_auctions_by_date` resolves cards through a
single lateral join, `get_lots_by_owner` joins `auction_owners`, and card/deck
lists query the whole requested deck in one statement. Migration 017 supplies
the corresponding date, owner, and deck/card indexes. The integration metric
assertions protect the new admin pages at exactly one round trip per page.
