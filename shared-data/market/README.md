# Studio shared market data

`investment-studio-market` owns public numerical facts and imported public text.
Python consumers import `studio_market`. PostgreSQL stores the catalog, complete
capture markers and current projections. Immutable Parquet files preserve long
history and every observed revision; DuckDB opens them for analytical queries and
does not maintain another database copy. Portfolio positions, NAV, PM opinions,
research conclusions and reports remain in their application schemas.

## Configuration

Use the external `market.env` managed by the repository runtime loader. Required:

```text
INVESTMENT_STUDIO_MARKET_DATABASE_URL=postgresql://studio_user@localhost/investment_studio
INVESTMENT_STUDIO_MARKET_DATA_ROOT=/absolute/studio/market-data
INVESTMENT_STUDIO_MARKET_ROLE=collector
```

The default data directory is `~/.local/share/investment-studio/market-data`.
Database URLs must exclude passwords; use PostgreSQL credential configuration.
Source secrets are file references, never values in bundles or database URLs:
`INVESTMENT_STUDIO_MARKET_FMP_API_KEY_FILE`, `TUSHARE_TOKEN_FILE`,
`DATAHUB_API_KEY_FILE`, `GTJA_ACCESS_KEY_ID_FILE`, `GTJA_ACCESS_KEY_SECRET_FILE`
(each name has the same `INVESTMENT_STUDIO_MARKET_` prefix).
DataHub and official Tushare credentials are distinct. DataHub's configured API
URL defaults to the existing `http://datahubco.com/app-api/openapi/v1/tushare`.
The Regime DataHub adapter requests at most 5,000 rows per REST page and retains
the effective request parameters with each capture. Consumers continue by actual
row count and `has_more`; deterministic HTTP request/authentication failures do
not enter the transport retry path.

The cloud installation uses `ROLE=collector`; the local installation uses
`ROLE=replica`. Text receivers either configure `MI_HOST` and `MI_REMOTE_DIR`
for SSH pull, or `MI_INBOX_DIR` for a completed local SFTP inbox; choose one
transport. Explicit registered-stock/ETF price preparation may supplement missing source history locally through the same numerical collector; those observations retain their own capture clocks and immutable batches. The replica role still schedules only synchronization, with no whole-market acquisition or publication.
The replica additionally configures `NUMERIC_HOST` and
`NUMERIC_REMOTE_DIR`.
These names also use the full prefix. Hosts are existing trusted SSH aliases;
remote directories must be explicit absolute paths. No host is inferred.

The collector can additionally cover public securities used only by another
installation. Set `INVESTMENT_STUDIO_MARKET_ADDITIONAL_INSTRUMENTS` in its external
`market.env` to a JSON object mapping FMP symbols to `equity`, `etf`,
`public_fund`, or `index`, for example `'{"APLE":"equity","600900.SS":"equity"}'`.
These targets join the collector's registered identities for daily reference
supplements and market-close prices; matching identities are deduplicated and
conflicting types fail explicitly. Maintain this public coverage list when an
independent installation needs symbols outside the collector's registry. It does
not register business instruments or copy lists, research, holdings, or users.
Local reference projections continue to read delivered facts without provider calls.

## History and numerical meaning

Every published row has an immutable `numeric:<batch_id>:<row_index>` source ID.
`observed_at` is the actual response capture clock. `available_at` records the
provider's explicit publication clock where present; date-only publication uses
the end of that source date. An `as_of` query requires both clocks to precede the
cutoff. Importing revised history does not create observations in an earlier PIT
period. Financial/as-reported history whose earlier revisions were never captured
remains labelled `provider_history_with_current_revisions`.

For datasets with equivalent current and historical rankings, `latest(as_of)`
reads the current projection only after proving every row in the requested scope
was observed and available by that cutoff in the same database snapshot. Other
cutoffs retain the historical query; this optimization preserves source IDs,
total counts and the requested information clock without scanning full price history.

Forecasts are complete captures grouped by company and annual/quarter frequency.
The frequency capture becomes available only after its last required year
response. A→B→A remains three observations. An empty complete capture removes old
forecast horizons. ETF holdings likewise use one complete capture, including an
empty capture, so removed holdings do not reappear from older files.

`us_eod_daily.close` is FMP's split-adjusted price. Its `adjusted_close` includes
dividend adjustments. `raw_eod_daily` stores unadjusted OHLC from FMP's
non-split-adjusted endpoint (which uses `adj*` wire names); its `adjusted_open`,
`adjusted_high`, `adjusted_low`, `adjusted_close` come directly from the separate
dividend-adjusted response. The adjustment factor is adjusted close divided by
that dataset's close. Report price returns use split-adjusted closes, exclude
dividend total return, and disclose each symbol's actual source dates.

## Maintenance

Run through `bin/investment-studio market`. Examples:

```sh
bin/investment-studio market numeric status
bin/investment-studio market numeric query analyst_estimates --symbols AAPL --as-of 2026-09-07T08:00:00+08:00
bin/investment-studio market numeric collect --groups raw_eod --symbols SPY,QQQ --start 2026-09-01 --end 2026-09-04
bin/investment-studio market numeric collect --groups financial_details,rating_history --symbols AAPL --start 2025-01-01
bin/investment-studio market pipeline daily
bin/investment-studio market pipeline weekly
bin/investment-studio market pipeline registered-prices --market hk
bin/investment-studio market pipeline publish
bin/investment-studio market pipeline sync
bin/investment-studio market pipeline status
```

The daily pipeline collects whole-market US EOD increments, corporate actions,
analyst PIT captures, changed financial statements, macro/market series, ETF
holdings, events, and registered assets' typed supplements. Registered raw prices
have separate market-close jobs and are published immediately after acquisition.
They resume from each symbol's latest published raw observation when it predates
the normal overlap window, filling missed closes and retaining an observed price
for adjustment-change detection after downtime.
Only symbols whose corporate actions changed require their adjusted price
history to be refreshed. Whole-market EOD advances from the last successfully
published bulk session, using provider session dates for holidays.
The default US universe retains US delisted listings by their recorded exchange;
the provider's global delisted directory does not expand US price reconciliation
into overseas markets. Explicit registered-symbol supplements retain their own
market closing clocks.
Raw price history is also refreshed for those action revisions and when changed
adjusted prices are observed in the overlapping recent window. CN/HK/US and the
supported European FMP suffixes use their own closing-time contracts; a US cutoff
does not suppress an already completed CN or HK session.

Weekly work refreshes the directory/profiles, ETF disclosure history, official
ETF filings and low-frequency Chinese futures facts. Additional explicit groups
include `financial_history`, `financial_details`, `rating_history`, and `tushare`.
Official ETF sources run independently by fund: a failed issuer or SEC source
remains a failed item while other funds' successful captures are retained and
published. A partial group is never reported as fully covered.
Detailed as-reported financials, SEC filing metadata and individual grading
history accept explicit symbols; they are not claimed to refresh daily for every
US security. Provider entitlements, pagination and quota failures remain visible
in collection/pipeline status, with actual successful batch dates unchanged.
Macro and market-series acquisition record a result per source series, so one
failed source does not stop other independent series. Failure diagnostics retain
the exception class and source-code location without copying credential-bearing
request URLs or response bodies.

Regime's existing cloud source workers write their specialized HK/FRED/DataHub
observations to this same catalog. Hourly `publish` includes these ready batches;
the shared pipeline does not copy private Regime configuration or models.

## Migration and delivery

`numeric migrate-source /explicit/source` previews the source plan. Add `--apply`
to import. The source DuckDB is read-only; original observation tables and raw
captures rebuild Studio Parquet in bounded chunks. Minute futures, minute-derived
materializations and source Parquet exports are excluded. The receipt lists
source/eligible/excluded counts and filtering reasons; raw evidence remains
traceable. Successfully imported source/dataset batches are skipped on rerun.
`--reimport` explicitly creates another imported capture. After successful import,
the old project and source database are not runtime dependencies.

`pipeline publish` writes pending ready batches to `numeric/outbox`, then atomically
adds each archive to `index.json`. The only numerical archive protocol is
`investment-studio-numeric-bundle` version 1. It carries Parquet, necessary raw
objects, batch/file/capture catalog records and original clocks/source IDs; no
application schemas. Local `sync` follows the entire authenticated directory index
and imports every missing archive, preserving completed receipts if a later
transfer fails. Exact repeated imports are idempotent.

Raw-response paths contain the SHA-256 of the uncompressed response body. New
captures use zero gzip mtime and the platform-neutral OS header byte. When an
existing raw gzip has a different envelope, import still verifies the incoming
compressed object against the bundle manifest, then requires both decoded bodies
to match the path's body hash. It keeps the existing bytes and permissions. This
exception applies only to canonical `numeric/raw/<provider>/<sha-prefix>/<sha>.gz`
paths; other immutable objects retain exact size/hash matching. Invalid gzip or a
different body remains a conflict. Historical captures need no rewrite or re-export.

After correcting a delivery failure, rerun the existing scheduled entrypoint to
resume missing packages and refresh the application projections:

```sh
ENV_ROOT="$HOME/.config/orataba/secrets/investment-studio" \
  PYTHON_BIN="$PWD/.venv/bin/python" bash infra/scripts/run_market_pipeline.sh sync
```

Run this from the repository root with that installation's external configuration.
Check the numeric catch-up result, application projection results and current data
audit; do not delete delivery receipts or immutable objects to force recovery.

MI publishes `mi-text-<sha256>.zip` plus its checksum receipt. Text catch-up lists
only those completed archives, reuses the text importer and retains a receipt per
successful archive. Temporary exports are excluded. SSH host checking is strict;
the archive and its checksum/index arrive from the same trusted host.

## Scheduled entrypoints

`infra/scripts/install_market_pipeline.py --scheduler systemd --role collector
--env-root /external/config` writes nine user service/timer pairs. Daily collection
runs at 07:15 Shanghai time, weekly collection Saturday 11:00, publication hourly
at :40, and text catch-up hourly at :20. Persistent timers catch up after downtime.
BTC/USD has a separate daily 08:15 Shanghai collection after the UTC day closes;
it publishes the shared series and projects registered crypto instruments without
changing the other markets' daily collection time.
Registered prices run at CN15:30, HK17:00, US07:00 and Europe07:05 Shanghai time.
`--scheduler launchd --role replica` writes hourly/login catch-up plus a daily
08:20 synchronization before the 08:30 BTC research check. Delayed collection or
delivery remains an explicit data gap, and does not fabricate a current close. The runner
loads the validated external environment. Acquisition and delivery use separate
locks so a long bulk task cannot block a market close; publication and status
updates serialize their shared directory mutations.
The downstream projection runner can explicitly wait for an existing refresh
with `--lock-wait-seconds`; the default remains immediate overlap rejection.
Only lock contention is retried, and an expired wait still returns exit code 75
without claiming a completed projection.

The installer only writes definitions. It does not enable or start any service.
Activation and source-host configuration are separate deployment actions.

Initial migration storage includes normalized data plus one complete bootstrap
archive. Directory catch-up deletes each incoming temporary ZIP after successful
import, before any later publication creates an outgoing archive. Do not retain
both an incoming bootstrap copy and a second outgoing copy on a constrained
server. Keep the one published bootstrap until the other Studio installation has
verified its full import and a recovery copy exists. Operators can then retire its
ZIP and checksum after upgrading all configured receivers to support retired
payloads. Under the outbox publish lock, atomically publish the retired index
before deleting the ZIP and checksum, so interrupted cleanup only leaves extra
payload files. Retain the index entry's original name, SHA, bytes and batch IDs,
adding `payload_state: "retired"`, `retired_at` in UTC and `retirement_reason`.
Retained batch IDs prevent republication. A replica
must have every retired batch ready, even if an old delivery receipt survives;
otherwise synchronization reports `NumericBootstrapRequired` and the missing IDs.
For a new or restored replica, explicitly run `numeric export-bundle` on the
collector's retained immutable store with `--batch-ids` for those missing IDs,
transfer and verify the new archive using its own checksum, then run
`numeric import-bundle` on the replica and resume sync. Re-exported ZIPs have a new
identity; never reuse the retired ZIP's checksum. Recovery material must include
the batch catalog, Parquet and referenced raw objects, not only a database dump.
No automatic archive deletion or history eviction is performed; measure the
completed data directory and PostgreSQL size before first publication, then
monitor the actual increment rate.

## Interrupted price revision recovery

Corporate-action captures record `price_revision_requests` in their immutable
batch metadata. Announced actions become due on their effective date. Successful
full-history captures write an exact `price_revision_completed` receipt only
after every part succeeds; a failed symbol remains due across process restarts
and does not stop another symbol. Raw and split-adjusted datasets acknowledge
requests independently. Rebuilds cover the retained history, including prices
before 2000 when present. Incremental raw captures also persist an obligation if
a previously observed dividend-adjusted price changes.

The daily pipeline runs outstanding US rebuilds even if the ordinary EOD stage
fails or is already current, and separately reconciles registered raw histories.
A targeted retry can run only those outstanding obligations:

```sh
bin/investment-studio market numeric collect --groups price_revisions --end 2026-09-07
bin/investment-studio market numeric collect --groups raw_price_revisions --symbols SPY,QQQ --end 2026-09-07
```

For a collector failure predating these receipts, preview the recovery from the
explicit incident start. With no symbol list the scope is the current US universe;
pass explicit registered symbols to include other markets:

```sh
bin/investment-studio market numeric recover-price-revisions --since 2026-09-07T23:15:00+00:00 --end 2026-09-07
```

Add `--apply` to append the reviewed receipts. The command makes no provider calls.
It compares immutable action versions and overlapping raw price revisions,
retains each triggering source ID and its actual observation clock, and credits
an existing rebuild only when its successful full-history request ranges are
contiguous, cover the requested cutoff, and include every retained observation
date. A partial history is not completion.
Future actions remain pending until effective. Receipt batches contain no price
or action rows, so recovery never changes a source fact or backdates information.
Repeated application is idempotent. Then retry the outstanding price groups,
publish their batches, and synchronize/project downstream consumers as usual.
