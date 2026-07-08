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

## Restore On Mac

1. Clone the repository.
2. Install Python, Node.js, npm, PostgreSQL tooling, and Docker or a local PostgreSQL server.
3. Start PostgreSQL and create database/user according to `infra/postgres/docker-compose.yml`, then copy each backend `.env.example` to `.env` and adapt it to your local database.
4. Restore the freeze dump:

```bash
pg_restore --clean --if-exists --no-owner --no-acl \
  -h 127.0.0.1 -U yungu -d yungu \
  data/migration/yungu_local_2026-07-08.pgdump
```

5. Install frontend dependencies:

```bash
npm --prefix apps/platform/frontend install
npm --prefix apps/portfolio/frontend install
npm --prefix apps/watchlist/frontend install
```

6. Create Python environments for each backend or one shared development environment, then install the backend dependencies from each backend `pyproject.toml`.
7. Run the validation commands from `README.md`.

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
