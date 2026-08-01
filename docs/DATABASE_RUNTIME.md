# PostgreSQL runtime ownership

## Contract

Each process constructs exactly one `db.pool.DatabaseRuntime` in its composition
root. The runtime owns the asyncpg pool, its initialization lock and shutdown.
The same object is passed to `db.lifecycle.init_db()` and
`db.lifecycle.close_db()`.

`db.core.DatabaseAccess` is a temporary non-owning adapter for existing query
functions. It delegates to the runtime installed by the composition root and
must not create, close or replace a production pool.

Only `db/pool.py::DatabaseRuntime.start()` may call `asyncpg.create_pool`.
`bot`, `userbot`, handlers, domain code and repositories must not create pools.

## Process lifecycle

1. Parse and validate `DatabaseSettings` in the process entrypoint.
2. Construct `DatabaseRuntime(settings)` in the application composition root.
3. Call `await init_db(runtime)` before registering work that uses PostgreSQL.
4. Inject pool/repository/UoW dependencies into new application services.
5. Call `await close_db(runtime)` during shutdown, including failed startup.

A runtime can be started again after shutdown. Its old pool and event-loop lock
are discarded, so repeated start/stop tests cannot observe stale references.

## Deprecation map

| Deprecated API | Replacement | Removal target |
| --- | --- | --- |
| `db.core.pool_proxy` | Constructor-injected repository or `DatabaseRuntime` | issue #31 vertical slices |
| `db.core.db_pool` | Constructor-injected repository/UoW | issue #31 vertical slices |
| `db.core.get_db_pool()` | `DatabaseRuntime.start()` in composition/lifecycle code | after scripts migrate |
| `db.core.init_db/close_db` re-exports | `db.lifecycle.init_db(runtime)` / `close_db(runtime)` | next major cleanup |
| `db.pool.configure_database()` | Construct `DatabaseRuntime(settings)` | after maintenance scripts migrate |
| `db.legacy` | Thematic repository/use case | issues #29-#31 |
| `db.db` | Thematic repository/use case | issues #29-#31 |
| `db.legacy_impl` | Removed; operations live in thematic `db.*` modules | completed in #28 |
| Direct SQL from handlers | Application use case + repository | issue #30 |

Compatibility names are permitted only while they delegate to the installed
runtime. They may not own a pool, mutate another module's pool reference or
silently construct a connection pool.

## CI rules

`scripts/check_database_boundaries.py` enforces:

- exactly one `asyncpg.create_pool` call, in `db/pool.py`;
- no `db/legacy_impl.py` runtime module;
- no handler/domain imports of `db.core`, `db.pool`, `db.lifecycle` or
  `db.legacy_impl`.

`scripts/check_persistence_exceptions.py` keeps the typed persistence error
policy and rejects restoration of the retired implementation.

## Testing

Unit tests create independent runtimes with injected pool factories. The
PostgreSQL CI service also runs a real start/stop/start cycle to verify that a
closed pool is not retained and a new pool can be opened in the same process.
