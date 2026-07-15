# Migration Data Policy

Raw database dumps are not repository assets. They contain business records
and must live in encrypted, access-controlled storage outside Git. This
directory only documents the restore boundary; `.gitignore` blocks common dump
and key formats.

Every restore artifact must be distributed as a pair:

- a PostgreSQL custom-format dump containing only `instrument_registry`,
  `portfolio`, and `watchlist`;
- a sibling SHA-256 file named `<dump-name>.sha256`.

After obtaining the pair through the approved private channel, restrict local
permissions and verify it before restore:

```bash
chmod 600 /secure/path/portfolio-operations-workbench.pgdump \
  /secure/path/portfolio-operations-workbench.sha256

cd /secure/path
shasum -a 256 -c portfolio-operations-workbench.sha256
```

Then pass the absolute dump path explicitly. The wrapper has no repository
default and refuses an omitted or relative path:

```bash
CONFIRM_RESTORE=portfolio_ops \
  infra/postgres/restore_project_dump.sh \
  /secure/path/portfolio-operations-workbench.pgdump
```

The wrapper verifies the checksum and archive, validates the exact database
target, stops installed local services, disconnects remaining clients, and
writes a private pre-restore backup before changing any schema. It restores
only the three project schemas, applies all current migrations, and rolls back
to the safety backup if restore, validation, or migration fails.

Safety backups are stored under
`${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`
unless `PORTFOLIO_OPS_RESTORE_BACKUP_DIR` is set. A non-local target remains
blocked unless the wrapper's explicit remote-restore safeguards are satisfied.
