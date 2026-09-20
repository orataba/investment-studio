# Investment Studio Server Deployment

This project runs eight user-systemd services in production:

- `investment-studio-home-api.service`
- `investment-studio-home-web.service`
- `investment-studio-watchlist-api.service`
- `investment-studio-watchlist-web.service`
- `investment-studio-portfolio-api.service`
- `investment-studio-portfolio-web.service`
- `investment-studio-briefing-api.service`
- `investment-studio-briefing-web.service`

Five data schedules are installed separately. Each uses an `investment-studio-<name>.service` and `.timer` pair: `market-data-refresh` for nightly settlement; `cn-market-data-refresh`, `hk-market-data-refresh`, and `us-market-data-refresh` for closing prices; `cn-hk-reference-data-refresh` for pre-open Tushare reference acquisition. FMP references are projected by the public collection or arrival pipeline.

## Deployment ownership

The Studio service user/group is `investment-studio`; its current release is
`/opt/investment-studio/current`. Watchlist, Portfolio and `shared-data` form one
business group. Home owns account/session management in the `identity` schema and navigation. Runtime files and secrets
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

Build the four frontend bundles before installing web services:

```bash
export VITE_HOME_URL=https://yunguyungu.com
export VITE_WATCHLIST_URL=https://watchlist.yunguyungu.com
export VITE_PORTFOLIO_URL=https://portfolio.yunguyungu.com
export VITE_REGIME_URL=https://regime.yunguyungu.com
export VITE_BRIEFING_URL=https://briefing.yunguyungu.com
npm --prefix home/frontend ci
npm --prefix home/frontend run build -- --manifest
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/watchlist/frontend run build -- --manifest
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/portfolio/frontend run build -- --manifest
npm --prefix apps/briefing/frontend ci
npm --prefix apps/briefing/frontend run build -- --manifest
install -d deploy/regime-ui
cp apps/regime/app/multi_market_regime_dashboard/static/* deploy/regime-ui/
```

Stage Regime's static UI from the source checkout before replacing `apps/regime`
with the server's installed-release symlink. Nginx serves this UI from
`/opt/investment-studio/current/deploy/regime-ui`; `/api/` still uses the independent
Regime service. This allows UI-only releases without modifying the verified
Regime package or its data. Include `deploy/regime-ui` alongside the four `dist`
directories when publishing frontends, and reload Nginx after `nginx -t` when
its configuration changes. Keep the previous UI assets for rollback.

Before switching a Studio release, retain the preceding build's hashed assets
in the new `dist/assets` without overwriting new files. An already-open page may
request its previous version's lazy-loaded chunks after the switch. Use the
preceding build's `dist/.vite/manifest.json` (`file`, `css`, and `assets` entries)
to copy only that build's files; leave the new manifest unchanged so the next
deployment does not accumulate every historical build. HTML always comes from
the new release. Do not replace this with frontend exception retries.

Reproduce the locked Python environment (including test tooling):

```bash
infra/scripts/sync_python_env.sh
```

Provision the six runtime environment files directly from the approved secret
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
test -f "$ENV_ROOT/market.env"
test -f "$ENV_ROOT/briefing.env"
chmod 600 "$ENV_ROOT/home.env" "$ENV_ROOT/data.env" "$ENV_ROOT/watchlist.env" "$ENV_ROOT/portfolio.env" "$ENV_ROOT/market.env" "$ENV_ROOT/briefing.env"
```

Home requires `INVESTMENT_STUDIO_HOME_DATABASE_URL` for its `identity` schema; it does not read business records.
The data file must also set the shared instrument-data database URL. Every required database variable must contain
the same password-free canonical PostgreSQL target. Store authentication in the
service user's `0600` `.pgpass`, never in a command, repository file, or
credential-bearing URL. A separate Alembic variable is optional; when present it
may use a different database role but must retain the same host, port, and database.

- `home.env`: `INVESTMENT_STUDIO_HOME_DATABASE_URL`, account/session and navigation configuration
- `data.env`: `INVESTMENT_STUDIO_DATA_DATABASE_URL`,
  `INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL`
- `watchlist.env`: `INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL`
- `portfolio.env`: `INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL`
- `market.env`: `INVESTMENT_STUDIO_MARKET_DATABASE_URL`, data root and `ROLE=collector`
- `briefing.env`: `INVESTMENT_STUDIO_BRIEFING_DATABASE_URL`, callback on `8110` and `EDITION_ROLE=publisher`

The installer rejects a missing `ENV_ROOT`, repository-local `.env` files or
symlinks, insecure file/directory permissions, password-bearing URLs, and mixed
database targets before it writes or restarts any unit.

`INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL` is optional and is validated
against the same database target when present. The installer rejects conflicting
schema values, and data-maintenance refresh execution commands pin
`INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data` and
`INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion`, so a stale secret
template cannot redirect private ingestion tables into a shared schema.

Provision the release's frozen research runtime as the service user before
switching services. This installs DSH 0.1.1-rc.2 and its locked transitive
dependencies; it needs Node 24 and pnpm 11 or 12. An already populated official
registry store can be reused with `infra/harness/install.sh --offline`.

```bash
infra/harness/install.sh
infra/harness/run.sh --version
```

Install and start the app units with that external directory explicitly bound:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  infra/systemd/install_app_services.sh
```

The installer binds both API and web services to `127.0.0.1` by default. Set
`API_HOST` or `WEB_HOST` explicitly only when a reverse proxy or network policy
requires another bind address.

## Upgrading an existing release

Use a new immutable release directory; do not install dependencies or rebuild
bundles inside the live `current` target. Complete these steps in order:

1. Stage the reviewed commit and its pinned submodule, locked Python environment,
   frozen Harness and four production frontend bundles. Retain the previous
   manifest's hashed assets as described above. Stage the Regime static UI
   separately and verify its API compatibility; preserve the independently
   installed Regime software paths and environment when that application is not
   part of this upgrade.
2. Save the current symlink target, external configuration, unit definitions,
   enablement and exact active service/timer sets in a private release recovery
   directory. Record Regime's actual paths from its external runtime and scheduler
   configuration: they may refer to an older Studio release than `current`.
   Retain every still-referenced release, including its virtual environment.
3. Put external writes into maintenance. Stop scheduled triggers first, then
   their workers and the eight Studio app services. Include public-market and
   Briefing jobs, the retired US reference job, independent Regime timers/source
   workers, and any source containers. Verify they are inactive; the Studio app
   installer does not control the independent Regime installation. Preserve public
   objects with the database backup scope described below.
4. As the Studio service user, run the new release's `install_app_services.sh`
   with its immutable `PROJECT_ROOT`, its `PYTHON_BIN`, the existing external
   `ENV_ROOT`, and the user systemd bus available. Keep `RUN_MIGRATIONS=true` and
   `START_SERVICES=true`. This performs the checked schema backup, migrations,
   catalog refresh, Watchlist reconciliation, stale snapshot rebuild, integrity
   audit and eight-endpoint readiness gate. Because schedules were stopped before
   invocation, the installer leaves them stopped during acceptance. It removes
   the retired US reference definitions; do not restore that timer on success.
5. While maintenance remains active, regenerate the five supported data schedules
   and the Briefing schedules with `START_TIMERS=false`; regenerate the collector
   definitions with `install_market_pipeline.py` (which never starts them).
   Point every generated command at the new immutable release. Atomically replace
   `current`, validate the Nginx configuration, and reload if it changed. Check
   loopback health, authenticated resource permissions, enabled Research, catalog
   discovery, diagnostic records, compressed/cacheable assets and a previous
   manifest's lazy chunk through the actual ingress.
6. Accept the release before reopening writes and restoring the previously active
   schedules, excluding retired units. A persistent market timer can immediately
   catch up when reactivated; inspect the resulting job state. Keep previously
   inactive independent Regime services inactive unless their repair or startup
   is part of the authorized release. Inspect remaining failures separately from
   application readiness; an unavailable provider channel is not repaired by
   resetting a failed unit.

Before the installer accepts the upgrade, its failure handler restores its own
database/unit changes. The outer release procedure must also restore any changed
symlink, Nginx configuration, external configuration and separately generated
schedule files from its snapshot. After new writes have been accepted, follow the
forward-repair rule below instead of automatically restoring the old database.

## Shared public data and Briefing

Initialize the project-owned public data directory and migrate the `market_data`, `market_text` and `briefing` schemas before switching readers. Import the numerical bootstrap through the bundle importer; never copy local Portfolio or Watchlist databases to the cloud. Remove imported temporary archives before creating a full outbound archive to avoid three full copies.

The cloud is the numerical collector and formal report publisher. Install shared pipeline definitions with `infra/scripts/install_market_pipeline.py --scheduler systemd --role collector --env-root /absolute/external/config`, then enable its timers. The existing private data jobs project the shared facts and settle NAV. Install formal report schedules with `infra/systemd/install_briefing_timers.sh`.

The information feed may push completed archives into a dedicated SFTP inbox. Bind `INVESTMENT_STUDIO_MARKET_MI_INBOX_DIR` to that directory; otherwise configure a trusted `MI_HOST` and `MI_REMOTE_DIR` for pulling. Configure one transport. The SFTP sender publishes a readable checksum receipt before atomically renaming the final ZIP. Keep the dedicated receiver confined to its inbox, with no shell or forwarding.

Regime retains its model and private materialized inputs. Its source workers must install `studio_market` and use explicitly configured public-data credentials and directory access; preserve the private snapshot roles. A source-code or static UI update alone does not switch its running collector.

For an existing Studio-integrated Regime installation, build the application wheel
and source container image from the same pinned submodule commit. Install the
release's `studio_market` and `studio_identity` packages alongside the existing
locked model dependencies. Update every explicit software path in
`/etc/regime-dashboard/runtime.env`, `/etc/asset-regime-market-data/scheduler.env`, the Regime units and Compose
configuration together. These live configurations define the running code; the
original standalone installer's release marker does not. Back up the existing
configuration, pause active Regime writers during the switch, verify imports and
health under the actual service identities, and restore the previous schedules.
Preserve the private database, runtime generations, approved models and secrets.
Do not rerun the standalone seed installer for this integrated software upgrade.

For a focused Studio market-adapter repair, build one complete `investment-studio-market`
wheel and deploy that same artifact to both the native Regime environment named by
`runtime.env`/`scheduler.env` and a new source-worker image. A verified compatible
dependency graph may be retained with an explicit `--no-deps` package replacement;
record the before/after package versions and run the dependency check. This is a
documented maintenance exception to the otherwise immutable native release: retain
its old wheel and directory, and record the installed wheel/source hashes and exact
Python path. Never copy an individual module into the installed package.

Build the new source image from the verified prior image ID, preserve its user,
entrypoint and runtime configuration, and validate imports, package-file hashes and
dependencies in a container without network or production mounts. Then back up
`compose.env` privately and replace only `REGIME_DATA_IMAGE`. Record the old/new
image IDs and wheel hash together; retain the previous image and configuration for
rollback. The Studio app installer neither updates this baked-in package nor switches
Regime's native environment. Keep schedules paused until both consumers are verified;
restart native readers during the coordinated acceptance step. This operation does
not change model dependencies, approved models, database contents or data volumes.

Back up public Parquet/original objects together with PostgreSQL metadata; a schema dump alone cannot restore shared market data. See [Market Data Pipeline](MARKET_DATA_PIPELINE.md).

## Portfolio Research

Portfolio Research is enabled by default on local and cloud deployments. Keep
`INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED=true` in the server's external
`portfolio.env`; older releases explicitly set this to false, so deployment must
update that existing value. The frontend reads capabilities and current portfolio
access together from `/api/portfolios/session?portfolio_id=...` to expose the
workspace. Research uses the same portfolio permissions as other portfolio APIs;
enabling it does not grant users access to additional portfolios. An operator may
explicitly set false to disable the routes and UI while retaining saved runs.

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
create a second risk ledger. These connections are independent of the Portfolio Research capability. Restart both APIs after updating their files and
check `/api/research/connections` on Watchlist and `/api/instrument-risk` on
Portfolio through the authenticated ingress.

Watchlist uses the existing external `portfolio-copilot.env` and the Harness
installed under that release's `infra/harness/node_modules`, with Node available
to the service user. Research, Briefing and transaction-capture callers execute
the same installed CLI directly; research requests never run a package manager
or download dependencies. Missing credentials or
runtime disables assistant execution while saved research and risk records remain
available. Do not copy local secrets over the server configuration during a release.

## Network and access-control boundary

Home validates real users and stores revocable opaque sessions in PostgreSQL.
Each protected application API resolves that identity and enforces its own team,
portfolio, private-conversation and task permissions; ingress checks do not replace
these controls. Cloud Home must use `AUTH_MODE=account` in a production environment,
and each business application must use `INVESTMENT_STUDIO_AUTH_MODE=account`.
Cloud account mode rejects local-owner credentials, including delegated children.
Even server-local calls need a valid user or service credential. A production ingress must:

- keep PostgreSQL and all four application API ports private;
- terminate TLS;
- require login for protected pages and preserve API authentication errors;
- permit the explicitly public Regime state/history and published Briefing surfaces;
- forward only to loopback web services, which already proxy their matching API;
- avoid publishing health endpoints as a substitute for access control.

Cloud-console login, a hard-to-guess IP, or an unrestricted company-network bind
does not provide page-level access control. Before exposing protected services,
initialize the identity database, real users and explicit portfolio grants. Do
not set `HOST=0.0.0.0` merely to make the current apps reachable.

The `yunguyungu.com` deployment uses the Nginx configuration at
`infra/nginx/yunguyungu.conf` and these Home settings in the external
`home.env`:

```text
INVESTMENT_STUDIO_HOME_ENVIRONMENT=production
INVESTMENT_STUDIO_HOME_AUTH_MODE=account
INVESTMENT_STUDIO_HOME_DATABASE_URL=postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_NAME=__Secure-yungu_session
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_SECURE=true
INVESTMENT_STUDIO_HOME_AUTH_COOKIE_DOMAIN=yunguyungu.com
INVESTMENT_STUDIO_HOME_APP_URLS={"watchlist":"https://watchlist.yunguyungu.com","portfolio":"https://portfolio.yunguyungu.com","regime":"https://regime.yunguyungu.com","briefing":"https://briefing.yunguyungu.com"}
```

The former single-username, password-file and signed-cookie secret settings are
not runtime authentication fallbacks. Initialize the first owner through the Home
CLI using an explicitly supplied private password hash. The approved owner is
`shaw` (display name `shaw`); claim confirmed old research/private conversations
under his stable user ID and grant only him manager access to existing portfolios.
Create `leo`, `aaron` and `huangwei` as team members and `yungu` as a team reader,
without any initial access to existing portfolios. Portfolio managers may grant
that access later. Keep every password outside documentation and release files.
These are initialization requirements, not a record that deployment has occurred.

Keep `yunguyungu.com` as the login and application portal. Create four `A`
records named `watchlist`, `portfolio`, `regime`, and `briefing`, all pointing to
the server IPv4 address. Point `www` to the same server; HTTPS `www` redirects
to the canonical root domain with its path and query preserved.

The root and `www` use the operator-supplied certificate chain and matching
private key at `/etc/nginx/certificates/www.yunguyungu.com/fullchain.pem` and
`privkey.pem`. Install the supplied `.pem` as `fullchain.pem` and `.key` as
`privkey.pem`; keep the directory root-owned `0700` and the private key `0600`.
Verify the certificate's SAN includes both names and its public key matches the
private key before `nginx -t` and reload. This certificate is renewed through
its issuer and replaced here before expiry; Certbot does not renew this pair.

The four application subdomains use the independently renewed certificate at
`/etc/letsencrypt/live/yunguyungu.com/`. It must include all four subdomains;
the supplied root/`www` certificate cannot be used for those hosts.
The homepage cards and cross-app deep links use only these
production hosts. There is no application selector on the IP address and no
separate Basic authentication prompt for the apps.

Users log in once at the homepage. Its secure, HTTP-only opaque-session cookie covers
`yunguyungu.com` and the four subdomains, including independent browser tabs.
Unauthenticated protected page requests return to the homepage login with their
destination preserved; protected APIs return `401` instead of login HTML. Public
Regime and published Briefing reads remain available. Logging out revokes the
server-side session across applications and invalidates its delegated tasks.

The server's Certbot 5.8 environment is at
`/opt/certbot-ip/.venv/bin/certbot` with its matching Nginx plugin. The system
`certbot.service` uses this executable. The domain certificate renewal runs
`nginx -t && systemctl reload nginx` after successful renewal. Verify renewal
with `renew --dry-run --run-deploy-hooks --no-random-sleep-on-renew --cert-name yunguyungu.com` before
treating the timer as healthy.

Keep runtime configuration and service tokens owned by the
service user with mode `0600`. Password hashes are held in `identity`; any private
bootstrap hash file is initialization input, not a continuing login dependency.
Nginx protects private pages while the application APIs enforce their own account
and resource permissions. Public Regime and published Briefing endpoints retain
their documented anonymous-read policy.

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

The installer generates all eight replacement units privately before changing
systemd state. It snapshots the previous unit files, enablement, and active set,
including the market-data refresh service/timer. Before migration it stops every
database writer and creates a verified backup of the project schemas with
the shared archive/manifest/checksum primitive, then runs
`infra/scripts/migrate_all.sh`. After migration it refreshes the FMP stock and
ETF search catalogs required by the current exchange contract, reconciles the four
system Watchlists from the shared registry, and rebuilds only Portfolio snapshots
whose calculation version or source lineage is stale, prepares the latest reliable
Portfolio holdings/risk page projections, then runs the read-only live-data audit.
Watchlist reconciliation also backfills the compact screener projections from
stored charts. A failed projection or incomplete backfill fails readiness; do not
bypass this preparation and leave the first browser request to rebuild it.
Watchlist reconciliation uses the same membership and row
materialization as normal directory reads, preserving user lists, views and
research; a registry or materialization error fails the install and rolls back.
The integrity gate runs only after these release maintenance steps, without
depending on a browser visit to synchronize the directories. The backup covers `identity`, `instrument_data`,
`instrument_registry`, `data_ingestion`, `platform`, `portfolio`, `watchlist`,
`market_data`, `market_text` and `briefing`; preserve external service credentials separately. Migration order is dependency-aware so
Data Ingestion raw-evidence storage exists before destructive shared NAV cleanup.

Before accepting an upgrade, the installer checks HTTP readiness for all eight
API and web endpoints; a running process alone is insufficient. `HEALTH_ATTEMPTS`
defaults to 60, with bounded requests and a one-second pause between attempts.

Keep external business writes paused until a migration workflow with automatic
database rollback has accepted the upgrade. Stop scheduled triggers before
checking their workers, including independent Regime source services and any
remaining source containers. Once APIs or schedules may have accepted new writes,
do not replay the pre-upgrade dump automatically: recover the code in place when
the migration is backward compatible, or keep maintenance in effect and repair
forward. Restore the previously active schedules only after acceptance.

Migration, unit publication, daemon reload, enablement, restart, or readiness
gate failure restores the database, old unit files, prior enablement, and exact
prior active set in that order. The refresh timer is restored only after the API
writers. A database or service-state rollback failure leaves all managed writers
stopped and retains the private recovery directory. Successful safety backups
remain under `${XDG_STATE_HOME:-~/.local/state}/investment-studio/postgres-backups/`
unless `INVESTMENT_STUDIO_SYSTEMD_BACKUP_ROOT` is explicitly set.

Retain a verified snapshot of the currently accepted database baseline, the latest
pre-upgrade backup(s) and upgrade archive needed for rollback, and the previous
formal recovery point. Retire redundant intermediate full dumps only after the
migration, application readiness and data acceptance checks have succeeded.
Before deletion, verify the retained manifests' schema scope and archive checksums,
and decode each retained dump with `pg_restore --file=/dev/null <archive>`; a table
of contents alone does not check all compressed data. Confirm that the referenced
external object root and separately protected credentials remain available.
Never delete backups automatically by age during backup creation or a failed rollout.

A routine online `pg_dump` uses an MVCC snapshot: normal application writes may
continue while no DDL or migration is running. Coordinated migration and restore
still require the writer shutdown documented above. Custom-format dumps are already
compressed; do not blanket-recompress dumps, Parquet files or gzip archives. When
estimating cleanup savings, account for shared hard links and confirm actual free
space afterward with `df`; summed directory sizes can count the same blocks twice.

Use native journald limits in a system configuration drop-in under `[Journal]`:
`SystemMaxUse=512M`, `SystemKeepFree=5G`, and `MaxRetentionSec=30day`.
After applying it, verify the effective configuration with
`systemd-analyze cat-config systemd/journald.conf` and usage with `journalctl --disk-usage`.
These limits constrain journals, not other files; retain the existing logrotate
and Regime artifact-pruning policies for their respective outputs.

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
warns when a configured portfolio has no current analytics taxonomy selection or
when its current taxonomy configuration/root/unassigned policies are
incomplete; `--fail-on-warning` turns that operational warning into a release
gate. Do not create a default selection in runtime code or by an unreviewed
SQL backfill. Configure the selection and scope policies through the Portfolio
Taxonomies API, retain the automatic revision and operator audit, then rerun the audit.
Taxonomy configuration has no effective-date scheduling. Its migration and every
subsequent edit invalidate affected analytics from the start of portfolio history;
complete the resulting snapshot rebuild before release acceptance.

An audit result of `passed` is therefore the data-integrity gate. An audit result
of `warning` may still allow market-data refresh and ordinary operational pages,
but Risk and Risk Budget must be labelled unavailable until the analytics scope
warnings are cleared.

Install all five data schedules as the service user, using the same installer:

```bash
for schedule in settlement: market:cn market:hk market:us reference:cn-hk; do
  CHANNEL="${schedule%%:*}" MARKET_SCOPE="${schedule#*:}" \
    PROJECT_ROOT="$PWD" BACKEND_ROOT="$PWD/shared-data" PYTHON_BIN="$PWD/.venv/bin/python" \
    ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
    infra/systemd/install_market_data_refresh_timer.sh
done
```

The app installer retires the former `us-reference-data-refresh` service/timer: it snapshots their files, enablement and active state with the release, stops and disables them, and removes their definitions on success. A failed install restores them with the other prior units. Do not reinstall this obsolete timer.

During a coordinated release, set `START_TIMERS=false` for this installer and
the Briefing timer installer. Regenerate definitions while writers are paused,
verify the upgraded services and data, then restore the previously active timers.
Shared market pipeline definition generation never starts its timers.

| Channel / market scope | Schedule |
| --- | --- |
| `market / cn` | 15:30 Asia/Shanghai |
| `market / hk` | Actual Hong Kong session close + 30 minutes |
| `market / us` | Actual US session close + 30 minutes |
| `reference / cn-hk` | 08:00 Asia/Shanghai |
| `settlement` | 21:00 Asia/Shanghai |

IANA timezones handle US daylight saving time. The shared runner selects instruments by their configured
market calendars and skips closed markets. Hong Kong and US post-close timers check at each hour
`:30`; the shared `infra/scripts/market_close_schedule.py` condition runs the batch only in the half-hour
window beginning 30 minutes after the actual session close, including half-days. A calendar error fails
the service; an ordinary off-schedule tick leaves the previous refresh summary unchanged.

The pre-open reference batch only acquires Tushare reference inputs. FMP reference snapshots are
projected after successful public collection or replica arrival, with failures retained in pipeline status.
The matching market's research starts at 08:30 and collects current news and event evidence. Collect the initial references before
activating research on a new release. Watchlist reads these snapshots without an external DuckDB source.
Nightly settlement covers the FMP catalogue, Tushare fund NAVs, email NAVs, FX and private-fund projections,
without repeating equity closing-price or reference collection.

All batches retain the shared exclusive `fcntl` lock. Scheduled refreshes wait up to `LOCK_WAIT_SECONDS` (default 900 seconds) for the current writer; waiting is logged and expiry remains an explicit exit 75. This bounded wait covers overlap between source acquisition and settlement without running simultaneous writers. Each schedule has its own log and atomic summary
under `~/.local/state/investment-studio`. Item and downstream failures fail the service. Settlement retains
its existing bounded systemd restart policy; the four market/source schedules do not add restarts. App installation,
database restore and the service-group controls include every data service/timer in their stop/restore sets.

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
curl --noproxy '*' -fsS http://127.0.0.1:8100/api/health
curl --noproxy '*' -fsS http://127.0.0.1:8101/api/health
curl --noproxy '*' -fsS http://127.0.0.1:3100/
curl --noproxy '*' -fsS http://127.0.0.1:3101/
curl --noproxy '*' -fsS http://127.0.0.1:3102/
```


These health checks do not verify account isolation. Test protected portfolio and
research routes using real account sessions and a separate member without a grant;
keep credentials in private client storage, outside commands and logs.

多账号启用、历史作者认领、组合管理者指派和服务凭证配置见 [多账号体系](MULTI_ACCOUNT_SYSTEM.md)。云端启用前必须先迁移身份库并配置真实账号、会话与后台服务身份；本文不表示实际启用验收已经完成。

## Request and job diagnostics

Four Studio APIs emit structured `http_request` JSON records to their service
journals: request ID, route template, status, total duration, response bytes,
SQL count/time and identity/authorization spans. `X-Request-ID` correlates API,
web proxy and Nginx records; `Server-Timing` exposes time to response headers
and SQL time in browser developer tools. Full response duration also includes
streaming/background completion and can differ from the header timing.

Authenticated browsers send bounded `browser_event` batches to Home with page
load/request durations, HTTP status, original request ID and JavaScript exception
name/code location. No form contents, response bodies, exception messages,
query strings, URL fragments, cookies or credentials enter these diagnostic
records. Logged-out/offline pages do not persist client diagnostics. Failed
telemetry is discarded and never retried. Successful writes remain independently
recorded in the existing identity/portfolio audit tables.

Portfolio recalculation, Watchlist recalculation/research, Portfolio Research
and Briefing generation emit start/finish operations with IDs and durations.
Errors include their class and source frames without exception payloads. Read
Home's journal for browser events and the relevant API journal for its request:

```bash
journalctl --user -u investment-studio-home-api --since '30 minutes ago' -o cat
journalctl --user -u investment-studio-portfolio-api --since '30 minutes ago' -o cat
```

Nginx writes `/var/log/nginx/studio-access.log` as JSON, including upstream and
full request time, response size and request ID. Existing `/var/log/nginx/*.log`
rotation applies. JavaScript/CSS/JSON are compressed; Vite's hashed `/assets/`
files are immutable and do not require a session check. HTML revalidates and
protected pages still authenticate; every private API checks current access.
The web proxy also supports conditional requests and HEAD without transferring
file bodies. Verify compression and cache headers on actual deployed assets.

Portfolio analytics reuse unchanged source generations under a bounded LRU and
memory budget. Every read checks source freshness, and writes/recalculations
change the generation; a wall-clock minute no longer forces the same expensive
calculation again. Live task warnings and derivative observations remain outside
that cache.
