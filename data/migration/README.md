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
CONFIRM_RESTORE=portfolio_ops infra/postgres/restore_project_dump.sh
```

The wrapper requires and verifies the checksum, validates the archive and exact
database target, stops any installed local app services, disconnects remaining
clients, and writes a private pre-restore backup of the current three schemas.
It then restores only `instrument_registry`, `portfolio`, and `watchlist` and
applies all current migrations. If restore, validation, or migration fails, it
automatically drops the partial state, restores the safety backup, and restarts
the services. Services are restarted only after either the new database or the
rollback is complete.

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
