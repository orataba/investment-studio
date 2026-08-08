import type {
  InstrumentCore,
  InstrumentIdentifier,
  DataStatus,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
  QuoteSelectionPolicy,
} from '../../../../../packages/instrument-core/ts/src'

export type { InstrumentCore, InstrumentIdentifier } from '../../../../../packages/instrument-core/ts/src'

export type WorkspaceSection = {
  label: string
  href: string
  status: string
}

export type TaxonomyAssignmentScope = 'instrument' | 'account' | 'cash_bucket'

export type PortfolioWorkspaceSummary = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  nav: number
  day_change_value: number | null
  day_change_pct: number | null
  default_planning_taxonomy_id?: string | null
  toolbar_label: string
  badges: string[]
  sections: WorkspaceSection[]
}

export type PortfolioEntryRecord = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  nav: number
  day_change_value: number | null
  day_change_pct: number | null
  securities_count: number
  sort_order: number
  default_planning_taxonomy_id?: string | null
}

export type PortfolioCreatePayload = {
  name?: string | null
}

export type HoldingsSummaryCard = {
  label: string
  value: string
  tone: string
}

export type SparklinePoint = {
  date: string
  value: number
}

export type ReturnSeriesPoint = {
  start_date?: string | null
  date: string
  value: number
}

export type HoldingReturnSeries = {
  first_return_start_date?: string | null
  points: ReturnSeriesPoint[]
}

export type PortfolioInstrumentChartRangeKey = '1m' | '3m' | '6m' | 'ytd' | '1y' | 'all'
export type PortfolioReturnSemantics = 'unknown' | 'price_return' | 'total_return'

export type PortfolioInstrumentPriceChartPoint = {
  date: string
  value: number
}

export type PortfolioInstrumentPriceChartSummary = {
  point_count: number
  change_value: number | null
  change_pct: number | null
  high: number | null
  low: number | null
}

export type PortfolioInstrumentPriceChartResponse = {
  portfolio_id: string
  instrument_core: InstrumentCore
  as_of_date: string
  range_key: PortfolioInstrumentChartRangeKey
  chart_basis: string | null
  return_semantics?: PortfolioReturnSemantics
  metric_family: string | null
  currency: string
  coverage_state?: PortfolioPerformanceCoverageState
  selection_reason?: string | null
  split_adjusted?: boolean
  points: PortfolioInstrumentPriceChartPoint[]
  summary: PortfolioInstrumentPriceChartSummary
}

type PortfolioTransactionExecutionQuoteBase = {
  portfolio_id: string
  instrument_id: string
  requested_as_of_date: string
  currency: string
}

export type PortfolioTransactionExecutionQuoteResponse =
  | (PortfolioTransactionExecutionQuoteBase & {
      selection_role: 'trading' | 'valuation'
      value: number
      quote_date: string
      quote_basis: QuoteBasis
      metric_family: MetricFamily
      provider: string | null
      status: 'complete'
      stale: boolean
      price_unit: PriceUnit
      price_scale: number
      unavailable_reason: null
    })
  | (PortfolioTransactionExecutionQuoteBase & {
      selection_role: null
      value: null
      quote_date: null
      quote_basis: null
      metric_family: null
      provider: null
      status: 'unavailable'
      stale: false
      price_unit: null
      price_scale: null
      unavailable_reason: string
    })

export type PortfolioPerformanceCoverageState = 'complete' | 'partial' | 'unavailable'
export type PortfolioPerformanceBasis = 'market_value' | 'operational_carrying_basis'

export type PortfolioDailyPerformancePoint = {
  as_of_date: string
  coverage_state: PortfolioPerformanceCoverageState
  valuation_coverage_state: PortfolioPerformanceCoverageState
  return_coverage_state: PortfolioPerformanceCoverageState
  book_pnl_coverage_state: PortfolioPerformanceCoverageState
  attribution_coverage_state: PortfolioPerformanceCoverageState
  return_chain_continuous: boolean
  stale_price_flag: boolean
  stale_fx_flag: boolean
  market_observation_count: number
  return_observation_eligible: boolean
  return_observation_exclusion_reason: string | null
  performance_basis: PortfolioPerformanceBasis
  performance_label: string
  beginning_nav: number | null
  ending_nav: number | null
  pending_settlement: number | null
  realized_pnl: number | null
  derivative_lifecycle_realized_pnl: number | null
  unrealized_pnl: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  cash_currency_gains: number | null
  pending_settlement_currency_gains?: number | null
  instrument_currency_gains: number | null
  return_of_capital_amount: number | null
  total_pnl: number | null
  external_cash_in: number
  external_cash_out: number
  net_external_inflow: number
  absolute_change: number | null
  delta: number | null
  daily_twr: number | null
  cumulative_twr: number | null
  drawdown: number | null
}

export type PortfolioPerformanceSummary = {
  start_date: string | null
  end_date: string | null
  requested_start_date?: string | null
  requested_end_date?: string | null
  effective_start_date?: string | null
  effective_end_date?: string | null
  as_of_clamp_reason?: string | null
  start_boundary_kind?: 'close_eod' | 'funded_bod' | 'imported_opening_eod' | null
  include_start_date_return?: boolean
  coverage_state: PortfolioPerformanceCoverageState
  valuation_coverage_state: PortfolioPerformanceCoverageState
  return_coverage_state: PortfolioPerformanceCoverageState
  book_pnl_coverage_state: PortfolioPerformanceCoverageState
  attribution_coverage_state: PortfolioPerformanceCoverageState
  snapshot_count: number
  return_observation_count: number
  risk_return_observation_count: number
  risk_annualization_periods_per_year: number | null
  risk_calculation_frequency: PortfolioCalculationFrequency
  risk_minimum_sample_count: number
  risk_sample_count: number
  risk_result_status: 'available' | 'insufficient_samples' | 'unavailable'
  risk_unavailable_reason: string | null
  performance_basis: PortfolioPerformanceBasis
  performance_label: string
  ordinary_sleeve_twr_status: 'unavailable'
  ordinary_sleeve_twr_reason: string
  latest_complete_as_of_date: string | null
  start_nav: number | null
  end_nav: number | null
  external_cash_in: number
  external_cash_out: number
  net_external_inflow: number
  cumulative_twr: number | null
  annualization_eligible: boolean
  annualization_years: number | null
  annualization_unavailable_reason: string | null
  annualized_twr: number | null
  irr: number | null
  mwror: number | null
  irr_solver_status:
    | 'unique_root'
    | 'invalid_cash_flows'
    | 'no_root'
    | 'multiple_roots_or_non_unique'
    | null
  irr_unavailable_reason: string | null
  absolute_change: number | null
  delta: number | null
  realized_pnl: number | null
  derivative_lifecycle_realized_pnl: number | null
  unrealized_pnl: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  cash_currency_gains: number | null
  pending_settlement_currency_gains?: number | null
  instrument_currency_gains: number | null
  return_of_capital_amount: number | null
  total_pnl: number | null
  mean_daily_return: number | null
  annualized_return_from_daily_mean: number | null
  annualized_volatility: number | null
  annualized_downside_volatility: number | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  current_drawdown: number | null
  max_drawdown: number | null
  max_drawdown_days: number | null
  drawdown_duration_days: number | null
  quality_warnings: string[]
}

export type PortfolioPerformanceResponse = {
  portfolio_id: string
  base_currency: string
  valuation_timezone: string
  valuation_cutoff_policy: string
  summary: PortfolioPerformanceSummary
  daily_series: PortfolioDailyPerformancePoint[]
}

export type PortfolioPeriodCalculationLine = {
  key: string
  label: string
  amount: number | null
  line_kind: 'boundary' | 'performance' | 'external' | 'detail'
  parent_key: string | null
  sort_order: number
}

export type PortfolioPeriodCalculationSummary = {
  start_date: string | null
  end_date: string | null
  requested_start_date?: string | null
  requested_end_date?: string | null
  effective_start_date?: string | null
  effective_end_date?: string | null
  as_of_clamp_reason?: string | null
  start_boundary_kind?: 'close_eod' | 'funded_bod' | 'imported_opening_eod' | null
  include_start_date_return?: boolean
  coverage_state: PortfolioPerformanceCoverageState
  stale_price_flag: boolean
  stale_fx_flag: boolean
  initial_value: number | null
  final_value: number | null
  delta: number | null
  capital_gains: number | null
  realized_capital_gains: number | null
  unrealized_capital_gains: number | null
  earnings: number | null
  fees: number | null
  taxes: number | null
  cash_currency_gains: number | null
  pending_settlement_currency_gains?: number | null
  instrument_currency_gains: number | null
  deposits: number
  withdrawals: number
  net_external_inflow: number
}

export type PortfolioPerformanceCalculationResponse = {
  portfolio_id: string
  base_currency: string
  valuation_timezone: string
  valuation_cutoff_policy: string
  summary: PortfolioPeriodCalculationSummary
  lines: PortfolioPeriodCalculationLine[]
}

export type PortfolioContributionAxis = 'instrument' | 'account' | 'instrument_type' | 'currency' | 'taxonomy'
export type PortfolioCalculationFrequency = 'daily' | 'weekly' | 'monthly'
export type PortfolioRiskCalculationFrequency = 'auto' | PortfolioCalculationFrequency
export type PortfolioRiskCovarianceModel =
  | 'ewma_vol_shrinkage_corr_covariance'
  | 'ewma_covariance'
  | 'sample_covariance'
export type PortfolioRiskContributionMode = 'signed' | 'abs'

export type PortfolioRiskPolicyRecord = {
  model_name: string
  model_role: 'production' | string
  covariance_model_id: PortfolioRiskCovarianceModel
  lookback_days: number
  calculation_frequency: PortfolioRiskCalculationFrequency
  resolved_calculation_frequency: PortfolioCalculationFrequency
  missing_return_policy: PortfolioResearchMissingReturnPolicy
  contribution_mode: PortfolioRiskContributionMode
  parameters: Record<string, unknown>
  parameters_by_frequency: Record<string, Record<string, unknown>>
}

export type PortfolioForwardRiskSummary = {
  status: 'ok' | 'unavailable' | string
  errors: string[]
  scope_name: string
  scope_policy_versions: number[]
  configuration_versions: number[]
  total_nav: number | null
  modeled_net_exposure: number | null
  modeled_gross_exposure: number | null
  excluded_carrying_value: number | null
  excluded_liability: number | null
  cash_unallocated_exposure: number | null
  coverage_ratio: number | null
  excluded_rows: PortfolioAnalyticsScopeExcludedRow[]
  calculation_frequency: PortfolioCalculationFrequency
  modeled_weight_basis?: 'eligible_gross_exposure' | string
  risk_model?: PortfolioRiskPolicyRecord | null
  portfolio_variance?: number | null
  portfolio_volatility?: number | null
  observation_count?: number | null
  coverage?: {
    policy: PortfolioResearchMissingReturnPolicy
    window_start_date: string
    window_end_date: string
    return_interval?: string | null
    rows_before: number
    rows_after: number
    complete_row_count: number
    missing_row_count: number
    missing_row_fraction: number
    missing_rows: Array<{
      date: string
      missing_members: string[]
    }>
    latest_complete_date?: string | null
    trailing_staleness_days?: number | null
  } | null
}

export type PortfolioHoldingRegion =
  | 'market_valued_positions'
  | 'structured_and_long_derivatives'
  | 'written_option_obligations'
  | 'cash_and_settlement'

export type PortfolioPerformanceScope =
  | 'ordinary'
  | 'derivative_lifecycle'
  | 'operational_only'
  | 'unallocated'

export type PortfolioAnalyticsScopeExcludedRow = {
  line_id: string | null
  instrument_id: string | null
  instrument_name: string | null
  holding_region: PortfolioHoldingRegion
  exposure_base: number
  exclusion_reason: string | null
  scope_status: string
}

export type PortfolioAnalyticsScopeSummary = {
  scope_name: string
  scope_policy_versions: number[]
  configuration_versions: number[]
  taxonomy_selection_versions: number[]
  total_nav: number | null
  modeled_net_exposure: number
  modeled_gross_exposure: number
  excluded_carrying_value: number
  excluded_liability: number
  cash_unallocated_exposure: number
  coverage_ratio: number | null
  excluded_rows: PortfolioAnalyticsScopeExcludedRow[]
  cash_scope_breakdown: Array<{
    performance_scope: PortfolioPerformanceScope
    currency: string
    net_cash_effect: number
    absolute_cash_activity: number
  }>
  ordinary_sleeve_twr_status: 'unavailable'
  ordinary_sleeve_twr_reason: string
}

export type PortfolioPeriodCalculationGroupMetrics = {
  beginning_weight: number | null
  average_weight: number | null
  ending_weight: number | null
  period_return: number | null
  period_return_coverage_state: PortfolioPerformanceCoverageState
  initial_value: number | null
  final_value: number | null
  delta: number | null
  residual_delta: number | null
  capital_gains: number | null
  realized_capital_gains: number | null
  unrealized_pnl_change: number | null
  earnings: number | null
  expense_cash_amount: number | null
  fees: number | null
  taxes: number | null
  cash_currency_gains: number | null
  pending_settlement_currency_gains?: number | null
  instrument_currency_gains: number | null
  total_pnl: number | null
  period_contribution: number | null
  risk_calculation_frequency: PortfolioCalculationFrequency
  risk_return_observation_count: number
  risk_annualization_periods_per_year: number | null
  annualized_volatility: number | null
  sharpe_ratio: number | null
  correlation_to_portfolio: number | null
  beta_to_portfolio: number | null
  realized_risk_contribution: number | null
}

export type PortfolioPeriodCalculationGroupChildRecord = PortfolioPeriodCalculationGroupMetrics & {
  axis: PortfolioContributionAxis
  taxonomy_id: string | null
  parent_group_key: string
  parent_group_label: string
  item_key: string
  item_label: string
  item_kind: 'instrument' | 'cash'
}

export type PortfolioPeriodCalculationGroupRecord = PortfolioPeriodCalculationGroupMetrics & {
  axis: PortfolioContributionAxis
  taxonomy_id: string | null
  group_key: string
  group_label: string
  children: PortfolioPeriodCalculationGroupChildRecord[]
}

export type PortfolioPeriodCalculationGroupsSummary = {
  axis: PortfolioContributionAxis
  taxonomy_id: string | null
  group_key: string | null
  group_label: string | null
  start_date: string | null
  end_date: string | null
  requested_start_date?: string | null
  requested_end_date?: string | null
  effective_start_date?: string | null
  effective_end_date?: string | null
  as_of_clamp_reason?: string | null
  start_boundary_kind?: 'close_eod' | 'funded_bod' | 'imported_opening_eod' | null
  include_start_date_return?: boolean
  group_count: number
  total_initial_value: number | null
  total_final_value: number | null
  total_delta: number | null
  total_residual_delta: number | null
  total_pnl: number | null
  total_period_contribution: number | null
  contribution_residual: number | null
  risk_calculation_frequency: PortfolioCalculationFrequency
  risk_frequency_status_label: string | null
  risk_basis_coverage_state: PortfolioPerformanceCoverageState
  risk_basis_requested_instrument_count: number
  risk_basis_resolved_instrument_count: number
  risk_return_observation_count: number
  risk_annualization_periods_per_year: number | null
  annualized_volatility: number | null
  sharpe_ratio: number | null
}

export type PortfolioPerformanceCalculationGroupsResponse = {
  portfolio_id: string
  base_currency: string
  valuation_timezone: string
  valuation_cutoff_policy: string
  summary: PortfolioPeriodCalculationGroupsSummary
  groups: PortfolioPeriodCalculationGroupRecord[]
}

export type PortfolioPeriodBoundaryHoldingRecord = {
  position_id: string
  instrument_id: string
  instrument_ref: InstrumentCore
  quantity: number
  cost_basis: number | null
  cost_basis_base: number | null
  last_price: number | null
  market_value: number | null
  market_value_base: number | null
  currency: string
  portfolio_weight: number | null
  account_ids: string[]
  account_count: number
  open_position_lot_count: number
}

export type PortfolioPeriodBoundaryHoldingsSummary = {
  axis: PortfolioContributionAxis | null
  taxonomy_id: string | null
  group_key: string | null
  group_label: string | null
  start_date: string | null
  start_boundary_date?: string | null
  end_date: string | null
  start_position_count: number
  end_position_count: number
  start_total_market_value_base: number | null
  end_total_market_value_base: number | null
}

export type PortfolioPeriodBoundaryHoldingsResponse = {
  portfolio_id: string
  base_currency: string
  valuation_timezone: string
  valuation_cutoff_policy: string
  summary: PortfolioPeriodBoundaryHoldingsSummary
  start_positions: PortfolioPeriodBoundaryHoldingRecord[]
  end_positions: PortfolioPeriodBoundaryHoldingRecord[]
}

export type PortfolioContributionLineRecord = {
  axis: PortfolioContributionAxis
  group_key: string
  group_label: string
  start_value_base: number | null
  end_value_base: number | null
  beginning_weight: number | null
  average_weight: number | null
  ending_weight: number | null
  realized_pnl: number | null
  unrealized_pnl_change: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  fee_amount: number | null
  tax_amount: number | null
  cash_currency_gains: number | null
  pending_settlement_currency_gains?: number | null
  instrument_currency_gains: number | null
  total_pnl: number | null
  period_contribution: number | null
}

export type PortfolioContributionReportSummary = {
  axis: PortfolioContributionAxis
  taxonomy_id: string | null
  group_key: string | null
  group_label: string | null
  start_date: string | null
  end_date: string | null
  requested_start_date?: string | null
  requested_end_date?: string | null
  effective_start_date?: string | null
  effective_end_date?: string | null
  as_of_clamp_reason?: string | null
  start_boundary_kind?: 'close_eod' | 'funded_bod' | 'imported_opening_eod' | null
  include_start_date_return?: boolean
  coverage_state: PortfolioPerformanceCoverageState
  slice_count: number
  group_count: number
  observation_count: number
  start_nav: number | null
  end_nav: number | null
  portfolio_arithmetic_return: number | null
  portfolio_cumulative_twr: number | null
  total_period_contribution: number | null
  contribution_residual: number | null
}

export type PortfolioContributionReportResponse = {
  portfolio_id: string
  base_currency: string
  valuation_timezone: string
  valuation_cutoff_policy: string
  summary: PortfolioContributionReportSummary
  lines: PortfolioContributionLineRecord[]
  daily_slices: Array<{
    as_of_date: string
    axis: PortfolioContributionAxis
    group_key: string
    group_label: string
    coverage_state: PortfolioPerformanceCoverageState
    market_observation_count: number
    return_observation_eligible: boolean
    beginning_value_base: number | null
    ending_value_base: number | null
    beginning_weight: number | null
    ending_weight: number | null
    cash_balance_base: number | null
    position_market_value_base: number | null
    open_cost_basis_base: number | null
    realized_pnl: number | null
    unrealized_pnl: number | null
    unrealized_pnl_change: number | null
    income_cash_amount: number | null
    expense_cash_amount: number | null
    fee_amount: number | null
    tax_amount: number | null
    cash_currency_gains: number | null
    pending_settlement_currency_gains?: number | null
    instrument_currency_gains: number | null
    total_pnl: number | null
    daily_return: number | null
    daily_contribution: number | null
  }>
}

export type PortfolioHoldingRow = {
  line_id: string
  holding_region: PortfolioHoldingRegion
  holding_kind?:
    | 'position'
    | 'option_obligation'
    | 'settled_cash'
    | 'restricted_cash'
    | 'pending_subscription'
    | 'settlement_receivable'
    | 'settlement_payable'
    | 'position_recognition_adjustment'
    | string
  available_for_trading?: boolean
  economic_instrument_id?: string | null
  economic_instrument_ref?: InstrumentCore | null
  transaction_ids?: string[]
  instrument_core: InstrumentCore
  quantity: number
  last_price: number | null
  quote_as_of_date?: string | null
  quote_metric_family?: string | null
  quote_basis?: string | null
  quote_provider?: string | null
  quote_status?: string | null
  market_value: number | null
  market_value_base?: number | null
  day_change_pct: number | null
  day_change_value: number | null
  day_change_value_base?: number | null
  cost_basis_method?: 'fifo' | 'moving_average' | 'mixed' | string | null
  cost_basis: number | null
  cost_basis_base?: number | null
  allocation: number | null
  price_chart_1m: SparklinePoint[]
  price_chart_3m: SparklinePoint[]
  price_chart_6m: SparklinePoint[]
  price_chart_1y: SparklinePoint[]
  instrument_trend_as_of_date?: string | null
  instrument_trend_basis?: string | null
  instrument_trend_coverage?: {
    state: PortfolioPerformanceCoverageState
    observation_count: number
    start_date: string | null
    end_date: string | null
    available_return_windows: string[]
  } | null
  instrument_trend_reason?: string | null
  instrument_trend_split_adjusted?: boolean
  instrument_risk_frequency?: PortfolioCalculationFrequency | null
  instrument_return_1w?: number | null
  instrument_return_1m?: number | null
  instrument_return_3m?: number | null
  instrument_return_6m?: number | null
  instrument_return_mtd?: number | null
  instrument_return_ytd?: number | null
  instrument_return_1y?: number | null
  instrument_volatility_1m?: number | null
  instrument_volatility_3m?: number | null
  instrument_volatility_6m?: number | null
  instrument_volatility_1y?: number | null
  instrument_return_series_1m?: HoldingReturnSeries | null
  instrument_return_series_3m?: HoldingReturnSeries | null
  instrument_return_series_6m?: HoldingReturnSeries | null
  instrument_return_series_1y?: HoldingReturnSeries | null
  instrument_return_series_all?: HoldingReturnSeries | null
  instrument_holding_return_series?: HoldingReturnSeries | null
  instrument_current_drawdown?: number | null
  instrument_max_drawdown?: number | null
  instrument_holding_max_drawdown?: number | null
  instrument_holding_start_date?: string | null
  forward_risk_share?: number | null
  forward_contribution_to_variance?: number | null
  forward_annualized_volatility?: number | null
  forward_risk_observation_count?: number | null
  forward_risk_status?: string | null
  coverage_status: string
  account_ids?: string[]
  account_count?: number
  open_position_lot_count?: number
  is_liability?: boolean
  scope_status: string
  taxonomy_id: string | null
  taxonomy_node_id: string | null
  resolved_policy_node_id: string | null
  inherited_from_node_id: string | null
  analytics_scope_policy_id: string | null
  scope_policy_version: number | null
  configuration_version: number | null
  taxonomy_selection_version: number | null
  analytics_scope_policy?: PortfolioPerformanceScope
  analytics_scope_valuation_eligible?: boolean
  analytics_scope_system_exclusion_reason?: string | null
  analytics_scope: PortfolioPerformanceScope
  performance_scope: PortfolioPerformanceScope
  performance_eligible: boolean
  risk_eligible: boolean
  risk_budget_eligible: boolean
  valuation_basis_policy: string
  exclusion_reason: string | null
  open_contract_quantity?: number | null
  required_underlying_quantity?: number | null
  underlying_position_quantity?: number | null
  covered_underlying_quantity?: number | null
  uncovered_underlying_quantity?: number | null
  covered_ratio?: number | null
  obligation_status?: string | null
  obligation_coverage_status?: string | null
  coverage_type?: string | null
  related_underlying_id?: string | null
  expiry_date?: string | null
  days_to_expiry?: number | null
  strike?: number | null
  option_type?: 'call' | 'put' | string | null
  contract_multiplier?: number | null
  settlement_type?: 'physical' | 'cash' | string | null
  assignment_notional?: number | null
  assignment_notional_base?: number | null
  premium_received_gross?: number | null
  premium_basis_remaining?: number | null
  liability_value?: number | null
  liability_value_base?: number | null
  carrying_value?: number | null
  carrying_value_base?: number | null
  fair_value?: number | null
  fair_value_coverage_status?: string | null
  valuation_basis?: string | null
  settlement_date?: string | null
  pending_until_date?: string | null
  pending_status?: string | null
  settlement_amount?: number | null
  settlement_amount_base?: number | null
}

export type HoldingsOperationalSummary = {
  uncovered_obligation_count: number
  uncovered_underlying_quantity: number
  expiry_buckets: Array<{
    bucket:
      | 'expired_or_due'
      | 'next_7_days'
      | 'next_30_days'
      | 'next_90_days'
      | 'later'
      | 'unknown'
    obligation_count: number
    open_contract_quantity: number
    required_underlying_quantity: number
    uncovered_underlying_quantity: number
    carrying_liability_base: number | null
  }>
  assignment_exposure: {
    obligation_count: number
    open_contract_quantity: number
    deliverable_underlying_quantity: number
    uncovered_underlying_quantity: number
    strike_notional_base: number | null
  }
  settlement_exposure: {
    pending_line_count: number
    receivable_base: number | null
    payable_base: number | null
    net_base: number | null
    earliest_settlement_date: string | null
    overdue_line_count: number
    unavailable_base_line_count: number
  }
}

export type HoldingsOperationalAlert = {
  code: string
  severity: 'critical' | 'warning' | 'info' | string
  title: string
  message: string
  related_line_ids: string[]
}

export type HoldingsWorkspaceResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  view_label: string
  detail_level?: 'compact' | 'full'
  coverage_note: string
  quality_warnings: string[]
  risk_basis?: {
    requested_frequency: 'auto' | PortfolioCalculationFrequency
    resolved_frequency: PortfolioCalculationFrequency
    default_frequency: PortfolioCalculationFrequency
    source_frequency_counts: Record<string, number>
    status_label: string
    coverage_state?: PortfolioPerformanceCoverageState
    gap_count?: number
    gap_instrument_ids?: string[]
    gap_details?: Array<{
      instrument_id: string
      gap_count: number
      gap_detection_basis: string
      gap_date_sample: string[]
    }>
  }
  risk_policy?: PortfolioRiskPolicyRecord | null
  forward_risk?: PortfolioForwardRiskSummary | null
  analytics_scope_summary: PortfolioAnalyticsScopeSummary
  operational_summary: HoldingsOperationalSummary
  operational_alerts: HoldingsOperationalAlert[]
  summary_cards: HoldingsSummaryCard[]
  rows: PortfolioHoldingRow[]
  totals: {
    market_value: number | null
    cash_balance?: number | null
    pending_settlement?: number | null
    nav?: number | null
    day_change_pct: number | null
    day_change_value: number | null
    cost_basis: number | null
    allocation: number | null
  }
}

export type PortfolioInstrumentHoldingRow = Omit<
  PortfolioHoldingRow,
  'price_chart_1m' | 'price_chart_3m' | 'price_chart_6m' | 'price_chart_1y'
>

export type PortfolioInstrumentHoldingProjectionResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  view_label: string
  quality_warnings: string[]
  rows: PortfolioInstrumentHoldingRow[]
}

export type HoldingsWorkspaceFilters = {
  as_of_date?: string
  include_details?: boolean
}

export type SharedMarketDataPoint = {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  price_unit: PriceUnit
  price_scale: string
  provider?: string | null
  status: DataStatus
}

export type SharedInstrumentRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type: InstrumentCore['instrument_type']
  currency: string
  identifiers: InstrumentIdentifier[]
  latest_market_data: SharedMarketDataPoint[]
  quote_selection_policy: QuoteSelectionPolicy
  coverage_state: DataStatus
}

export type PortfolioSharedInstrumentsResponse = {
  portfolio_id: string
  instruments: SharedInstrumentRecord[]
}

type RawSharedInstrumentRecord = {
  instrument_core: InstrumentCore
  coverage_state: DataStatus
  latest_market_data: SharedMarketDataPoint[]
  quote_selection_policy: QuoteSelectionPolicy
}

type RawPortfolioSharedInstrumentsResponse = {
  portfolio_id: string
  instruments: RawSharedInstrumentRecord[]
}

export const SUPPORTED_PORTFOLIO_CURRENCIES = ['USD', 'HKD', 'CNY'] as const
export type SupportedPortfolioCurrency = (typeof SUPPORTED_PORTFOLIO_CURRENCIES)[number]

export type PortfolioSharedFxRateRecord = {
  base_currency: SupportedPortfolioCurrency
  quote_currency: SupportedPortfolioCurrency
  rate: number
  as_of_date: string
  source_kind: string
  instrument_id?: string | null
  source_instrument_ids: string[]
  provider?: string | null
  status: string
}

export type PortfolioSharedFxRatesResponse = {
  portfolio_id: string
  supported_currencies: SupportedPortfolioCurrency[]
  maintained_pairs: string[]
  rates: PortfolioSharedFxRateRecord[]
}

export type PortfolioTaxonomyRecord = {
  taxonomy_id: string
  portfolio_id: string
  name: string
  taxonomy_type: string
  purpose?: string | null
  primary_assignment_scope: TaxonomyAssignmentScope
  planning_enabled: boolean
  budgeting_level?: string | null
  root_default_target_dimension: 'weight' | 'risk_budget'
  status: string
  source_template_ref?: string | null
}

export type PortfolioTaxonomyNodeRecord = {
  taxonomy_node_id: string
  taxonomy_id: string
  parent_taxonomy_node_id?: string | null
  node_name: string
  node_code?: string | null
  sort_order: number
  is_terminal: boolean
  default_target_dimension: 'weight' | 'risk_budget'
  status: string
}

export type PortfolioTaxonomyAssignmentRecord = {
  assignment_id: string
  taxonomy_id: string
  target_scope: TaxonomyAssignmentScope
  target_entity_id: string
  taxonomy_node_id: string
  status: string
}

export type PortfolioAnalyticsScopePolicyRecord = {
  analytics_scope_policy_id: string
  portfolio_id: string
  taxonomy_id: string
  taxonomy_node_id: string
  risk_eligible: boolean
  risk_budget_eligible: boolean
  performance_scope: 'ordinary' | 'derivative_lifecycle' | 'operational_only' | 'unallocated'
  valuation_basis: 'market' | 'fair_value' | 'carrying' | 'event' | 'obligation' | 'cash' | 'unknown'
  exclusion_reason?: string | null
  effective_from: string
  effective_to?: string | null
  policy_version: number
  superseded_by_policy_id?: string | null
  created_at: string
}

export type PortfolioAnalyticsTaxonomySelectionRecord = {
  analytics_taxonomy_selection_id: string
  portfolio_id: string
  taxonomy_id?: string | null
  effective_from: string
  effective_to?: string | null
  selection_version: number
  superseded_by_selection_id?: string | null
  created_at: string
}

export type PortfolioTargetSetType = 'saa' | 'taa'

export type PortfolioTargetSetRecord = {
  target_set_id: string
  taxonomy_id: string
  comparator_taxonomy_node_id?: string | null
  target_set_type: PortfolioTargetSetType
  name: string
  weight_enabled: boolean
  risk_budget_enabled: boolean
  status: string
  notes?: string | null
}

export type PortfolioTargetSetLineRecord = {
  target_line_id: string
  target_set_id: string
  target_member_type: 'taxonomy_node' | TaxonomyAssignmentScope
  target_member_id: string
  taxonomy_node_id?: string | null
  target_weight?: number | null
  target_risk_share?: number | null
  notes?: string | null
}

export type PortfolioTargetSetIntegrityIssueRecord = {
  taxonomy_id: string
  comparator_taxonomy_node_id?: string | null
  scope_label: string
  target_set_id: string
  target_set_type: PortfolioTargetSetType
  target_set_name: string
  issue_code: 'invalid_active_target_set'
  message: string
}

export type PortfolioInstrumentUniverseRecord = {
  portfolio_id: string
  instrument_id: string
  instrument_ref?: InstrumentCore | null
  source: string
  holding_state: 'held' | 'not_held' | string
  first_transaction_date?: string | null
  last_transaction_date?: string | null
  transaction_count: number
  research_lifecycle: 'held' | 'observed' | 'former'
  research_eligibility: 'eligible' | 'pm_review_required'
  research_pm_approved: boolean
  research_pm_approved_at?: string | null
  status: string
  created_at?: string | null
  updated_at?: string | null
  instrument_trend_basis?: string | null
  instrument_risk_frequency?: PortfolioCalculationFrequency | null
  instrument_return_series_all?: HoldingReturnSeries | null
}

export type PortfolioTaxonomyCatalogResponse = {
  portfolio_id: string
  default_planning_taxonomy_id?: string | null
  risk_basis?: {
    requested_frequency: 'auto' | PortfolioCalculationFrequency
    resolved_frequency: PortfolioCalculationFrequency
    default_frequency: PortfolioCalculationFrequency
    source_frequency_counts: Record<string, number>
    status_label: string
    coverage_state?: PortfolioPerformanceCoverageState
    gap_count?: number
    gap_instrument_ids?: string[]
  } | null
  taxonomies: PortfolioTaxonomyRecord[]
  taxonomy_nodes: PortfolioTaxonomyNodeRecord[]
  taxonomy_assignments: PortfolioTaxonomyAssignmentRecord[]
  analytics_scope_policy_version: number
  analytics_scope_policies: PortfolioAnalyticsScopePolicyRecord[]
  analytics_taxonomy_selections: PortfolioAnalyticsTaxonomySelectionRecord[]
  instrument_universe: PortfolioInstrumentUniverseRecord[]
  target_sets: PortfolioTargetSetRecord[]
  target_set_lines: PortfolioTargetSetLineRecord[]
  target_set_integrity_issues: PortfolioTargetSetIntegrityIssueRecord[]
}

export type PortfolioDefaultPlanningTaxonomyUpdatePayload = {
  taxonomy_id?: string | null
  effective_from: string
}

export type PortfolioDefaultPlanningTaxonomyResponse = {
  portfolio_id: string
  default_planning_taxonomy_id?: string | null
}

export type PortfolioInstrumentUniverseCreatePayload = {
  instrument_id: string
}

export type PortfolioResearchInstrumentEligibilityUpdatePayload = {
  pm_approved: boolean
}

export type PortfolioResearchRunStatus = 'running' | 'completed' | 'failed'
export type PortfolioResearchTargetDimension = 'scope_default' | 'weight' | 'risk_budget'
export type PortfolioResearchCapitalMode = 'unit_notional' | 'fixed_gross' | 'target_volatility' | 'volatility_cap'
export type PortfolioResearchCalculationFrequency = 'auto' | 'daily' | 'weekly' | 'monthly'
export type PortfolioResearchMissingReturnPolicy = 'strict' | 'complete_case_drop'
export type PortfolioResearchBacktestRebalanceFrequency = '1w' | '1m' | '3m'
export type PortfolioResearchArtifactPreviewKind = 'text' | 'html' | 'binary'
export type PortfolioResearchAsOfMode = 'dynamic' | 'pinned'

export type PortfolioResearchPlanningTaxonomyOption = {
  taxonomy_id: string
  name: string
  taxonomy_type: string
  budgeting_level?: string | null
}

export type PortfolioResearchPlanningScopeOption = {
  taxonomy_node_id?: string | null
  label: string
  path: string
  depth: number
  default_target_dimension: 'weight' | 'risk_budget'
  has_children: boolean
}

export type PortfolioResearchSettingsRecord = {
  portfolio_id: string
  planning_taxonomy_id?: string | null
  planning_taxonomy_name?: string | null
  comparator_taxonomy_node_id?: string | null
  comparator_taxonomy_node_name?: string | null
  as_of_mode: PortfolioResearchAsOfMode
  as_of_date?: string | null
  pinned_as_of_date?: string | null
  lookback_days: number
  calculation_frequency: PortfolioResearchCalculationFrequency
  missing_return_policy: PortfolioResearchMissingReturnPolicy
  target_dimension: PortfolioResearchTargetDimension
  capital_mode: PortfolioResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  frozen_taxonomy_node_ids: string[]
  top_sleeve_weight_bounds: PortfolioResearchTopSleeveWeightBoundRecord[]
  backtest_rebalance_frequency: PortfolioResearchBacktestRebalanceFrequency
  backtest_benchmark_instrument_id?: string | null
  backtest_cash_yield_annual: number
  backtest_commission_bps: number
  backtest_tax_bps: number
  backtest_slippage_bps: number
  backtest_implementation_delay_days: number
  backtest_robustness_scenarios: PortfolioResearchBacktestRobustnessScenarioRecord[]
  backtest_walk_forward_training_months: number
  backtest_walk_forward_test_months: number
  notes?: string | null
  updated_at?: string | null
}

export type PortfolioResearchSettingsUpdatePayload = {
  planning_taxonomy_id?: string | null
  comparator_taxonomy_node_id?: string | null
  as_of_mode: PortfolioResearchAsOfMode
  as_of_date?: string | null
  lookback_days: number
  calculation_frequency?: PortfolioResearchCalculationFrequency
  missing_return_policy?: PortfolioResearchMissingReturnPolicy
  covariance_model_id?: PortfolioRiskCovarianceModel
  contribution_mode?: PortfolioRiskContributionMode
  target_dimension?: PortfolioResearchTargetDimension
  capital_mode?: PortfolioResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  frozen_taxonomy_node_ids?: string[] | null
  top_sleeve_weight_bounds?: PortfolioResearchTopSleeveWeightBoundRecord[] | null
  backtest_rebalance_frequency?: PortfolioResearchBacktestRebalanceFrequency
  backtest_benchmark_instrument_id?: string | null
  backtest_cash_yield_annual?: number
  backtest_commission_bps?: number
  backtest_tax_bps?: number
  backtest_slippage_bps?: number
  backtest_implementation_delay_days?: number
  backtest_robustness_scenarios?: PortfolioResearchBacktestRobustnessScenarioRecord[] | null
  backtest_walk_forward_training_months?: number
  backtest_walk_forward_test_months?: number
  notes?: string | null
}

export type PortfolioResearchBacktestRobustnessScenarioRecord = {
  scenario_id: string
  label: string
  cash_yield_annual: number
  commission_bps: number
  tax_bps: number
  slippage_bps: number
  implementation_delay_days: number
}

export type PortfolioResearchTopSleeveWeightBoundRecord = {
  taxonomy_node_id: string
  min_weight?: number | null
  max_weight?: number | null
}

export type PortfolioResearchContextSignalRecord = {
  label: string
  value: string
  tone: string
}

export type PortfolioResearchCalculationFrequencyProfile = {
  requested_frequency: PortfolioResearchCalculationFrequency
  resolved_frequency: Exclude<PortfolioResearchCalculationFrequency, 'auto'>
  default_frequency: Exclude<PortfolioResearchCalculationFrequency, 'auto'>
  source_frequency_counts: Record<string, number>
  options: Array<{
    frequency: Exclude<PortfolioResearchCalculationFrequency, 'auto'>
    label: string
    available: boolean
    reason?: string | null
  }>
  status_label: string
}

export type PortfolioResearchHoldingSnapshotRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type?: string | null
  allocation?: number | null
  market_value_base?: number | null
  cost_basis_base?: number | null
  base_currency: string
  price?: number | null
}

export type PortfolioResearchPlanningGroupSnapshotRecord = {
  group_key: string
  group_label: string
  start_allocation?: number | null
  end_allocation?: number | null
  allocation_change?: number | null
  start_value_base?: number | null
  end_value_base?: number | null
  period_contribution?: number | null
  total_pnl?: number | null
  average_weight?: number | null
  ending_weight?: number | null
  position_count: number
}

export type PortfolioResearchFindingRecord = {
  title: string
  detail: string
}

export type PortfolioResearchContextPoint = {
  date: string
  value?: number | null
}

export type PortfolioResearchCurrentContextSummary = {
  period_return?: number | null
  annualized_volatility?: number | null
  current_drawdown?: number | null
  max_drawdown?: number | null
  start_nav?: number | null
  end_nav?: number | null
}

export type PortfolioResearchPlanningTargetSummary = {
  root_saa_configured: boolean
  root_taa_configured: boolean
  scoped_target_set_count: number
}

export type PortfolioResearchCurrentContextRecord = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  lookback_start: string
  lookback_end: string
  nav?: number | null
  holdings_count: number
  planning_group_count: number
  chart_label?: string | null
  chart_note?: string | null
  chart_currency?: string | null
  summary: PortfolioResearchCurrentContextSummary
  planning_target_summary?: PortfolioResearchPlanningTargetSummary | null
  quality_warnings: string[]
  chart_points: PortfolioResearchContextPoint[]
  top_holdings: PortfolioResearchHoldingSnapshotRecord[]
  planning_groups: PortfolioResearchPlanningGroupSnapshotRecord[]
}

export type PortfolioResearchScopeSelectionRecord = {
  taxonomy_node_id?: string | null
  label: string
  path: string
  depth: number
  default_target_dimension: 'weight' | 'risk_budget'
  member_source: string
}

export type PortfolioResearchMemberTargetRecord = {
  member_type: string
  member_id: string
  label: string
  scope_path?: string | null
  member_path?: string | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  selected_target_dimension?: PortfolioResearchTargetDimension | null
  source_target_set_type?: 'saa' | 'taa' | null
  current_weight?: number | null
  current_risk_share?: number | null
  target_weight?: number | null
  weight_change?: number | null
  configured_weight?: number | null
  configured_risk_share?: number | null
  selected_target_value?: number | null
}

export type PortfolioResearchSolvedResultRowRecord = {
  member_type: string
  member_id: string
  label: string
  top_sleeve_id?: string | null
  top_sleeve_label: string
  current_weight?: number | null
  solved_weight?: number | null
  current_value_base?: number | null
  target_value_base?: number | null
  target_risk_share?: number | null
  forward_risk_contribution?: number | null
}

export type PortfolioResearchSolvedResultGroupRecord = {
  top_sleeve_id?: string | null
  top_sleeve_label: string
  current_weight?: number | null
  solved_weight?: number | null
  current_value_base?: number | null
  target_value_base?: number | null
  target_risk_share?: number | null
  forward_risk_contribution?: number | null
  min_weight?: number | null
  max_weight?: number | null
  bound_status?: string | null
  rows: PortfolioResearchSolvedResultRowRecord[]
}

export type PortfolioResearchSolveEventRecord = {
  as_of_date: string
  scope_node_id?: string | null
  scope_label: string
  scope_path?: string | null
  scope_depth?: number | null
  requested_target_dimension?: string | null
  taxonomy_default_target_dimension?: 'weight' | 'risk_budget' | null
  target_dimension?: PortfolioResearchTargetDimension | null
  solver_kind?: string | null
  solver_detail?: string | null
  solver_message?: string | null
  target_status?: string | null
  execution_ready?: boolean | null
  covariance_model?: string | null
  covariance_observations?: number | null
  risk_contribution_mode?: string | null
  missing_return_policy?: PortfolioResearchMissingReturnPolicy | null
  return_rows_before_policy?: number | null
  return_rows_after_policy?: number | null
  missing_return_row_count?: number | null
  missing_return_row_fraction?: number | null
  dropped_return_rows?: Array<{ date: string; missing_members: string[] }> | null
  latest_complete_return_date?: string | null
  trailing_complete_return_staleness_days?: number | null
  calculation_frequency?: Exclude<PortfolioResearchCalculationFrequency, 'auto'> | null
  gap_turnover?: number | null
  current_weight_total?: number | null
  target_weight_total?: number | null
  max_weight_gap?: number | null
  max_risk_share_gap?: number | null
  estimated_risk_sleeve_volatility?: number | null
  target_volatility?: number | null
  gross_exposure?: number | null
  risky_allocation_scaling_factor?: number | null
  member_count: number
  scope_solve_count?: number | null
}

export type PortfolioResearchTargetWeightGapRecord = {
  member_type: string
  member_id: string
  label: string
  current_weight?: number | null
  target_weight?: number | null
  gap?: number | null
  current_value_base?: number | null
  target_value_base?: number | null
  base_currency: string
  action: string
  research_lifecycle?: 'held' | 'observed' | 'former' | null
  research_eligibility?: 'eligible' | 'pm_review_required' | null
  research_pm_approved?: boolean | null
  execution_status: 'ready' | 'manual_review_required'
  execution_note?: string | null
}

export type PortfolioResearchTargetRowRecord = {
  member_type: string
  member_id: string
  label: string
  current_weight?: number | null
  current_value_base?: number | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  selected_target_dimension?: PortfolioResearchTargetDimension | null
  source_target_set_type?: 'saa' | 'taa' | null
  source_target_set_id?: string | null
  source_label?: string | null
  selected_target_value?: number | null
  target_weight?: number | null
  target_risk_share?: number | null
  implementation_weight?: number | null
  gap_to_implementation?: number | null
  action?: string | null
  research_lifecycle?: 'held' | 'observed' | 'former' | null
  research_eligibility?: 'eligible' | 'pm_review_required' | null
  research_pm_approved?: boolean | null
  execution_status: 'ready' | 'manual_review_required'
  execution_note?: string | null
}

export type PortfolioResearchBacktestPointRecord = {
  date: string
  value?: number | null
}

export type PortfolioResearchBacktestSleeveValueRecord = {
  top_sleeve_id?: string | null
  top_sleeve_label: string
  value?: number | null
}

export type PortfolioResearchBacktestSleevePointRecord = {
  date: string
  sleeves: PortfolioResearchBacktestSleeveValueRecord[]
}

export type PortfolioResearchBacktestTargetWeightRecord = {
  instrument_id: string
  target_weight: number
  top_sleeve_id?: string | null
  top_sleeve_label: string
  top_sleeve_path?: string | null
  first_usable_observation_date?: string | null
}

export type PortfolioResearchBacktestExecutionRecord = {
  decision_date: string
  scheduled_execution_date: string
  actual_execution_date: string
  taxonomy_configuration_version?: number | null
  taxonomy_configuration_effective_from?: string | null
  target_weights: PortfolioResearchBacktestTargetWeightRecord[]
  cash_target_weight: number
  risky_buy_turnover: number
  risky_sell_turnover: number
  cash_leg_turnover: number
  one_way_turnover: number
  commission_cost: number
  tax_cost: number
  slippage_cost: number
  total_cost: number
  nav_before_execution: number
  nav_after_execution: number
}

export type PortfolioResearchBacktestContributionReconciliationRecord = {
  date: string
  nav_change: number
  linked_contribution: number
  residual: number
  execution_cost_contribution: number
}

export type PortfolioResearchBacktestMethodologyRecord = {
  name: string
  point_in_time_universe: boolean
  point_in_time_taxonomy: boolean
  decision_rule: string
  execution_rule: string
  cash_return_rule: string
  cost_rule: string
  contribution_linking: string
  assumptions: Record<string, number>
}

export type PortfolioResearchBacktestPointInTimeCoverageRecord = {
  status: 'complete' | 'partial' | 'unavailable'
  decision_count: number
  first_decision_date?: string | null
  last_decision_date?: string | null
  configuration_versions_used: number[]
  historical_instrument_count: number
  first_usable_observation_by_instrument: Record<string, string>
  skipped_rebalances: Array<{ date: string; reason: string }>
  unavailable_reason?: string | null
}

export type PortfolioResearchBacktestMetricsRecord = {
  start_date?: string | null
  end_date?: string | null
  period_return?: number | null
  ytd_return?: number | null
  annualization_eligible?: boolean
  annualization_years?: number | null
  annualization_unavailable_reason?: string | null
  annualized_return?: number | null
  annualized_volatility?: number | null
  sharpe_ratio?: number | null
  max_drawdown?: number | null
  max_drawdown_start_date?: string | null
  max_drawdown_end_date?: string | null
  max_drawdown_days?: number | null
  max_drawdown_recovery_date?: string | null
  max_drawdown_recovery_days?: number | null
  current_drawdown?: number | null
  calmar_ratio?: number | null
}

export type PortfolioResearchBacktestRobustnessResultRecord = PortfolioResearchBacktestRobustnessScenarioRecord & {
  metrics?: PortfolioResearchBacktestMetricsRecord | null
  period_return_delta?: number | null
  ending_value?: number | null
  total_turnover?: number | null
  total_cost?: number | null
  warnings: string[]
}

export type PortfolioResearchBacktestWalkForwardWindowRecord = {
  training_start_date: string
  training_end_date: string
  test_start_date: string
  test_end_date: string
  configuration_versions_used: number[]
  points: PortfolioResearchBacktestPointRecord[]
  metrics?: PortfolioResearchBacktestMetricsRecord | null
  available: boolean
  unavailable_reason?: string | null
}

export type PortfolioResearchBacktestWalkForwardRecord = {
  validation_method?: 'rolling_temporal_holdout' | string
  parameter_selection?: 'fixed_point_in_time_policy' | string
  parameter_optimization?: boolean
  methodology_note?: string | null
  available: boolean
  unavailable_reason?: string | null
  training_months: number
  test_months: number
  windows: PortfolioResearchBacktestWalkForwardWindowRecord[]
  oos_points: PortfolioResearchBacktestPointRecord[]
  oos_metrics?: PortfolioResearchBacktestMetricsRecord | null
}

export type PortfolioResearchBacktestRecord = {
  rebalance_frequency: PortfolioResearchBacktestRebalanceFrequency
  common_history_start_date?: string | null
  start_date?: string | null
  end_date?: string | null
  lookback_days: number
  points: PortfolioResearchBacktestPointRecord[]
  metrics?: PortfolioResearchBacktestMetricsRecord | null
  top_sleeve_weight_points: PortfolioResearchBacktestSleevePointRecord[]
  top_sleeve_contribution_points: PortfolioResearchBacktestSleevePointRecord[]
  contribution_reconciliation_points: PortfolioResearchBacktestContributionReconciliationRecord[]
  execution_records: PortfolioResearchBacktestExecutionRecord[]
  total_turnover: number
  total_cost: number
  methodology?: PortfolioResearchBacktestMethodologyRecord | null
  point_in_time_coverage?: PortfolioResearchBacktestPointInTimeCoverageRecord | null
  robustness_results: PortfolioResearchBacktestRobustnessResultRecord[]
  walk_forward?: PortfolioResearchBacktestWalkForwardRecord | null
  warnings: string[]
}

export type PortfolioResearchBacktestBenchmarkRecord = {
  instrument_id?: string | null
  label?: string | null
  points: PortfolioResearchBacktestPointRecord[]
  metrics?: PortfolioResearchBacktestMetricsRecord | null
  warnings: string[]
}

export type PortfolioResearchBacktestRelativeMetricsRecord = PortfolioResearchBacktestMetricsRecord & {
  excess_return?: number | null
  tracking_error?: number | null
  information_ratio?: number | null
}

export type PortfolioResearchBacktestBenchmarkComparisonResponse = {
  backtest_benchmark?: PortfolioResearchBacktestBenchmarkRecord | null
  backtest_relative_metrics?: PortfolioResearchBacktestRelativeMetricsRecord | null
}

export type PortfolioResearchRunDetailRecord = {
  headline?: string | null
  coverage_note?: string | null
  signals: PortfolioResearchContextSignalRecord[]
  findings: PortfolioResearchFindingRecord[]
  next_questions: string[]
  top_holdings: PortfolioResearchHoldingSnapshotRecord[]
  planning_groups: PortfolioResearchPlanningGroupSnapshotRecord[]
  selected_scope?: PortfolioResearchScopeSelectionRecord | null
  target_assumptions: string[]
  target_rows: PortfolioResearchTargetRowRecord[]
  member_targets: PortfolioResearchMemberTargetRecord[]
  leaf_targets: PortfolioResearchMemberTargetRecord[]
  solved_result_groups: PortfolioResearchSolvedResultGroupRecord[]
  solve_event?: PortfolioResearchSolveEventRecord | null
  scope_solve_events: PortfolioResearchSolveEventRecord[]
  target_weight_gaps: PortfolioResearchTargetWeightGapRecord[]
  backtest?: PortfolioResearchBacktestRecord | null
  backtest_benchmark?: PortfolioResearchBacktestBenchmarkRecord | null
  backtest_relative_metrics?: PortfolioResearchBacktestRelativeMetricsRecord | null
  warnings: string[]
}

export type PortfolioResearchArtifactRecord = {
  artifact_id: string
  label: string
  path: string
  media_type: string
  preview_kind: PortfolioResearchArtifactPreviewKind
}

export type PortfolioResearchRunRecord = {
  research_run_id: string
  portfolio_id: string
  job_type: string
  status: PortfolioResearchRunStatus
  requested_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  as_of_date?: string | null
  planning_taxonomy_id?: string | null
  planning_taxonomy_name?: string | null
  lookback_days: number
  requested_by?: string | null
  headline?: string | null
  error_message?: string | null
  reliability_state: 'current' | 'stale' | 'unassessed' | 'not_completed'
  is_current: boolean
  reliability_reasons: string[]
  artifact_count: number
  artifacts: PortfolioResearchArtifactRecord[]
  detail?: PortfolioResearchRunDetailRecord | null
}

export type PortfolioResearchRunCreatePayload = {
  requested_by?: string | null
}

export type PortfolioResearchWorkbenchResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  default_planning_taxonomy_id?: string | null
  planning_taxonomy_options: PortfolioResearchPlanningTaxonomyOption[]
  planning_scope_options: PortfolioResearchPlanningScopeOption[]
  calculation_frequency: PortfolioResearchCalculationFrequencyProfile
  settings: PortfolioResearchSettingsRecord
  risk_policy: PortfolioRiskPolicyRecord
  current_context: PortfolioResearchCurrentContextRecord
  instrument_universe: PortfolioInstrumentUniverseRecord[]
  detail_level: 'compact' | 'selected_run'
  runs: PortfolioResearchRunRecord[]
  selected_run?: PortfolioResearchRunRecord | null
}

export type PortfolioResearchArtifactContentResponse = {
  filename: string
  path: string
  media_type: string
  encoding: 'text'
  preview_kind: PortfolioResearchArtifactPreviewKind
  content: string
}

export type PortfolioTaxonomyCreatePayload = {
  effective_from: string
  name: string
  taxonomy_type?: string
  purpose?: string | null
  primary_assignment_scope: TaxonomyAssignmentScope
  planning_enabled?: boolean
  budgeting_level?: string | null
  root_default_target_dimension?: 'weight' | 'risk_budget'
  status?: string
  source_template_ref?: string | null
}

export type PortfolioTaxonomyNodeCreatePayload = {
  effective_from: string
  node_name: string
  node_code?: string | null
  parent_taxonomy_node_id?: string | null
  sort_order?: number | null
  is_terminal?: boolean
  default_target_dimension?: 'weight' | 'risk_budget'
  status?: string
}

export type PortfolioTaxonomyUpdatePayload = {
  effective_from: string
  name?: string | null
  taxonomy_type?: string | null
  purpose?: string | null
  planning_enabled?: boolean
  budgeting_level?: string | null
  root_default_target_dimension?: 'weight' | 'risk_budget' | null
  status?: string | null
}

export type PortfolioTaxonomyNodeUpdatePayload = {
  effective_from: string
  node_name?: string | null
  node_code?: string | null
  parent_taxonomy_node_id?: string | null
  sort_order?: number | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  status?: string | null
}

export type PortfolioTaxonomyAssignmentCreatePayload = {
  effective_from: string
  target_scope: TaxonomyAssignmentScope
  target_entity_id: string
  taxonomy_node_id: string
  status?: string
}

export type PortfolioTaxonomyAssignmentUpdatePayload = {
  effective_from: string
  taxonomy_node_id?: string | null
  status?: string | null
}

export type PortfolioTargetSetLinePayload = {
  target_member_type: 'taxonomy_node' | TaxonomyAssignmentScope
  target_member_id: string
  taxonomy_node_id?: string | null
  target_weight?: number | null
  target_risk_share?: number | null
  notes?: string | null
}

export type PortfolioTargetSetCreatePayload = {
  effective_from: string
  comparator_taxonomy_node_id?: string | null
  target_set_type: PortfolioTargetSetType
  name: string
  weight_enabled: boolean
  risk_budget_enabled: boolean
  status?: string
  notes?: string | null
  lines: PortfolioTargetSetLinePayload[]
}

export type PortfolioTargetSetUpdatePayload = {
  effective_from: string
  name?: string | null
  weight_enabled?: boolean
  risk_budget_enabled?: boolean
  status?: string | null
  notes?: string | null
  lines?: PortfolioTargetSetLinePayload[]
}

export type PortfolioAnalyticsScopePolicyUpsertPayload = {
  risk_eligible: boolean
  risk_budget_eligible: boolean
  performance_scope: PortfolioAnalyticsScopePolicyRecord['performance_scope']
  valuation_basis: PortfolioAnalyticsScopePolicyRecord['valuation_basis']
  exclusion_reason?: string | null
  effective_from: string
  effective_to?: string | null
}

export type PortfolioAccountRecord = {
  account_id: string
  portfolio_id: string
  account_name: string
  account_type: string
  currency: string
  institution?: string | null
  default_settlement_cash_account_id?: string | null
  cost_basis_method?: 'moving_average' | 'fifo' | null
  allowed_instrument_types?: string[] | null
  opened_at?: string | null
  closed_at?: string | null
  status: string
}

export type PortfolioAccountsResponse = {
  portfolio_id: string
  accounts: PortfolioAccountRecord[]
}

export type PortfolioAccountCreatePayload = {
  account_name: string
  account_type: string
  currency: string
  institution?: string | null
  default_settlement_cash_account_id?: string | null
  cost_basis_method?: 'moving_average' | 'fifo' | null
  allowed_instrument_types?: string[] | null
  opened_at?: string | null
  closed_at?: string | null
  status?: string
}

export type PortfolioAccountUpdatePayload = {
  account_name?: string | null
  institution?: string | null
  default_settlement_cash_account_id?: string | null
  cost_basis_method?: 'moving_average' | 'fifo' | null
  allowed_instrument_types?: string[] | null
  opened_at?: string | null
  closed_at?: string | null
  status?: string | null
}

export type PortfolioLedgerPostingRecord = {
  posting_id: string
  transaction_id: string
  portfolio_id: string
  account_id: string
  attribution_account_id?: string | null
  settlement_cash_account_id?: string | null
  posting_role: string
  source_transaction_type: string
  trade_date: string
  settlement_date: string
  effective_date: string
  recognition_start_date?: string | null
  instrument_id?: string | null
  instrument_ref?: InstrumentCore | null
  cash_amount_delta?: number | null
  pending_amount_delta?: number | null
  quantity_delta?: number | null
  cost_basis_delta?: number | null
  liability_amount_delta?: number | null
  realized_pnl_delta?: number | null
  option_action?: PortfolioOptionAction | null
  obligation_id?: string | null
  currency: string
  transfer_group_id?: string | null
  note?: string | null
}

export type PortfolioAccountPositionRecord = {
  position_id?: string | null
  account_id: string
  instrument_id: string
  instrument_ref: InstrumentCore
  quantity: number
  cost_basis?: number | null
  last_price?: number | null
  market_value?: number | null
  carrying_value: number | null
  fair_value: number | null
  fair_value_coverage_status: 'complete' | 'partial' | 'unavailable'
  valuation_basis: 'market_quote' | 'carried_cost'
  coverage_status: string
  currency: string
  cost_basis_method?: 'moving_average' | 'fifo' | null
  open_position_lot_count?: number
}

/**
 * Written-option obligation rows returned by the account ledger workspace.
 *
 * These are liability read-model rows, not negative long positions.  The
 * backend intentionally keeps the lifecycle realization detail extensible;
 * the identity, coverage, premium basis, and liability fields below are the
 * stable contract consumed by account and audit surfaces.
 */
export type PortfolioOptionObligationRecord = {
  obligation_id: string
  portfolio_id?: string | null
  account_id: string
  option_instrument_id: string
  instrument_id: string
  instrument_ref?: InstrumentCore | null
  related_underlying_id: string
  open_contract_quantity: number
  covered_underlying_quantity: number
  remaining_quantity: number
  premium_received_gross: number
  premium_basis_remaining: number
  carrying_liability: number
  opened_at?: string | null
  expiry_date?: string | null
  status: string
  coverage_type?: string | null
  option_type?: 'call' | 'put' | string | null
  strike?: number | null
  contract_multiplier?: number | null
  settlement_type?: 'physical' | 'cash' | string | null
  contract_currency?: string | null
  realized_pnl?: number
  opening_fee_expense?: number
  realizations?: Array<Record<string, unknown>>
}

export type PortfolioAccountWorkspaceAccount = {
  account: PortfolioAccountRecord
  default_settlement_cash_account_name?: string | null
  linked_transaction_count: number
  linked_posting_count: number
  derived_cash_balance: number
  derived_cash_balance_base?: number | null
  pending_settlement: number
  pending_settlement_base?: number | null
  derivative_liability: number
  derivative_liability_base?: number | null
  open_option_obligation_count: number
  account_value_base?: number | null
  valuation_coverage_state: PortfolioPerformanceCoverageState
  valuation_missing_components: string[]
  position_line_count: number
  position_market_value?: number | null
  position_market_value_currency?: string | null
}

export type PortfolioAccountsWorkspaceResponse = {
  portfolio_id: string
  base_currency: string
  summary: {
    account_count: number
    deposit_account_count: number
    securities_account_count: number
    ledger_posting_count: number
    position_line_count: number
    valuation_coverage_state: PortfolioPerformanceCoverageState
    valued_account_count: number
    unvalued_account_count: number
    open_option_obligation_count: number
    derivative_liability_base: number | null
  }
  derivation_boundary: {
    ledger_postings: string
    positions: string
    position_lots?: string
    holdings: string
    snapshot: string
  }
  selected_account_id?: string | null
  accounts: PortfolioAccountWorkspaceAccount[]
  ledger_postings: PortfolioLedgerPostingRecord[]
  positions: PortfolioAccountPositionRecord[]
  option_obligations: PortfolioOptionObligationRecord[]
  linked_transactions_summary?: PortfolioTransactionListResponse['summary'] | null
  linked_transactions: PortfolioTransactionRecord[]
}

export type PortfolioLedgerPostingListResponse = {
  portfolio_id: string
  summary: {
    posting_count: number
    cash_posting_count: number
    position_posting_count: number
    pending_posting_count?: number
  }
  ledger_postings: PortfolioLedgerPostingRecord[]
}

export type PortfolioTransactionRecord = {
  transaction_id: string
  transaction_sequence: number
  portfolio_id: string
  transaction_type: string
  option_action?: PortfolioOptionAction | null
  lifecycle_event_type?: string | null
  flow_scope: string
  trade_date: string
  trade_time: string
  trade_at: string
  trade_timezone: string
  trade_time_is_estimated: boolean
  settlement_date: string
  position_effective_date: string | null
  economic_date: string
  external_flow_date: string | null
  entitlement_date: string | null
  acquisition_date: string | null
  account: PortfolioAccountRecord
  settlement_cash_account: PortfolioAccountRecord | null
  instrument_id: string | null
  instrument_ref: InstrumentCore | null
  quantity: number | null
  source_quantity: string | null
  price: number | null
  source_price: string | null
  gross_amount: number
  source_gross_amount: string | null
  counter_amount: number | null
  source_counter_amount: string | null
  fx_rate: number | null
  source_fx_rate: string | null
  fees: number
  source_fees: string | null
  fee_category: PortfolioFeeCategory
  taxes: number
  source_taxes: string | null
  currency: string
  transfer_scope: string | null
  transfer_object_type: string | null
  transfer_group_id: string | null
  counterparty_account_id: string | null
  source_system?: string | null
  external_reference?: string | null
  event_group_id?: string | null
  related_instrument_id?: string | null
  net_cash_effect: number | null
  note: string | null
  created_at: string | null
  row_version: number
}

export type PortfolioOptionAction =
  | 'buy_to_open'
  | 'sell_to_close'
  | 'sell_to_open'
  | 'buy_to_close'

export type PortfolioTransactionChangeLogRecord = {
  change_id: string
  portfolio_id: string
  transaction_id: string
  change_type: 'create' | 'update' | 'delete'
  row_version: number
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  request_idempotency_key: string | null
  changed_at: string
}

export type PortfolioFeeCategory =
  | 'unknown'
  | 'transaction_cost'
  | 'management_fee'
  | 'custody_fee'
  | 'administration_fee'
  | 'performance_fee'
  | 'other'

export type PortfolioTransactionListResponse = {
  portfolio_id: string
  summary: {
    total_transactions: number
    instrument_transactions: number
    external_cash_flows: number
    opening_balance_records: number
  }
  derivation_boundary: {
    ledger_postings: string
    positions: string
    position_lots?: string
    holdings: string
    snapshot: string
  }
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionWorkspaceResponse = {
  portfolio_id: string
  summary: PortfolioTransactionListResponse['summary']
  derivation_boundary: PortfolioTransactionListResponse['derivation_boundary']
  selected_transaction_id: string | null
  transactions: PortfolioTransactionRecord[]
  selected_transaction: PortfolioTransactionRecord | null
  delete_scope_row_versions: Record<string, number>
  ledger_summary: PortfolioLedgerPostingListResponse['summary']
  ledger_postings: PortfolioLedgerPostingRecord[]
  related_position_lot_summary: PortfolioPositionLotListResponse['summary']
  related_position_lots: PortfolioPositionLotRecord[]
  change_log_summary?: {
    change_count: number
  }
  change_log?: PortfolioTransactionChangeLogRecord[]
}

export type PortfolioInstrumentEventTaskStatus =
  | 'pending'
  | 'processed'
  | 'not_applicable'
  | 'source_cancelled'
  | 'no_entitlement'
  | 'needs_review'

export type PortfolioInstrumentEventTaskLinkedTransaction = {
  transaction_id: string
  transaction_type: string
  trade_date: string
  position_effective_date: string | null
  settlement_date: string
  entitlement_date: string | null
  gross_amount: number
  quantity: number | null
  link_role: 'distribution' | 'reinvestment' | 'reinvestment_purchase'
  linked_event_revision_id: string
  linked_by: string
  linked_at: string
}

export type PortfolioInstrumentEventTaskRecord = {
  instrument_event_task_id: string
  portfolio_id: string
  account_id: string
  instrument_id: string
  instrument_name: string | null
  event_source: string
  event_action_id: string
  current_event_revision_id: string
  event_type: string
  source_revision_kind: 'original' | 'correction' | 'cancellation'
  source_event_state: 'active' | 'cancelled'
  announcement_date: string | null
  record_date: string | null
  effective_date: string
  payable_date: string | null
  cash_per_unit: number | null
  unit_ratio: number | null
  reinvestment_nav: number | null
  entitled_quantity: number
  expected_gross_amount: number | null
  resolution_status: 'pending' | 'processed' | 'not_applicable'
  reviewed_event_revision_id: string | null
  resolution_note: string | null
  resolved_by: string | null
  resolved_at: string | null
  status: PortfolioInstrumentEventTaskStatus
  attention_required: boolean
  attention_reason: string | null
  linked_transactions: PortfolioInstrumentEventTaskLinkedTransaction[]
  row_version: number
  created_at: string
  updated_at: string
}

export type PortfolioInstrumentEventTaskListResponse = {
  portfolio_id: string
  accounting_policy: 'official_unit_nav_assume_no_unrecorded_distribution'
  attention_count: number
  tasks: PortfolioInstrumentEventTaskRecord[]
}

export type PortfolioInstrumentEventTaskReviewPayload = {
  decision: 'processed' | 'not_applicable' | 'reopened'
  transaction_ids: string[]
  note: string
  reviewed_by: string
  expected_row_version: number
}

export type PortfolioTransactionPositionPreviewResponse = {
  portfolio_id: string
  account_id: string
  instrument_id: string
  as_of_date: string
  trade_at: string
  quantity: number
}

export type PortfolioPositionRecord = {
  position_id: string
  portfolio_id: string
  instrument_id: string
  instrument_ref: InstrumentCore
  quantity: number
  cost_basis?: number | null
  last_price?: number | null
  market_value?: number | null
  currency: string
  account_ids: string[]
  account_count: number
  open_position_lot_count: number
}

export type PortfolioPositionListResponse = {
  portfolio_id: string
  summary: {
    position_count: number
    priced_position_count: number
    open_position_lot_count: number
  }
  positions: PortfolioPositionRecord[]
}

export type PortfolioPositionLotRealizationRecord = {
  realization_id: string
  transaction_id: string
  transaction_type: string
  trade_date: string
  position_effective_date: string
  quantity: number
  proceeds?: number | null
  cost_basis_released: number
  realized_pnl?: number | null
  price?: number | null
  remaining_quantity_after: number
  remaining_cost_basis_after: number
  status_after: 'open' | 'closed'
  note?: string | null
}

export type PortfolioPositionLotRecord = {
  position_lot_id: string
  portfolio_id: string
  account_id: string
  instrument_id: string
  instrument_ref: InstrumentCore
  currency: string
  cost_basis_method: 'moving_average' | 'fifo'
  opened_by_transaction_id: string
  opening_transaction_type: string
  opened_at: string
  closed_at?: string | null
  status: 'open' | 'closed'
  close_reason?: 'disposed' | 'transferred' | 'corporate_action' | null
  source_position_lot_id?: string | null
  corporate_action_event_id?: string | null
  corporate_action_event?: Record<string, unknown> | null
  corporate_action_quantity_out?: number | null
  corporate_action_cost_basis_out?: number | null
  predecessor_quantity?: number | null
  unit_cost_basis_before?: number | null
  unit_cost_basis_after?: number | null
  entry_quantity: number
  remaining_quantity: number
  realized_quantity: number
  transferred_quantity: number
  entry_gross_amount: number
  entry_fee_amount: number
  entry_tax_amount: number
  entry_cost_basis: number
  entry_cost_per_unit?: number | null
  remaining_cost_basis: number
  realized_cost_basis: number
  transferred_cost_basis: number
  realized_proceeds: number
  realized_pnl: number
  income_cash_amount: number
  expense_cash_amount: number
  return_of_capital_amount: number
  entry_price?: number | null
  average_exit_price?: number | null
  current_market_value?: number | null
  unrealized_pnl?: number | null
  holding_period_days?: number | null
  linked_transaction_count: number
  realization_count: number
  realizations: PortfolioPositionLotRealizationRecord[]
}

export type PortfolioPositionLotFilters = {
  account_id?: string
  instrument_id?: string
  status?: 'open' | 'closed'
  as_of_date?: string
}

export type PortfolioPositionLotListResponse = {
  portfolio_id: string
  summary: {
    position_lot_count: number
    open_position_lot_count: number
    closed_position_lot_count: number
    realized_pnl: number
  }
  position_lots: PortfolioPositionLotRecord[]
}

export type PortfolioTransactionFilters = {
  account_id?: string
  transaction_type?: string
  instrument_id?: string
  start_date?: string
  end_date?: string
}

export type PortfolioTransactionCreatePayload = {
  transaction_type: string
  lifecycle_event_type?: string | null
  trade_date: string
  trade_time?: string | null
  settlement_date?: string | null
  position_effective_date?: string | null
  entitlement_date?: string | null
  acquisition_date?: string | null
  account_id: string
  settlement_cash_account_id?: string | null
  instrument_id?: string | null
  quantity?: number | null
  price?: number | null
  gross_amount: number
  counter_amount?: number | null
  fx_rate?: number | null
  fees?: number
  fee_category?: PortfolioFeeCategory
  taxes?: number
  currency: string
  counterparty_account_id?: string | null
  source_system?: string | null
  external_reference?: string | null
  event_group_id?: string | null
  related_instrument_id?: string | null
  note?: string | null
}

export type PortfolioTransactionUpdatePayload = PortfolioTransactionCreatePayload & {
  expected_row_version: number
}

export type PortfolioPerformanceFilters = {
  start_date?: string
  end_date?: string
}

export type PortfolioInternalTransferCreatePayload = {
  trade_date: string
  trade_time?: string | null
  settlement_date?: string | null
  transfer_object_type: 'cash' | 'position'
  from_account_id: string
  to_account_id: string
  instrument_id?: string | null
  quantity?: number | null
  gross_amount?: number | null
  note?: string | null
}

export type PortfolioTransactionBatchResponse = {
  portfolio_id: string
  created_count: number
  transfer_group_id?: string | null
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionCsvPreviewRow = {
  row_number: number
  transaction: PortfolioTransactionCreatePayload | null
  errors: string[]
}

export type PortfolioTransactionCsvPreviewResponse = {
  portfolio_id: string
  preview_digest: string
  headers: string[]
  row_count: number
  valid_count: number
  error_count: number
  warnings: string[]
  batch_errors: string[]
  rows: PortfolioTransactionCsvPreviewRow[]
}

export type PortfolioTransactionCsvImportResponse = {
  portfolio_id: string
  preview_digest: string
  created_count: number
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionDeleteResponse = {
  portfolio_id: string
  deleted_count: number
  deleted_transaction_ids: string[]
  transfer_group_id?: string | null
  event_group_id?: string | null
}

export type PortfolioTableViewScope = 'holdings' | 'performance_calculation'

export type PortfolioTableViewStoreResponse<TStore = unknown> = {
  portfolio_id: string
  view_scope: PortfolioTableViewScope
  store: TStore | null
  created_at: string | null
  updated_at: string | null
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const GET_CACHE_TTL_MS = 60_000
const GET_CACHE_MAX_ENTRIES = 128

type CachedGetRequest = {
  expiresAt: number
  promise: Promise<unknown>
}

const getRequestCache = new Map<string, CachedGetRequest>()

export function clearPortfolioApiCache() {
  getRequestCache.clear()
}

function trimGetRequestCache() {
  while (getRequestCache.size > GET_CACHE_MAX_ENTRIES) {
    const oldestKey = getRequestCache.keys().next().value
    if (oldestKey === undefined) {
      break
    }
    getRequestCache.delete(oldestKey)
  }
}

function fetchJson<T>(
  baseUrl: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const cacheKey = method === 'GET' ? `${baseUrl}${path}` : null
  const now = Date.now()

  if (cacheKey) {
    const cached = getRequestCache.get(cacheKey)
    if (cached && cached.expiresAt > now) {
      getRequestCache.delete(cacheKey)
      getRequestCache.set(cacheKey, cached)
      return cached.promise as Promise<T>
    }
    if (cached) {
      getRequestCache.delete(cacheKey)
    }
  }

  const request = fetch(`${baseUrl}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  }).then(async (response) => {
    if (!response.ok) {
      const body = await response.text()
      if (body) {
        let message = body
        try {
          const parsed = JSON.parse(body) as { detail?: string }
          message = parsed.detail || body
        } catch {
          message = body
        }
        throw new Error(message)
      }

      throw new Error(`Request failed: ${response.status}`)
    }

    return (await response.json()) as T
  })

  if (cacheKey) {
    getRequestCache.set(cacheKey, {
      expiresAt: now + GET_CACHE_TTL_MS,
      promise: request,
    })
    trimGetRequestCache()
    request.catch(() => {
      getRequestCache.delete(cacheKey)
    })
    return request
  }

  return request.then((value) => {
    getRequestCache.clear()
    return value
  })
}

function buildQuery(filters: Record<string, string | undefined>) {
  const searchParams = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value) {
      searchParams.set(key, value)
    }
  })
  const queryString = searchParams.toString()
  return queryString ? `?${queryString}` : ''
}

export function getWorkspaceSummary() {
  return fetchJson<PortfolioWorkspaceSummary>(API_BASE_URL, '/api/workspace/summary')
}

export function getWorkspaceSummaryForPortfolio(portfolioId: string) {
  return fetchJson<PortfolioWorkspaceSummary>(
    API_BASE_URL,
    `/api/workspace/summary?portfolio_id=${encodeURIComponent(portfolioId)}`,
  )
}

export function getHoldingsWorkspace(
  portfolioId?: string,
  filters: HoldingsWorkspaceFilters = {},
) {
  const query = buildQuery({
    portfolio_id: portfolioId,
    as_of_date: filters.as_of_date,
    include_details: filters.include_details ? 'true' : undefined,
  })
  return fetchJson<HoldingsWorkspaceResponse>(API_BASE_URL, `/api/workspace/holdings${query}`)
}

export function getPortfolioInstrumentHoldingProjection(
  portfolioId: string,
  instrumentId: string,
  filters: Pick<HoldingsWorkspaceFilters, 'as_of_date'> = {},
) {
  const query = buildQuery({
    portfolio_id: portfolioId,
    instrument_id: instrumentId,
    as_of_date: filters.as_of_date,
  })
  return fetchJson<PortfolioInstrumentHoldingProjectionResponse>(
    API_BASE_URL,
    `/api/workspace/holdings/instrument${query}`,
  )
}

export function getPortfolios() {
  return fetchJson<PortfolioEntryRecord[]>(API_BASE_URL, '/api/portfolios')
}

export function createPortfolio(payload: PortfolioCreatePayload) {
  return fetchJson<PortfolioEntryRecord>(API_BASE_URL, '/api/portfolios', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function copyPortfolio(portfolioId: string) {
  return fetchJson<PortfolioEntryRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/copy`, {
    method: 'POST',
  })
}

export type PortfolioRiskPolicyUpdatePayload = {
  covariance_model_id: PortfolioRiskCovarianceModel
  lookback_days: number
  calculation_frequency: PortfolioRiskCalculationFrequency
  missing_return_policy: PortfolioResearchMissingReturnPolicy
  contribution_mode: PortfolioRiskContributionMode
}

export function getPortfolioRiskPolicy(portfolioId: string) {
  return fetchJson<PortfolioRiskPolicyRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/risk-policy`)
}

export function updatePortfolioRiskPolicy(portfolioId: string, payload: PortfolioRiskPolicyUpdatePayload) {
  return fetchJson<PortfolioRiskPolicyRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/risk-policy`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deletePortfolio(portfolioId: string) {
  return fetchJson<{ portfolio_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}`,
    {
      method: 'DELETE',
    },
  )
}

export function reorderPortfolios(portfolioIds: string[]) {
  return fetchJson<PortfolioEntryRecord[]>(API_BASE_URL, '/api/portfolios/reorder', {
    method: 'POST',
    body: JSON.stringify({ portfolio_ids: portfolioIds }),
  })
}

export function getPortfolioTableViewStore<TStore>(
  portfolioId: string,
  viewScope: PortfolioTableViewScope,
) {
  return fetchJson<PortfolioTableViewStoreResponse<TStore>>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/table-views/${viewScope}`,
  )
}

export function savePortfolioTableViewStore<TStore>(
  portfolioId: string,
  viewScope: PortfolioTableViewScope,
  store: TStore,
) {
  return fetchJson<PortfolioTableViewStoreResponse<TStore>>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/table-views/${viewScope}`,
    {
      method: 'PUT',
      body: JSON.stringify({ store }),
    },
  )
}

export function getPortfolioAccounts(portfolioId: string) {
  return fetchJson<PortfolioAccountsResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/accounts`)
}

export function createPortfolioAccount(portfolioId: string, payload: PortfolioAccountCreatePayload) {
  return fetchJson<PortfolioAccountRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/accounts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updatePortfolioAccount(
  portfolioId: string,
  accountId: string,
  payload: PortfolioAccountUpdatePayload,
) {
  return fetchJson<PortfolioAccountRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/accounts/${encodeURIComponent(accountId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  )
}

export function getPortfolioAccountsWorkspace(portfolioId: string, accountId?: string) {
  const query = accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''
  return fetchJson<PortfolioAccountsWorkspaceResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/accounts/workspace${query}`,
  )
}

export function getPortfolioTransactionsWorkspace(
  portfolioId: string,
  filters: PortfolioTransactionFilters & { transaction_id?: string } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioTransactionWorkspaceResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/workspace${query}`,
  )
}

export function getPortfolioTransactionPositionPreview(
  portfolioId: string,
  filters: {
    account_id: string
    instrument_id: string
    as_of_date: string
    trade_time?: string
    exclude_transaction_id?: string
  },
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioTransactionPositionPreviewResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/position-preview${query}`,
  )
}

export function getPortfolioPositions(portfolioId: string) {
  return fetchJson<PortfolioPositionListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/positions`,
  )
}

export function getPortfolioPositionLots(
  portfolioId: string,
  filters: PortfolioPositionLotFilters = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioPositionLotListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/position-lots${query}`,
  )
}

export function getPortfolioFxRates(portfolioId: string) {
  return fetchJson<PortfolioSharedFxRatesResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/fx-rates`,
  )
}

export function getTransactionLedgerPostings(portfolioId: string, transactionId: string) {
  return fetchJson<PortfolioLedgerPostingListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}/ledger-postings`,
  )
}

export function getPortfolioTransactions(portfolioId: string, filters: PortfolioTransactionFilters = {}) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioTransactionListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions${query}`,
  )
}

export function getPortfolioTransactionExecutionQuote(
  portfolioId: string,
  instrumentId: string,
  asOfDate: string,
) {
  const query = buildQuery({ instrument_id: instrumentId, as_of_date: asOfDate })
  return fetchJson<PortfolioTransactionExecutionQuoteResponse>(
    API_BASE_URL,
    `/api/portfolios/${encodeURIComponent(portfolioId)}/transactions/execution-quote${query}`,
  )
}

export function getPortfolioInstrumentPriceChart(
  portfolioId: string,
  instrumentId: string,
  filters: {
    as_of_date?: string
    range?: PortfolioInstrumentChartRangeKey
  } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioInstrumentPriceChartResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/instruments/${instrumentId}/price-chart${query}`,
  )
}

export function getPortfolioPerformance(portfolioId: string, filters: PortfolioPerformanceFilters = {}) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioPerformanceResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/performance${query}`)
}

export function getPortfolioPerformanceCalculation(
  portfolioId: string,
  filters: PortfolioPerformanceFilters = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioPerformanceCalculationResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/performance/calculation${query}`,
  )
}

export function getPortfolioPerformanceCalculationGroups(
  portfolioId: string,
  filters: PortfolioPerformanceFilters & { axis?: PortfolioContributionAxis; taxonomy_id?: string } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioPerformanceCalculationGroupsResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/performance/calculation/groups${query}`,
  )
}

export function getPortfolioPerformanceContribution(
  portfolioId: string,
  filters: PortfolioPerformanceFilters & { axis?: PortfolioContributionAxis; taxonomy_id?: string; group_key?: string } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioContributionReportResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/performance/contribution${query}`,
  )
}

export function getPortfolioPerformanceBoundaryHoldings(
  portfolioId: string,
  filters: PortfolioPerformanceFilters = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioPeriodBoundaryHoldingsResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/performance/boundary-holdings${query}`,
  )
}

export function getPortfolioTaxonomyCatalog(
  portfolioId: string,
  filters: {
    include_market_profile?: boolean
    as_of_date?: string
  } = {},
) {
  const query = buildQuery({
    include_market_profile: filters.include_market_profile ? 'true' : undefined,
    as_of_date: filters.as_of_date,
  })
  return fetchJson<PortfolioTaxonomyCatalogResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/taxonomies${query}`)
}

export function updatePortfolioDefaultPlanningTaxonomy(
  portfolioId: string,
  payload: PortfolioDefaultPlanningTaxonomyUpdatePayload,
) {
  return fetchJson<PortfolioDefaultPlanningTaxonomyResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/default-planning`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function createPortfolioInstrumentUniverseRecord(
  portfolioId: string,
  payload: PortfolioInstrumentUniverseCreatePayload,
) {
  return fetchJson<PortfolioInstrumentUniverseRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/instrument-universe`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function deletePortfolioInstrumentUniverseRecord(portfolioId: string, instrumentId: string) {
  return fetchJson<{ portfolio_id: string; instrument_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/instrument-universe/${encodeURIComponent(instrumentId)}`,
    {
      method: 'DELETE',
    },
  )
}

export function getPortfolioResearchWorkbench(
  portfolioId: string,
  selectedRunId?: string,
  options: { include_details?: boolean } = {},
) {
  const query = buildQuery({
    selected_run_id: selectedRunId,
    include_details: options.include_details ? 'true' : undefined,
  })
  return fetchJson<PortfolioResearchWorkbenchResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/workbench${query}`,
  )
}

export function getPortfolioResearchRun(portfolioId: string, researchRunId: string) {
  return fetchJson<PortfolioResearchRunRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/runs/${encodeURIComponent(researchRunId)}`,
  )
}

export function updatePortfolioResearchInstrumentEligibility(
  portfolioId: string,
  instrumentId: string,
  payload: PortfolioResearchInstrumentEligibilityUpdatePayload,
) {
  return fetchJson<PortfolioInstrumentUniverseRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/instruments/${encodeURIComponent(instrumentId)}/eligibility`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function updatePortfolioResearchSettings(
  portfolioId: string,
  payload: PortfolioResearchSettingsUpdatePayload,
) {
  return fetchJson<PortfolioResearchSettingsRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/settings`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function createPortfolioResearchRun(
  portfolioId: string,
  payload: PortfolioResearchRunCreatePayload = {},
) {
  return fetchJson<PortfolioResearchRunRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/runs`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function getPortfolioResearchBacktestBenchmarkComparison(
  portfolioId: string,
  researchRunId: string,
  benchmarkInstrumentId: string,
) {
  const query = buildQuery({ benchmark_instrument_id: benchmarkInstrumentId })
  return fetchJson<PortfolioResearchBacktestBenchmarkComparisonResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/runs/${researchRunId}/benchmark-comparison${query}`,
  )
}

export function getPortfolioResearchArtifactContent(portfolioId: string, path: string) {
  const query = buildQuery({ path })
  return fetchJson<PortfolioResearchArtifactContentResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/artifacts/content${query}`,
  )
}

export function createPortfolioTaxonomy(
  portfolioId: string,
  payload: PortfolioTaxonomyCreatePayload,
) {
  return fetchJson<PortfolioTaxonomyRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/taxonomies`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function deletePortfolioTaxonomy(portfolioId: string, taxonomyId: string, effectiveFrom: string) {
  const query = buildQuery({ effective_from: effectiveFrom })
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}${query}`,
    {
      method: 'DELETE',
    },
  )
}

export function updatePortfolioTaxonomy(
  portfolioId: string,
  taxonomyId: string,
  payload: PortfolioTaxonomyUpdatePayload,
) {
  return fetchJson<PortfolioTaxonomyRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function createPortfolioTaxonomyNode(
  portfolioId: string,
  taxonomyId: string,
  payload: PortfolioTaxonomyNodeCreatePayload,
) {
  return fetchJson<PortfolioTaxonomyNodeRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/nodes`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function updatePortfolioTaxonomyNode(
  portfolioId: string,
  taxonomyId: string,
  taxonomyNodeId: string,
  payload: PortfolioTaxonomyNodeUpdatePayload,
) {
  return fetchJson<PortfolioTaxonomyNodeRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/nodes/${taxonomyNodeId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  )
}

export function deletePortfolioTaxonomyNode(
  portfolioId: string,
  taxonomyId: string,
  taxonomyNodeId: string,
  effectiveFrom: string,
) {
  const query = buildQuery({ effective_from: effectiveFrom })
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; taxonomy_node_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/nodes/${taxonomyNodeId}${query}`,
    {
      method: 'DELETE',
    },
  )
}

export function createPortfolioTaxonomyAssignment(
  portfolioId: string,
  taxonomyId: string,
  payload: PortfolioTaxonomyAssignmentCreatePayload,
) {
  return fetchJson<PortfolioTaxonomyAssignmentRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/assignments`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function updatePortfolioTaxonomyAssignment(
  portfolioId: string,
  taxonomyId: string,
  assignmentId: string,
  payload: PortfolioTaxonomyAssignmentUpdatePayload,
) {
  return fetchJson<PortfolioTaxonomyAssignmentRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/assignments/${assignmentId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  )
}

export function deletePortfolioTaxonomyAssignment(
  portfolioId: string,
  taxonomyId: string,
  assignmentId: string,
  effectiveFrom: string,
) {
  const query = buildQuery({ effective_from: effectiveFrom })
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; assignment_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/assignments/${assignmentId}${query}`,
    {
      method: 'DELETE',
    },
  )
}

export function createPortfolioTargetSet(
  portfolioId: string,
  taxonomyId: string,
  payload: PortfolioTargetSetCreatePayload,
) {
  return fetchJson<PortfolioTargetSetRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/target-sets`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function updatePortfolioTargetSet(
  portfolioId: string,
  taxonomyId: string,
  targetSetId: string,
  payload: PortfolioTargetSetUpdatePayload,
) {
  return fetchJson<PortfolioTargetSetRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/target-sets/${targetSetId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  )
}

export function deletePortfolioTargetSet(
  portfolioId: string,
  taxonomyId: string,
  targetSetId: string,
  effectiveFrom: string,
) {
  const query = buildQuery({ effective_from: effectiveFrom })
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; target_set_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/target-sets/${targetSetId}${query}`,
    {
      method: 'DELETE',
    },
  )
}

export function replacePortfolioAnalyticsScopePolicy(
  portfolioId: string,
  taxonomyId: string,
  taxonomyNodeId: string,
  payload: PortfolioAnalyticsScopePolicyUpsertPayload,
) {
  return fetchJson<PortfolioAnalyticsScopePolicyRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/analytics-scope-policies/${encodeURIComponent(taxonomyNodeId)}`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function createPortfolioTransaction(
  portfolioId: string,
  payload: PortfolioTransactionCreatePayload,
  idempotencyKey: string,
) {
  return fetchJson<PortfolioTransactionRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions`,
    {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(payload),
    },
  )
}

export function portfolioTransactionCsvDownloadUrl(portfolioId: string) {
  return `${API_BASE_URL}/api/portfolios/${encodeURIComponent(portfolioId)}/transactions.csv`
}

export function portfolioTransactionCsvTemplateUrl(portfolioId: string) {
  return `${API_BASE_URL}/api/portfolios/${encodeURIComponent(portfolioId)}/transactions/csv-template`
}

export function previewPortfolioTransactionCsv(
  portfolioId: string,
  csvText: string,
  defaultSourceSystem = 'portfolio_csv_upload',
) {
  return fetchJson<PortfolioTransactionCsvPreviewResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/csv/preview`,
    {
      method: 'POST',
      body: JSON.stringify({
        csv_text: csvText,
        default_source_system: defaultSourceSystem,
      }),
    },
  )
}

export function importPortfolioTransactionCsv(
  portfolioId: string,
  csvText: string,
  previewDigest: string,
  idempotencyKey: string,
  defaultSourceSystem = 'portfolio_csv_upload',
) {
  return fetchJson<PortfolioTransactionCsvImportResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/csv/import`,
    {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        csv_text: csvText,
        default_source_system: defaultSourceSystem,
        preview_digest: previewDigest,
      }),
    },
  )
}

export function getPortfolioInstrumentEventTasks(
  portfolioId: string,
  attentionOnly = false,
) {
  const query = attentionOnly ? '?attention_only=true' : ''
  return fetchJson<PortfolioInstrumentEventTaskListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/instrument-event-tasks${query}`,
  )
}

export function reviewPortfolioInstrumentEventTask(
  portfolioId: string,
  instrumentEventTaskId: string,
  payload: PortfolioInstrumentEventTaskReviewPayload,
) {
  return fetchJson<PortfolioInstrumentEventTaskRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/instrument-event-tasks/${instrumentEventTaskId}/reviews`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function updatePortfolioTransaction(
  portfolioId: string,
  transactionId: string,
  payload: PortfolioTransactionUpdatePayload,
) {
  return fetchJson<PortfolioTransactionRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function deletePortfolioTransaction(
  portfolioId: string,
  transactionId: string,
  expectedRowVersions: Record<string, number>,
) {
  return fetchJson<PortfolioTransactionDeleteResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}`,
    {
      method: 'DELETE',
      body: JSON.stringify({ expected_row_versions: expectedRowVersions }),
    },
  )
}

export function createPortfolioInternalTransfer(
  portfolioId: string,
  payload: PortfolioInternalTransferCreatePayload,
  idempotencyKey: string,
) {
  return fetchJson<PortfolioTransactionBatchResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/internal-transfer`,
    {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(payload),
    },
  )
}

export function getPortfolioInstruments(portfolioId: string) {
  return fetchJson<RawPortfolioSharedInstrumentsResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/instruments`).then(
    (response) => ({
      portfolio_id: response.portfolio_id,
      instruments: response.instruments.map((instrument) => ({
        ...instrument.instrument_core,
        coverage_state: instrument.coverage_state,
        latest_market_data: instrument.latest_market_data,
        quote_selection_policy: instrument.quote_selection_policy,
      })),
    }),
  )
}
