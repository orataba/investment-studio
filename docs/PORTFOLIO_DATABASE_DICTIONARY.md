# Portfolio database dictionary

As of 2026-08-11. Verified against SQLAlchemy metadata and migration heads `instrument_registry@20260810_0023` and `portfolio@20260810_0049`.

This file is for architecture and integration review. External systems should use the APIs documented in [`TRANSACTION_INTEGRATION.md`](TRANSACTION_INTEGRATION.md), not write these tables directly.

Notation: **PK** = primary key, **FK** = foreign key, `?` = nullable, JSON fields are named with `_json` or `_json`-equivalent suffixes. PostgreSQL logical schemas are shown even though local SQLite tests use flat table names.

## Ownership map

| Layer | Tables | Write owner |
|---|---|---|
| Canonical portfolio facts | `portfolio_record`, `account_record`, `derivative_contract_record`, `transaction_record` | Portfolio command API |
| Audit/idempotency controls | `transaction_change_log`, `transaction_idempotency_record`, `transaction_id_allocator` | Portfolio transaction store |
| Derived accounting/read models | daily snapshots, holding snapshots, contribution slices, calculation state, instrument universe | Portfolio calculation services; never edited by integrations |
| Planning/research | taxonomy, targets, effective-dated analytics scope/configuration, research settings/runs | Portfolio planning and research APIs |
| Registry identity/market facts | `instrument_registry.*` | Registry API and ingestion jobs |

The `instrument_id` values stored in Portfolio are logical references to reusable market assets in the shared Registry. Portfolio deliberately snapshots `instrument_ref_json` on those transaction facts for audit continuity; downstream code must not replace that snapshot with an invented name or type. FCNs and options instead use Portfolio-local `derivative_contract_id` records; their underlyings and deliverables may reference Registry market assets.

## Portfolio schema

### `portfolio.portfolio_record`

One row per portfolio.

| Columns |
|---|
| **PK** `portfolio_id VARCHAR`; `portfolio_name VARCHAR`; `base_currency VARCHAR`; `valuation_timezone VARCHAR`; `valuation_cutoff_policy VARCHAR`; `as_of_date DATE?`; `nav FLOAT?`; `day_change_value FLOAT?`; `day_change_pct FLOAT?`; `securities_count INTEGER`; `sort_order INTEGER`; `default_planning_taxonomy_id VARCHAR?`; `risk_policy_json JSON?` |

### `portfolio.account_record`

Cash and securities accounts owned by a portfolio.

| Columns |
|---|
| **PK** `account_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `account_name VARCHAR`; `account_type VARCHAR`; `currency VARCHAR`; `institution VARCHAR?`; `default_settlement_cash_account_id VARCHAR?`; `cost_basis_method VARCHAR?`; `allowed_instrument_types_json JSON?`; `opened_at DATE?`; `closed_at DATE?`; `status VARCHAR` |

### `portfolio.transaction_record`

Canonical transaction fact. Long positions, cash movements, FCN lifecycle events, and short-option positions originate here.

| Field group | Columns |
|---|---|
| Identity | **PK** `transaction_id VARCHAR`; unique `transaction_sequence INTEGER`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `transaction_type VARCHAR`; `lifecycle_event_type VARCHAR?`; `row_version INTEGER` |
| Event time | `trade_date DATE`; `trade_time VARCHAR`; `trade_at VARCHAR`; `trade_timezone VARCHAR`; `trade_time_is_estimated BOOLEAN`; `settlement_date DATE`; `position_effective_date DATE?`; `entitlement_date DATE?`; `acquisition_date DATE?`; `created_at VARCHAR?` |
| Accounts/references | `account_id VARCHAR`; `settlement_cash_account_id VARCHAR?`; `counterparty_account_id VARCHAR?`; `instrument_id VARCHAR?`; `instrument_ref_json JSON?`; `derivative_contract_id VARCHAR?`; composite **FK** `(portfolio_id, derivative_contract_id) → derivative_contract_record` |
| Quantities/amounts | `quantity FLOAT?`; `source_quantity NUMERIC(28,12)?`; `price FLOAT?`; `source_price NUMERIC(28,12)?`; `gross_amount FLOAT`; `source_gross_amount NUMERIC(28,8)?`; `counter_amount FLOAT?`; `source_counter_amount NUMERIC(28,8)?`; `fx_rate FLOAT?`; `source_fx_rate NUMERIC(28,12)?`; `fees FLOAT`; `source_fees NUMERIC(28,8)?`; `fee_category VARCHAR`; `taxes FLOAT`; `source_taxes NUMERIC(28,8)?`; `currency VARCHAR` |
| Linking/source | `transfer_scope VARCHAR?`; `transfer_object_type VARCHAR?`; `transfer_group_id VARCHAR?`; `source_system VARCHAR(100)?`; `external_reference VARCHAR(200)?`; `note VARCHAR?` |

Important constraints:

- Unique `(portfolio_id, source_system, external_reference)` when a complete source identity is present.
- `external_reference` requires `source_system`.
- A transaction may reference a Registry instrument or a Portfolio-local derivative contract, never both. Cash-only facts may reference neither.
- Derivative facts do not carry relation or event-group fields. Each contract event is recorded independently; `transfer_group_id` is reserved for paired internal transfers.
- Option lifecycle facts are limited to long/writer expiry or cash settlement. Expiry has zero cash; cash settlement has a positive gross amount whose direction is derived from long versus writer. Physical delivery is normalized into an option cash-settlement fact plus an independent ordinary-security trade.
- The API preserves exact source decimals alongside float calculation projections.
- Trade date, position-effective date, entitlement date, and settlement date are independent accounting facts.
- `transaction_sequence` is a database-coordinated, immutable replay tie-breaker. It is not a business-facing source identifier and integrations must not allocate it.

### `portfolio.derivative_contract_record`

Immutable Portfolio-local FCN and option terms. A contract is created atomically with its first transaction and is reused by later event rows inside the same Portfolio; it is not a Registry instrument.

| Columns |
|---|
| **PK** `(portfolio_id, derivative_contract_id)`; composite **FK** `(portfolio_id, account_id) → account_record`; `contract_name VARCHAR`; `contract_type VARCHAR` (`fcn` or `option`); `currency VARCHAR`; unique `(portfolio_id, external_reference)`; `terms_json JSON`; `created_at VARCHAR` |

Option terms contain one Registry `underlying_instrument_id`, Call/Put type, expiry, strike, and multiplier. Settlement mode is deliberately not a contract term; the operator records expiry or cash settlement when the outcome is known. FCN terms contain notional, issue/maturity dates, issuer, counterparty, Registry underlying/deliverable IDs, and barrier description. The terms support event accounting; they do not create daily derivative pricing, covariance, or research-series eligibility.

### `portfolio.transaction_change_log`

Append-only create/update/delete audit history.

| Columns |
|---|
| **PK** `change_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `transaction_id VARCHAR`; `change_type VARCHAR`; `row_version INTEGER`; `before_json JSON?`; `after_json JSON?`; `request_idempotency_key VARCHAR?`; `changed_at VARCHAR` |

### `portfolio.transaction_idempotency_record`

Request replay protection.

| Columns |
|---|
| **PK** `(portfolio_id, idempotency_key)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `operation VARCHAR`; `request_hash VARCHAR`; `transaction_ids_json JSON`; `created_at VARCHAR` |

### `portfolio.transaction_id_allocator`

Database-coordinated externally visible transaction-id sequence.

| Columns |
|---|
| **PK** `allocator_key VARCHAR`; `next_value INTEGER` |

### `portfolio.portfolio_daily_snapshot`

Derived portfolio-level NAV, return, and reliability snapshot.

| Columns |
|---|
| **PK** `(portfolio_id, as_of_date)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `coverage_state VARCHAR`; `valuation_coverage_state VARCHAR`; `return_coverage_state VARCHAR`; `book_pnl_coverage_state VARCHAR`; `attribution_coverage_state VARCHAR`; `nav FLOAT?`; `beginning_nav FLOAT?`; `ending_nav FLOAT?`; `daily_twr FLOAT?`; `cumulative_twr FLOAT?`; `drawdown FLOAT?`; `snapshot_json JSON`; `calculated_at VARCHAR` |

`snapshot_json` also carries the separate market-risk chain: `risk_scope_excluded_pnl`, `market_risk_pnl`, `market_risk_daily_return`, `market_risk_cumulative_return`, `market_risk_drawdown`, observation eligibility/coverage, and basis label. These fields treat derivatives and base-currency cash as zero-return capital while preserving all cash facts in NAV and operational TWR.

### `portfolio.portfolio_daily_holding_snapshot`

Derived per-account/per-instrument holding lines.

| Columns |
|---|
| **PK** `(portfolio_id, as_of_date, account_id, position_reference_id, holding_kind)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `instrument_id VARCHAR?`; `derivative_contract_id VARCHAR?`; `holding_kind VARCHAR`; `currency VARCHAR`; `quantity FLOAT`; `cost_basis FLOAT?`; `cost_basis_base FLOAT?`; `last_price FLOAT?`; `market_value FLOAT?`; `market_value_base FLOAT?`; `portfolio_weight FLOAT?`; `holding_json JSON`; `calculated_at VARCHAR` |

Exactly one of `instrument_id` and `derivative_contract_id` is present. `position_reference_id` is the corresponding stable identity used by the composite primary key. `holding_kind` keeps a long `position`, a short `option_obligation`, settled cash, and pending monetary lines distinct. For FCN/option long positions, `holding_json.quote_basis=carried_cost` identifies event-valued cost carrying; it is not a Registry quote. Short-option rows use `premium_liability`, a null `last_price`, and a negative NAV amount.

### `portfolio.portfolio_daily_contribution_slice`

Derived attribution slice by instrument/account/taxonomy axis.

| Columns |
|---|
| **PK** `(portfolio_id, as_of_date, axis, group_key)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `group_label VARCHAR`; `coverage_state VARCHAR`; `total_pnl FLOAT?`; `daily_contribution FLOAT?`; `slice_json JSON`; `calculated_at VARCHAR` |

`slice_json` 同时保存该分组独立的市场风险链：`market_risk_excluded_pnl`、`market_risk_total_pnl`、`market_risk_daily_return`、`market_risk_daily_contribution`、`market_risk_observation_count` 及 coverage/eligibility。衍生品与本币现金的经营现金结果从风险分子剔除；非本币现金的 FX 结果保留。Realized volatility、correlation、beta 与 risk contribution 只读取这些字段，不按 group 名称或 holding category 做补充过滤。

### `portfolio.portfolio_calculation_state`

Snapshot invalidation and refresh coordination.

| Columns |
|---|
| **PK/FK** `portfolio_id → portfolio_record.portfolio_id`; `daily_snapshot_status VARCHAR`; `dirty_from DATE?`; `refreshed_from DATE?`; `refreshed_to DATE?`; `refreshed_at VARCHAR?`; `refresh_request_id VARCHAR?`; `refresh_started_at VARCHAR?`; `refresh_completed_at VARCHAR?`; `source_market_data_updated_at VARCHAR?`; `error_message VARCHAR?` |

### `portfolio.portfolio_instrument_universe_record`

Derived held/observed/former-instrument universe and research approval state.

| Columns |
|---|
| **PK** `(portfolio_id, instrument_id)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `instrument_ref_json JSON?`; `source VARCHAR`; `holding_state VARCHAR`; `first_transaction_date DATE?`; `last_transaction_date DATE?`; `transaction_count INTEGER`; `research_pm_approved BOOLEAN`; `research_pm_approved_at VARCHAR?`; `status VARCHAR`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `portfolio.portfolio_instrument_event_task`

Portfolio/account review projection of a Registry distribution/corporate action.

| Columns |
|---|
| **PK** `instrument_event_task_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; **FK** `account_id → account_record.account_id`; `instrument_id VARCHAR`; `event_source VARCHAR`; `event_action_id VARCHAR`; `current_event_revision_id VARCHAR`; `event_type VARCHAR`; `source_revision_kind VARCHAR`; `source_event_state VARCHAR`; `announcement_date DATE?`; `record_date DATE?`; `effective_date DATE`; `payable_date DATE?`; `cash_per_unit NUMERIC(28,12)?`; `unit_ratio NUMERIC(28,12)?`; `reinvestment_nav NUMERIC(28,12)?`; `entitled_quantity NUMERIC(28,12)`; `resolution_status VARCHAR`; `reviewed_event_revision_id VARCHAR?`; `resolution_note VARCHAR?`; `resolved_by VARCHAR?`; `resolved_at VARCHAR?`; `row_version INTEGER`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `portfolio.portfolio_instrument_event_task_link`

Links a review task to the transaction facts that resolved it.

| Columns |
|---|
| **PK** `instrument_event_task_link_id VARCHAR`; **FK** `instrument_event_task_id → portfolio_instrument_event_task.instrument_event_task_id`; **FK** `transaction_id → transaction_record.transaction_id`; `link_role VARCHAR`; `linked_event_revision_id VARCHAR`; `linked_by VARCHAR`; `linked_at VARCHAR` |

### `portfolio.portfolio_instrument_event_task_review`

Append-only task review decision.

| Columns |
|---|
| **PK** `instrument_event_task_review_id VARCHAR`; **FK** `instrument_event_task_id → portfolio_instrument_event_task.instrument_event_task_id`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `event_revision_id VARCHAR`; `decision VARCHAR`; `linked_transaction_ids_json JSON`; `note VARCHAR`; `reviewed_by VARCHAR`; `reviewed_at VARCHAR` |

### `portfolio.portfolio_table_view_store`

Per-user-interface table preferences stored at portfolio/scope level.

| Columns |
|---|
| **PK** `(portfolio_id, view_scope)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `store_json JSON`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `portfolio.taxonomy_record`

Portfolio classification/planning taxonomy.

| Columns |
|---|
| **PK** `taxonomy_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `name VARCHAR`; `taxonomy_type VARCHAR`; `purpose VARCHAR?`; `primary_assignment_scope VARCHAR`; `planning_enabled BOOLEAN`; `budgeting_level VARCHAR?`; `root_default_target_dimension VARCHAR`; `status VARCHAR`; `source_template_ref VARCHAR?` |

### `portfolio.taxonomy_node_record`

| Columns |
|---|
| **PK** `taxonomy_node_id VARCHAR`; **FK** `taxonomy_id → taxonomy_record.taxonomy_id`; `parent_taxonomy_node_id VARCHAR?`; `node_name VARCHAR`; `node_code VARCHAR?`; `sort_order INTEGER`; `is_terminal BOOLEAN`; `default_target_dimension VARCHAR`; `status VARCHAR` |

### `portfolio.taxonomy_assignment_record`

| Columns |
|---|
| **PK** `assignment_id VARCHAR`; **FK** `taxonomy_id → taxonomy_record.taxonomy_id`; `target_scope VARCHAR`; `target_entity_id VARCHAR`; `taxonomy_node_id VARCHAR`; `status VARCHAR` |

### `portfolio.portfolio_analytics_policy_state`

Monotonic configuration version for one portfolio. Scope-policy, selection, and taxonomy-configuration changes share this version sequence so materialized analytics can retain one auditable input identity.

| Columns |
|---|
| **PK/FK** `portfolio_id → portfolio_record.portfolio_id`; `current_version INTEGER`; `updated_at VARCHAR` |

### `portfolio.analytics_scope_policy_record`

Effective-dated eligibility and valuation policy for an exact taxonomy node or the reserved `__root__` / `__unassigned__` policy nodes. Active ranges for the same policy key may not overlap.

| Columns |
|---|
| **PK** `analytics_scope_policy_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR`; `taxonomy_node_id VARCHAR`; `risk_eligible BOOLEAN`; `risk_budget_eligible BOOLEAN`; `performance_scope VARCHAR`; `valuation_basis VARCHAR`; `exclusion_reason VARCHAR?`; `effective_from DATE`; `effective_to DATE?`; `policy_version INTEGER`; `superseded_by_policy_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.analytics_taxonomy_selection_record`

Effective-dated explicit selection of the taxonomy used by analytics. A null `taxonomy_id` is an audited explicit unassignment; absence of an effective row also fails closed.

| Columns |
|---|
| **PK** `analytics_taxonomy_selection_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR?`; `effective_from DATE`; `effective_to DATE?`; `selection_version INTEGER`; `superseded_by_selection_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.taxonomy_configuration_revision`

Point-in-time snapshot of the selected taxonomy, nodes, assignments, target sets, and target lines used by historical Research and Risk resolution. Runtime analytics must not reconstruct historical policy from the current mutable taxonomy tables.

| Columns |
|---|
| **PK** `taxonomy_configuration_revision_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR`; `effective_from DATE`; `effective_to DATE?`; `configuration_version INTEGER`; `configuration_json JSON`; `superseded_by_revision_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.target_set_record`

| Columns |
|---|
| **PK** `target_set_id VARCHAR`; **FK** `taxonomy_id → taxonomy_record.taxonomy_id`; `comparator_taxonomy_node_id VARCHAR?`; `target_set_type VARCHAR`; `name VARCHAR`; `weight_enabled BOOLEAN`; `risk_budget_enabled BOOLEAN`; `status VARCHAR`; `notes VARCHAR?` |

### `portfolio.target_set_line_record`

| Columns |
|---|
| **PK** `target_line_id VARCHAR`; **FK** `target_set_id → target_set_record.target_set_id`; `taxonomy_node_id VARCHAR?`; `target_member_type VARCHAR`; `target_member_id VARCHAR`; `target_weight FLOAT?`; `target_risk_share FLOAT?`; `notes VARCHAR?` |

`target_member_type` is limited to `taxonomy_node`, `instrument`, `cash_bucket`, and `derivative_bucket`. Cash and derivative buckets may carry a weight target but never a risk target.

### `portfolio.research_settings_record`

| Columns |
|---|
| **PK/FK** `portfolio_id → portfolio_record.portfolio_id`; `planning_taxonomy_id VARCHAR?`; `comparator_taxonomy_node_id VARCHAR?`; `as_of_mode VARCHAR`; `as_of_date DATE?`; `lookback_days INTEGER`; `calculation_frequency VARCHAR`; `missing_return_policy VARCHAR`; `target_dimension VARCHAR`; `capital_mode VARCHAR`; `gross_exposure FLOAT?`; `target_volatility FLOAT?`; `max_gross_exposure FLOAT?`; `frozen_taxonomy_node_ids_json JSON?`; `top_sleeve_weight_bounds_json JSON?`; `backtest_rebalance_frequency VARCHAR`; `backtest_benchmark_instrument_id VARCHAR?`; `backtest_cash_yield_annual FLOAT`; `backtest_commission_bps FLOAT`; `backtest_tax_bps FLOAT`; `backtest_slippage_bps FLOAT`; `backtest_implementation_delay_days INTEGER`; `backtest_robustness_scenarios_json JSON?`; `backtest_walk_forward_training_months INTEGER`; `backtest_walk_forward_test_months INTEGER`; `notes VARCHAR?`; `updated_at VARCHAR?` |

### `portfolio.research_run_record`

| Columns |
|---|
| **PK** `research_run_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `job_type VARCHAR`; `status VARCHAR`; `requested_at VARCHAR?`; `started_at VARCHAR?`; `finished_at VARCHAR?`; `as_of_date DATE?`; `planning_taxonomy_id VARCHAR?`; `lookback_days INTEGER`; `requested_by VARCHAR?`; `headline VARCHAR?`; `detail_json JSON?`; `artifacts_json JSON?`; `request_payload_json JSON?`; `error_message VARCHAR?` |

## Instrument Registry tables read by Portfolio

### `instrument_registry.instrument`

Canonical reusable market-asset identity. `instrument_type` supports `fund`, `etf`, `index`, `equity`, `cash`, `fx`, and `other`. Direct bonds, FCNs, and options are deliberately outside Registry; direct bonds also have no current Portfolio transaction model.

| Columns |
|---|
| **PK** `instrument_id VARCHAR`; `instrument_name VARCHAR`; `instrument_type VARCHAR`; `currency VARCHAR`; `quote_selection_policy_json JSON`; `source_settings_json JSON`; `refresh_status_json JSON`; `lifecycle_state_json JSON`; `market_data_updated_at VARCHAR?`; `calculation_inputs_updated_at VARCHAR?` |

Registry owns identity, quote selection, market data, and corporate actions for assets reusable across portfolios. Portfolio owns the contract-specific FCN/option terms and event history. Boundary migrations do not guess or silently backfill removed bond or derivative records; they require those rows to be resolved before migration.

### `instrument_registry.instrument_identifier`

| Columns |
|---|
| **PK** `instrument_identifier_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `identifier_type VARCHAR`; `identifier_value VARCHAR`; `is_primary BOOLEAN` |

### `instrument_registry.instrument_broker_identifier`

Broker-facing reconciliation identity. Each represented broker has exactly one primary identifier at the API/model boundary; `(broker, identifier_type, identifier_value)` is globally unique in the Registry.

| Columns |
|---|
| **PK** `instrument_broker_identifier_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `broker VARCHAR`; `identifier_type VARCHAR`; `identifier_value VARCHAR`; `is_primary BOOLEAN` |

### `instrument_registry.instrument_market_data`

Canonical point observations used for valuation/return roles of Registry market assets. Portfolio-local FCN/options have no rows here under the event-accounting policy.

| Columns |
|---|
| **PK** `instrument_market_data_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `metric_family VARCHAR`; `quote_basis VARCHAR`; `as_of_date DATE`; `value TEXT`; `currency VARCHAR`; `price_unit VARCHAR`; `price_scale NUMERIC(28,12)`; `provider VARCHAR?`; `status VARCHAR`; `nav_lineage_kind VARCHAR?`; `nav_derivation_method_version VARCHAR?`; `nav_derivation_anchor_date DATE?`; `nav_lineage_evidence_json JSON?`; **FK** `fund_nav_adjustment_factor_id → fund_nav_adjustment_factor.fund_nav_adjustment_factor_id` |

### `instrument_registry.fund_nav_event`

Immutable fund distribution/split revision facts consumed by Portfolio's instrument-event review flow.

| Columns |
|---|
| **PK** `fund_nav_event_id VARCHAR`; `fund_nav_action_id VARCHAR`; `revision_number INTEGER`; `revision_kind VARCHAR`; self-**FK** `supersedes_fund_nav_event_id`; **FK** `instrument_id → instrument.instrument_id`; `event_type VARCHAR`; `announcement_date DATE?`; `record_date DATE?`; `effective_date DATE`; `payable_date DATE?`; `sequence_order INTEGER?`; `cash_per_unit NUMERIC?`; `unit_ratio NUMERIC?`; `evidence_kind VARCHAR`; `source VARCHAR`; `external_event_id VARCHAR?`; `provenance_json JSON`; `recorded_by VARCHAR`; `revision_reason VARCHAR`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_registry.fund_nav_reinvestment_evidence`

| Columns |
|---|
| **PK** `fund_nav_reinvestment_evidence_id VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_event_id → fund_nav_event.fund_nav_event_id`; `revision_number INTEGER`; `revision_kind VARCHAR`; self-**FK** `supersedes_fund_nav_reinvestment_evidence_id`; `reinvestment_nav NUMERIC`; `evidence_kind VARCHAR`; `source VARCHAR`; `external_evidence_id VARCHAR?`; `provenance_json JSON`; `recorded_by VARCHAR`; `revision_reason VARCHAR`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_registry.fund_nav_projection_run`

| Columns |
|---|
| **PK** `fund_nav_projection_run_id VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; `input_fingerprint VARCHAR(64)`; `source_observation_fingerprint VARCHAR(64)`; `projection_kind VARCHAR`; `projection_status VARCHAR`; `method_version VARCHAR`; `anchor_date DATE?`; `source_provider VARCHAR`; `evidence_json JSON`; `created_by VARCHAR`; `created_at VARCHAR` |

### `instrument_registry.fund_nav_projection_run_event`

| Columns |
|---|
| composite **PK/FK** `fund_nav_projection_run_id → fund_nav_projection_run`; composite **PK/FK** `fund_nav_event_id → fund_nav_event` |

### `instrument_registry.fund_nav_projection_run_reinvestment_evidence`

| Columns |
|---|
| composite **PK/FK** `fund_nav_projection_run_id → fund_nav_projection_run`; composite **PK/FK** `fund_nav_reinvestment_evidence_id → fund_nav_reinvestment_evidence` |

### `instrument_registry.fund_nav_current_projection`

| Columns |
|---|
| **PK/FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_projection_run_id → fund_nav_projection_run.fund_nav_projection_run_id`; `updated_at VARCHAR`; `updated_by VARCHAR` |

### `instrument_registry.fund_nav_adjustment_factor`

| Columns |
|---|
| **PK** `fund_nav_adjustment_factor_id VARCHAR`; `factor_logical_key VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_projection_run_id → fund_nav_projection_run.fund_nav_projection_run_id`; `as_of_date DATE`; `factor_level NUMERIC`; `factor_kind VARCHAR`; **FK** `fund_nav_event_id → fund_nav_event.fund_nav_event_id`?; **FK** `fund_nav_reinvestment_evidence_id → fund_nav_reinvestment_evidence.fund_nav_reinvestment_evidence_id`?; self-**FK** `previous_fund_nav_adjustment_factor_id`?; `evidence_kind VARCHAR`; `method_version VARCHAR`; `anchor_date DATE`; `source_provider VARCHAR`; `evidence_json JSON`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_registry.corporate_action_event`

| Columns |
|---|
| **PK** `corporate_action_event_id VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; `action_type VARCHAR`; `announcement_date DATE?`; `record_date DATE?`; `effective_date DATE`; `payable_date DATE?`; `new_units TEXT`; `old_units TEXT`; `quantity_rounding VARCHAR`; `quantity_precision INTEGER`; `cost_basis_treatment VARCHAR`; `source VARCHAR`; `external_event_id VARCHAR?`; `status VARCHAR`; `provenance_json JSON`; `created_at VARCHAR`; `updated_at VARCHAR` |

### Registry metadata and fund-NAV lineage tables

These tables support Registry freshness and strict fund total-return lineage. Portfolio reads their projections through Registry services rather than joining them from external integrations.

| Table | Primary key | Remaining columns |
|---|---|---|
| `registry_metadata` | `registry_key` | `registry_name`, `market_data_updated_at?` |
| `fund_nav_event` | `fund_nav_event_id` | `fund_nav_action_id`, `revision_number`, `revision_kind`, `supersedes_fund_nav_event_id?`, `instrument_id`, `event_type`, `announcement_date?`, `record_date?`, `effective_date`, `payable_date?`, `sequence_order?`, `cash_per_unit?`, `unit_ratio?`, `evidence_kind`, `source`, `external_event_id?`, `provenance_json`, `recorded_by`, `revision_reason?`, `created_at`, `updated_at` |
| `fund_nav_reinvestment_evidence` | `fund_nav_reinvestment_evidence_id` | `instrument_id`, `fund_nav_event_id`, `revision_number`, `revision_kind`, `supersedes_fund_nav_reinvestment_evidence_id?`, `reinvestment_nav`, `evidence_kind`, `source`, `external_evidence_id?`, `provenance_json`, `recorded_by`, `revision_reason?`, `created_at`, `updated_at` |
| `fund_nav_projection_run` | `fund_nav_projection_run_id` | `instrument_id`, `input_fingerprint`, `source_observation_fingerprint`, `projection_kind`, `projection_status`, `method_version`, `anchor_date`, `source_provider`, `evidence_json`, `created_by`, `created_at` |
| `fund_nav_projection_run_event` | `(fund_nav_projection_run_id, fund_nav_event_id)` | no additional columns |
| `fund_nav_projection_run_reinvestment_evidence` | `(fund_nav_projection_run_id, fund_nav_reinvestment_evidence_id)` | no additional columns |
| `fund_nav_current_projection` | `instrument_id` | `fund_nav_projection_run_id`, `updated_at`, `updated_by` |
| `fund_nav_adjustment_factor` | `fund_nav_adjustment_factor_id` | `factor_logical_key`, `instrument_id`, `fund_nav_projection_run_id`, `as_of_date`, `factor_level`, `factor_kind`, `fund_nav_event_id?`, `fund_nav_reinvestment_evidence_id?`, `previous_fund_nav_adjustment_factor_id?`, `evidence_kind`, `method_version`, `anchor_date`, `source_provider`, `evidence_json`, `created_at`, `updated_at` |

## Safe integration views

For a colleague implementing transaction ingestion, the relevant read sequence is:

1. List accounts through `GET /api/portfolios/{portfolio_id}/accounts`.
2. List canonical instruments through `GET /api/portfolios/{portfolio_id}/instruments`.
3. Preview and import the transaction batch through the endpoints in `TRANSACTION_INTEGRATION.md`.
4. Read created facts through `GET /api/portfolios/{portfolio_id}/transactions`.

Do not treat daily snapshots, holdings, lots, postings, or instrument-universe rows as input tables. They are deterministic projections of facts plus Registry data and may be rebuilt.

Before a release is described as Risk-ready, run the read-only audit with
`--fail-on-warning`. An effective analytics taxonomy selection and its
point-in-time configuration are business-owned facts; the application does not
invent a default when they are absent. A portfolio with no
`default_planning_taxonomy_id` is explicitly outside Risk / Research readiness
and therefore is not reported as a missing-selection warning; runtime analytics
still fail closed for that portfolio.
