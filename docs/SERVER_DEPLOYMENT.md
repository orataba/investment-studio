# Portfolio Operations Server Deployment

This project runs six user-systemd services in production:

- `portfolio-ops-platform-api.service`
- `portfolio-ops-platform-web.service`
- `portfolio-ops-watchlist-api.service`
- `portfolio-ops-watchlist-web.service`
- `portfolio-ops-portfolio-api.service`
- `portfolio-ops-portfolio-web.service`

The market-data timer is installed separately as:

- `portfolio-ops-market-data-refresh.service`
- `portfolio-ops-market-data-refresh.timer`

## Install App Services

Build the three frontend bundles before installing web services:

```bash
npm --prefix apps/platform/frontend ci
npm --prefix apps/platform/frontend run build
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/portfolio/frontend run build
```

Reproduce the locked Python environment (including test tooling):

```bash
infra/scripts/sync_python_env.sh
```

Provision the three runtime environment files directly from the approved secret
manager. They live outside the worktree; backend `.env` files and symlinks are
not supported. Repository `.env.example` files are key-name templates only and
must never be copied as runtime configuration:

```bash
ENV_ROOT="$HOME/.config/portfolio-ops/env"
install -d -m 700 "$ENV_ROOT"

# Provision these files directly from the secret manager; do not source them.
test -f "$ENV_ROOT/platform.env"
test -f "$ENV_ROOT/watchlist.env"
test -f "$ENV_ROOT/portfolio.env"
chmod 600 "$ENV_ROOT/platform.env" "$ENV_ROOT/watchlist.env" "$ENV_ROOT/portfolio.env"
```

Each file must explicitly set its app database URL. The Platform file must also
set the instrument-registry database URL. Every required variable must contain
the same password-free canonical PostgreSQL target. Store authentication in the
service user's `0600` `.pgpass`, never in a command, repository file, or
credential-bearing URL. A separate Alembic variable is optional; when present it
may use a different database role but must retain the same host, port, and database.

- `platform.env`: `PORTFOLIO_OPS_PLATFORM_DATABASE_URL`,
  `PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL`
- `watchlist.env`: `PORTFOLIO_OPS_WATCHLIST_DATABASE_URL`
- `portfolio.env`: `PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL`

The installer rejects a missing `ENV_ROOT`, repository-local `.env` files or
symlinks, insecure file/directory permissions, password-bearing URLs, and mixed
database targets before it writes or restarts any unit.

Install and start the app units with that external directory explicitly bound:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/systemd/install_app_services.sh
```

The installer binds both API and web services to `127.0.0.1` by default. Set
`API_HOST` or `WEB_HOST` explicitly only when a reverse proxy or network policy
requires another bind address.

The installer generates all six replacement units privately before changing
systemd state. It snapshots the previous unit files, enablement, and active set,
including the market-data refresh service/timer. Before migration it stops every
database writer and creates a verified backup of the three project schemas with
the shared archive/manifest/checksum primitive, then runs
`infra/scripts/migrate_all.sh` in Registry, Portfolio, Watchlist order.

Migration, unit publication, daemon reload, enablement, restart, or active-state
gate failure restores the database, old unit files, prior enablement, and exact
prior active set in that order. The refresh timer is restored only after the API
writers. A database or service-state rollback failure leaves all managed writers
stopped and retains the private recovery directory. Successful safety backups
remain under `${XDG_STATE_HOME:-~/.local/state}/portfolio-operations-workbench/postgres-backups/`
unless `PORTFOLIO_OPS_SYSTEMD_BACKUP_ROOT` is explicitly set.

`RUN_MIGRATIONS=false` is available only for maintenance workflows that have
already applied and verified the same release migrations separately; staged unit
publication and old-unit rollback still remain transactional in that mode.

The same migration entry point can be run independently before another process
manager deploys the applications:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/scripts/migrate_all.sh
```

Install and start the market-data timer:

```bash
PROJECT_ROOT="$PWD" BACKEND_ROOT="$PWD/apps/platform/backend" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/systemd/install_market_data_refresh_timer.sh
```

The timer defaults to `21:00 Asia/Shanghai`. It uses a non-blocking `fcntl` lock,
atomically records the latest run summary under `~/.local/state/portfolio-ops`,
and treats item-level or downstream refresh failures as a failed run. The
installer's bounded systemd restart policy retries those failures without
allowing overlapping batches.

## Ports

- Platform API: `8102`
- Platform Web: `3100`
- Watchlist API: `8100`
- Watchlist Web: `3101`
- Portfolio API: `8101`
- Portfolio Web: `3102`

## Health Checks

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8102/api/health
curl --noproxy '*' -fsS http://127.0.0.1:8100/api/watchlists
curl --noproxy '*' -fsS http://127.0.0.1:8101/api/portfolios
curl --noproxy '*' -fsS http://127.0.0.1:3100/
curl --noproxy '*' -fsS http://127.0.0.1:3101/
curl --noproxy '*' -fsS http://127.0.0.1:3102/
```
