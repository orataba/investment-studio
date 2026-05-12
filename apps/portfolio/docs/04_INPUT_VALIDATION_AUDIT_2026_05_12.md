# Portfolio Input Validation Audit - 2026-05-12

This note records the documentation follow-up after the risk/research input validation pass. It supersedes the 2026-05-10 response-boundary wording for legacy instrument references and pins the current missing-return contract.

## 1. Scope

Reviewed current Portfolio docs, README files and recent validation code paths after commit `61c9b9c Tighten portfolio risk input validation`.

The review focused on:

- stale documentation that still described pairwise covariance entry samples;
- stale wording that implied legacy `asset_*` request/response compatibility;
- missing documentation for the Research missing-return policy and stricter risk-budget solver threshold;
- whether historical phase docs or generated artifacts should be deleted.

## 2. Current Contract

Canonical instrument references:

- persisted and serialized Portfolio data uses `instrument_id`, `instrument_name` and `instrument_type`;
- migration `20260511_0022_canonical_instrument_refs.py` canonicalizes historical JSON keys from `asset_*` to `instrument_*`;
- runtime validation rejects `asset_id`, `asset_name`, `asset_type` and account-level `allowed_asset_types`; callers must use `instrument_*` and `allowed_instrument_types`;
- this is not a compatibility fallback. Old field names are migration input only, not accepted runtime API or store shape.

Missing returns:

- default Research risk input policy is `strict`;
- active return matrices must be complete across every participating member;
- missing single-member returns are not filled with `0`, not stale-carried across target periods and not combined through pairwise covariance entries;
- explicit `complete_case_drop` may drop entire rows containing any missing active member, but only within the configured safety limits: at most `10%` missing rows, latest complete row no more than `5` daily / `14` weekly / `62` monthly days stale, and enough complete observations for the selected model.

Solver behavior:

- `sample_covariance` uses the complete aligned sample and the `n - 1` denominator;
- covariance annualization uses the actual observation density of the complete sample index;
- risk-budget solve must satisfy max absolute risk-share gap `1e-4` share units (`0.01 percentage points`);
- negative signed risk share, infeasible target sums, missing cash residual members or unavailable positive volatility estimates fail explicitly;
- no solver path falls back to target weights, equal weights, alternate contribution modes, unit gross or legacy solver output.

## 3. Documentation Actions

Updated:

- [../README.md](../README.md) for current Portfolio status, canonical instrument refs, missing-return policy and 2026-05-12 calculation status.
- [01_CALCULATION_SPEC.md](./01_CALCULATION_SPEC.md) to replace pairwise covariance language with complete aligned matrix semantics and document `complete_case_drop` limits.
- [02_GIPS_ALIGNMENT.md](./02_GIPS_ALIGNMENT.md) to make the GIPS-informed governance note match strict missing-return handling.
- [03_CALCULATION_AUDIT_2026_05_10.md](./03_CALCULATION_AUDIT_2026_05_10.md) to mark the old response-boundary legacy ref fix as superseded by canonical migration/runtime validation.

Kept:

- repository `docs/PHASE1_PLATFORM_SETUP_PLAN.md`, because it is already explicitly marked as historical and not a current operating guide;
- app README files that still describe active workflows;
- ignored/generated `apps/portfolio/backend/research_outputs/` artifacts, which are runtime outputs rather than canonical docs;
- `ref/pmw` reference material, which is not part of the current docs index.

No current docs were deleted in this pass.

## 4. Submission Checklist

Before future Portfolio risk/research changes are merged:

- search docs for stale terms such as `pairwise covariance`, `asset_*`, `fallback`, `共同有效收益`, and loose solver-threshold wording;
- run backend Research tests when solver, covariance, missing-return policy or TargetSet behavior changes;
- run frontend build when Risk or Research UI labels/settings change;
- keep generated research outputs, frontend `dist/` and cache directories out of source control.
