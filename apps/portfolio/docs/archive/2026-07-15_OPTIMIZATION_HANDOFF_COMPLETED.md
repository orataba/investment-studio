# Portfolio Optimization Handoff — Completed Record

- Status: completed and archived
- Completed: 2026-07-15, Asia/Shanghai
- Starting commit: `f0bfcd7a87d14ef35d571aec0f01b21206cf1c52`
- Delivery branch: `codex/portfolio-optimization-clean-head`
- Live database: `portfolio_ops_rollback_44d`

This is the historical close-out record for the optimization round that started from the former `03_OPTIMIZATION_HANDOFF.md`. It is not an active plan, compatibility guide, or deferred backlog. Current behavior is defined by code, tests, migrations, [`../01_CALCULATION_SPEC.md`](../01_CALCULATION_SPEC.md), and [`../02_GIPS_ALIGNMENT.md`](../02_GIPS_ALIGNMENT.md).

## Delivered outcome

The round completed the correctness, contract, reliability, UX, performance, and maintainability work described by the handoff. Transitional readers, write aliases, forwarding facades, phase-named tests, and frontend field fallbacks were removed once their callers were migrated. There is one implementation path for each finalized contract.

### Calculation and data correctness

- Cash remains part of NAV and weight allocation but is excluded from covariance risk budgets. Cash risk-share placeholders are rejected; the Taxonomies UI renders cash risk as `N/A`.
- Portfolio TWR, drawdown, period metrics, attribution, and overview boundaries now share the same return chain and reliable valuation endpoints.
- Valuation, return, book-P&L, and attribution coverage are distinct required facts. Unreliable valuation gaps break and later re-anchor the return chain instead of manufacturing a bridging return.
- Short-period annualized TWR, MWRR, and Calmar are withheld by the backend. Research metrics and stale-result governance follow the same eligibility rules.
- Quote resolution is identity-aware by metric family, quote basis, and currency. Ambiguous or incomplete candidates fail closed.
- Price unit and scale are derived from instrument identity by the shared instrument-core contract. Write commands do not accept caller-supplied derived price fields, and bulk writes reject such rows atomically.
- Maintained FX instrument identities, pairs, quote currencies, and positive finite spot-rate validation are defined once in instrument-core. Generic and dedicated writes use that authority; inverse and cross rates fail closed, and a cross rate is complete only when both legs are complete.
- NAV files without an explicit currency inherit the selected fund's canonical currency only after instrument selection. An explicit mismatch is rejected; parsers no longer default every file to CNY.
- Watchlist quote derivation accepts only complete, positive, identity-valid shared observations. It no longer promotes partial series or silently rewrites a supplied metric family from its quote basis.
- Every Registry observation now has a positive finite decimal value, canonical status, master-instrument currency, canonical unit/scale, and—when applicable—a maintained FX identity. All five quote-policy roles are persisted and validated; runtime readers no longer infer absent roles.
- Fixed-income transaction math, dirty/clean-price eligibility, accrued interest, settlement dates, fee treatment, pending-settlement FX, and source precision are covered by executable tests.
- Transaction audit controls, idempotency, row versioning, research lifecycle, and compact list payloads are implemented without replacing the ledger with an event-sourcing layer. Delete requests must supply the exact row-version map returned by the workspace, and internal-transfer pairs can only be written through the atomic paired-transfer endpoint.

### Architecture and maintainability

- The Portfolio performance kernel is split into focused modules for valuation/FX, return chaining, period metrics, attribution, and holdings market profiles.
- Production callers import focused modules directly when they need module-owned primitives, while `performance.py` remains the cross-module report and snapshot orchestration boundary. No legacy same-name compatibility forwarding exports are retained.
- Backend and frontend tests use semantic behavior names rather than delivery-phase names.
- Required API fields are represented as required frontend types; legacy split-coverage fallbacks were removed.
- Performance, Holdings, and security-chart resources are identity-gated, so a portfolio, instrument, or range switch cannot render the prior resource for one frame. Risk and benchmark calculations fail closed when allocation, value, currency, or NAV-basis facts are incomplete.
- Holdings and Performance table views use the backend as their only persistence authority. Browser-local initialization, migration, writes, and empty-backend compatibility behavior were removed.
- Portfolio instrument-list points and execution quotes have typed price contracts at the response boundary. Available execution quotes require a complete identity/unit/scale tuple; unavailable quotes cannot carry stale value fields.
- Platform, Portfolio, and Watchlist share the canonical price contract through instrument-core while retaining their product boundaries.

### Operations and repository hygiene

- LaunchAgent runners require the current exact argument contract; the obsolete four-argument fallback was removed.
- Backup and restore tooling requires an explicit absolute dump path and validates database identity before acting.
- Schema backup and rollback use one shared primitive. Password-bearing application URLs are converted to password-free libpq URLs plus private `0600` passfiles, and schema replacement is replayed in one PostgreSQL transaction so a failure after `DROP SCHEMA` preserves the prior schemas.
- Platform, Portfolio, and Watchlist settings require an explicit non-empty database URL. Release migrations validate all three targets before running, and neither application startup nor migration tooling reads repository-local backend `.env` files. Real runtime secrets live outside the repository.
- The systemd installer stages units before publication, stops the refresh timer/service with the six app writers, takes a verified schema backup, and restores the database, old units, enablement, and exact prior active set on any deployment failure. A rollback failure leaves every managed writer stopped.
- Raw PostgreSQL dumps and private-key formats are ignored. The previously tracked dump was removed from the working tree and preserved outside the repository with restrictive permissions.
- Database migrations are audited before application. Irreversible normalization does not pretend to support downgrade; restoring the pre-upgrade backup is the rollback procedure.

## Live migration record

Before each live cutover, all application services were stopped and the actual LaunchAgent database URL was verified without printing credentials. Four private custom-format rollback backups were created; the first 0011 attempt also proved the failure path when an operator-side post-check contained invalid SQL, and the verified backup restored 0010 before services restarted:

| Cutover | Backup | Bytes | TOC entries |
| --- | --- | ---: | ---: |
| Portfolio final head | `/Users/shaw/Library/Application Support/portfolio-operations-workbench/backups/portfolio_ops_rollback_44d-pre-final-head-20260715T091402Z.pgdump` | 4,250,316 | 292 |
| Registry 0010 | `/Users/shaw/Library/Application Support/portfolio-operations-workbench/backups/portfolio_ops_rollback_44d-pre-registry-0010-20260715T105205Z.pgdump` | 4,257,170 | 296 |
| Registry 0011 rollback drill | `/Users/shaw/Library/Application Support/portfolio-operations-workbench/backups/portfolio_ops_rollback_44d-pre-registry-0011-20260715T123245Z-34731.pgdump` | 4,257,997 | 296 |
| Registry 0011 final cutover | `/Users/shaw/Library/Application Support/portfolio-operations-workbench/backups/portfolio_ops_rollback_44d-pre-registry-0011-20260715T123654Z-36718.pgdump` | 4,258,103 | 296 |

Each dump has a basename-only sibling SHA-256 file that passes `shasum -c`, plus a checked schema manifest and manifest checksum. Dumps, manifests, and checksum files are mode `0600` inside a mode `0700` directory. The fourth dump is the immediate rollback point for the final live state.

Final live migration heads:

| Schema | Head |
| --- | --- |
| Instrument Registry | `20260715_0011` |
| Portfolio | `20260715_0039` |
| Watchlist | `20260712_0025` |

Portfolio migration `20260715_0039` normalizes cash target rows only after constructing and validating a complete merge plan. PostgreSQL execution locks all related tables. Its downgrade raises an explicit restore-from-backup error because the merge is intentionally irreversible.

Registry migration `20260715_0011` builds on the 0010 advisory-mutex cutover. Under an access-exclusive lock it preflights every instrument and observation, materializes all five quote-policy roles, promotes successful email refresh cursors once, installs strict type/currency/status constraints, and replaces both cross-table triggers with the full observation, price, currency, and maintained-FX contract. Both live trigger functions contain the transaction-scoped per-instrument advisory mutex. The migration was also replayed against a full-data isolated restore before the final cutover; its 21-check audit completed with no failure.

Post-migration invariants:

| Check | Result |
| --- | ---: |
| Market-data rows | 232,316 |
| Portfolio transactions | 43 |
| Portfolio snapshots | 119 |
| Reserved cash nodes | 0 |
| Noncanonical cash target lines | 0 |
| Price-contract violations | 0 |
| Observation/status/currency violations | 0 |
| FX-identity violations | 0 |
| Quote-policy violations | 0 |
| Successful email refresh cursor gaps | 0 |
| Canonical cash lines | 2 |

All six application services and the scheduler restored their prior loaded state. The three API health endpoints and three production SPA roots responded successfully. The daily market-data scheduler remained loaded for `21:00` Asia/Shanghai and was not triggered manually during migration. Its ordinary `21:00` run later completed successfully with exit code `0`: 118 instruments were updated from the Tushare and email sources, with no failed items or downstream failures.

## Verification record

| Gate | Result |
| --- | --- |
| Portfolio backend | `411 passed, 2 skipped`; both explicitly rerun against the dedicated PostgreSQL test role and passed |
| Portfolio frontend | `43` files, `160` tests; TypeScript and production Vite build passed |
| Platform backend | `148 passed, 3 skipped`; all three explicitly rerun against PostgreSQL and passed |
| Platform frontend | `6` files, `23` tests; production build passed |
| Watchlist backend | `92 passed, 3 skipped`; all three explicitly rerun against the dedicated PostgreSQL test role and passed |
| Watchlist frontend | `4` files, `13` tests; production build passed |
| Infrastructure | `9` Python audit tests and all `12` shell integration suites passed, including real PostgreSQL restore-failure injection and systemd migration/unit rollback faults |
| Migration drift | Registry, Portfolio, and Watchlist temporary-database upgrades reached their exact heads with no new autogenerate operations |
| Source hygiene | Python compileall, shell syntax validation, secret/large-file scans, and `git diff --check` passed |

After the scheduled refresh, the live read-only audit completed all `21` checks with `0` failures and `0` warnings. Policy-preferred adjusted-close coverage is complete (`listed_total_return_coverage=0`); no raw-close substitution or warning suppression was used.

Real-browser acceptance passed for:

- `/portfolios/3/overview`
- `/portfolios/3/holdings`
- `/portfolios/3/performance`
- `/portfolios/3/risk`
- `/portfolios/3/taxonomies`
- `/portfolios/3/research`
- `/portfolios/3/transactions`
- `/portfolios/3/accounts`
- `/portfolios/3/holdings/anz73a`
- Platform `/instruments`
- Watchlist `/`

Every route reached its page-specific business-data marker with no final loading state, application error, or fake-zero summary. Cold Risk, Taxonomies, and Research requests were explicitly awaited until their business-data markers replaced the loading state. Browser console capture contained no warnings or errors. The detail page also verified that Official NAV drives valuation while Total Return NAV remains a separate performance series.

## Remaining operational observations

- The removed database dump can still exist in historical Git objects and the remote history. Purging it requires a separately authorized history rewrite and coordinated force-push; it was intentionally not performed as part of an ordinary code optimization round.
- There is no active implementation phase or deferred compatibility cleanup in this archive.
