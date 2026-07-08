# Migration Data

This directory stores portable data snapshots that are intentionally committed for migration.

- `yungu_local_2026-07-08.pgdump`: PostgreSQL custom-format dump for `instrument_registry`, `portfolio`, and `watchlist`.
- `yungu_local_2026-07-08.sha256`: checksum for the dump.

Restore example:

```bash
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U yungu -d yungu \
  data/migration/yungu_local_2026-07-08.pgdump
```
