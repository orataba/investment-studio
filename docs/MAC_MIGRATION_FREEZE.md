# Mac Migration Freeze

> Security update (2026-07-10): the three real backend `.env` files were removed from the current tree and all reachable Git history. Statements below that describe committed secrets are superseded by this note. Git keeps only `.env.example`; real values live in a machine-local protected secret store.

Freeze date: 2026-07-09

This repository is the durable project handoff for moving `Portfolio Operations Workbench` from WSL to macOS. Git carries code, docs, migration-safe data, configuration templates, and reproducible setup notes. Secrets and machine-local runtime state stay out of Git and must be restored separately.

## What Is In Git

- `apps/`: Platform, Watchlist, and Portfolio backend/frontend source, tests, Alembic migrations, and app docs.
- `packages/`: shared Python and frontend packages.
- `infra/`: PostgreSQL schema bootstrap, instrument-registry migrations, macOS user-LaunchAgent services, and the Linux user-systemd market-data timer installer.
- `docs/`: operating docs, design baseline, database workflow, and this migration note.
- `.env.example`: local-development configuration templates.
- `nav/`: NAV attachment image data captured for migration.
- `data/migration/`: portable PostgreSQL dump and checksum files for this freeze.

## What Is Not In Git

- `.local-pg/`: raw PostgreSQL data directory; use the dump in `data/migration/` instead.
- `node_modules/`, `.venv/`, `dist/`, `build/`, caches, bytecode, SQLite files, and local backups.
- `ref/`: local reference checkout/material, including its own `.git`, dependencies, and caches.
- User-level `systemd --user` unit files under `~/.config/systemd/user`; macOS cannot run these directly.

## Data Snapshot

The freeze includes:

- `nav/`: 104 NAV attachment files, about 65 MB total.
- `data/migration/portfolio_ops_2026-07-09_current.pgdump`: custom-format PostgreSQL dump for schemas `instrument_registry`, `portfolio`, and `watchlist`.
- `data/migration/portfolio_ops_2026-07-09_current.sha256`: checksum for the dump.

The committed dump was created from the local `portfolio_ops` PostgreSQL database at `2026-07-09 16:53 Asia/Shanghai`, after the A-share ETF core-pool import, initial market-data backfill, All Covered watchlist materialization, and local database rename cleanup finished. It includes only the Portfolio Operations project schemas: `instrument_registry`, `portfolio`, and `watchlist`.

The project schema sizes at audit time were approximately:

- `instrument_registry`: 174 MB
- `portfolio`: 115 MB
- `watchlist`: 12 MB

## Fresh Mac Bring-Up

These steps assume a new macOS machine and a fresh clone. Registering or starting the backend services does not create the old business data by itself; the freeze dump must be restored into PostgreSQL first.

### 1. Clone And Install Tools

Clone the GitHub repository, then install:

- Python 3.12 or newer
- Node.js and npm
- Docker Desktop or Colima
- PostgreSQL client tools: `psql`, `pg_isready`, `pg_restore`

### 2. Start PostgreSQL

The repository includes a local Docker profile that creates the `portfolio_ops` database, `portfolio_ops` user, and the three schemas used by this workspace.

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U portfolio_ops -d portfolio_ops; do
  sleep 1
done
```

Do not run `./infra/postgres/rebuild_local_schemas.sh` after restoring the dump unless you intentionally want to wipe the restored data. That script drops and recreates `instrument_registry`, `portfolio`, and `watchlist`.

### 3. Restore The Freeze Data

Validate the committed dump before restoring it:

```bash
sha256sum -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

On macOS, `sha256sum` may be installed by `brew install coreutils`; if it is unavailable, use:

```bash
shasum -a 256 -c data/migration/portfolio_ops_2026-07-09_current.sha256
```

Restore the data into the local database:

```bash
CONFIRM_RESTORE=portfolio_ops infra/postgres/restore_project_dump.sh
```

The wrapper stops the installed local launchd jobs before taking a private
pre-restore backup. It restores only the three project schemas, runs all current
migrations, and restarts the jobs after success. Any restore or migration
failure automatically replaces the partial database with the safety backup
before the jobs are restarted. The retained backup path is printed at the end.

Check that the restored schemas are populated:

```bash
PGPASSWORD=portfolio_ops psql -h 127.0.0.1 -U portfolio_ops -d portfolio_ops -c "
select 'instrument_registry.instrument' as table_name, count(*) from instrument_registry.instrument
union all
select 'portfolio.portfolio_record', count(*) from portfolio.portfolio_record
union all
select 'watchlist.watchlist', count(*) from watchlist.watchlist;
"
```

Exact row counts can change after later refreshes, but these tables should not be empty immediately after a successful restore.

### 4. Verify Local Backend Configuration

The repository carries only `.env.example`. Restore the three real backend `.env` files from the protected machine-local secret store:

```bash
test -f apps/platform/backend/.env
test -f apps/watchlist/backend/.env
test -f apps/portfolio/backend/.env
```

Review them locally if the new machine uses different endpoints:

- `PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN`
- email IMAP credentials, if mail refresh should run on the Mac
- Watchlist copilot API key, if enabled
- any non-local database URL, CORS, or frontend URL override

The local development database defaults are:

- database: `portfolio_ops`
- user: `portfolio_ops`
- password: `portfolio_ops`
- host: `127.0.0.1`
- port: `5432`
- schemas: `instrument_registry`, `watchlist`, `portfolio`

### 5. Install Backend Dependencies

One shared virtual environment is enough for local migration validation:

```bash
infra/scripts/sync_python_env.sh
source .venv/bin/activate
```

### 6. Install Frontend Dependencies

```bash
npm --prefix apps/platform/frontend install
npm --prefix apps/watchlist/frontend install
npm --prefix apps/portfolio/frontend install
```

### 7. Start The Backends

Run these in three terminals with the virtual environment active:

```bash
(cd apps/platform/backend && uvicorn platform_app.main:app --host 127.0.0.1 --port 8002 --reload)
(cd apps/watchlist/backend && uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000 --reload)
(cd apps/portfolio/backend && uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001 --reload)
```

### 8. Start The Frontends

Run these in three more terminals:

```bash
npm --prefix apps/platform/frontend run dev -- --host 127.0.0.1 --port 5172
npm --prefix apps/watchlist/frontend run dev -- --host 127.0.0.1 --port 5173
npm --prefix apps/portfolio/frontend run dev -- --host 127.0.0.1 --port 5174
```

The Vite development servers are loopback-only by default. Use an authenticated reverse proxy for any deliberate remote access.

Then open:

- Platform: `http://127.0.0.1:5172`
- Watchlist: `http://127.0.0.1:5173`
- Portfolio: `http://127.0.0.1:5174`

### 9. Verify The Runtime

Check health endpoints:

```bash
curl --noproxy '*' http://127.0.0.1:8002/api/health
curl --noproxy '*' http://127.0.0.1:8000/api/health
curl --noproxy '*' http://127.0.0.1:8001/api/health
```

Then run the validation commands from `README.md`. On macOS, install the current
user LaunchAgents with `infra/launchd/install_local_services.sh`; do not copy the
historical Linux `systemd --user` unit files.

## Services

The WSL runtime had these user-systemd services active at freeze time. Older installed unit names may still use the pre-rename prefix; new installs should use the generic timer installer defaults.

- platform backend on `127.0.0.1:8002`
- watchlist backend on `127.0.0.1:8000`
- portfolio backend on `127.0.0.1:8001`
- platform frontend on `127.0.0.1:5172`
- watchlist frontend on `127.0.0.1:5173`
- portfolio frontend on `127.0.0.1:5174`
- `portfolio-ops-market-data-refresh.timer` scheduled at `08:00 Asia/Shanghai`

The `08:00` entry above records the frozen WSL state only. The current installers
use `21:00`: macOS registers
`com.orataba.portfolio-ops.market-data-refresh` through
`infra/launchd/install_local_services.sh`, and the Linux/systemd installer also
defaults to `21:00 Asia/Shanghai`. The macOS job is a one-shot calendar task with
`KeepAlive=false`, so installing or restoring services does not trigger it
immediately.

## Configuration And Secrets

The repository intentionally excludes real backend `.env` files. Restore and rotate only the values still needed for:

- database connection overrides
- Platform Tushare token and API URL
- email IMAP refresh settings
- Watchlist copilot provider/API key, if enabled
- CORS/frontend URL overrides for any non-local deployment

Do not paste secret values into docs, Git commit messages, issue text, PR descriptions, or chat transcripts.
