# Portfolio database dictionary

This living dictionary explains table purpose, ownership and relationships. SQLAlchemy models and Alembic migrations remain the executable schema source; do not copy revision numbers into this document. Applying and checking current heads requires the explicit disposable-database workflow in [DATABASE_WORKFLOW.md](./DATABASE_WORKFLOW.md). A schema change must update this dictionary in the same change when table meaning or external integration guidance changes.

External systems must use the APIs documented in [Transaction Import API Guide](./TRANSACTION_IMPORT_API_GUIDE.md), not write these tables directly.

Notation: **PK** = primary key, **FK** = foreign key, `?` = nullable, JSON fields are named with `_json` or `_json`-equivalent suffixes. PostgreSQL logical schemas are shown even though local SQLite tests use flat table names.

## Ownership map

| Layer | Tables | Write owner |
|---|---|---|
| Canonical portfolio facts | `portfolio_record`, `account_record`, `derivative_contract_record`, `transaction_record`, `option_delivery_link` | Portfolio command API |
| Audit/idempotency controls | `transaction_change_log`, `transaction_idempotency_record`, `transaction_id_allocator` | Portfolio transaction store |
| Screenshot evidence and agent drafts | `transaction_capture_record`, `transaction_capture_batch`, `transaction_capture_batch_item`, `transaction_capture_analysis_revision` | Portfolio transaction-capture API and analysis worker |
| Derived accounting/read models | daily snapshots, holding snapshots, contribution slices, calculation state, instrument universe | Portfolio calculation services; never edited by integrations |
| Planning/research | current taxonomy, targets, analytics scope/configuration, research settings/runs | Portfolio planning and research APIs |
| Shared asset identity/market facts | `instrument_data.*` | Data maintenance CLI and ingestion jobs |

The `instrument_id` values stored in Portfolio are logical references to reusable market assets in shared Instrument Data. Portfolio deliberately snapshots `instrument_ref_json` on those transaction facts for audit continuity; downstream code must not replace that snapshot with an invented name or type. FCNs and options instead use Portfolio-local `derivative_contract_id` records; their underlyings and deliverables may reference Instrument Data market assets.

## Portfolio schema

### `portfolio.portfolio_record`

One row per portfolio.

| Columns |
|---|
| **PK** `portfolio_id VARCHAR`; `portfolio_name VARCHAR`; `base_currency VARCHAR`; `inception_date DATE`; `valuation_timezone VARCHAR`; `valuation_cutoff_policy VARCHAR`; `as_of_date DATE?`; `nav FLOAT?`; `day_change_value FLOAT?`; `day_change_pct FLOAT?`; `securities_count INTEGER`; `sort_order INTEGER`; `default_planning_taxonomy_id VARCHAR?`; `risk_policy_json JSON?` |

### `portfolio.account_record`

Cash and holding accounts owned by a portfolio. `account_category` is the canonical business classification: `cash`, `security`, `fcn`, or `option`.

| Columns |
|---|
| **PK** `account_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `account_name VARCHAR`; `account_type VARCHAR`; `account_category VARCHAR`; `currency VARCHAR`; `institution VARCHAR?`; `default_settlement_cash_account_id VARCHAR?`; `cost_basis_method VARCHAR?`; `cash_purpose VARCHAR?`; `collateral_reference VARCHAR?`; `opened_at DATE?`; `closed_at DATE?`; `status VARCHAR` |

`cash` requires the internal `deposit_account` type and carries neither a settlement mapping nor a cost method. `security`, `fcn`, and `option` require the internal `securities_account` type, one cost method, and a default Cash account in the same Portfolio and currency. A holding account cannot mix the three holding categories. Security transactions, FCN contracts/events, and option contracts/events must each use their matching category; the removed mixed-account and instrument-scope models are not runtime compatibility paths.

`cash_purpose` is operating, margin, collateral or financing (cash accounts only). Financing balances are liabilities, collateral is not freely available trading cash, and collateral_reference stores the confirmation basis. The default cost method is frozen after holding history exists.

### `portfolio.transaction_record`

Canonical transaction fact. Long positions, stock/ETF shorts, cash movements, FCN lifecycle events, and written-option obligations originate here. The API derives every row's `asset_domain` as `security`, `derivative`, or `cash` from its canonical reference; this is a read model, not a second persisted classification.

| Field group | Columns |
|---|---|
| Identity | **PK** `transaction_id VARCHAR`; unique `transaction_sequence INTEGER`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `transaction_type VARCHAR`; `lifecycle_event_type VARCHAR?`; `row_version INTEGER` |
| Event time | `trade_date DATE`; `trade_time VARCHAR`; `trade_at VARCHAR`; `trade_timezone VARCHAR`; `trade_time_is_estimated BOOLEAN`; `settlement_date DATE`; `position_effective_date DATE?`; `entitlement_date DATE?`; `acquisition_date DATE?`; `created_at VARCHAR?` |
| Accounts/references | `account_id VARCHAR`; `settlement_cash_account_id VARCHAR?`; `counterparty_account_id VARCHAR?`; `instrument_id VARCHAR?`; `instrument_ref_json JSON?`; `derivative_contract_id VARCHAR?`; composite **FK** `(portfolio_id, derivative_contract_id) → derivative_contract_record` |
| Quantities/amounts | `quantity FLOAT?`; `source_quantity NUMERIC(28,12)?`; `price FLOAT?`; `source_price NUMERIC(28,12)?`; `gross_amount FLOAT`; `source_gross_amount NUMERIC(28,8)?`; `counter_amount FLOAT?`; `source_counter_amount NUMERIC(28,8)?`; `fx_rate FLOAT?`; `source_fx_rate NUMERIC(28,12)?`; `fees FLOAT`; `source_fees NUMERIC(28,8)?`; `fee_category VARCHAR`; `taxes FLOAT`; `source_taxes NUMERIC(28,8)?`; `currency VARCHAR` |
| Linking/source | `transfer_scope VARCHAR?`; `transfer_object_type VARCHAR?`; `transfer_group_id VARCHAR?`; `source_system VARCHAR(100)?`; `external_reference VARCHAR(200)?`; `note VARCHAR?`; `asset_deliveries_json JSON?`; `lot_selections_json JSON?` |

Important constraints:

- Unique `(portfolio_id, source_system, external_reference)` when a complete source identity is present.
- `external_reference` requires `source_system`.
- No transaction may predate its Portfolio `inception_date`; opening balances must use that date for both trade and settlement.
- A paired internal transfer stores command-level source identity on `transfer_out` only, so the unique source fact survives collapsed export without duplicating the key on `transfer_in`.
- A transaction may reference a Registry instrument or a Portfolio-local derivative contract, never both. Cash-only facts may reference neither.
- Derivative facts do not carry generic relation or event-group fields; `transfer_group_id` is reserved for paired internal transfers. Confirmed option physical delivery uses the dedicated `option_delivery_link` relation described below. FCN final physical redemptions store asset_deliveries_json on the source fact; receiving security lots carry the same source reference and confirmed local fair value, without fabricated cash purchases.
- Option lifecycle facts include long/writer expiry, cash settlement, and physical exercise/assignment. Expiry and the option leg of physical delivery have zero cash; cash settlement has a positive gross amount whose direction is derived from long versus writer. Physical delivery is created only by the dedicated atomic command, which also creates the ordinary-security trade at strike.
- The API preserves exact source decimals alongside float calculation projections.
- Trade date, position-effective date, entitlement date, and settlement date are independent accounting facts.
- `transaction_sequence` is a database-coordinated, immutable replay tie-breaker. It is not a business-facing source identifier and integrations must not allocate it.

### `portfolio.derivative_contract_record`

Versioned Portfolio-local FCN and option terms. A contract is created atomically with its first transaction and is reused by later event rows inside the same Portfolio; it is not a Registry instrument.

| Columns |
|---|
| **PK** `(portfolio_id, derivative_contract_id)`; composite **FK** `(portfolio_id, account_id) → account_record`; `contract_name VARCHAR`; `contract_type VARCHAR` (`fcn` or `option`); `currency VARCHAR`; unique `(portfolio_id, external_reference)`; `terms_json JSON`; `row_version INTEGER`; `amendments_json JSON?`; `created_at VARCHAR` |

Option terms contain one Registry `underlying_instrument_id`, Call/Put type, expiry, strike, and multiplier. Confirmed settlement_type, exercise_style, strike_currency, exercise dates and terms_reference are contract terms; absent terms stay unconfirmed. Recorded outcomes must comply with known terms. Audited amendments preserve before/after terms, reason, reviewer and version, and cannot replace contract identity or invalidate existing outcomes. FCN master terms contain notional, optional annual coupon rate, issue/final-observation/maturity dates, issuer, and counterparty. Each FCN underlying is a separate term object with Registry `instrument_id`, optional initial reference price, strike/knock-in/knock-out levels expressed in percentage points, and a deliverable flag. The terms support event accounting; they do not create daily derivative pricing, covariance, or research-series eligibility.

### `portfolio.option_delivery_link`

Canonical one-to-one relationship for a confirmed option physical-delivery outcome. It is created in the same database transaction as both linked transaction facts; integrations must use the option-outcome command rather than write or infer this relationship.

| Columns |
|---|
| **PK/FK** `option_transaction_id → transaction_record.transaction_id`; unique **FK** `stock_transaction_id → transaction_record.transaction_id`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `underlying_instrument_id VARCHAR`; `created_at VARCHAR` |

The option transaction must be a zero-cash `option_long_exercise` or `option_writer_assignment`; the stock transaction must reference the contract underlying, use `quantity = contract quantity × multiplier`, and trade at strike in the direction implied by Call/Put and long/written side. A link cannot point to the same row twice. Linked transactions cannot be updated separately and are deleted as one scope. Premium currency may differ from the underlying currency. The stock leg uses explicit strike_currency, which must match the underlying quote and both delivery accounts; contractual strike-to-underlying FX conversion is not inferred. Both legs preserve the outcome trade_time. Attached fee_category belongs to the stock leg, while lot_selections selects long option disposal lots.

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

### `portfolio.transaction_capture_record`

Immutable screenshot evidence stored inside its selected Portfolio. Duplicate image content is rejected per Portfolio while the original filename and binary remain available for audit and later re-analysis.

| Columns |
|---|
| **PK** `capture_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `original_filename VARCHAR(255)`; `media_type VARCHAR(50)`; `byte_size INTEGER`; `content_sha256 VARCHAR(64)`; `content BYTEA`; `created_at VARCHAR`; unique `(portfolio_id, content_sha256)` |

### `portfolio.transaction_capture_batch`

One ordered group of screenshots submitted for transaction import, Portfolio initialization, position reconciliation, or automatic classification. Agent execution state is coordination metadata; no transaction is committed from this table.

| Columns |
|---|
| **PK** `batch_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `purpose VARCHAR`; `status VARCHAR`; `content_key VARCHAR(64)`; `capture_count INTEGER`; `latest_analysis_revision INTEGER`; `analysis_run_status VARCHAR`; `analysis_run_attempt INTEGER`; `analysis_run_started_at VARCHAR?`; `analysis_run_completed_at VARCHAR?`; `analysis_run_error VARCHAR(1000)?`; `created_at VARCHAR`; `updated_at VARCHAR`; unique `(portfolio_id, content_key)` |

### `portfolio.transaction_capture_batch_item`

| Columns |
|---|
| **PK/FK** `batch_id → transaction_capture_batch.batch_id`; **PK/FK** `capture_id → transaction_capture_record.capture_id`; `ordinal INTEGER`; unique `(batch_id, ordinal)` |

### `portfolio.transaction_capture_analysis_revision`

Append-only model or human interpretation of a screenshot batch. `analysis_json` retains the structured evidence and uncertainty; the optional preview bundle records validation against the normal transaction-import contract. Committing still requires the separate preview/confirmation path.

| Columns |
|---|
| **PK/FK** `batch_id → transaction_capture_batch.batch_id`; **PK** `revision INTEGER`; `source VARCHAR`; `harness VARCHAR(80)?`; `provider VARCHAR(50)?`; `model VARCHAR(120)?`; `harness_session_id VARCHAR(255)?`; `finish_reason VARCHAR(80)?`; `schema_version VARCHAR(80)`; `analysis_json JSON`; `transaction_import_json JSON?`; `preview_digest VARCHAR(64)?`; `preview_error_count INTEGER?`; `preview_json JSON?`; `created_at VARCHAR` |

### `portfolio.portfolio_daily_snapshot`

Derived portfolio-level NAV, return, and reliability snapshot.

| Columns |
|---|
| **PK** `(portfolio_id, as_of_date)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `coverage_state VARCHAR`; `valuation_coverage_state VARCHAR`; `return_coverage_state VARCHAR`; `book_pnl_coverage_state VARCHAR`; `attribution_coverage_state VARCHAR`; `nav FLOAT?`; `beginning_nav FLOAT?`; `ending_nav FLOAT?`; `daily_twr FLOAT?`; `cumulative_twr FLOAT?`; `drawdown FLOAT?`; `snapshot_json JSON`; `calculation_state_json JSON?`; `calculated_at VARCHAR` |

`snapshot_json` also carries the separate market-risk chain: `risk_scope_excluded_pnl`, `market_risk_pnl`, `market_risk_daily_return`, `market_risk_cumulative_return`, `market_risk_drawdown`, observation eligibility/coverage, and basis label. These fields treat derivatives and base-currency cash as zero-return capital while preserving all cash facts in NAV and operational TWR.

`calculation_state_json` is the rebuildable period-calculation input published by the same daily kernel and source generation. It contains open long/short lots, selected valuation and FX points, boundary information, and that day's lot, corporate-action, option and cash events. Period calculations read boundary states and project only events within the requested range, then apply the existing lot and return rules. They do not reload security/FX histories or reconstruct the ledger on every date change. This column is deferred in ordinary snapshot queries and is not included in public snapshot payloads. Migration `20260906_0060` adds the column; the calculation-version change requires existing snapshots to be rebuilt before these reads become available.

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

### `portfolio.portfolio_workspace_read_model`

Disposable latest-date holdings analysis and risk-frequency projections. The source key includes the implementation version, published accounting generation, valuation date and applicable analysis policy. Background publication checks the key again while holding the portfolio lock; live task and derivative-lifecycle overlays are read separately. Each portfolio/surface retains one latest projection, not an unbounded set of arbitrary date ranges.

| Columns |
|---|
| **PK** `(portfolio_id, surface)`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `source_key VARCHAR`; `payload_json JSON?`; `calculated_at VARCHAR`; `error_type VARCHAR?` |

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

Current eligibility and valuation policy for an exact taxonomy node or the reserved `__root__` / `__unassigned__` policy nodes. Automatic versions and supersession retain the change audit; runtime reads use the current version without a date filter. A partial unique index enforces one unsuperseded row per portfolio, taxonomy and node.

| Columns |
|---|
| **PK** `analytics_scope_policy_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR`; `taxonomy_node_id VARCHAR`; `risk_eligible BOOLEAN`; `risk_budget_eligible BOOLEAN`; `performance_scope VARCHAR`; `valuation_basis VARCHAR`; `exclusion_reason VARCHAR?`; `policy_version INTEGER`; `superseded_by_policy_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.analytics_taxonomy_selection_record`

Current explicit selection of the taxonomy used by analytics. A null `taxonomy_id` is an audited explicit unassignment; absence of a current row also fails closed. A partial unique index enforces one unsuperseded selection per portfolio.

| Columns |
|---|
| **PK** `analytics_taxonomy_selection_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR?`; `selection_version INTEGER`; `superseded_by_selection_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.taxonomy_configuration_revision`

Automatically versioned audit snapshot of a taxonomy, its nodes, assignments, target sets and target lines. Current analytics use current configuration throughout their historical observations. A saved Research run freezes its own complete current configuration in `request_payload_json`; reading that archived run does not re-resolve current taxonomy. A partial unique index enforces one unsuperseded audit revision per portfolio and taxonomy.

| Columns |
|---|
| **PK** `taxonomy_configuration_revision_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `taxonomy_id VARCHAR`; `configuration_version INTEGER`; `configuration_json JSON`; `superseded_by_revision_id VARCHAR?`; `created_at VARCHAR` |

### `portfolio.concentration_policy_revision`

Immutable, effective-dated concentration limits and FCN principal allocation settings owned by a portfolio. Each revision stores the complete policy document: direct security, single FCN and custom taxonomy default/entity limits, plus equal/custom linked-underlying principal allocations. No built-in financial limits are seeded. The editor uses an expected revision; a portfolio row lock serializes concurrent first saves as well as later saves. Historical reads choose the latest effective date not after the holding date, then the highest revision on that date.

| Columns |
|---|
| **PK/FK** `portfolio_id → portfolio_record.portfolio_id` (cascade delete); **PK** `revision INTEGER > 0`; `effective_from DATE`; `settings_json JSON`; `created_by VARCHAR`; `created_at VARCHAR` |

Index: `(portfolio_id, effective_from, revision)`. Taxonomy/node and portfolio-local FCN references in the document are validated on edits; unchanged archived references can remain for history. This does not change NAV, the production analytics taxonomy, or materialized valuation generation. Portfolio copy refuses effective-dated configuration history until explicit identity remapping exists. Downgrade refuses to erase saved policies.

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
| **PK/FK** `portfolio_id → portfolio_record.portfolio_id`; `planning_taxonomy_id VARCHAR?`; `comparator_taxonomy_node_id VARCHAR?`; `as_of_mode VARCHAR`; `as_of_date DATE?`; `lookback_days INTEGER`; `calculation_frequency VARCHAR (= daily)`; `missing_return_policy VARCHAR`; `target_dimension VARCHAR`; `capital_mode VARCHAR`; `gross_exposure FLOAT?`; `target_volatility FLOAT?`; `max_gross_exposure FLOAT?`; `frozen_taxonomy_node_ids_json JSON?`; `top_sleeve_weight_bounds_json JSON?`; `backtest_rebalance_frequency VARCHAR`; `backtest_benchmark_instrument_id VARCHAR?`; `backtest_cash_yield_annual FLOAT`; `backtest_commission_bps FLOAT`; `backtest_tax_bps FLOAT`; `backtest_slippage_bps FLOAT`; `backtest_implementation_delay_days INTEGER`; `backtest_robustness_scenarios_json JSON?`; `backtest_walk_forward_training_months INTEGER`; `backtest_walk_forward_test_months INTEGER`; `notes VARCHAR?`; `updated_at VARCHAR?` |

### `portfolio.research_run_record`

| Columns |
|---|
| **PK** `research_run_id VARCHAR`; **FK** `portfolio_id → portfolio_record.portfolio_id`; `job_type VARCHAR`; `status VARCHAR`; `requested_at VARCHAR?`; `started_at VARCHAR?`; `finished_at VARCHAR?`; `as_of_date DATE?`; `planning_taxonomy_id VARCHAR?`; `lookback_days INTEGER`; `requested_by VARCHAR?`; `headline VARCHAR?`; `detail_json JSON?`; `artifacts_json JSON?`; `request_payload_json JSON?`; `error_message VARCHAR?` |

## Instrument Registry tables read by Portfolio

### `instrument_data.instrument`

Canonical reusable market-asset identity. `instrument_type` supports `public_fund`, `private_fund`, `etf`, `index`, `equity`, `cash`, `fx`, and `other`. Equity and ETF rows require a canonical MIC `exchange_code`; non-listed rows must leave it empty. Current listing MICs cover US, Hong Kong, mainland China, London, Xetra, Paris, Amsterdam, Milan, and SIX. Direct bonds, FCNs, and options are deliberately outside Registry; direct bonds also have no current Portfolio transaction model.

| Columns |
|---|
| **PK** `instrument_id VARCHAR`; `instrument_name VARCHAR`; `instrument_type VARCHAR`; `currency VARCHAR`; `exchange_code VARCHAR(4)?`; `quote_selection_policy_json JSON`; `source_settings_json JSON`; `refresh_status_json JSON`; `lifecycle_state_json JSON`; `market_data_updated_at VARCHAR?`; `calculation_inputs_updated_at VARCHAR?` |

Registry owns identity, quote selection, market data, and corporate actions for assets reusable across portfolios. Portfolio owns the contract-specific FCN/option terms and event history. Boundary migrations do not guess or silently backfill removed bond or derivative records; they require those rows to be resolved before migration.

### `instrument_data.instrument_identifier`

| Columns |
|---|
| **PK** `instrument_identifier_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `identifier_type VARCHAR`; `identifier_value VARCHAR`; `is_primary BOOLEAN` |

### `instrument_data.instrument_broker_identifier`

Broker-facing reconciliation identity. Each represented broker has exactly one primary identifier at the API/model boundary; `(broker, identifier_type, identifier_value)` is globally unique in the Registry.

| Columns |
|---|
| **PK** `instrument_broker_identifier_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `broker VARCHAR`; `identifier_type VARCHAR`; `identifier_value VARCHAR`; `is_primary BOOLEAN` |

### `instrument_data.instrument_market_data`

Canonical point observations used for valuation/return roles of Registry market assets. Portfolio-local FCN/options have no rows here under the event-accounting policy.

| Columns |
|---|
| **PK** `instrument_market_data_id INTEGER`; **FK** `instrument_id → instrument.instrument_id`; `metric_family VARCHAR`; `quote_basis VARCHAR`; `as_of_date DATE`; `value TEXT`; `currency VARCHAR`; `price_unit VARCHAR`; `price_scale NUMERIC(28,12)`; `provider VARCHAR?`; `status VARCHAR`; `nav_lineage_kind VARCHAR?`; `nav_derivation_method_version VARCHAR?`; `nav_derivation_anchor_date DATE?`; `nav_lineage_evidence_json JSON?`; **FK** `fund_nav_adjustment_factor_id → fund_nav_adjustment_factor.fund_nav_adjustment_factor_id` |

### `instrument_data.fund_nav_event`

Immutable fund distribution/split revision facts consumed by Portfolio's instrument-event review flow.

| Columns |
|---|
| **PK** `fund_nav_event_id VARCHAR`; `fund_nav_action_id VARCHAR`; `revision_number INTEGER`; `revision_kind VARCHAR`; self-**FK** `supersedes_fund_nav_event_id`; **FK** `instrument_id → instrument.instrument_id`; `event_type VARCHAR`; `announcement_date DATE?`; `record_date DATE?`; `effective_date DATE`; `payable_date DATE?`; `sequence_order INTEGER?`; `cash_per_unit NUMERIC?`; `unit_ratio NUMERIC?`; `evidence_kind VARCHAR`; `source VARCHAR`; `external_event_id VARCHAR?`; `provenance_json JSON`; `recorded_by VARCHAR`; `revision_reason VARCHAR`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_data.fund_nav_reinvestment_evidence`

| Columns |
|---|
| **PK** `fund_nav_reinvestment_evidence_id VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_event_id → fund_nav_event.fund_nav_event_id`; `revision_number INTEGER`; `revision_kind VARCHAR`; self-**FK** `supersedes_fund_nav_reinvestment_evidence_id`; `reinvestment_nav NUMERIC`; `evidence_kind VARCHAR`; `source VARCHAR`; `external_evidence_id VARCHAR?`; `provenance_json JSON`; `recorded_by VARCHAR`; `revision_reason VARCHAR`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_data.fund_nav_projection_run`

| Columns |
|---|
| **PK** `fund_nav_projection_run_id VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; `input_fingerprint VARCHAR(64)`; `source_observation_fingerprint VARCHAR(64)`; `projection_kind VARCHAR`; `projection_status VARCHAR`; `method_version VARCHAR`; `anchor_date DATE?`; `source_provider VARCHAR`; `evidence_json JSON`; `created_by VARCHAR`; `created_at VARCHAR` |

### `instrument_data.fund_nav_projection_run_event`

| Columns |
|---|
| composite **PK/FK** `fund_nav_projection_run_id → fund_nav_projection_run`; composite **PK/FK** `fund_nav_event_id → fund_nav_event` |

### `instrument_data.fund_nav_projection_run_reinvestment_evidence`

| Columns |
|---|
| composite **PK/FK** `fund_nav_projection_run_id → fund_nav_projection_run`; composite **PK/FK** `fund_nav_reinvestment_evidence_id → fund_nav_reinvestment_evidence` |

### `instrument_data.fund_nav_current_projection`

| Columns |
|---|
| **PK/FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_projection_run_id → fund_nav_projection_run.fund_nav_projection_run_id`; `updated_at VARCHAR`; `updated_by VARCHAR` |

### `instrument_data.fund_nav_adjustment_factor`

| Columns |
|---|
| **PK** `fund_nav_adjustment_factor_id VARCHAR`; `factor_logical_key VARCHAR`; **FK** `instrument_id → instrument.instrument_id`; **FK** `fund_nav_projection_run_id → fund_nav_projection_run.fund_nav_projection_run_id`; `as_of_date DATE`; `factor_level NUMERIC`; `factor_kind VARCHAR`; **FK** `fund_nav_event_id → fund_nav_event.fund_nav_event_id`?; **FK** `fund_nav_reinvestment_evidence_id → fund_nav_reinvestment_evidence.fund_nav_reinvestment_evidence_id`?; self-**FK** `previous_fund_nav_adjustment_factor_id`?; `evidence_kind VARCHAR`; `method_version VARCHAR`; `anchor_date DATE`; `source_provider VARCHAR`; `evidence_json JSON`; `created_at VARCHAR`; `updated_at VARCHAR` |

### `instrument_data.corporate_action_event`

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
3. Preview and atomically commit machine JSON through `transaction-imports/preview` and `transaction-imports/commit` as documented in [Transaction Import API Guide](./TRANSACTION_IMPORT_API_GUIDE.md), or use the canonical CSV/Excel workflow in [Transaction Operations](../apps/portfolio/docs/04_TRANSACTION_OPERATIONS.md).
4. Read created facts through `GET /api/portfolios/{portfolio_id}/transactions`.

Do not treat daily snapshots, holdings, lots, postings, or instrument-universe rows as input tables. They are deterministic projections of facts plus Registry data and may be rebuilt.

Before a release is described as Risk-ready, run the read-only audit with
`--fail-on-warning`. A current analytics taxonomy selection and its
configuration are business-owned facts; the application does not
invent a default when they are absent. A portfolio with no
`default_planning_taxonomy_id` is explicitly outside Risk / Research readiness
and therefore is not reported as a missing-selection warning; runtime analytics
still fail closed for that portfolio.
