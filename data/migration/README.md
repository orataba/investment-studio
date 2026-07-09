# Migration Data

This directory stores portable data snapshots that are intentionally committed for migration.

- `portfolio_ops_2026-07-09_current.pgdump`: PostgreSQL custom-format dump for `instrument_registry`, `portfolio`, and `watchlist`.
- `portfolio_ops_2026-07-09_current.sha256`: checksum for the dump.

Snapshot source:

- Created from the production server at `2026-07-09 14:54 Asia/Shanghai`, after the `2026-07-09 14:35` market-data refresh finished.
- Includes only the Portfolio Operations project schemas: `instrument_registry`, `portfolio`, and `watchlist`.
- Excludes the shared `public` schema in `research_data_foundation`; that schema contains other foundation/project data and is not required to restore this project.

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

Then restore:

```bash
PGPASSWORD=portfolio_ops \
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U portfolio_ops -d portfolio_ops \
  data/migration/portfolio_ops_2026-07-09_current.pgdump
```

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
