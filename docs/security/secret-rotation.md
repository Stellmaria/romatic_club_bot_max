# Secret rotation playbook

This runbook covers production rotation for Telegram credentials, Telethon sessions, UID cryptographic keys, PostgreSQL credentials, and the Server Supervisor token.

## General procedure

1. Open an incident/change record with owner, reason, start time, affected environments, and rollback boundary.
2. Create the replacement secret in the authoritative secret store. Never paste it into Telegram, issue comments, CI logs, shell history, or repository files.
3. Deploy consumers that can accept both old and new values where overlap is required.
4. Switch producers/clients to the new value.
5. Verify readiness, critical business flows, worker health, and audit events.
6. Revoke the old value.
7. Search recent logs and CI artifacts for accidental disclosure, then record completion and evidence.

## Telegram bot token

1. Revoke and regenerate the token through BotFather.
2. Update `BOT_TOKEN` in the production secret store.
3. Restart only the bot service.
4. Verify bot identity, polling/webhook ownership, `/start`, schedule, exchange, auction publication, and admin authorization.
5. Confirm the previous token is rejected.

Expected interruption: polling/webhook delivery pauses between revocation and successful restart.

## Telethon API credentials and session

### API ID/API hash

1. Create replacement application credentials when Telegram permits it.
2. Update the userbot secret store.
3. Re-provision a session if Telegram invalidates the existing authorization.
4. Verify userbot readiness, channel access, publication, and flood-wait handling.
5. Revoke the old application/session where supported.

### Session

1. Stop the userbot to avoid concurrent session writes.
2. Back up the encrypted session artifact and record its checksum.
3. Run the controlled provisioning command from an operator terminal.
4. Store the new session only in the dedicated userbot secret volume.
5. Start the userbot and verify authorization and target channel access.
6. Delete superseded session copies from hosts and operator workstations.

Never commit `.session`, session strings, phone numbers, login codes, or 2FA passwords.

## UID keys

`UID_HASH_KEY` and `UID_ENC_KEY` have different migration properties. Losing either key can make identity matching or decryption impossible.

### `UID_ENC_KEY`

1. Back up the database and verify a full restore in disposable PostgreSQL.
2. Add a key identifier to encrypted records if it is not already present.
3. Set the replacement in `UID_ENC_KEY`, retain the old value temporarily in `UID_ENC_KEY_PREVIOUS`, and deploy. Runtime writes use only the new key while reads accept both.
4. Run the UID encryption migration in bounded transactions. The regression contract `tests/test_uid_key_rotation.py` proves old ciphertext remains readable and rotates to the active key without plaintext loss. Record migrated, skipped, and failed counts without logging plaintext UID values.
5. Verify every encrypted row can be decrypted with the new key and business lookups still work.
6. Remove `UID_ENC_KEY_PREVIOUS` only after every encrypted row is readable with the active key and backup-retention review is complete.

### `UID_HASH_KEY`

A keyed hash cannot be re-derived without decryptable source data. Rotation therefore requires dual-hash support:

1. Deploy support for `uid_hash_v1` and `uid_hash_v2` or an equivalent versioned representation.
2. For every decryptable UID, calculate the new keyed hash and store it transactionally.
3. During migration, query both versions and reject ambiguous duplicates.
4. Verify uniqueness, ownership, bids, audit references, and admin lookup flows.
5. Switch writes and reads to the new hash version, then retire the old key after all rows and backups pass the migration policy.

Do not rotate UID keys by simply replacing environment variables. That converts a security maintenance task into data loss, a surprisingly popular human shortcut.

## PostgreSQL credentials

1. Create a new least-privilege role/password or rotate the existing role inside a controlled maintenance window.
2. Grant only required database/schema/table/sequence privileges.
3. Update `DATABASE_URL` for each service separately.
4. Roll services one at a time and verify pool creation, migrations policy, reads, writes, outbox, and worker transactions.
5. Revoke the previous credential and terminate stale sessions.
6. Verify backups, restore jobs, monitoring, and operator tooling use the new credential.

## Server Supervisor token

1. Generate at least 32 random bytes using an approved password/secret generator.
2. Write the replacement token to the root-owned token file with mode `0600`.
3. Restart the supervisor proxy and dependent bot component in a coordinated window.
4. Verify authenticated status/restart operations and confirm unauthenticated and old-token requests fail.
5. Remove old token files and inspect proxy logs for disclosure.

## Evidence checklist

- Change/incident identifier
- Secret owner and approving operator
- Rotation timestamp
- Deployment commit/image digest
- Readiness and critical-flow results
- Revocation confirmation
- Backup/restore confirmation for UID or DB rotations
- Follow-up date for removing compatibility keys or temporary exceptions
