# Mac Migration Freeze

Freeze date: 2026-07-08

This repository is the durable project handoff for moving the Yungu PM workspace from WSL to macOS. The rule is: Git carries code, docs, migration-safe data, and reproducible configuration. Machine-local runtime state stays out of Git and must be rebuilt or copied over a secure side channel.

## What Is In Git

- `apps/`: Platform, Watchlist, and Portfolio backend/frontend source, tests, Alembic migrations, and app docs.
- `packages/`: shared Python and frontend packages.
- `infra/`: PostgreSQL schema bootstrap, instrument-registry migrations, and the Linux user-systemd market-data timer installer.
- `docs/`: operating docs, design baseline, database workflow, and this migration note.
- `.env.example`: safe local-development configuration templates.
- `nav/`: NAV attachment image data captured for migration.
- `data/migration/`: portable PostgreSQL dump and checksum files for this freeze.

## What Is Not In Git

- Real `.env` and `.env.*` files: carry secrets and local service endpoints; migrate them separately if still needed. `.env.example` templates are intentionally kept in Git.
- `.local-pg/`: raw PostgreSQL data directory; use the dump in `data/migration/` instead.
- `node_modules/`, `.venv/`, `dist/`, `build/`, caches, bytecode, SQLite files, and local backups.
- `ref/`: local reference checkout/material, including its own `.git`, dependencies, and caches.
- User-level `systemd --user` unit files under `~/.config/systemd/user`; macOS cannot run these directly.

## Data Snapshot

The freeze includes:

- `nav/`: 104 NAV attachment files, about 65 MB total.
- `data/migration/yungu_local_2026-07-08.pgdump`: custom-format PostgreSQL dump for schemas `instrument_registry`, `portfolio`, and `watchlist`.
- `data/migration/yungu_local_2026-07-08.sha256`: checksum for the dump.

The raw local database size at audit time was approximately:

- `instrument_registry`: 174 MB
- `portfolio`: 99 MB
- `watchlist`: 11 MB

## Fresh Mac Bring-Up

These steps assume a new macOS machine and a fresh clone. Registering or starting the backend services does not create the old business data by itself; the freeze dump must be restored into PostgreSQL first.

### 1. Clone And Install Tools

Clone the GitHub repository, then install:

- Python 3.12 or newer
- Node.js and npm
- Docker Desktop or Colima
- PostgreSQL client tools: `psql`, `pg_isready`, `pg_restore`

### 2. Start PostgreSQL

The repository includes a local Docker profile that creates the `yungu` database, `yungu` user, and the three schemas used by this workspace.

```bash
(cd infra/postgres && docker compose up -d)

until pg_isready -h 127.0.0.1 -p 5432 -U yungu -d yungu; do
  sleep 1
done
```

Do not run `./infra/postgres/rebuild_local_schemas.sh` after restoring the dump unless you intentionally want to wipe the restored data. That script drops and recreates `instrument_registry`, `portfolio`, and `watchlist`.

### 3. Restore The Freeze Data

Validate the committed dump before restoring it:

```bash
sha256sum -c data/migration/yungu_local_2026-07-08.sha256
```

On macOS, `sha256sum` may be installed by `brew install coreutils`; if it is unavailable, use:

```bash
shasum -a 256 -c data/migration/yungu_local_2026-07-08.sha256
```

Restore the data into the local database:

```bash
PGPASSWORD=yungu \
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U yungu -d yungu \
  data/migration/yungu_local_2026-07-08.pgdump
```

Check that the restored schemas are populated:

```bash
PGPASSWORD=yungu psql -h 127.0.0.1 -U yungu -d yungu -c "
select 'instrument_registry.instrument' as table_name, count(*) from instrument_registry.instrument
union all
select 'portfolio.portfolio_record', count(*) from portfolio.portfolio_record
union all
select 'watchlist.watchlist', count(*) from watchlist.watchlist;
"
```

Exact row counts can change after later refreshes, but these tables should not be empty immediately after a successful restore.

### 4. Register Local Backend Configuration

Copy the committed templates into local `.env` files:

```bash
cp apps/platform/backend/.env.example apps/platform/backend/.env
cp apps/watchlist/backend/.env.example apps/watchlist/backend/.env
cp apps/portfolio/backend/.env.example apps/portfolio/backend/.env
```

Then edit only the local `.env` files for secrets and machine-specific endpoints:

- `YUNGU_PLATFORM_TUSHARE_TOKEN`
- email IMAP credentials, if mail refresh should run on the Mac
- Watchlist copilot API key, if enabled
- any non-local database URL, CORS, or frontend URL override

The local development database defaults are:

- database: `yungu`
- user: `yungu`
- password: `yungu`
- host: `127.0.0.1`
- port: `5432`
- schemas: `instrument_registry`, `watchlist`, `portfolio`

### 5. Install Backend Dependencies

One shared virtual environment is enough for local migration validation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e packages/instrument-core/python
pip install -e apps/platform/backend
pip install -e apps/watchlist/backend
pip install -e apps/portfolio/backend
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
npm --prefix apps/platform/frontend run dev -- --host 0.0.0.0 --port 5172
npm --prefix apps/watchlist/frontend run dev -- --host 0.0.0.0 --port 5173
npm --prefix apps/portfolio/frontend run dev -- --host 0.0.0.0 --port 5174
```

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

Then run the validation commands from `README.md`. On macOS, recreate background jobs later with foreground dev commands, `launchd`, Homebrew services, or Docker-managed processes; do not copy Linux `systemd --user` unit files blindly.

## Services

The WSL runtime had these user-systemd services active at freeze time:

- `yungu-platform-backend.service` on `127.0.0.1:8002`
- `yungu-watchlist-backend.service` on `127.0.0.1:8000`
- `yungu-portfolio-backend.service` on `127.0.0.1:8001`
- `yungu-platform-frontend.service` on `0.0.0.0:5172`
- `yungu-watchlist-frontend.service` on `0.0.0.0:5173`
- `yungu-portfolio-frontend.service` on `0.0.0.0:5174`
- `yungu-market-data-refresh.timer` scheduled at `09:00 Asia/Shanghai`

On macOS, recreate these as foreground dev commands, `launchd` jobs, Homebrew services, or Docker-managed processes. Do not copy Linux unit files blindly.

## Secrets

The repository intentionally excludes backend `.env` files. Review and migrate only the values still needed for:

- database connection overrides
- Platform Tushare token and API URL
- email IMAP refresh settings
- Watchlist copilot provider/API key, if enabled
- CORS/frontend URL overrides for any non-local deployment

Do not paste secrets into docs, Git commit messages, or chat transcripts.
