# Migration Data

This directory stores portable data snapshots that are intentionally committed for migration.

- `portfolio_ops_2026-07-09_current.pgdump`: PostgreSQL custom-format dump for `instrument_registry`, `portfolio`, and `watchlist`.
- `portfolio_ops_2026-07-09_current.sha256`: checksum for the dump.

Snapshot source:

- Created from the local `portfolio_ops` PostgreSQL database at `2026-07-09 16:53 Asia/Shanghai`, after the A-share ETF core-pool import, initial market-data backfill, All Covered watchlist materialization, and local database rename cleanup finished.
- Includes only the Portfolio Operations project schemas: `instrument_registry`, `portfolio`, and `watchlist`.
- Excludes `public` and any non-project schemas; they are not required to restore this project.

Starting or registering the backend services on a new computer does not recreate the old data automatically. Restore this dump into PostgreSQL before using the apps.

Minimal restore flow:

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U portfolio_ops -d portfolio_ops; do
  sleep 1
done

sha256sum -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

If `sha256sum` is unavailable on macOS, install GNU coreutils or use:

```bash
shasum -a 256 -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

Then restore with the project wrapper. The explicit confirmation protects an
existing local database from accidental replacement:

```bash
PORTFOLIO_OPS_DB_HOST=127.0.0.1 \
PORTFOLIO_OPS_DB_PORT=5432 \
PORTFOLIO_OPS_DB_NAME=portfolio_ops \
PORTFOLIO_OPS_DB_USER=portfolio_ops \
CONFIRM_RESTORE=portfolio_ops \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=YYYY-MM-DD \
  infra/postgres/restore_project_dump.sh
```

The wrapper requires and verifies the checksum, validates the archive and exact
database target, stops any installed local app services, disconnects remaining
clients, and writes a private pre-restore backup of the current three schemas.
It then restores only `instrument_registry`, `portfolio`, and `watchlist` and
applies all current migrations, rebuilds Portfolio and Watchlist for the explicit
as-of date, and requires a zero-failure/zero-warning audit. If restore,
validation, migration, rebuild, or audit fails, it automatically drops the
partial state and restores the safety backup. A successful rollback restarts the
previously active services; a rollback failure leaves them stopped and prints
the retained recovery-artifact path. The wrapper accepts explicit
`PORTFOLIO_OPS_DB_*` target fields rather than a database URL and independently
verifies the connected database identity before destructive work.

Safety backups are retained under
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`.
Override that location with `PORTFOLIO_OPS_RESTORE_BACKUP_DIR`. A non-local
database target is refused by default and requires both
`ALLOW_REMOTE_RESTORE=true` and the full confirmation token printed by the
wrapper.

Quick verification:

```bash
PGPASSWORD=portfolio_ops psql -h 127.0.0.1 -U portfolio_ops -d portfolio_ops -c "
select 'instrument_registry.instrument' as table_name, count(*) from instrument_registry.instrument
union all
select 'portfolio.portfolio_record', count(*) from portfolio.portfolio_record
union all
select 'watchlist.watchlist', count(*) from watchlist.watchlist;
"
```

Do not run `./infra/postgres/rebuild_local_schemas.sh` after restoring unless you intentionally want to delete and rebuild the restored schemas.
