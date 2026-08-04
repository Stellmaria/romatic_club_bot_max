# Database migration, backup and recovery policy

## Objectives

Production database operations use one controlled migration runner. The bot and
userbot always start with `DB_AUTO_MIGRATE=false`; neither application process is
allowed to change schema during startup.

Service objectives:

- **RPO:** at most 24 hours for the encrypted off-host archive, plus the latest
  verified pre-deploy dump for deployment-related incidents.
- **RTO:** 60 minutes for a database restore and controlled application restart.
- **Restore drill frequency:** at least weekly and before every production deploy.
- **Local retention:** 14 days by default.
- **Encrypted off-host retention:** 90 days by default.

Changing these values requires an operations review and an update to `.env`.

## Migration policy

`migration-runner` is the only production service allowed to execute migrations.
The deployment sequence is:

1. validate PostgreSQL major version and free disk space;
2. create and inspect a custom-format `pg_dump`;
3. snapshot the Telethon session;
4. build the target images;
5. restore the dump into disposable PostgreSQL on tmpfs;
6. run the target migration plan and apply it to the disposable copy;
7. run integrity and auction business probes;
8. plan and apply migrations once against production;
9. start bot and userbot with automatic migrations disabled;
10. verify readiness, metrics, database access and zero restart loops.

Migrations after version 19 must begin with all three metadata comments:

```sql
-- compatibility: expand
-- rollback: forward-fix
-- note: Adds a nullable column consumed only by the new code path.
```

Allowed compatibility values:

- `expand`: compatible with the currently running and target code;
- `contract`: removes or tightens an old contract and requires explicit
  `ROMATIC_ALLOW_CONTRACT_MIGRATION=true` during a bounded maintenance phase.

Allowed rollback values:

- `code-only-safe`: old code can run against the migrated schema;
- `forward-fix`: do not automatically roll code back after the schema changes;
- `restore-required`: reversal requires the verified pre-deploy dump and a
  declared data-loss window.

Versions 1 through 19 are immutable legacy migrations and are conservatively
classified as `restore-required` without changing their checksums.

## Rollback matrix

| Deployment state | Automatic action | Operator strategy |
|---|---|---|
| No production migration applied | Restore Telethon snapshot if needed, reset code, rebuild and start previous runtime | Code-only rollback |
| Only `code-only-safe` migrations applied | Reset code and start previous runtime | Code-only rollback, retain expanded schema |
| `forward-fix` or `restore-required` migration applied | Stop bot/userbot and block automatic code rollback | Forward-fix preferred; restore only after impact review |
| Restore drill failed | Runtime is not replaced | Fix dump, schema or migration before retrying |

The database is never restored automatically. Restoring production can discard
writes made after the dump and therefore requires an explicit incident decision.

## Disposable restore drill

Run against a specific dump:

```bash
ROMATIC_APP_DIR=/srv/romatic-club \
  bash deploy/server/restore-drill.sh \
  /srv/romatic-club/server-data/backups/predeploy-<stamp>.dump
```

The drill starts `restore-drill-postgres` with tmpfs storage, restores the full
dump, validates required tables and auction message-ID invariants, applies target
migrations through `migration-runner`, verifies zero pending migrations and writes
a mode `0600` report under:

```text
server-data/runtime/restore-drills/
```

No production table is modified by the drill.

## Encrypted off-host archives

Set these host values in `.env`:

```text
ROMATIC_OFFSITE_BACKUP_DIR=/mnt/remote/romatic-club
ROMATIC_BACKUP_ENCRYPTION_KEY_FILE=/srv/romatic-secrets/backup-aes256.key
```

The destination must be an externally managed mount and contain the marker:

```text
.romatic-offsite-target
```

Generate a 32-byte key without printing it:

```bash
umask 077
openssl rand 32 > /srv/romatic-secrets/backup-aes256.key
```

Archive the latest dump and session snapshot:

```bash
ROMATIC_APP_DIR=/srv/romatic-club \
  bash deploy/server/archive-backups.sh
```

Archives use AES-256-GCM. The job verifies authentication, plaintext size and
SHA-256 before promoting a `.part` file. Manifests contain hashes and sizes, but
never keys, session bytes or database contents.

## Restore incident procedure

1. Stop bot and userbot while preserving PostgreSQL and supervisor-proxy.
2. Select a verified dump and read its restore-drill report.
3. Record the dump timestamp, current incident time and expected data-loss window.
4. Restore into disposable PostgreSQL again and run target verification.
5. Obtain explicit operator approval for production replacement.
6. Restore production using `pg_restore --clean --if-exists --exit-on-error` in a
   controlled maintenance window.
7. Run `python -m db.migrator verify --json` through `migration-runner`.
8. Start bot/userbot and verify readiness, workers, Telegram session and business
   probes.
9. Record actual RPO/RTO and attach sanitized evidence to the incident.

Never paste database credentials, encryption keys, Telegram session contents or
user records into GitHub, chat or restore-drill reports.
