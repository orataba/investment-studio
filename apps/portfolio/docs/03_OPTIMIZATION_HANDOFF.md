# Portfolio Optimization Handoff

- Status: active execution handoff for the next Portfolio optimization round
- Prepared: 2026-07-15, Asia/Shanghai
- Code baseline before this documentation-only cleanup: `e379fa5a879692c835693020a6f86be321be3070`
- Branch: `codex/rollback-portfolio-overhaul`
- Restored application tree: equivalent to pre-overhaul `44d9842`

This is the only active review/backlog document. It intentionally replaces separate review, retrospective, blueprint and roadmap files. When the work is complete, move durable rules into the canonical documents and delete or archive this handoff.

## 1. Objective

Improve the restored Portfolio application without redesigning its mature pages or reintroducing the failed 2026-07 overhaul.

The execution order is:

1. lock the current product contract with rendered-page and calculation tests;
2. fix observed correctness defects;
3. fix calculation and data-contract boundaries;
4. improve reliability, auditability and performance;
5. extract modules only around already-tested behavior.

Do not treat this as authorization for a second all-at-once rewrite.

### 1.1 Hard product/runtime scope: Portfolio only

The product behavior and runtime implementation phases below apply only to `apps/portfolio` and the `portfolio` database schema. Read-only inspection of repository-level contracts and synchronized updates to shared documentation are allowed. This handoff does not authorize changes to `apps/watchlist`, Watchlist fund/instrument detail pages, the `watchlist` schema or shared runtime code; those require the proven shared-contract stop condition in section 7.

The two apps intentionally reuse familiar investment labels, but identical tab names do not mean identical pages, metrics or components:

| Page label | Portfolio meaning | Watchlist meaning |
| --- | --- | --- |
| Overview | One portfolio's NAV, TWR index, drawdown, sleeves and holdings under `/portfolios/:portfolioId/overview` | One instrument/fund's profile and market-data overview inside `/instruments/:instrumentId` |
| Performance | Portfolio ledger performance, external-flow-neutral TWR, period P&L and attribution under `/portfolios/:portfolioId/performance` | Single instrument/fund NAV or price returns and benchmark comparison in the Instrument Detail `Performance` tab |
| Risk | Current multi-holding covariance, contribution, correlation and target gaps under `/portfolios/:portfolioId/risk` | Single instrument/fund volatility, drawdown and benchmark risk in Instrument Detail |
| Research | Portfolio target solve, capital overlay and hypothetical rebalance/backtest under `/portfolios/:portfolioId/research` | Fund/instrument qualitative research and Watchlist-local research context |

Identity checks before any browser or code change:

- Portfolio code path: `apps/portfolio`; API `127.0.0.1:8001`; web `127.0.0.1:5174`; route prefix `/portfolios/`.
- Watchlist code path: `apps/watchlist`; API `127.0.0.1:8000`; web `127.0.0.1:5173`; routes `/watchlists/` and `/instruments/`.
- Confirm the app header, URL prefix, source file path and API origin before diagnosing or accepting a page.
- Never use a Watchlist Instrument Detail screenshot, selector, fixture or metric definition as evidence for Portfolio Performance/Risk, or vice versa.
- A shared visual language is allowed; shared business calculations or page components require a proven cross-app contract and a separate, explicitly reviewed change with tests in both apps.
- If a Portfolio fix appears to require Watchlist behavior changes, stop under the shared-contract condition in section 7 instead of broadening scope silently.

## 2. Authoritative Reading Order

1. [`../../../README.md`](../../../README.md) — repository and runtime entry point.
2. [`../../../docs/PLATFORM_BOUNDARIES.md`](../../../docs/PLATFORM_BOUNDARIES.md) — hard Platform / Watchlist / Portfolio ownership boundaries.
3. [`../README.md`](../README.md) — current Portfolio capability and commands.
4. [`01_CALCULATION_SPEC.md`](./01_CALCULATION_SPEC.md) — canonical Portfolio calculation and target contract.
5. [`02_GIPS_ALIGNMENT.md`](./02_GIPS_ALIGNMENT.md) — Portfolio GIPS-informed governance boundary.
6. [`../../../docs/FRONTEND_DESIGN_BASELINE.md`](../../../docs/FRONTEND_DESIGN_BASELINE.md) — mature UI guardrails and cross-app page identity.
7. [`../../../docs/USER_MANUAL.md`](../../../docs/USER_MANUAL.md) — user-facing workflow.
8. This file — Portfolio-only implementation deviations, order and acceptance gates.

Current code and live behavior take precedence over archived material. If code and a canonical document disagree, do not silently choose one: reproduce the difference, decide the intended contract, update the test and document in the same bounded change.

## 3. Locked Product Decisions

### 3.1 Preserve the mature product

Keep these structures unless a reproduced defect requires a local change:

- Overview main chart, Portfolio Value / TWR Index switch, real-date range slider, drawdown, sleeve overview and top holdings;
- Holdings dense table, locked Instrument column, grouping, saved columns, export and Security Detail;
- Performance scorecard, risk summary, Calculation bridge and grouped attribution;
- Transactions Fact / Lots / Postings inspector and accounting-impact workflow;
- Accounts read-only derived account, cash, position and ledger views;
- Taxonomies tree, assignments, TargetSet workflow and instrument-universe state;
- Risk strict aligned samples, no missing-return zero fill and no pairwise covariance fallback;
- white, cool-neutral, table-first terminal visual language.

Ordinary reader-facing returns use two decimals. Unit values, input parameters and daily statistics may use other business-appropriate precision. Display precision must not reduce stored facts or calculation precision.

### 3.2 Risk Target excludes cash

This decision supersedes the earlier review suggestion that Risk should accept a `cash_bucket:__cash__` risk-target line.

- Cash may have `target_weight`; it participates in NAV, capital allocation and Weight Target Gap.
- Cash must not have `target_risk_share`, including a `0%` placeholder.
- A risk-budget TargetSet contains only eligible non-cash risk-bearing members; their target risk shares sum to `100% ± epsilon`.
- Cash is excluded from risk-budget completeness, the covariance-risk denominator and Risk Target Gap.
- If a combined table shows cash for capital context, its Risk Target cell is `— / N/A`, not `0.00%`.
- A scope containing no risk-bearing member returns `no risky members / unavailable`; it does not produce a cash risk budget.
- Research may place residual capital into a system cash-like member after solving risky sleeves. That residual is capital-overlay output, not risk-budget input.
- A future explicit cash risk factor would require a separate product decision and model; do not infer it from cash weight.

Known implementation deviations:

- `frontend/src/pages/TaxonomiesPage.tsx` currently creates, validates, renders and submits cash risk as `0` around lines 405, 504–512, 1841–1847, 2132 and 2723–2725.
- `backend/portfolio_app/services/portfolio_store.py` around lines 1896–1910 requires a cash risk line equal to zero.
- `frontend/src/pages/RiskPage.tsx` around lines 1858–1874 rejects the resulting direct cash line. Correct behavior is not to accept a cash risk target: Weight Gap may use cash weight, while Risk Gap filters cash entirely.
- `backend/portfolio_app/services/research_solver.py` around lines 2002–2149 injects cash risk zero and permits cash-only risk-budget paths.
- Existing tests in `test_taxonomies_api.py` around 867–903 and 1083–1161, and `test_research_api.py` around 728–777, encode the old behavior and must be rewritten deliberately.

Required data transition:

- preserve cash `target_weight` rows;
- set legacy cash `target_risk_share` to null where the row also carries valid weight data;
- delete a row only when it exists solely as a cash risk-budget placeholder and no other enabled dimension needs it;
- reject new cash `target_risk_share` values, including explicit zero;
- audit before migrating; do not blindly delete TargetSet rows.

### 3.3 GIPS boundary

- Continue to state **GIPS-informed**, never GIPS compliant.
- The backend, not only the UI, must withhold annualized TWR/MWR and Calmar for periods shorter than one year.
- Fixed-income NAV requires dirty price or clean price plus accrued interest.
- External cash flow defaults to actual receipt/payment date unless a documented, consistently applied pre-announced-and-investable policy applies.
- Current return basis is `after recorded expenses`; do not label it formal gross-of-fees or net-of-fees until fee classification and completeness exist.
- Formal firm definition, composites, GIPS reports, 36-month disclosure, advertising rules and verification are a separate future compliance program.

### 3.4 Benchmark boundary

The current product has a Manual comparator, not a persisted primary benchmark assignment. Until the full assignment model exists, all relative metrics must use the manual name and require:

- same currency or explicit backend FX conversion;
- verified total-return basis for canonical relative results;
- a reliable start anchor;
- coverage of every eligible portfolio return date.

## 4. What Must Not Return

Do not merge or cherry-pick the failed overhaul commits `11afd2f`, `0d0e1c7`, `c022e4c` or `b11d0cb` as implementation units.

Do not restore:

- the sealed publication / exact manifest / worker-fencing architecture;
- end-to-end Decimal50 or huge NUMERIC fields for statistical analytics;
- simultaneous schema, calculation, API, UI and operations replacement;
- the Research-to-Allocation rename;
- publication lineage, rounding audit or daily internal-audit tables on the Performance page;
- four-decimal ordinary return display;
- old review and blueprint files deleted from the current tree.

Useful principles from the failed blueprint may be reimplemented independently: facts/calculations/judgment separation, one calculation authority, finite-data honesty, additive revision history and reproducible tests.

## 5. Verified Current Baseline

- The restored Overview main chart and range controls are present.
- Holdings current live rows have 1W/MTD/YTD values where their selected series has valid history; the failed-overhaul blanket blanks are gone.
- The previous rollback validation passed 519 Portfolio backend tests and frontend test/build, but the frontend suite lacks rendered DOM/E2E coverage and therefore did not protect page contracts.
- `infra/scripts/audit_live_data.py --json` reported 0 hard failures and 1 warning on the rollback database. The warning is missing same-date `adjusted_close` coverage for 13 active equity/ETF instruments on two recent dates; do not fill it with raw close.
- Portfolio 3 live API returned cumulative TWR about `3.6335%`, while the full-range Overview chart headline showed about `3.06%`.
- Performance calendar did not complete within 30 seconds in the local sample. Holdings was about 147 KiB and Research workbench about 110 KiB because list payloads carry repeated detail arrays.

These observations are a review snapshot, not a production SLA or a claim that every latent defect has affected live data. Current live data has no bond positions.

## 6. Delivery Plan

### Phase 0 — Characterization before behavior changes

Add the smallest rendered-page/browser contract suite that protects:

- Overview chart, range slider, headline/summary agreement and quality warning;
- Holdings default trend columns, legitimate unavailable reasons and total-row semantics;
- Performance one selected period across summary/chart/Calculation, ordinary returns at two decimals and no internal audit UI;
- Risk Weight Gap with cash, Risk Target Gap without cash and valid non-cash SAA/TAA comparison;
- Taxonomies cash weight plus null/absent cash risk target;
- Research short-history metrics and former-instrument status;
- Transactions form eligibility and accounting-impact inspector.

Add formula golden tests before changing each calculation. Source-string `toContain` tests do not count as rendered-page protection.

### Phase 1A — Observed correctness defects

1. **Risk cash contract**

   Implement the locked decision in section 3.2 across model validation, migration, Taxonomies, Risk, Research and tests.
2. **Overview TWR boundary**

   `frontend/src/lib/performanceSeries.ts` starts the index after applying the first visible daily return, while the navigation chart rebases from that point and drops the first day. Chart headline, drawdown and summary must share the backend period boundary.
3. **Short-period and Research metrics**

   Backend annualized fields are unavailable below one year. Research YTD requires a year-start anchor; Sharpe uses annualized arithmetic mean excess return divided by annualized volatility; Calmar follows the annualization eligibility.

Do these as separate reviewable changes, not one refactor PR.

### Phase 1B — Return-chain and coverage correctness

1. Split `valuation_coverage`, `return_coverage`, `book_pnl_coverage` and `attribution_coverage`. Fair-value NAV/TWR must not depend on cost-basis or attribution completeness.
2. Break the return chain when an external flow occurs inside an unreliable valuation gap. The next reliable point re-anchors and has no bridging return.
3. Require reliable start/end boundaries and continuous coverage for closed monthly, MTD and YTD metrics; inception inside a period is not full-period return.
4. Backend endpoints clamp to the latest reliable endpoint and return requested/effective as-of plus reason.

Golden counterexample: D1 NAV 100; D2 deposit 100 with an unpriced asset; D3 NAV 200. D3 daily TWR must not be `+100%`.

### Phase 1C — Quote identity and fixed income

1. Resolve market series by at least metric family, quote basis and currency; multiple candidates are ambiguous, not silently merged.
2. Validate valuation-series currency and explicit price unit/scale.
3. Fix bond transaction scale: face quantity `1000` at percent-of-par price `98.5` has gross `985`.
4. For bonds, dirty price is directly eligible; clean price requires same-date accrued interest. Clean-only valuation fails closed.
5. Audit any historical bond data before applying scale conversion. Current live data has no bonds, so these are latent blockers rather than confirmed live losses.

### Phase 2 — Data reliability and auditability

- Holdings trend selection returns basis, coverage and reason; one recent total-return point must not lock out a complete alternate price history. Never splice bases.
- Raw-price trend across a split is adjusted from confirmed actions or withheld.
- Overview and Performance use the same benchmark guard; price-only comparator remains exploratory.
- External-flow/economic dates and fee categories follow sections 3.3 and the canonical spec.
- Track pending-settlement monetary FX separately from asset capital gain.
- Add expected frequency/calendar/release-lag semantics and fix adjusted-close ingestion completeness.
- Add transaction change log, request idempotency and optimistic row version without rebuilding the ledger as event sourcing.
- Preserve source quantity/amount precision with practical NUMERIC/Decimal scales; continue statistics in float64 with tolerances.
- Snapshot refresh checks source generation before and after calculation and discards mixed-generation output.
- XIRR returns solver status and publishes only a unique valid root; risk metrics require explicit minimum samples and frequency metadata.

### Phase 3 — UX and performance

- Risk correlation defaults to Current Holdings; Full Universe remains optional and failed matrices identify missing members/dates.
- Holdings Portfolio Total must not look like portfolio TWR. Remove its trend return or label it `Current-weight blended instrument return` with coverage.
- Research shows Held / Observed / Former and explicit research eligibility; an unapproved former instrument with positive target requires PM review.
- Overview shows actionable shared quality warnings; generic corporate-action warnings appear only when an actual issue is detected.
- Taxonomies must not intercept Tab globally; mutation shortcuts require scoped focus and modifiers.
- Add Latest/Reset and MTD/QTD/YTD/1Y/SI period controls without changing the Performance page structure.
- Loading uses skeleton/`aria-busy`, not fake `$0.00 / 0.00%` data.
- Profile Performance calendar first; then add bounded caching/batching. Default Holdings and Research lists return compact rows and lazy-load detail.

### Phase 4 — Behavior-preserving extraction

Only after the preceding contracts are green, extract pure responsibilities from `performance.py` behind the existing facade:

- `valuation_fx`
- `return_chain`
- `period_metrics`
- `attribution`
- `holdings_market_profile`

Frontend extraction follows the same rule: move pure calculation, request state and presentation components one responsibility at a time while preserving routes, DTOs, DOM structure and visual behavior.

## 7. Hard Delivery Gates

Every implementation unit must satisfy:

- one semantic family per change;
- additive/reversible schema migrations with an explicit data audit;
- no destructive local-database rebuild or loss of transactions;
- API/DTO compatibility unless an additive transition is documented;
- calculation golden tests plus rendered-page contract tests;
- old/new characterization for pure module extraction;
- `git diff --check` and no secrets, runtime outputs, databases, logs or build artifacts in Git.

Minimum automated checks:

```bash
(cd apps/portfolio/backend && pytest)
(cd apps/portfolio/backend && pytest tests/test_taxonomies_api.py tests/test_research_api.py -q)
npm --prefix apps/portfolio/frontend run test
npm --prefix apps/portfolio/frontend run build
.venv/bin/python infra/scripts/audit_live_data.py --json
git diff --check
```

Browser acceptance must cover Overview, Holdings, Performance, Risk, Taxonomies, Research, Transactions, Accounts and Security Detail with seeded or current authorized data. A blank or unavailable result is acceptable only with the correct reason.

Every browser acceptance record must include the Portfolio URL under `127.0.0.1:5174/portfolios/...`; a similarly named Watchlist tab under `127.0.0.1:5173/instruments/...` is not valid evidence.

Stop and ask before:

- deleting or rebuilding live/local business data;
- changing the GIPS/no-compliance product boundary;
- creating a formal composite/reporting system;
- replacing routes or mature page layouts;
- modifying Platform/Watchlist without a proven shared-contract need.

## 8. New Conversation Startup

Use the existing services if they are running. Do not start duplicate ports.

```bash
cd /Users/shaw/Projects/portfolio-operations-workbench
git status --short
git branch --show-current
git rev-parse HEAD
infra/launchd/status_local_services.sh
curl -fsS http://127.0.0.1:8001/api/health
```

Portfolio entry:

```text
http://127.0.0.1:5174/portfolios/3/overview
```

Recommended prompt for the new conversation:

> Read `apps/portfolio/docs/03_OPTIMIZATION_HANDOFF.md` completely and treat it as the active, Portfolio-only execution contract. Before changing anything, confirm that every proposed application/runtime code change is scoped to `apps/portfolio`, browser pages use `127.0.0.1:5174/portfolios/...`, and APIs use port 8001; repository-level contracts may be inspected and shared documentation may be synchronized, but do not modify `apps/watchlist`, the `watchlist` schema or shared runtime code without satisfying the handoff stop condition. Do not confuse similarly named Watchlist Instrument Detail tabs on port 5173. Inspect the current diff and runtime, then execute the full roadmap sequentially. Start with Phase 0, pass its gates, and implement each Phase 1A item as a separate bounded change before continuing through Phases 1B, 1C, 2, 3 and 4. Do not combine semantic families or skip a failed gate. Preserve the mature Portfolio UI, do not restore the failed overhaul, do not rebuild the database, and continue until the roadmap is complete or a handoff-defined stop condition genuinely requires user direction. At each phase, leave a clear diff, test report and remaining-risk summary.

## 9. Handoff Retirement

After all phases are complete:

1. move stable calculation rules to `01_CALCULATION_SPEC.md`;
2. move stable GIPS policies to `02_GIPS_ALIGNMENT.md`;
3. move stable visual/interaction rules to `FRONTEND_DESIGN_BASELINE.md`;
4. update `USER_MANUAL.md` for user-visible behavior;
5. delete this handoff if it has no unique historical value, or archive one dated completion record under `docs/archive/`.

Do not leave a second permanent specification behind.
