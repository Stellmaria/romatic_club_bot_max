# PostgreSQL integration suite

This suite is intentionally destructive. Every test creates a uniquely named sibling database, applies the required schema, and drops that database after the test. It refuses to run unless both safeguards are present:

- `TEST_DATABASE_URL` points to a database whose name contains `test`, `testing`, `integration`, or `ci`;
- `POSTGRES_INTEGRATION_CONFIRM=1` is set explicitly.

The server must be PostgreSQL 17, matching the production Compose major version.

## Local run

Run the complete suite with one command:

```bash
python scripts/run_postgres_integration.py
```

The runner starts `postgres:17-alpine`, waits for readiness, sets the destructive-test confirmation, runs only the `integration` marker, and removes the container afterwards.

Additional pytest arguments may be passed after `--`:

```bash
python scripts/run_postgres_integration.py -- -k publication -vv
```

To inspect the container after a run:

```bash
python scripts/run_postgres_integration.py --keep-container
```

## Manual service

A pre-existing disposable PostgreSQL can also be used:

```bash
export TEST_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/integration_test'
export POSTGRES_INTEGRATION_CONFIRM=1
python -m pytest -m integration tests/integration
```

Never point these variables at production, staging, or a developer database containing useful data. The fixtures require permission to create and drop sibling databases.

## Failure diagnostics

When `POSTGRES_KEEP_FAILED_DATABASES=1` is set, a failed test leaves its isolated database intact and records its name in:

```text
var/integration-artifacts/failed-databases.txt
```

The local runner and CI collect:

- PostgreSQL container logs;
- database activity statistics;
- schema-only dumps of failed isolated databases.

CI uploads these files as the `postgres-integration-diagnostics` artifact.

## Covered contracts

The suite verifies:

- clean migration install, idempotent rerun, checksums and PostgreSQL version;
- advisory-lock serialization of concurrent migration runners;
- transactional rollback of failed migrations;
- upgrade from the supported minimal legacy migration journal;
- unique, check and foreign-key constraints;
- concurrent schedule approval, bid placement and publication claims;
- independent role updates under row contention;
- globally unique UID approval under concurrent requests;
- transactional outbox deduplication, `SKIP LOCKED`, and unknown-delivery handling.
