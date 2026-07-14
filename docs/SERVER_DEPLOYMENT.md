# Portfolio Operations Server Deployment

This project runs nine user-systemd services in production:

- `portfolio-ops-platform-api.service`
- `portfolio-ops-platform-outbox-worker.service`
- `portfolio-ops-platform-web.service`
- `portfolio-ops-watchlist-api.service`
- `portfolio-ops-watchlist-worker.service`
- `portfolio-ops-watchlist-web.service`
- `portfolio-ops-portfolio-api.service`
- `portfolio-ops-portfolio-worker.service`
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

The installer intentionally has no target-less migration shortcut. Prepare the
external runtime environment below, then install with the full explicit
database target and valuation date shown there.

The installer binds both API and web services to `127.0.0.1` by default. Set
`API_HOST` or `WEB_HOST` explicitly only when a reverse proxy or network policy
requires another bind address. If any of the six `*_API_PORT` / `*_WEB_PORT`
values are overridden, release and restore runtime gates use the same values for
readiness, web smoke, and the Portfolio read-contract smoke. Wildcard bind hosts
(`0.0.0.0` or `::`) are probed through `127.0.0.1`; a concrete bind host is
probed at that address. `API_HEALTH_HOST` and `WEB_HEALTH_HOST` may override only
the probe address when routing requires it, and must also be passed to direct
release or restore commands.

Before applying migrations, the installer records and stops all nine managed
long-running units plus the market-data refresh timer/running oneshot. It then delegates to
`infra/scripts/release_database.sh`, which creates a verified pre-release
backup, applies all four Alembic chains, rebuilds Portfolio and Watchlist,
requires a zero-failure/zero-warning audit, and creates a verified post-release
backup. If the installer fails after fencing the writers, managed services stay
stopped for operator review. If the database-release workflow itself fails after
mutation, it first attempts to restore the verified pre-release backup; even
after a successful rollback, the installer does not restart services
automatically. If that workflow has already completed and a later unit
installation or restart fails, the audited new database remains in place while
services stay stopped.
`RUN_MIGRATIONS=false` is available only for maintenance workflows that have
already applied and verified the same release migrations separately.

For production deployments, keep runtime-specific environment files outside the
Git worktree and point systemd at that directory:

```bash
SECRET_SOURCE=/secure/path/to/portfolio-ops-env
install -d -m 700 "$HOME/.config/portfolio-ops/env"
install -m 600 "$SECRET_SOURCE/platform.env" "$HOME/.config/portfolio-ops/env/platform.env"
install -m 600 "$SECRET_SOURCE/watchlist.env" "$HOME/.config/portfolio-ops/env/watchlist.env"
install -m 600 "$SECRET_SOURCE/portfolio.env" "$HOME/.config/portfolio-ops/env/portfolio.env"

CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_RELEASE_DATABASE_URL='postgresql+psycopg://portfolio_ops:REDACTED@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
  PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/portfolio-ops/env" \
  infra/systemd/install_app_services.sh
```

To remove the managed user units without deleting databases, state, or logs:

```bash
PROJECT_ROOT="$PWD" infra/systemd/uninstall_app_services.sh
```

For another process manager, run the same fail-closed database release first.
Do not invoke `migrate_all.sh` as a production deployment command: it is the
internal four-chain runner and does not own backup, rollback, rebuild, audit,
writer fencing, or health checks.

```bash
CONFIRM_RELEASE='portfolio_ops@127.0.0.1:5432' \
PORTFOLIO_OPS_RELEASE_DATABASE_URL='postgresql+psycopg://portfolio_ops:REDACTED@127.0.0.1:5432/portfolio_ops' \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE=YYYY-MM-DD \
PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER=none \
  infra/scripts/release_database.sh
```

With `PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER=none`, the external deployer must
fence every writer before invocation and must not restart applications unless
the command succeeds. The release still rejects remaining client connections,
creates verified pre/post backups, rebuilds both derived-state domains, and
requires a zero-warning audit. If it fails after database mutation, it attempts
to restore the pre-release backup and leaves services stopped; the external
deployer must preserve that fail-closed state for operator review. The systemd
installer itself records and stops all nine long-running processes (including
the Platform market-data outbox worker, Watchlist recalc worker, and Portfolio Daily calculation worker) plus the refresh timer/running oneshot;
an interrupted oneshot refresh is not replayed
automatically after the audited rebuild.

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
curl --noproxy '*' -fsS http://127.0.0.1:8102/api/readiness
curl --noproxy '*' -fsS http://127.0.0.1:8100/api/readiness
curl --noproxy '*' -fsS http://127.0.0.1:8101/api/readiness
curl --noproxy '*' -fsS http://127.0.0.1:3100/
curl --noproxy '*' -fsS http://127.0.0.1:3101/
curl --noproxy '*' -fsS http://127.0.0.1:3102/
```

Platform readiness is fail-closed unless the instrument registry migration is current,
a fresh running outbox worker is registered, no dead-letter event exists, and the oldest
pending/processing event remains within the configured age bound. The outbox worker unit
requires the Watchlist API unit and uses the Platform runtime environment.
