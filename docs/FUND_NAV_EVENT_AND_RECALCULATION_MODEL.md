# Fund NAV Event, Projection, and Recalculation Model

## Canonical public data

The shared Registry publishes exactly two fund NAV quote bases:

- `official_nav`: the provider's unit NAV.
- `total_return_nav`: a provider-explicit or dividend-reinvested total-return NAV.

Reported cash-cumulative NAV is retained only as raw evidence. It is never
published as total return. When reinvestment cannot be proved, the affected
`total_return_nav` rows are absent and consumers render `NA`; `official_nav`
remains available.

## Immutable audit ledger

A stable `fund_nav_action_id` identifies one economic action. Each edit appends
a `FundNavEvent` revision with `original`, `correction`, or `cancellation`; no
revision is updated or deleted. Every revision records its predecessor,
operator, reason, source, and provenance.

Dividend reinvestment price is separate evidence bound to one exact action
revision. It has its own immutable revision chain. Correcting an action never
silently carries forward evidence from the superseded action revision. The
operator must submit new evidence explicitly.

The data CLI requires a client mutation ID, operator identity, revision
reason, and optimistic predecessor ID. A byte-for-byte semantic replay is a
no-op. Reusing a mutation ID for different facts, branching from an old head,
or changing an already-recorded decision is rejected.

Confirming a private action candidate is a durable operation across the
`data_ingestion` and `instrument_data` schemas. Data processing first records an
exact request snapshot, fingerprint, mutation ID, operator, and candidate-bound
event ID in `confirming` state. Registry publication then commits, and only
afterward does the candidate become `resolved`. A crash at either boundary
leaves the same request resumable; a concurrent rejection or a different
confirmation conflicts. The CLI lists `confirming` decisions and provides a
`nav candidate-resume` command, so a pending decision is never hidden or converted into a
second Registry action.

## Projection versions and factors

Every publication creates or reuses a deterministic projection run whose
fingerprint covers the durable raw observations and the exact current action
and evidence revision IDs. Runs and factors are append-only; one atomic current
pointer selects the published run.

Projection kinds are:

- `provider_explicit`: factor levels are implied directly by provider
  unit/total pairs. A factor may carry forward across later unit-NAV dates
  only while no current fund action intervenes; those later totals retain
  derived lineage back to the provider factor.
- `event_derived`: one verified anchor followed by ordered action factors.
- `hybrid_reanchored`: an event-derived segment can be restarted by a later
  provider-explicit total, then continue through later verified actions.

Projection status is:

- `complete`: every complete unit-NAV date has a provable total-return row.
- `partial`: at least one total-return row is provable, but one or more unit-NAV
  dates are intentionally `NA`.
- `unavailable`: no total-return row is provable.

Factor records use deterministic logical keys inside a run. The Registry owns
physical factor IDs and validates every referenced chain. An event-derived cash-distribution
factor requires the current action revision and current reinvestment evidence;
a split factor requires the current split revision. Same-day actions require
an explicit unique sequence. An unexplained cash discontinuity, missing
reinvestment evidence, or ambiguous order breaks only the affected segment.
A later provider-explicit total may re-anchor the curve without rewriting
history.

The existing v7 `provider_implied / provider_cash_cumulative` path is separate:
it infers an endpoint adjustment from sufficiently distinguishable cash-cumulative
provider disclosures and the first complete unit-NAV observation after the cash
change. Its factor records the inferred cash amount, endpoint NAV and source
observations. This is not a confirmed action or proof of the investor's actual
reinvestment date. It never creates Portfolio cash or units. A verified notice
and exact reinvestment evidence take precedence; investor-specific performance
fees belong only to Portfolio and do not reduce the instrument's gross return.

## Recalculation and concurrency

The Registry publication transaction atomically appends revisions and factors,
switches the projection pointer, replaces the canonical NAV rows, advances the
source generation watermark, and returns:

- `changed`
- `dirty_from`
- `market_data_updated_at`
- `published_projection_run_id`

Data processing sends downstream notifications only when `changed` is true. The HTTP
notification is a low-latency hint, not a correctness boundary.

Portfolio stores durable per-portfolio recalculation requests. Dirty dates are
merged to the earliest affected date, work for one portfolio is serialized,
expired claims recover after restart, and a source-generation fence discards a
calculation whose input changed while it was running. A bounded Registry
reconciliation scan repairs a lost notification.

Watchlist uses the same pattern with durable per-instrument jobs, leases,
heartbeats, deduplication, and a source-generation fence. Its bounded keyset
reconciliation compares local materialization cutoffs with set-based Registry
summaries, so a restart or failed notification eventually self-heals without a
full Registry scan or N+1 history loads.

The canonical Registry publication finishes atomically before an admin
request is acknowledged. Heavy Portfolio and Watchlist rebuilds run from their
durable queues, so they do not hold the admin request open. The same consumer
is not recalculated concurrently, and an older calculation cannot overwrite
results derived from newer source data.

## Portfolio accounting boundary

Portfolio valuation and ledger-driven return use `official_nav`. In the
absence of a confirmed cash-distribution event, Portfolio applies the explicit
assumption that there is no unrecorded distribution and keeps return
calculable. This does not make `official_nav` a total-return series and does
not relax the `NA` rule for fund research or risk analytics.

A current Registry `cash_distribution` revision is projected into an
idempotent review task for every entitled Portfolio securities account. The
task is keyed by portfolio, account, source, and stable action ID; it contains
the current Registry revision and entitlement calculation, but it never posts
cash or units. There are no fund-specific rules in Portfolio.

An operator records and links the actual `dividend`,
`dividend_reinvestment`, or cash-dividend plus reinvestment-purchase facts.
Portfolio validates the account, instrument, entitlement date, and rounded
gross entitlement before resolving the task. Registry corrections and
cancellations reopen the task without mutating linked transactions. Current
links and immutable human-review history are stored separately.

Transaction changes use the existing snapshot invalidation and durable
portfolio recalculation queue. Event notifications synchronize tasks as part
of the same downstream handoff; lost notifications are repaired when the
Portfolio task/read paths reconcile against current Registry revisions.

## Email acquisition

Configured mailbox folders are acquisition coverage, not the fund universe.
`INBOX` remains configured alongside dedicated product folders. Each folder
has an independent `UIDVALIDITY + UID` cursor; normal runs fetch only new UIDs.
Headers are fetched in batches, full messages only for discovery candidates,
attachments are content-addressed, and parse/routing work is durable and
retryable.

A database lease serializes the mailbox scan across the API and scheduler.
Lease tokens, expiry, heartbeat, and `UIDVALIDITY` fencing prevent a recovered
or old-generation worker from advancing a newer cursor. Retry is mailbox-batch
scoped, so one failed fund cannot trigger another full scan of the same folder.
A newly discovered or retry UID that disappears between search and fetch is
confirmed once, recorded as terminal, and cannot force the folder to restart
or block later mail.

XLS and XLSX attachments are parsed across every worksheet with worksheet
provenance and deterministic duplicate removal. Conflicting same-email values
fail closed. Unsupported, empty, or oversized attachments remain visible in
the operations inventory; PDFs are not silently OCR-guessed into canonical
NAV. A first cutover can run an explicit full-history scan, newest first, but
both that scan and a fresh cursor are bounded by the configured inclusive
`INVESTMENT_STUDIO_DATA_EMAIL_HISTORY_START_DATE`. The deployment value must
preserve the prior year-end observation required for YTD reporting. IMAP applies `SINCE` before
header transfer and the ingestion layer rejects older durable headers before
body acquisition. NAV observations embedded in a newer attachment but dated
before the same boundary remain rejected raw evidence and never enter the
publication outbox. The same boundary filters every durable source row for an
email-managed fund, including legacy Registry snapshots retained as audit
evidence. Each scheduled run detects any current canonical NAV projection that
still crosses the boundary and rebuilds it atomically, so a boundary change
cannot leave stale published history behind. The durable folder cursors then
make scheduled runs incremental.

After source refresh, the same locked job queries Registry current-projection
pointers directly and rebuilds only runs whose method version is older than
the deployed projection contract. This reconciliation reads durable raw
evidence and never rescans the mailbox or refetches market data. The live-data
audit fails while any current pointer still references an older method, so a
code deployment cannot silently leave mixed projection semantics behind.

Parsed rows are matched only by exact Registry identity or an explicit product
rule. Active fund names and identifiers widen header discovery, but never the
final exact-routing threshold. Ambiguous, conflicting, unmatched, and
unsupported items remain in the operations inventory for review; the system
does not auto-create a fund from an email guess. Daily or weekly frequency
controls freshness expectations, never whether a mailbox folder is scanned.

When an operator registers a previously missing fund with an exact identifier
or exact fund name, the next email run requeues only valid `unmatched` evidence
for identities now present in Registry. It reuses the immutable attachment and
parsed rows instead of downloading or parsing the mailbox again. Rejected rows
stay rejected, and ambiguous identities stay in administrator review rather
than being reassigned by a fuzzy or first-match rule.
