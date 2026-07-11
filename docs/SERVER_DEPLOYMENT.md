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

Install and start the app units:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_app_services.sh
```

The installer binds both API and web services to `127.0.0.1` by default. Set
`API_HOST` or `WEB_HOST` explicitly only when a reverse proxy or network policy
requires another bind address.

Before applying migrations, the installer records and stops every managed
service, then runs `infra/scripts/migrate_all.sh`. This prevents a worker from
the previous release from writing across a new fencing migration. The command
applies the instrument registry, Portfolio, and Watchlist Alembic chains in
order. If migration or restart fails, the installer restores the services that
were active before the update.
`RUN_MIGRATIONS=false` is available only for maintenance workflows that have
already applied and verified the same release migrations separately.

For production deployments, keep runtime-specific environment files outside the
Git worktree and point systemd at that directory:

```bash
mkdir -p "$HOME/.config/portfolio-ops/env"
cp apps/platform/backend/.env "$HOME/.config/portfolio-ops/env/platform.env"
cp apps/watchlist/backend/.env "$HOME/.config/portfolio-ops/env/watchlist.env"
cp apps/portfolio/backend/.env "$HOME/.config/portfolio-ops/env/portfolio.env"

PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/systemd/install_app_services.sh
```

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
