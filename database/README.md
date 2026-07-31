# Database schema policy

## Sources of truth

The schema inputs have different roles and must not be mixed:

1. `pgadmin_schema.sql` is the newest DDL-only pgAdmin ERD export supplied by
   the owner. It is authoritative for the current table, column, primary-key,
   unique-key and foreign-key inventory that it contains.
2. `reference_schema.sql` is the earlier, fuller catalog snapshot. It remains a
   supplementary source for domain enums, checks, indexes, functions and the
   `v_user_uid_status` view because the pgAdmin export does not include them.
3. `../db/migrations/NNN_description.sql` is the **only executable forward
   history**. `db/migrator.py` is the only runner. Applied files are immutable;
   add a new numbered migration instead of editing an existing one.
4. `bootstrap.sql` is the reproducible empty-database schema at the current code
   contract. It is not a production upgrade script.
5. `migrations/*.sql` is an archived copy of the retired migration history. It
   is kept for audit only, is not packaged and must never receive new files.

The compatibility module `db/migrations.py` delegates to `db.migrator`; it does
not own a second lock, checksum ledger or SQL directory.

The supplied per-table data dump is a private import source, not schema
authority and not a test fixture. Keep it outside version control. Do not read,
log, copy or include its values in issues, tests, documentation or migration
diagnostics.

## Safe deployment order

For a new database:

1. Create an empty PostgreSQL database and install the `pg_trgm` extension, or
   let `bootstrap.sql` create it with an appropriately privileged role.
2. Run `bootstrap.sql`.
3. Start the application migration runner from `db/migrator.py` so every active
   migration version and checksum is recorded.
4. Import private per-table files only into an isolated restore or staging
   database, in foreign-key order, with application writers stopped.
5. Reconcile sequences from catalog maxima, run integrity checks and compare row
   counts privately. Promote only after application smoke tests pass.

For an existing production database, never run `bootstrap.sql`. Take a backup,
capture a fresh DDL-only snapshot, run the normal application startup migration
step and verify the schema before restarting writers.

## Migration rules

1. Add a new numbered, transactional and repeat-safe file only to
   `db/migrations`.
2. The new version must be greater than every existing active version.
3. Guard additive DDL with `IF EXISTS`, `IF NOT EXISTS` or catalog checks.
4. Inventory data before tightening a constraint. Prefer an additive `NOT
   VALID` constraint plus a later validation migration when legacy rows may be
   unknown.
5. Never coerce an unknown status or enum label to a convenient fallback.
6. Do not put private values or row samples in SQL or tests.
7. Update `bootstrap.sql` to the post-migration empty-database shape and extend
   the schema tests.
8. Run static tests and, separately, integration tests against a disposable
   PostgreSQL database. Production connections are never used by the test
   suite.

## Archived history

Files under `database/migrations` document the retired runner used during older
refactoring phases. Their checksums may still be tested for historical audit,
but they are not part of deployment and must not be imported by production
code. Any still-required schema effect from that archive must be expressed as a
new active migration in `db/migrations`, never by re-enabling the old runner.
