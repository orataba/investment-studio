# Portfolio Calculation Audit - 2026-05-10

## 1. Scope

This audit covered the Portfolio calculation surface that can change investment interpretation:

- daily true TWR, cumulative TWR, annualized TWR and drawdown;
- period bridge calculation, period P&L split, group TWR and contribution;
- instrument-level quote-derived trend metrics versus portfolio cash-flow-aware performance;
- realized volatility, Sharpe, Sortino, covariance, correlation and realized risk contribution;
- point-in-time Risk page drift and risk-budget comparators;
- Research recursive target-weight solve, target set resolution, covariance estimation and risk-budget solver diagnostics;
- materialized daily snapshots, holdings snapshots and contribution slices versus rebuilt calculation paths.

The review standard is correctness first: fair-value performance, external cash-flow neutrality, consistent valuation frequency, explicit coverage, and no hidden substitution between unrelated comparators or target dimensions.

## 2. Canonical Decisions Confirmed

- Portfolio TWR remains the primary return measure. IRR / MWROR is a supplementary money-efficiency measure and cannot replace TWR in summaries.
- External flows are limited to portfolio-boundary deposits, withdrawals and true investor distributions. Internal trades, dividends, coupons, fees, taxes and internal transfers are not portfolio-level external flows.
- Query-window TWR index, daily series and drawdown must be rebased inside the requested window. They must not reuse inception-to-date cumulative TWR as a period curve.
- Cost basis method only affects book cost, realized book gain, unrealized book P&L and lot display. It does not feed fair-value return.
- Risk statistics use return observations, not NAV path changes. Stale price carry-forward can preserve state views but cannot create risk samples.
- `sample_covariance` is a sample estimator with denominator `n - 1`. Population covariance is not the canonical plain sample risk model.
- Risk / Research frequency alignment uses daily / weekly / monthly period-end observations. Missing period returns remain missing; cross-period forward fill is not allowed.
- SAA/TAA and weight/risk-budget comparators are independent. A missing comparator is unavailable, not replaced by another comparator.
- Multi-member Research scopes require complete active target sets. Missing targets, invalid totals, insufficient aligned history or an unsatisfied risk-budget solve are explicit errors, not synthetic targets.

## 3. Changes Made In This Audit

- Removed Research risk-budget substitutes that returned target weights when history or solver quality was insufficient.
- Removed automatic equal local target generation for multi-member scopes without configured complete target sets.
- Enforced target non-negativity and target total validation for Research scope resolution.
- Changed Research and frontend Risk `sample_covariance` to `n - 1`.
- Removed stale frontend labels for old Research solver states.
- Updated Portfolio README, calculation spec, domain model, PRD and GIPS-informed methodology docs to match the strict calculation policy.

## 4. Submission Checklist

Before merging a calculation change:

- run backend full tests from `apps/portfolio/backend`;
- run `tests/test_research_api.py` when Research, target sets, covariance or solver behavior changes;
- run frontend build when Risk, Research or shared IA labels change;
- search docs and UI labels for stale substitute-success language tied to calculation results;
- verify `git status --short` only contains intentional source and documentation changes;
- keep `apps/portfolio/backend/research_outputs/` and frontend `dist/` as ignored runtime/build artifacts.
