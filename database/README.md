# Database schema policy

## Sources of truth

The schema has four distinct inputs. They are not interchangeable:

1. `pgadmin_schema.sql` is the newest DDL-only pgAdmin ERD export supplied by
   the owner. It is authoritative for the current table, column, primary-key,
   unique-key, and foreign-key inventory that it contains.
2. `reference_schema.sql` is the earlier, fuller catalog snapshot. It is the
   supplementary source for domain enums, checks, indexes, functions, and the
   `v_user_uid_status` view, because the pgAdmin ERD export does not emit any
   enum, check, function, view, or trigger definitions.
3. `migrations/*.sql` is the immutable forward history. Applied migrations
   must never be edited; add a new numbered migration instead. The runtime
   verifies their SHA-256 checksums in `schema_migrations`.
4. `bootstrap.sql` is the reproducible empty-database schema at the current
   code contract. It is not a production upgrade script. After bootstrap, run
   the normal migration runner once so every migration version/checksum is
   recorded.

The supplied per-table data dump is a private import source, not schema
authority and not a test fixture. Keep it outside version control. Do not read,
log, copy, or include its values in issues, tests, documentation, or migration
diagnostics.

## Safe deployment order

For a new database:

1. Create an empty PostgreSQL database and install the `pg_trgm` extension (or
   let `bootstrap.sql` create it with an appropriately privileged role).
2. Run `bootstrap.sql`.
3. Start the migration runner before importing any data. On an empty database,
   migrations 001-007 are idempotent and establish the checksum ledger without
   rewriting imported rows.
4. Import the private per-table files only into an isolated restore/staging
   database, in foreign-key order, with application writers stopped.
5. Reconcile sequences from catalog maxima, run integrity checks, and compare
   row counts privately. Promote only after application smoke tests pass.

For an existing production database, never run `bootstrap.sql`. Take a backup,
capture a fresh DDL-only snapshot, run the migration runner, and verify the
schema before restarting writers.

Migration 003 is intentionally a data migration: it archives duplicate bid
rows before deleting the duplicates. Migration 005 converts historical Moscow
wall-clock timestamps to `timestamptz`. Migration 006 classifies existing
outbox delivery state. These must run under the migration lock and before any
post-migration dump is imported; do not replay their transformations manually.

## Unknown values and constraints

Never coerce an unknown status or enum label to a convenient fallback during a
schema migration. Migrations 002 and 004 inventory unsupported workflow states
and abort atomically. Resolve such rows through an explicit, reviewed business
mapping, then rerun the migration. Extra enum labels are preserved: removing or
renaming an enum value requires a separate audited migration and a prior data
inventory.

The newest pgAdmin file emits no CHECK constraints or triggers, so their
absence from that ERD export is not proof that production lacks them. Alignment
007 therefore makes no status/enum data rewrite and does not drop or replace
any production object. It only restores the one code-required column that was
missing from the old bootstrap and conditionally installs four behaviours for
which the earlier snapshot functions, old bootstrap trigger definitions, and
current SQL contracts agree:

- immutable auction currency after the first bid;
- automatic `updated_at` on appeals and marketplace listings;
- synchronization of legacy/current UID verification compatibility columns.

The legacy `trg_auctions_fix_end_time` and `trg_prevent_time_change` triggers are
deliberately not installed. They conflict with the variable-duration,
restart/extension workflow introduced by migration 004. Their functions remain
available as historical schema objects, but no current bootstrap trigger calls
them.

## Updating the schema

1. Add a new numbered, transactional and repeat-safe migration.
2. Guard additive DDL with `IF EXISTS`/`IF NOT EXISTS` or catalog checks.
3. Inventory data before tightening a constraint. Prefer an additive `NOT
   VALID` constraint plus a later validation migration when legacy rows may be
   unknown.
4. Do not put private values or row samples in SQL or tests.
5. Update `bootstrap.sql` to the post-migration empty-database shape and extend
   `tests/test_database_schema.py`.
6. Run the static schema tests and, separately, integration tests against a
   disposable PostgreSQL database. No production connection is used by the
   static suite.
