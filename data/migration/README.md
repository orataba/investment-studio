# Migration Data

This directory stores portable data snapshots that are intentionally committed for migration.

- `yungu_local_2026-07-08.pgdump`: PostgreSQL custom-format dump for `instrument_registry`, `portfolio`, and `watchlist`.
- `yungu_local_2026-07-08.sha256`: checksum for the dump.

Starting or registering the backend services on a new computer does not recreate the old data automatically. Restore this dump into PostgreSQL before using the apps.

Minimal restore flow:

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U yungu -d yungu; do
  sleep 1
done

sha256sum -c data/migration/yungu_local_2026-07-08.sha256
```

If `sha256sum` is unavailable on macOS, install GNU coreutils or use:

```bash
shasum -a 256 -c data/migration/yungu_local_2026-07-08.sha256
```

Then restore:

```bash
PGPASSWORD=yungu \
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U yungu -d yungu \
  data/migration/yungu_local_2026-07-08.pgdump
```

Quick verification:

```bash
PGPASSWORD=yungu psql -h 127.0.0.1 -U yungu -d yungu -c "
select 'instrument_registry.instrument' as table_name, count(*) from instrument_registry.instrument
union all
select 'portfolio.portfolio_record', count(*) from portfolio.portfolio_record
union all
select 'watchlist.watchlist', count(*) from watchlist.watchlist;
"
```

Do not run `./infra/postgres/rebuild_local_schemas.sh` after restoring unless you intentionally want to delete and rebuild the restored schemas.
