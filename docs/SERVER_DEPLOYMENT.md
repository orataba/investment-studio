# Investment Studio Server Deployment

This project runs six user-systemd services in production:

- `investment-studio-home-api.service`
- `investment-studio-home-web.service`
- `investment-studio-watchlist-api.service`
- `investment-studio-watchlist-web.service`
- `investment-studio-portfolio-api.service`
- `investment-studio-portfolio-web.service`

The market-data timer is installed separately as:

- `investment-studio-market-data-refresh.service`
- `investment-studio-market-data-refresh.timer`

## Deployment ownership

The Studio service user/group is `investment-studio`; its current release is
`/opt/investment-studio/current`. Watchlist, Portfolio and `shared-data` form one
business group. Home owns only login and navigation. Runtime files and secrets
are outside the release tree, as listed in [Architecture](ARCHITECTURE.md).

Regime is developed as the `apps/regime` Git submodule but installed using its own
verified release package. It retains its independent PostgreSQL container,
`/var/lib/regime-dashboard` state and `/etc/regime-dashboard` credentials. A server
release may link `apps/regime` to that installed Regime release; the link is not a
second source checkout or a shared database. Upgrade this link after a Regime software upgrade.

Use `sudo /opt/investment-studio/current/bin/investment-studio services status all`
to inspect the groups. `home`, `investments` and `regime` can be started, stopped
or restarted separately. Regime control delegates to the installed release.

## Install App Services

Build the three frontend bundles before installing web services:

```bash
export VITE_HOME_URL=https://yunguyungu.com
export VITE_WATCHLIST_URL=https://watchlist.yunguyungu.com
export VITE_PORTFOLIO_URL=https://portfolio.yunguyungu.com
npm --prefix home/frontend ci
npm --prefix home/frontend run build
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/portfolio/frontend run build
install -d deploy/regime-ui
cp apps/regime/app/multi_market_regime_dashboard/static/* deploy/regime-ui/
```

Stage Regime's static UI from the source checkout before replacing `apps/regime`
with the server's installed-release symlink. Nginx serves this UI from
`/opt/investment-studio/current/deploy/regime-ui`; `/api/` still uses the independent
Regime service. This allows UI-only releases without modifying the verified
Regime package or its data. Include `deploy/regime-ui` alongside the three `dist`
directories when publishing frontends, and reload Nginx after `nginx -t` when
its configuration changes. Keep the previous UI assets for rollback.

Reproduce the locked Python environment (including test tooling):

```bash
infra/scripts/sync_python_env.sh
```

Provision the four runtime environment files directly from the approved secret
manager. They live outside the worktree; backend `.env` files and symlinks are
not supported. Repository `.env.example` files are key-name templates only and
must never be copied as runtime configuration:

```bash
ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio"
install -d -m 700 "$ENV_ROOT"

# Provision these files directly from the secret manager; do not source them.
test -f "$ENV_ROOT/home.env"
test -f "$ENV_ROOT/data.env"
test -f "$ENV_ROOT/watchlist.env"
test -f "$ENV_ROOT/portfolio.env"
chmod 600 "$ENV_ROOT/home.env" "$ENV_ROOT/data.env" "$ENV_ROOT/watchlist.env" "$ENV_ROOT/portfolio.env"
```

Home has only authentication and navigation configuration, with no database URL.
The data file must also set the shared instrument-data database URL. Every required database variable must contain
the same password-free canonical PostgreSQL target. Store authentication in the
service user's `0600` `.pgpass`, never in a command, repository file, or
credential-bearing URL. A separate Alembic variable is optional; when present it
may use a different database role but must retain the same host, port, and database.

- `home.env`: `INVESTMENT_STUDIO_HOME_*` authentication and navigation only
- `data.env`: `INVESTMENT_STUDIO_DATA_DATABASE_URL`,
  `INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL`
- `watchlist.env`: `INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL`
- `portfolio.env`: `INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL`

The installer rejects a missing `ENV_ROOT`, repository-local `.env` files or
symlinks, insecure file/directory permissions, password-bearing URLs, and mixed
database targets before it writes or restarts any unit.

`INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL` is optional and is validated
against the same database target when present. The installer rejects conflicting
schema values, and data-maintenance refresh execution commands pin
`INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data` and
`INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion`, so a stale secret
template cannot redirect private ingestion tables into a shared schema.

Install and start the app units with that external directory explicitly bound:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  infra/systemd/install_app_services.sh
```

The installer binds both API and web services to `127.0.0.1` by default. Set
`API_HOST` or `WEB_HOST` explicitly only when a reverse proxy or network policy
requires another bind address.

## Personal Portfolio Research

Local and cloud Portfolio use the same code and frontend bundle. Set
`INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED=true` in the personal local
`portfolio.env`, and `INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED=false` in the
server's external `portfolio.env`. Research is disabled when the key is omitted.
Restart the Portfolio API after changing this setting. The frontend reads
`/api/capabilities` at startup to control navigation and direct page access;
disabled deployments do not register Research API routes, including saved runs
and artifact access. Existing research data is retained.

Keep this external environment file across releases. Do not replace it with
the repository template during an update. No separate branch, build flag, or
cloud-specific source edit is needed.

## Cross-app research and instrument risk

Set these non-secret endpoints in the existing external environment files. The
server API ports differ from the local development defaults:

```text
# watchlist.env
INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL=http://127.0.0.1:8100/api
INVESTMENT_STUDIO_WATCHLIST_RESEARCH_PORTFOLIO_API_URL=http://127.0.0.1:8101/api
INVESTMENT_STUDIO_WATCHLIST_RESEARCH_REGIME_API_URL=http://127.0.0.1:3010/api

# portfolio.env
INVESTMENT_STUDIO_PORTFOLIO_WATCHLIST_API_URL=http://127.0.0.1:8100/api
```

The first URL lets the restricted Watchlist Harness call its own research tools.
The next two provide read-only evidence from Portfolio and Regime. The Portfolio
risk panel reads and updates Watchlist-owned instrument risk records; it does not
create a second risk ledger. These connections do not enable personal Portfolio
Research on the shared server. Restart both APIs after updating their files and
check `/api/research/connections` on Watchlist and `/api/instrument-risk` on
Portfolio through the authenticated ingress.

Watchlist uses the existing external `portfolio-copilot.env` and pinned Harness
runtime, with pnpm/Node available to the service user. Missing credentials or
runtime disables assistant execution while saved research and risk records remain
available. Do not copy local secrets over the server configuration during a release.

## Network and access-control boundary

The six application services do not enforce access independently. The Home
API provides a signed-cookie login for the shared ingress, but every application
service is still safe only while kept on loopback or behind that reviewed access
layer. A production ingress must:

- keep PostgreSQL and the three `810x` API ports private;
- terminate TLS;
- enforce authentication for both pages and `/api` requests;
- forward only to loopback web services, which already proxy their matching API;
- avoid publishing health endpoints as a substitute for access control.

Cloud-console login, a hard-to-guess IP, or an unrestricted company-network bind
does not provide page-level access control. Until an authentication design is
configured and operated, use server-local checks or a controlled SSH tunnel. Do
not set `HOST=0.0.0.0` merely to make the current apps reachable.

The `yunguyungu.com` deployment uses the Nginx configuration at
`infra/nginx/yunguyungu.conf` and these Home settings in the external
`home.env`:

```text
INVESTMENT_STUDIO_HOME_AUTH_USERNAME=yungu
INVESTMENT_STUDIO_HOME_AUTH_PASSWORD_HASH_FILE=/home/investment-studio/.config/orataba/secrets/investment-studio/auth/password-hash
INVESTMENT_STUDIO_HOME_AUTH_SESSION_SECRET_FILE=/home/investment-studio/.config/orataba/secrets/investment-studio/auth/session-secret
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_DOMAIN=yunguyungu.com
INVESTMENT_STUDIO_HOME_APP_URLS={"watchlist":"https://watchlist.yunguyungu.com","portfolio":"https://portfolio.yunguyungu.com","regime":"https://regime.yunguyungu.com"}
```

Keep `yunguyungu.com` as the login and application portal. Create three `A`
records named `watchlist`, `portfolio`, and `regime`, all pointing to
the server IPv4 address. The TLS certificate at
`/etc/letsencrypt/live/yunguyungu.com/` must include the root domain and all
three subdomains. The homepage cards and cross-app deep links use only these
production hosts. There is no application selector on the IP address and no
separate Basic authentication prompt for the apps.

Users log in once at the homepage. Its secure, HTTP-only cookie covers
`yunguyungu.com` and the three subdomains, including independent browser tabs.
Unauthenticated app page requests return to the homepage login with their
destination preserved; app APIs return `401` instead of login HTML. Logging out
at the homepage removes access across the apps.

The server's Certbot 5.8 environment is at
`/opt/certbot-ip/.venv/bin/certbot` with its matching Nginx plugin. The system
`certbot.service` uses this executable. The domain certificate renewal runs
`nginx -t && systemctl reload nginx` after successful renewal. Verify renewal
with `renew --dry-run --run-deploy-hooks --no-random-sleep-on-renew --cert-name yunguyungu.com` before
treating the timer as healthy.

Both referenced files must be owned by the service user with mode `0600`. The
password file stores a scrypt hash, never the plaintext password. Nginx applies
the same session check to pages and APIs on the root, Watchlist,
Portfolio, and Regime hosts. Only the login page, its static assets, and the
rate-limited login endpoint are public.

For a dedicated server database, `infra/postgres/docker-compose.server.yml`
binds PostgreSQL only to `127.0.0.1:55433`, reads its password from a Docker
secret file, and keeps data in a named volume. Set
`INVESTMENT_STUDIO_POSTGRES_PASSWORD_FILE` to an absolute root-owned `0600` file
before starting that Compose project. This profile is separate from the
insecure local-development Compose defaults.

The repository default for the DataHub Tushare provider is an external HTTP URL,
so its API key crosses a plaintext transport unless the deployment overrides it
with a provider-supported HTTPS endpoint. Keep this source on a trusted network,
verify the configured URL during deployment, and rotate the key if transport
exposure is suspected. Do not hide the limitation with certificate bypasses or
an automatic provider fallback.

The installer generates all six replacement units privately before changing
systemd state. It snapshots the previous unit files, enablement, and active set,
including the market-data refresh service/timer. Before migration it stops every
database writer and creates a verified backup of the four project schemas with
the shared archive/manifest/checksum primitive, then runs
`infra/scripts/migrate_all.sh`. After migration it refreshes the FMP stock and
ETF search catalogs required by the current exchange contract, rebuilds only
Portfolio snapshots whose calculation version or source lineage is stale, then
runs the read-only live-data audit. The backup covers `instrument_data`,
`data_ingestion`, `portfolio`, and `watchlist`; migration order is dependency-aware so
Data Ingestion raw-evidence storage exists before destructive shared NAV cleanup.

Migration, unit publication, daemon reload, enablement, restart, or active-state
gate failure restores the database, old unit files, prior enablement, and exact
prior active set in that order. The refresh timer is restored only after the API
writers. A database or service-state rollback failure leaves all managed writers
stopped and retains the private recovery directory. Successful safety backups
remain under `${XDG_STATE_HOME:-~/.local/state}/investment-studio/postgres-backups/`
unless `INVESTMENT_STUDIO_SYSTEMD_BACKUP_ROOT` is explicitly set.

`RUN_MIGRATIONS=false` is available only for maintenance workflows that have
already applied and verified the same release migrations separately; staged unit
publication and old-unit rollback still remain transactional in that mode.

The same migration entry point can be run independently before another process
manager deploys the applications:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  infra/scripts/migrate_all.sh
```

## Release preflight and analytics readiness

After migration and before declaring Risk/Risk Budget production-ready, run the
read-only data audit against the exact canonical local database target:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  INVESTMENT_STUDIO_LOCAL_DATABASE_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio' \
  "$PWD/.venv/bin/python" infra/scripts/audit_live_data.py --fail-on-warning --json
```

The audit is deliberately fail-closed for schema and data integrity. It also
warns when a portfolio has no effective-dated analytics taxonomy selection or
when its point-in-time taxonomy configuration/root/unassigned policies are
incomplete; `--fail-on-warning` turns that operational warning into a release
gate. Do not create a default selection in runtime code or by an unreviewed
SQL backfill. Configure the selection and scope policies through the Portfolio
Taxonomies API, record the effective date and operator, then rerun the audit.

An audit result of `passed` is therefore the data-integrity gate. An audit result
of `warning` may still allow market-data refresh and ordinary operational pages,
but Risk and Risk Budget must be labelled unavailable until the analytics scope
warnings are cleared.

Install and start the market-data timer:

```bash
PROJECT_ROOT="$PWD" BACKEND_ROOT="$PWD/shared-data" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  infra/systemd/install_market_data_refresh_timer.sh
```

The timer defaults to `21:00 Asia/Shanghai`. It uses a non-blocking `fcntl` lock,
atomically records the latest run summary under `~/.local/state/investment-studio`,
and treats item-level or downstream refresh failures as a failed run. The
installer's bounded systemd restart policy retries those failures without
allowing overlapping batches.

## Ports

- Home API: `8102`
- Home Web: `3100`
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
