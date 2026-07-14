import type {
  InstrumentCore,
  InstrumentIdentifier,
  DataStatus,
  MetricFamily,
  QuoteBasis,
  QuoteSelectionPolicy,
} from '../../../../../packages/instrument-core/ts/src'

export type { InstrumentCore, InstrumentIdentifier } from '../../../../../packages/instrument-core/ts/src'

export type WorkspaceSection = {
  label: string
  href: string
  status: string
}

export type PortfolioDailyPublicationMetadata = {
  publication_id: string
  run_id: string
  manifest_id: string
  methodology_version: string
  published_fencing_token: number
  published_at: string
  calculated_at: string
  requested_as_of: string
  effective_as_of: string
  output_range_start: string
  output_range_end: string
  output_schema_version: string
  canonical_output_hash: string
  captured_generation: number
  current_generation: number
  stale: boolean
  pending: boolean
  pending_generation: number | null
  pending_run_id: string | null
  pending_run_status: string | null
  pending_intent_id: string | null
  pending_intent_status: 'pending' | 'materialized' | null
  coverage_state: 'complete' | 'partial' | 'unavailable'
  reason_codes: string[]
}

export type PortfolioDailyPublishedAttributionAxis =
  | 'portfolio'
  | 'account'
  | 'instrument'
  | 'currency'
  | 'taxonomy'

export type PortfolioDailyPublishedCalendarFrequency = 'monthly' | 'weekly'
export type PortfolioDailyPublishedStatus = 'ready' | 'unavailable'
export type PortfolioDailyPublishedCoverageState = 'complete' | 'partial' | 'unavailable'

export type PortfolioDailyPublishedDecimalMetric = {
  method50: string
  published: string
  rounding_adjustment_exact: string
}

export type PortfolioDailyPublishedPerformanceSummary = {
  status: PortfolioDailyPublishedStatus
  start_date: string | null
  end_date: string | null
  effective_return_start_date: string | null
  effective_return_end_date: string | null
  elapsed_days: number | null
  snapshot_count: number
  measured_nav_count: number
  measured_return_count: number
  cumulative_twr: PortfolioDailyPublishedDecimalMetric | null
  annualized_twr: PortfolioDailyPublishedDecimalMetric | null
  current_drawdown: PortfolioDailyPublishedDecimalMetric | null
  max_drawdown: PortfolioDailyPublishedDecimalMetric | null
  return_chain_status: string | null
  coverage_state: PortfolioDailyPublishedCoverageState
  reason_codes: string[]
}

export type PortfolioDailyPublishedPerformanceStatistics = {
  status: PortfolioDailyPublishedStatus
  method_version: string
  reason_codes: string[]
  frequency: PortfolioDailyPublishedCalendarFrequency
  observation_count: number
  excluded_partial_bucket_count: number
  excluded_unavailable_bucket_count: number
  periods_per_year: PortfolioDailyPublishedDecimalMetric | null
  mean_period_return: PortfolioDailyPublishedDecimalMetric | null
  annualized_arithmetic_mean: PortfolioDailyPublishedDecimalMetric | null
  annualized_volatility: PortfolioDailyPublishedDecimalMetric | null
  annualized_downside_deviation: PortfolioDailyPublishedDecimalMetric | null
}

export type PortfolioDailyPublishedXirrResult = {
  status: PortfolioDailyPublishedStatus
  method_version: string
  reason_codes: string[]
  cash_flow_count: number
  annualized_headline_eligible: boolean
  rate: PortfolioDailyPublishedDecimalMetric | null
  xnpv_residual_exact: string | null
}

export type PortfolioDailyPublishedRebasedWealthPoint = {
  as_of_date: string
  wealth_index_method50: string
  peak_wealth_index_method50: string
  drawdown_method50: string
  wealth_chain_rounding_adjustment_exact: string | null
  return_chain_status: string
}

export type PortfolioDailyPublishedSnapshotRecord = {
  as_of_date: string
  base_currency: string
  measured_nav: boolean
  measured_position_market_value: boolean
  measured_book_pnl: boolean
  measured_return: boolean
  measured_external_flows: boolean
  unavailable_component_count: number
  opening_nav: string | null
  closing_nav: string | null
  position_market_value: string | null
  settled_cash: string | null
  pending_receivable: string | null
  pending_payable: string | null
  accrual_receivable: string | null
  accrual_payable: string | null
  external_flow_in: string | null
  external_flow_out: string | null
  economic_pnl: string | null
  realized_pnl_daily: string | null
  unrealized_pnl_beginning: string | null
  unrealized_pnl_ending: string | null
  unrealized_pnl_change: string | null
  gross_income_daily: string | null
  return_of_capital_daily: string | null
  capitalized_fee_daily: string | null
  capitalized_tax_daily: string | null
  expensed_fee_daily: string | null
  expensed_tax_daily: string | null
  local_price_effect_daily: string | null
  position_fx_effect_daily: string | null
  cash_fx_effect_daily: string | null
  pending_fx_effect_daily: string | null
  accrual_fx_effect_daily: string | null
  fx_conversion_effect_daily: string | null
  subperiod_twr_method50: string | null
  subperiod_twr_published: string | null
  cumulative_twr_method50: string | null
  cumulative_twr_published: string | null
  wealth_index_method50: string | null
  wealth_index_published: string | null
  peak_wealth_index_method50: string | null
  peak_wealth_index_published: string | null
  drawdown_method50: string | null
  drawdown_published: string | null
  wealth_chain_rounding_adjustment_exact: string | null
  reliable_anchor_date: string | null
  return_period_start_date: string | null
  return_period_end_date: string | null
  return_period_day_count: number | null
  calculation_status: string
  return_chain_status: string
  nav_coverage_state: PortfolioDailyPublishedCoverageState
  nav_reason_codes: string[]
  book_pnl_coverage_state: PortfolioDailyPublishedCoverageState
  book_pnl_reason_codes: string[]
  return_coverage_state: PortfolioDailyPublishedCoverageState
  return_reason_codes: string[]
  flow_coverage_state: PortfolioDailyPublishedCoverageState
  flow_reason_codes: string[]
  valuation_endpoint_status: 'fresh' | 'carry_forward' | 'stale' | 'unavailable'
  valuation_reason_codes: string[]
}

export type PortfolioDailyPublishedAttributionGroup = {
  axis: PortfolioDailyPublishedAttributionAxis
  group_key: string
  group_label: string
  opening_nav_exact: string
  closing_nav_exact: string
  external_flow_in_exact: string
  external_flow_out_exact: string
  internal_flow_in_exact: string
  internal_flow_out_exact: string
  economic_pnl_exact: string
  linked_contribution_method50: string
  linking_adjustment_exact: string
  linked_contribution_effective: string
  closure_residual_exact: string
}

export type PortfolioDailyPublishedAttributionSummary = {
  status: PortfolioDailyPublishedStatus
  method_version: string
  axis: PortfolioDailyPublishedAttributionAxis
  effective_start_date: string | null
  effective_end_date: string | null
  observation_count: number
  cumulative_twr_method50: string | null
  total_linked_contribution_effective: string | null
  total_linking_adjustment_exact: string | null
  closure_residual_exact: string | null
  reason_codes: string[]
}

export type PortfolioDailyPublishedReturnCalendarBucket = {
  bucket_key: string
  frequency: PortfolioDailyPublishedCalendarFrequency
  calendar_start_date: string
  calendar_end_date: string
  coverage_state: PortfolioDailyPublishedCoverageState
  coverage_reason_codes: string[]
  status: PortfolioDailyPublishedStatus
  effective_return_start_date: string | null
  effective_return_end_date: string | null
  observation_count: number
  cumulative_twr: PortfolioDailyPublishedDecimalMetric | null
  current_drawdown: PortfolioDailyPublishedDecimalMetric | null
  max_drawdown: PortfolioDailyPublishedDecimalMetric | null
  reason_codes: string[]
}

export type PortfolioDailyPublishedAttributionCalendarBucket = {
  bucket_key: string
  frequency: PortfolioDailyPublishedCalendarFrequency
  calendar_start_date: string
  calendar_end_date: string
  coverage_state: PortfolioDailyPublishedCoverageState
  coverage_reason_codes: string[]
  summary: PortfolioDailyPublishedAttributionSummary
  groups: PortfolioDailyPublishedAttributionGroup[]
}

export type PortfolioDailyPublishedPerformanceReportResponse = {
  portfolio_id: string
  publication: PortfolioDailyPublicationMetadata
  base_currency: string
  valuation_timezone: string
  axis: PortfolioDailyPublishedAttributionAxis
  selected_group_key: string | null
  frequency: PortfolioDailyPublishedCalendarFrequency
  performance: PortfolioDailyPublishedPerformanceSummary
  statistics: PortfolioDailyPublishedPerformanceStatistics
  xirr: PortfolioDailyPublishedXirrResult
  portfolio_bridge: PortfolioDailyPublishedAttributionGroup | null
  rebased_wealth_series: PortfolioDailyPublishedRebasedWealthPoint[]
  daily_series: PortfolioDailyPublishedSnapshotRecord[]
  attribution: PortfolioDailyPublishedAttributionSummary
  attribution_groups: PortfolioDailyPublishedAttributionGroup[]
  return_calendar: PortfolioDailyPublishedReturnCalendarBucket[]
  attribution_calendar: PortfolioDailyPublishedAttributionCalendarBucket[]
}

export type TaxonomyAssignmentScope = 'instrument' | 'account' | 'cash_bucket'
export type PortfolioOperatingProfile = 'standard_taxonomy' | 'external_etf_rotation'

export type PortfolioWorkspaceSummary = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  operating_profile: PortfolioOperatingProfile
  as_of_date: string
  nav: number | null
  nav_exact: string | null
  day_change_value: number | null
  day_change_value_exact: string | null
  day_change_pct: number | null
  day_change_pct_method50: string | null
  day_change_pct_published: string | null
  default_planning_taxonomy_id?: string | null
  toolbar_label: string
  badges: string[]
  sections: WorkspaceSection[]
  publication?: PortfolioDailyPublicationMetadata | null
}

export type PortfolioEntryRecord = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  operating_profile: PortfolioOperatingProfile
  as_of_date: string | null
  nav: number | null
  nav_exact: string | null
  day_change_value: number | null
  day_change_value_exact: string | null
  day_change_pct: number | null
  day_change_pct_method50: string | null
  day_change_pct_published: string | null
  securities_count: number
  sort_order: number
  lifecycle_status: 'active' | 'archived'
  default_planning_taxonomy_id?: string | null
  calculation_status: 'not_ready' | 'published' | 'stale'
  publication?: PortfolioDailyPublicationMetadata | null
}

export type PortfolioCreatePayload = {
  name: string
  base_currency: (typeof SUPPORTED_PORTFOLIO_CURRENCIES)[number]
  operating_profile: PortfolioOperatingProfile
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
  metric_family: string | null
  currency: string
  points: PortfolioInstrumentPriceChartPoint[]
  summary: PortfolioInstrumentPriceChartSummary
}

export type PortfolioTransactionExecutionQuoteResponse = {
  portfolio_id: string
  instrument_id: string
  requested_as_of_date: string
  selection_role: 'trading' | 'valuation' | null
  value: string | null
  suggested_transaction_price: string | null
  suggested_transaction_price_scale: 12
  suggested_transaction_price_rounding: 'ROUND_HALF_EVEN'
  suggested_transaction_price_was_rounded: boolean
  quote_date: string | null
  quote_basis: QuoteBasis | null
  metric_family: MetricFamily | null
  currency: string
  source_ref: string | null
  source_status: 'complete' | 'partial' | 'rejected' | 'withdrawn' | null
  status: DataStatus
  resolution_status: 'resolved' | 'unavailable'
  freshness_status: 'current' | 'late' | 'missing'
  ingestion_status: 'current' | 'unknown'
  reliability_status: 'reliable' | 'qualified' | 'unavailable'
  reason_codes: string[]
  stale: boolean
  carry_forward: boolean
  age_days: number | null
  quote_selection_policy_version: string | null
  quote_selection_policy_revision: string | null
  quote_series_id: string | null
  observation_id: string | null
  revision_id: string | null
  revision_number: number | null
  payload_hash: string | null
  source_published_at: string | null
  ingested_at: string | null
  calculation_dependency: Record<string, unknown>
}

export type PortfolioPerformanceHistoryReliability = {
  start_date: string | null
  end_date: string | null
  elapsed_days: number | null
  calendar_span_days: number | null
  minimum_history_days: number
  annualized_return_eligible: boolean
  annualized_return_reason_codes: Array<
    'performance_history_window_unavailable' | 'annualized_return_history_below_minimum'
  >
  sample_label: string
  annualization_message: string | null
}
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
  missing_return_policy: PortfolioRiskMissingReturnPolicy
  contribution_mode: PortfolioRiskContributionMode
  parameters: Record<string, unknown>
  parameters_by_frequency: Record<string, Record<string, unknown>>
}

export type PortfolioRiskWorkspaceError = {
  message: string
  reason_codes: string[]
  dependency?: Record<string, unknown> | null
}

export type PortfolioRiskWorkspacePoint = {
  date: string
  value: number
}

export type PortfolioRiskWorkspaceTargetGapRow = {
  id: string
  label: string
  current: number | null
  saa_target: number | null
  taa_target: number | null
  saa_gap: number | null
  taa_gap: number | null
  current_value_base: number | null
}

export type PortfolioRiskWorkspaceResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  operating_profile: PortfolioOperatingProfile
  as_of_date: string
  status: 'ready' | 'partial' | 'unavailable'
  planning_taxonomy: { taxonomy_id: string; name: string } | null
  risk_policy: PortfolioRiskPolicyRecord
  frequency_profile: {
    requested_frequency?: PortfolioRiskCalculationFrequency
    resolved_frequency: PortfolioCalculationFrequency
    status_label: string
    [key: string]: unknown
  }
  matrix_scope_options: Array<{
    value: string
    label: string
    kind: 'instrument' | 'taxonomy'
  }>
  rolling: {
    status: 'ready' | 'unavailable'
    errors: PortfolioRiskWorkspaceError[]
    lookback_days: number
    model_id: PortfolioRiskCovarianceModel
    portfolio_volatility_points: PortfolioRiskWorkspacePoint[]
    portfolio_sharpe_points: PortfolioRiskWorkspacePoint[]
    benchmark_volatility_points: PortfolioRiskWorkspacePoint[]
    benchmark_sharpe_points: PortfolioRiskWorkspacePoint[]
  }
  matrix: {
    status: 'ready' | 'unavailable'
    errors: PortfolioRiskWorkspaceError[]
    scope: string
    as_of_date: string | null
    available_as_of_dates: string[]
    groups: Array<{
      key: string
      label: string
      observation_count: number
      weight: number | null
    }>
    cells: Array<Array<{ value: number | null; observation_count: number }>>
    max_abs: number
    coverage: Record<string, unknown> | null
  }
  risk_contribution: {
    status: 'ready' | 'unavailable'
    errors: PortfolioRiskWorkspaceError[]
    rows: Array<{
      group_key: string
      group_label: string
      weight: number
      annualized_volatility: number
      risk_share: number
      contribution_to_variance: number
      observation_count: number
    }>
    portfolio_variance: number | null
    portfolio_volatility: number | null
    observation_count: number | null
  }
  allocation_policy_drift: {
    status: 'ready' | 'unavailable' | 'not_applicable'
    errors: PortfolioRiskWorkspaceError[]
    weight_rows: PortfolioRiskWorkspaceTargetGapRow[]
    risk_rows: PortfolioRiskWorkspaceTargetGapRow[]
  }
  coverage: {
    market_data_role: 'total_return'
    instrument_count: number
    ready_instrument_count: number
    instruments: Array<{
      instrument_id: string
      label: string
      scopes: string[]
      status: 'ready' | 'unavailable'
      observation_count: number
      first_observation_date: string | null
      last_observation_date: string | null
      warnings: string[]
      errors: PortfolioRiskWorkspaceError[]
    }>
  }
  calculation_lineage: Record<string, unknown>
  data_lineage: Record<string, unknown>
}

export type PortfolioForwardRiskSummary = {
  status: 'ok' | 'unavailable' | string
  errors: string[]
  risk_model?: PortfolioRiskPolicyRecord | null
  portfolio_variance?: number | null
  portfolio_volatility?: number | null
  observation_count?: number | null
}

export type PortfolioHoldingRow = {
  line_id: string
  instrument_core: InstrumentCore
  quantity: number
  last_price: number | null
  quote_as_of_date?: string | null
  quote_metric_family?: string | null
  quote_basis?: string | null
  quote_source_ref?: string | null
  quote_status?: string | null
  quote_resolution_status?: 'resolved' | 'unavailable' | null
  quote_source_status?: 'complete' | 'partial' | 'rejected' | 'withdrawn' | null
  quote_freshness_status?: 'current' | 'late' | 'missing' | null
  quote_ingestion_status?: 'current' | 'unknown' | null
  quote_reliability_status?: 'reliable' | 'qualified' | 'unavailable' | null
  quote_reason_codes?: string[]
  quote_canonical_instrument_type?: string | null
  quote_consumer_freshness_policy_type?: string | null
  quote_consumer_freshness_policy_version?: string | null
  quote_carry_forward?: boolean
  quote_age_days?: number | null
  quote_series_id?: string | null
  quote_observation_id?: string | null
  quote_revision_id?: string | null
  quote_revision_number?: number | null
  quote_payload_hash?: string | null
  quote_selection_policy_version?: string | null
  quote_selection_policy_revision?: string | null
  quote_calculation_dependency?: Record<string, unknown> | null
  quote_window_calculation_dependency?: Record<string, unknown> | null
  valuation_quote?: Record<string, unknown> | null
  market_value: number | null
  market_value_base?: number | null
  day_change_pct: number | null
  day_change_value: number | null
  day_change_value_base?: number | null
  portfolio_return_contribution?: number | null
  cost_basis_method?: 'fifo' | 'moving_average' | 'mixed' | string | null
  cost_basis: number | null
  cost_basis_base?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_base?: number | null
  unrealized_return?: number | null
  allocation: number | null
  price_chart_1m: SparklinePoint[]
  price_chart_3m: SparklinePoint[]
  price_chart_6m: SparklinePoint[]
  price_chart_1y: SparklinePoint[]
  instrument_trend_as_of_date?: string | null
  instrument_trend_basis?: string | null
  instrument_risk_frequency?: PortfolioCalculationFrequency | null
  instrument_return_1w?: number | null
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
  exact_values?: {
    quantity: string
    last_price: string | null
    market_value: string | null
    market_value_base: string | null
    day_change_pct: string | null
    day_change_value: string | null
    day_change_value_base: string | null
    portfolio_return_contribution: string | null
    cost_basis: string | null
    cost_basis_base: string | null
    unrealized_pnl: string | null
    unrealized_pnl_base: string | null
    unrealized_return: string | null
    allocation: string | null
  }
}

export type HoldingsWorkspaceResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  view_label: string
  coverage_note: string
  quality_warnings: string[]
  sealed_display_config: {
    taxonomy: PortfolioSealedTaxonomyDisplayConfig
  }
  risk_basis?: {
    requested_frequency: 'auto' | PortfolioCalculationFrequency
    resolved_frequency: PortfolioCalculationFrequency
    default_frequency: PortfolioCalculationFrequency
    source_frequency_counts: Record<string, number>
    status_label: string
  }
  risk_policy?: PortfolioRiskPolicyRecord | null
  forward_risk?: PortfolioForwardRiskSummary | null
  summary_cards: HoldingsSummaryCard[]
  rows: PortfolioHoldingRow[]
  totals: {
    market_value: number | null
    day_change_pct: number | null
    day_change_value: number | null
    cost_basis: number | null
    unrealized_pnl_base?: number | null
    unrealized_return?: number | null
    allocation: number | null
    exact_values?: {
      market_value: string | null
      day_change_pct: string | null
      day_change_value: string | null
      cost_basis: string | null
      unrealized_pnl_base: string | null
      unrealized_return: string | null
      allocation: string | null
    }
  }
  publication: PortfolioDailyPublicationMetadata
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
  row: PortfolioInstrumentHoldingRow | null
}

export type HoldingsWorkspaceFilters = {
  as_of_date?: string
}

export type SharedMarketDataPoint = {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  source_ref?: string | null
  status: DataStatus
}

export type SharedInstrumentRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type: InstrumentCore['instrument_type']
  currency: string
  identifiers: InstrumentIdentifier[]
  latest_market_data: SharedMarketDataPoint[]
  quote_selection_policy?: QuoteSelectionPolicy
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
  quote_selection_policy?: QuoteSelectionPolicy
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
  rate: string
  as_of_date: string
  source_kind: string
  instrument_id?: string | null
  source_instrument_ids: string[]
  source_ref?: string | null
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

export type PortfolioSealedTaxonomyDisplayConfig = {
  default_planning_taxonomy_id: string | null
  taxonomies: PortfolioTaxonomyRecord[]
  taxonomy_nodes: PortfolioTaxonomyNodeRecord[]
  taxonomy_assignments: PortfolioTaxonomyAssignmentRecord[]
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
  } | null
  taxonomies: PortfolioTaxonomyRecord[]
  taxonomy_nodes: PortfolioTaxonomyNodeRecord[]
  taxonomy_assignments: PortfolioTaxonomyAssignmentRecord[]
  instrument_universe: PortfolioInstrumentUniverseRecord[]
  target_sets: PortfolioTargetSetRecord[]
  target_set_lines: PortfolioTargetSetLineRecord[]
  target_set_integrity_issues: PortfolioTargetSetIntegrityIssueRecord[]
}

export type PortfolioDefaultPlanningTaxonomyUpdatePayload = {
  taxonomy_id?: string | null
}

export type PortfolioDefaultPlanningTaxonomyResponse = {
  portfolio_id: string
  default_planning_taxonomy_id?: string | null
}

export type PortfolioInstrumentUniverseCreatePayload = {
  instrument_id: string
}

export type PortfolioAllocationResearchRunStatus = 'running' | 'completed' | 'failed'
export type PortfolioAllocationResearchTargetDimension = 'scope_default' | 'weight' | 'risk_budget'
export type PortfolioAllocationResearchCapitalMode = 'unit_notional' | 'fixed_gross' | 'target_volatility' | 'volatility_cap'
export type PortfolioAllocationResearchCalculationFrequency = 'auto' | 'daily' | 'weekly' | 'monthly'
export type PortfolioRiskMissingReturnPolicy = 'strict' | 'complete_case_drop'
export type PortfolioPolicyReplayRebalanceFrequency = '1w' | '1m' | '3m'
export type PortfolioAllocationResearchArtifactPreviewKind = 'text' | 'html' | 'binary'
export type PortfolioAllocationResearchAsOfMode = 'dynamic' | 'pinned'

export type PortfolioAllocationResearchPlanningTaxonomyOption = {
  taxonomy_id: string
  name: string
  taxonomy_type: string
  budgeting_level?: string | null
}

export type PortfolioAllocationResearchPlanningScopeOption = {
  taxonomy_node_id?: string | null
  label: string
  path: string
  depth: number
  default_target_dimension: 'weight' | 'risk_budget'
  has_children: boolean
}

export type PortfolioAllocationResearchSettingsRecord = {
  portfolio_id: string
  planning_taxonomy_id?: string | null
  planning_taxonomy_name?: string | null
  comparator_taxonomy_node_id?: string | null
  comparator_taxonomy_node_name?: string | null
  as_of_mode: PortfolioAllocationResearchAsOfMode
  as_of_date?: string | null
  pinned_as_of_date?: string | null
  lookback_days: number
  calculation_frequency: PortfolioAllocationResearchCalculationFrequency
  missing_return_policy: PortfolioRiskMissingReturnPolicy
  target_dimension: PortfolioAllocationResearchTargetDimension
  capital_mode: PortfolioAllocationResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  frozen_taxonomy_node_ids: string[]
  top_sleeve_weight_bounds: PortfolioAllocationResearchTopSleeveWeightBoundRecord[]
  policy_replay_rebalance_frequency: PortfolioPolicyReplayRebalanceFrequency
  policy_replay_benchmark_instrument_id?: string | null
  notes?: string | null
  updated_at?: string | null
}

export type PortfolioAllocationResearchSettingsUpdatePayload = {
  planning_taxonomy_id?: string | null
  comparator_taxonomy_node_id?: string | null
  as_of_mode: PortfolioAllocationResearchAsOfMode
  as_of_date?: string | null
  lookback_days: number
  calculation_frequency?: PortfolioAllocationResearchCalculationFrequency
  missing_return_policy?: PortfolioRiskMissingReturnPolicy
  covariance_model_id?: PortfolioRiskCovarianceModel
  contribution_mode?: PortfolioRiskContributionMode
  target_dimension?: PortfolioAllocationResearchTargetDimension
  capital_mode?: PortfolioAllocationResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  frozen_taxonomy_node_ids?: string[] | null
  top_sleeve_weight_bounds?: PortfolioAllocationResearchTopSleeveWeightBoundRecord[] | null
  policy_replay_rebalance_frequency?: PortfolioPolicyReplayRebalanceFrequency
  policy_replay_benchmark_instrument_id?: string | null
  notes?: string | null
}

export type PortfolioAllocationResearchTopSleeveWeightBoundRecord = {
  taxonomy_node_id: string
  min_weight?: number | null
  max_weight?: number | null
}

export type PortfolioAllocationResearchContextSignalRecord = {
  label: string
  value: string
  tone: string
}

export type PortfolioAllocationResearchCalculationFrequencyProfile = {
  requested_frequency: PortfolioAllocationResearchCalculationFrequency
  resolved_frequency: Exclude<PortfolioAllocationResearchCalculationFrequency, 'auto'>
  default_frequency: Exclude<PortfolioAllocationResearchCalculationFrequency, 'auto'>
  source_frequency_counts: Record<string, number>
  options: Array<{
    frequency: Exclude<PortfolioAllocationResearchCalculationFrequency, 'auto'>
    label: string
    available: boolean
    reason?: string | null
  }>
  status_label: string
}

export type PortfolioAllocationResearchHoldingSnapshotRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type?: string | null
  allocation?: number | null
  market_value_base?: number | null
  cost_basis_base?: number | null
  base_currency: string
  price?: number | null
}

export type PortfolioAllocationResearchPlanningGroupSnapshotRecord = {
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

export type PortfolioAllocationResearchFindingRecord = {
  title: string
  detail: string
}

export type PortfolioAllocationResearchCurrentContextSummary = {
  period_return?: number | null
  annualized_volatility?: number | null
  current_drawdown?: number | null
  max_drawdown?: number | null
  start_nav?: number | null
  end_nav?: number | null
}

export type PortfolioAllocationResearchPlanningTargetSummary = {
  root_saa_configured: boolean
  root_taa_configured: boolean
  scoped_target_set_count: number
}

export type PortfolioAllocationResearchCurrentContextRecord = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  lookback_start: string
  lookback_end: string
  nav?: number | null
  holdings_count: number
  planning_group_count: number
  summary: PortfolioAllocationResearchCurrentContextSummary
  planning_target_summary?: PortfolioAllocationResearchPlanningTargetSummary | null
  quality_warnings: string[]
  top_holdings: PortfolioAllocationResearchHoldingSnapshotRecord[]
  planning_groups: PortfolioAllocationResearchPlanningGroupSnapshotRecord[]
}

export type PortfolioAllocationResearchScopeSelectionRecord = {
  taxonomy_node_id?: string | null
  label: string
  path: string
  depth: number
  default_target_dimension: 'weight' | 'risk_budget'
  member_source: string
}

export type PortfolioAllocationResearchMemberTargetRecord = {
  member_type: string
  member_id: string
  label: string
  scope_path?: string | null
  member_path?: string | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  selected_target_dimension?: PortfolioAllocationResearchTargetDimension | null
  source_target_set_type?: 'saa' | 'taa' | null
  current_weight?: number | null
  current_risk_share?: number | null
  target_weight?: number | null
  weight_change?: number | null
  configured_weight?: number | null
  configured_risk_share?: number | null
  selected_target_value?: number | null
}

export type PortfolioAllocationResearchSolvedResultRowRecord = {
  member_type: string
  member_id: string
  label: string
  top_sleeve_id?: string | null
  top_sleeve_label: string
  solved_weight?: number | null
  target_risk_share?: number | null
  forward_risk_contribution?: number | null
}

export type PortfolioAllocationResearchSolvedResultGroupRecord = {
  top_sleeve_id?: string | null
  top_sleeve_label: string
  solved_weight?: number | null
  target_risk_share?: number | null
  forward_risk_contribution?: number | null
  min_weight?: number | null
  max_weight?: number | null
  bound_status?: string | null
  rows: PortfolioAllocationResearchSolvedResultRowRecord[]
}

export type PortfolioAllocationResearchSolveEventRecord = {
  as_of_date: string
  scope_node_id?: string | null
  scope_label: string
  scope_path?: string | null
  scope_depth?: number | null
  requested_target_dimension?: string | null
  taxonomy_default_target_dimension?: 'weight' | 'risk_budget' | null
  target_dimension?: PortfolioAllocationResearchTargetDimension | null
  solver_kind?: string | null
  solver_detail?: string | null
  solver_message?: string | null
  covariance_model?: string | null
  covariance_observations?: number | null
  risk_contribution_mode?: string | null
  missing_return_policy?: PortfolioRiskMissingReturnPolicy | null
  return_rows_before_policy?: number | null
  return_rows_after_policy?: number | null
  missing_return_row_count?: number | null
  missing_return_row_fraction?: number | null
  dropped_return_rows?: Array<{ date: string; missing_members: string[] }> | null
  latest_complete_return_date?: string | null
  trailing_complete_return_staleness_days?: number | null
  calculation_frequency?: Exclude<PortfolioAllocationResearchCalculationFrequency, 'auto'> | null
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

export type PortfolioAllocationResearchTargetWeightGapRecord = {
  member_type: string
  member_id: string
  label: string
  current_weight?: number | null
  target_weight?: number | null
  gap?: number | null
  current_value_base?: number | null
  base_currency: string
  action: string
  execution_status: 'ready' | 'manual_review_required'
  execution_note?: string | null
}

export type PortfolioAllocationResearchTargetRowRecord = {
  member_type: string
  member_id: string
  label: string
  current_weight?: number | null
  current_value_base?: number | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  selected_target_dimension?: PortfolioAllocationResearchTargetDimension | null
  source_target_set_type?: 'saa' | 'taa' | null
  source_target_set_id?: string | null
  source_label?: string | null
  selected_target_value?: number | null
  target_weight?: number | null
  target_risk_share?: number | null
  implementation_weight?: number | null
  gap_to_implementation?: number | null
  action?: string | null
  execution_status: 'ready' | 'manual_review_required'
  execution_note?: string | null
}

export type PortfolioPolicyReplayPointRecord = {
  date: string
  value?: number | null
}

export type PortfolioPolicyReplaySleeveValueRecord = {
  top_sleeve_id?: string | null
  top_sleeve_label: string
  value?: number | null
}

export type PortfolioPolicyReplaySleevePointRecord = {
  date: string
  sleeves: PortfolioPolicyReplaySleeveValueRecord[]
}

export type PortfolioPolicyReplayMetricsRecord = {
  start_date?: string | null
  end_date?: string | null
  history_reliability: PortfolioPerformanceHistoryReliability
  period_return?: number | null
  ytd_return?: number | null
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

export type PortfolioPolicyReplayRecord = {
  rebalance_frequency: PortfolioPolicyReplayRebalanceFrequency
  common_history_start_date?: string | null
  start_date?: string | null
  end_date?: string | null
  lookback_days: number
  points: PortfolioPolicyReplayPointRecord[]
  metrics?: PortfolioPolicyReplayMetricsRecord | null
  top_sleeve_weight_points: PortfolioPolicyReplaySleevePointRecord[]
  top_sleeve_contribution_points: PortfolioPolicyReplaySleevePointRecord[]
  warnings: string[]
}

export type PortfolioPolicyReplayBenchmarkRecord = {
  instrument_id?: string | null
  label?: string | null
  points: PortfolioPolicyReplayPointRecord[]
  metrics?: PortfolioPolicyReplayMetricsRecord | null
  warnings: string[]
}

export type PortfolioPolicyReplayRelativeMetricsRecord = PortfolioPolicyReplayMetricsRecord & {
  excess_return?: number | null
  tracking_error?: number | null
  information_ratio?: number | null
}

export type PortfolioPolicyReplayBenchmarkComparisonResponse = {
  policy_replay_benchmark?: PortfolioPolicyReplayBenchmarkRecord | null
  policy_replay_relative_metrics?: PortfolioPolicyReplayRelativeMetricsRecord | null
}

export type PortfolioAllocationResearchRunDetailRecord = {
  headline?: string | null
  coverage_note?: string | null
  signals: PortfolioAllocationResearchContextSignalRecord[]
  findings: PortfolioAllocationResearchFindingRecord[]
  next_questions: string[]
  top_holdings: PortfolioAllocationResearchHoldingSnapshotRecord[]
  planning_groups: PortfolioAllocationResearchPlanningGroupSnapshotRecord[]
  selected_scope?: PortfolioAllocationResearchScopeSelectionRecord | null
  target_assumptions: string[]
  target_rows: PortfolioAllocationResearchTargetRowRecord[]
  member_targets: PortfolioAllocationResearchMemberTargetRecord[]
  leaf_targets: PortfolioAllocationResearchMemberTargetRecord[]
  solved_result_groups: PortfolioAllocationResearchSolvedResultGroupRecord[]
  solve_event?: PortfolioAllocationResearchSolveEventRecord | null
  scope_solve_events: PortfolioAllocationResearchSolveEventRecord[]
  target_weight_gaps: PortfolioAllocationResearchTargetWeightGapRecord[]
  policy_replay?: PortfolioPolicyReplayRecord | null
  policy_replay_benchmark?: PortfolioPolicyReplayBenchmarkRecord | null
  policy_replay_relative_metrics?: PortfolioPolicyReplayRelativeMetricsRecord | null
  warnings: string[]
}

export type PortfolioAllocationResearchArtifactRecord = {
  artifact_id: string
  label: string
  path: string
  media_type: string
  preview_kind: PortfolioAllocationResearchArtifactPreviewKind
}

export type PortfolioAllocationResearchRunRecord = {
  allocation_research_run_id: string
  portfolio_id: string
  job_type: string
  status: PortfolioAllocationResearchRunStatus
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
  artifacts: PortfolioAllocationResearchArtifactRecord[]
  detail?: PortfolioAllocationResearchRunDetailRecord | null
}

export type PortfolioAllocationResearchRunCreatePayload = {
  requested_by?: string | null
}

export type PortfolioAllocationResearchWorkbenchResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  default_planning_taxonomy_id?: string | null
  planning_taxonomy_options: PortfolioAllocationResearchPlanningTaxonomyOption[]
  planning_scope_options: PortfolioAllocationResearchPlanningScopeOption[]
  calculation_frequency: PortfolioAllocationResearchCalculationFrequencyProfile
  settings: PortfolioAllocationResearchSettingsRecord
  risk_policy: PortfolioRiskPolicyRecord
  current_context: PortfolioAllocationResearchCurrentContextRecord
  runs: PortfolioAllocationResearchRunRecord[]
  selected_run?: PortfolioAllocationResearchRunRecord | null
}

export type PortfolioAllocationResearchArtifactContentResponse = {
  filename: string
  path: string
  media_type: string
  encoding: 'text'
  preview_kind: PortfolioAllocationResearchArtifactPreviewKind
  content: string
}

export type PortfolioTaxonomyCreatePayload = {
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
  node_name: string
  node_code?: string | null
  parent_taxonomy_node_id?: string | null
  sort_order?: number | null
  is_terminal?: boolean
  default_target_dimension?: 'weight' | 'risk_budget'
  status?: string
}

export type PortfolioTaxonomyUpdatePayload = {
  name?: string | null
  taxonomy_type?: string | null
  purpose?: string | null
  planning_enabled?: boolean
  budgeting_level?: string | null
  root_default_target_dimension?: 'weight' | 'risk_budget' | null
  status?: string | null
}

export type PortfolioTaxonomyNodeUpdatePayload = {
  node_name?: string | null
  node_code?: string | null
  parent_taxonomy_node_id?: string | null
  sort_order?: number | null
  default_target_dimension?: 'weight' | 'risk_budget' | null
  status?: string | null
}

export type PortfolioTaxonomyAssignmentCreatePayload = {
  target_scope: TaxonomyAssignmentScope
  target_entity_id: string
  taxonomy_node_id: string
  status?: string
}

export type PortfolioTaxonomyAssignmentUpdatePayload = {
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
  name?: string | null
  weight_enabled?: boolean
  risk_budget_enabled?: boolean
  status?: string | null
  notes?: string | null
  lines?: PortfolioTargetSetLinePayload[]
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

export type PortfolioPublishedAccountPositionRecord = {
  as_of_date: string
  account_id: string
  instrument_id: string
  instrument_name: string
  instrument_type: string
  currency: string
  quantity_exact: string
  measured_price: boolean
  adopted_price_exact: string | null
  measured_market_value: boolean
  market_value_local_exact: string | null
  market_value_base_exact: string | null
  measured_base_cost: boolean
  cost_basis_local_exact: string
  cost_basis_base_exact: string | null
  open_lot_count: number
  valuation_coverage_state: PortfolioDailyPublishedCoverageState
  valuation_reason_codes: string[]
  base_cost_coverage_state: 'complete' | 'unavailable'
  base_cost_reason_codes: string[]
}

export type PortfolioPublishedAccountBalanceRecord = {
  as_of_date: string
  account_id: string
  component_type:
    | 'settled_cash'
    | 'pending_receivable'
    | 'pending_payable'
    | 'income_accrual'
    | 'fee_accrual'
    | 'tax_accrual'
    | 'other_accrual'
  component_key: string
  currency: string
  local_amount: string
  measured_base_amount: boolean
  base_amount_exact: string | null
  coverage_state: PortfolioDailyPublishedCoverageState
  reason_codes: string[]
}

export type PortfolioAccountWorkspaceAccount = {
  account: PortfolioAccountRecord
  default_settlement_cash_account_name?: string | null
  linked_transaction_count: number
  balance_component_count: number
  position_line_count: number
  open_lot_count: number
  settled_cash_local: string | null
  settled_cash_base_exact: string | null
  pending_receivable_local: string | null
  pending_receivable_base_exact: string | null
  pending_payable_local: string | null
  pending_payable_base_exact: string | null
  accrual_receivable_local: string | null
  accrual_receivable_base_exact: string | null
  accrual_payable_local: string | null
  accrual_payable_base_exact: string | null
  position_market_value_local_exact: string | null
  position_market_value_base_exact: string | null
  cost_basis_local_exact: string | null
  cost_basis_base_exact: string | null
  account_value_base_exact: string | null
  valuation_coverage_state: PortfolioDailyPublishedCoverageState
  valuation_reason_codes: string[]
  cost_basis_coverage_state: 'complete' | 'unavailable'
  cost_basis_reason_codes: string[]
}

export type PortfolioAccountsWorkspaceResponse = {
  portfolio_id: string
  base_currency: string
  as_of_date: string
  publication: PortfolioDailyPublicationMetadata
  summary: {
    account_count: number
    deposit_account_count: number
    securities_account_count: number
    balance_component_count: number
    position_line_count: number
    open_lot_count: number
    valuation_coverage_state: PortfolioDailyPublishedCoverageState
  }
  selected_account_id?: string | null
  accounts: PortfolioAccountWorkspaceAccount[]
  balances: PortfolioPublishedAccountBalanceRecord[]
  positions: PortfolioPublishedAccountPositionRecord[]
  linked_transactions_summary?: PortfolioTransactionListResponse['summary'] | null
  linked_transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionDecimal = string
export type PortfolioTransactionConsiderationBasis =
  | 'source_reported'
  | 'exact_quantity_price'
export type PortfolioTransactionNumericScaleState = 'declared' | 'legacy_inferred'

export function restorePortfolioTransactionInputScale(
  value: PortfolioTransactionDecimal | null | undefined,
  inputScale: number | null | undefined,
  options?: { zeroAsEmpty?: boolean },
): string {
  if (value == null) {
    return ''
  }
  const normalized = value.trim()
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(normalized)) {
    throw new Error(`Invalid transaction decimal response: ${value}`)
  }
  if (inputScale == null || !Number.isSafeInteger(inputScale) || inputScale < 0) {
    throw new Error(`Invalid transaction decimal input scale: ${String(inputScale)}`)
  }
  const [integerPart, fraction = ''] = normalized.split('.')
  if (fraction.length > inputScale) {
    throw new Error(
      `Transaction decimal ${normalized} exceeds its recorded input scale ${inputScale}.`,
    )
  }
  if (options?.zeroAsEmpty && /^0(?:\.0+)?$/.test(normalized)) {
    return ''
  }
  if (fraction.length === inputScale) {
    return normalized
  }
  return `${integerPart}${inputScale ? `.${fraction.padEnd(inputScale, '0')}` : ''}`
}

export type PortfolioTransactionActorInput = {
  actor_id: string
  display_name: string
  actor_type: 'user'
  actor_source: 'client_asserted'
}

export type PortfolioTransactionActorRecord = {
  actor_id: string
  display_name: string
  actor_type: 'user' | 'service' | 'migration'
  actor_source:
    | 'client_asserted'
    | 'authenticated_principal'
    | 'trusted_service'
    | 'migration'
}

export type PortfolioTransactionLifecycleStatus = 'active' | 'deleted'
export type PortfolioTransactionRevisionOperation = 'baseline' | 'create' | 'amend' | 'delete'

export type PortfolioTransactionRecord = {
  transaction_id: string
  portfolio_id: string
  transaction_type: string
  flow_scope: string
  trade_date: string
  trade_time: string
  trade_at: string
  trade_timezone: string
  trade_time_is_estimated: boolean
  settlement_date: string
  entitlement_date?: string | null
  acquisition_date?: string | null
  account: PortfolioAccountRecord
  settlement_cash_account?: PortfolioAccountRecord | null
  instrument_id?: string | null
  instrument_ref?: InstrumentCore | null
  quantity?: PortfolioTransactionDecimal | null
  price?: PortfolioTransactionDecimal | null
  gross_amount: PortfolioTransactionDecimal
  counter_amount?: PortfolioTransactionDecimal | null
  quoted_fx_rate?: PortfolioTransactionDecimal | null
  fees: PortfolioTransactionDecimal
  taxes: PortfolioTransactionDecimal
  consideration_basis?: PortfolioTransactionConsiderationBasis | null
  numeric_scale_state: PortfolioTransactionNumericScaleState
  quantity_input_scale?: number | null
  price_input_scale?: number | null
  gross_amount_input_scale: number
  counter_amount_input_scale?: number | null
  quoted_fx_rate_input_scale?: number | null
  fees_input_scale: number
  taxes_input_scale: number
  currency: string
  transfer_scope?: string | null
  transfer_object_type?: string | null
  transfer_group_id?: string | null
  counterparty_account_id?: string | null
  net_cash_effect?: PortfolioTransactionDecimal | null
  note?: string | null
  created_at?: string | null
  revision_id: string
  revision_number: number
  lifecycle_status: PortfolioTransactionLifecycleStatus
  last_mutation_id: string
  last_changed_at: string
  last_actor: PortfolioTransactionActorRecord
  last_change_reason?: string | null
}

export type PortfolioTransactionListResponse = {
  portfolio_id: string
  summary: {
    total_transactions: number
    instrument_transactions: number
    external_cash_flows: number
    opening_balance_records: number
  }
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionWorkspaceResponse = {
  portfolio_id: string
  summary: PortfolioTransactionListResponse['summary']
  selected_transaction_id?: string | null
  transactions: PortfolioTransactionRecord[]
  selected_transaction?: PortfolioTransactionRecord | null
}

export type PortfolioDailyPublishedPositionRecord = {
  as_of_date: string
  account_id: string
  instrument_id: string
  currency: string
  quantity_exact: string
  quantity: string
  measured_price: boolean
  measured_market_value: boolean
  measured_book_pnl: boolean
  adopted_price_exact: string | null
  price: string | null
  contract_multiplier_exact: string | null
  price_factor_exact: string | null
  adopted_fx_rate_exact: string | null
  market_value_local_exact: string | null
  market_value_base_exact: string | null
  cost_basis_local: string | null
  cost_basis_base: string | null
  economic_pnl_daily_base: string | null
  portfolio_weight: string | null
  return_contribution: string | null
  valuation_coverage_state: PortfolioDailyPublishedCoverageState
  valuation_coverage_reason_codes: string[]
  book_pnl_coverage_state: PortfolioDailyPublishedCoverageState
  book_pnl_reason_codes: string[]
  valuation_endpoint_status: 'fresh' | 'carry_forward' | 'stale' | 'unavailable'
  valuation_reason_codes: string[]
}

export type PortfolioDailyPublishedPositionListResponse = {
  portfolio_id: string
  publication: PortfolioDailyPublicationMetadata
  summary: {
    as_of_date: string
    position_count: number
    priced_position_count: number
    account_count: number
    measured_market_value: boolean
    market_value_base_exact: string | null
  }
  positions: PortfolioDailyPublishedPositionRecord[]
}

export type PortfolioDailyPublishedLotRecord = {
  as_of_date: string
  account_id: string
  instrument_id: string
  lot_id: string
  source_transaction_id: string
  source_revision_id: string
  source_revision_number: number
  custody_transaction_id: string
  custody_revision_id: string
  custody_revision_number: number
  acquisition_date: string
  currency: string
  open_quantity_exact: string
  open_quantity: string
  measured_base_cost: boolean
  acquisition_fx_rate_exact: string | null
  cost_basis_local_exact: string
  cost_basis_local: string
  unit_cost_local: string
  unit_cost_local_rounding_residual_exact: string
  cost_basis_base_exact: string | null
  cost_basis_base: string | null
  unit_cost_base: string | null
  unit_cost_base_rounding_residual_exact: string | null
  base_cost_coverage_state: 'complete' | 'unavailable'
  base_cost_reason_codes: string[]
}

export type PortfolioDailyPublishedLotFilters = {
  account_id?: string
  instrument_id?: string
  status?: 'open'
  as_of_date?: string
}

export type PortfolioDailyPublishedLotListResponse = {
  portfolio_id: string
  publication: PortfolioDailyPublicationMetadata
  summary: {
    as_of_date: string
    lot_count: number
    account_count: number
    instrument_count: number
    measured_base_cost: boolean
    open_quantity_exact: string
    cost_basis_base_exact: string | null
  }
  position_lots: PortfolioDailyPublishedLotRecord[]
}

export type PortfolioTransactionFilters = {
  account_id?: string
  transaction_type?: string
  instrument_id?: string
  start_date?: string
  end_date?: string
}

export type PortfolioTransactionFactPayload = {
  transaction_type: string
  trade_date: string
  trade_time?: string | null
  settlement_date?: string | null
  entitlement_date?: string | null
  acquisition_date?: string | null
  account_id: string
  settlement_cash_account_id?: string | null
  instrument_id?: string | null
  quantity?: PortfolioTransactionDecimal | null
  price?: PortfolioTransactionDecimal | null
  gross_amount: PortfolioTransactionDecimal
  counter_amount?: PortfolioTransactionDecimal | null
  quoted_fx_rate?: PortfolioTransactionDecimal | null
  consideration_basis?: PortfolioTransactionConsiderationBasis | null
  fees?: PortfolioTransactionDecimal
  taxes?: PortfolioTransactionDecimal
  currency: string
  transfer_scope?: string | null
  transfer_object_type?: string | null
  transfer_group_id?: string | null
  counterparty_account_id?: string | null
  note?: string | null
}

export type PortfolioTransactionCreatePayload = PortfolioTransactionFactPayload & {
  actor: PortfolioTransactionActorInput
  change_reason?: string | null
}

export type PortfolioTransactionUpdatePayload = PortfolioTransactionFactPayload & {
  expected_revision_id: string
  expected_revision_number: number
  actor: PortfolioTransactionActorInput
  change_reason: string
}

export type PortfolioTransactionDeletePayload = {
  expected_revision_id: string
  expected_revision_number: number
  actor: PortfolioTransactionActorInput
  change_reason: string
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
  quantity?: PortfolioTransactionDecimal | null
  gross_amount: PortfolioTransactionDecimal
  note?: string | null
  transfer_group_id?: string | null
  actor: PortfolioTransactionActorInput
  change_reason?: string | null
}

export type PortfolioTransactionRevisionSnapshot = {
  transaction_type: string
  trade_date: string
  trade_time: string
  trade_at: string
  trade_timezone: string
  trade_time_is_estimated: boolean
  settlement_date: string
  entitlement_date?: string | null
  acquisition_date?: string | null
  account_id: string
  settlement_cash_account_id?: string | null
  instrument_id?: string | null
  instrument_ref?: InstrumentCore | null
  quantity?: PortfolioTransactionDecimal | null
  price?: PortfolioTransactionDecimal | null
  gross_amount: PortfolioTransactionDecimal
  counter_amount?: PortfolioTransactionDecimal | null
  quoted_fx_rate?: PortfolioTransactionDecimal | null
  fees: PortfolioTransactionDecimal
  taxes: PortfolioTransactionDecimal
  consideration_basis?: PortfolioTransactionConsiderationBasis | null
  numeric_scale_state: PortfolioTransactionNumericScaleState
  quantity_input_scale?: number | null
  price_input_scale?: number | null
  gross_amount_input_scale: number
  counter_amount_input_scale?: number | null
  quoted_fx_rate_input_scale?: number | null
  fees_input_scale: number
  taxes_input_scale: number
  currency: string
  transfer_scope?: string | null
  transfer_object_type?: string | null
  transfer_group_id?: string | null
  counterparty_account_id?: string | null
  note?: string | null
  created_at?: string | null
}

export type PortfolioTransactionRevisionRecord = {
  revision_id: string
  transaction_id: string
  portfolio_id: string
  revision_number: number
  previous_revision_id?: string | null
  mutation_id: string
  operation: PortfolioTransactionRevisionOperation
  lifecycle_status: PortfolioTransactionLifecycleStatus
  recorded_at: string
  actor: PortfolioTransactionActorRecord
  change_reason?: string | null
  changed_fields: string[]
  snapshot: PortfolioTransactionRevisionSnapshot | null
}

export type PortfolioTransactionRevisionHistoryResponse = {
  portfolio_id: string
  transaction_id: string
  current_revision_id: string
  current_revision_number: number
  lifecycle_status: PortfolioTransactionLifecycleStatus
  revisions: PortfolioTransactionRevisionRecord[]
}

export type PortfolioTransactionBatchResponse = {
  portfolio_id: string
  mutation_id: string
  created_count: number
  transfer_group_id?: string | null
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionDeleteResponse = {
  portfolio_id: string
  mutation_id: string
  deleted_count: number
  deleted_transaction_ids: string[]
  transfer_group_id?: string | null
  revisions: PortfolioTransactionRevisionRecord[]
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

export type StructuredApiErrorDetail = {
  code?: string
  message?: string
  [key: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly detail: unknown
  readonly payload: unknown

  constructor({
    status,
    code,
    detail,
    payload,
    message,
  }: {
    status: number
    code: string | null
    detail: unknown
    payload: unknown
    message: string
  }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.payload = payload
  }
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseApiError(response: Response, body: string): ApiError {
  let payload: unknown = body || null
  if (body) {
    try {
      payload = JSON.parse(body) as unknown
    } catch {
      // Preserve a non-JSON response as the exact server-provided message.
    }
  }

  const detail = isObjectRecord(payload) && 'detail' in payload ? payload.detail : payload
  const code =
    isObjectRecord(detail) && typeof detail.code === 'string' && detail.code.trim()
      ? detail.code.trim()
      : null
  let message: string
  if (typeof detail === 'string' && detail.trim()) {
    message = detail
  } else if (
    isObjectRecord(detail) &&
    typeof detail.message === 'string' &&
    detail.message.trim()
  ) {
    message = detail.message
  } else if (code) {
    message = code
  } else if (detail !== null && detail !== undefined) {
    try {
      message = JSON.stringify(detail)
    } catch {
      message = `Request failed: ${response.status}`
    }
  } else {
    message = `Request failed: ${response.status}`
  }

  return new ApiError({
    status: response.status,
    code,
    detail,
    payload,
    message,
  })
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
      throw parseApiError(response, body)
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

const CANONICAL_DECIMAL_TEXT = /^(0|-?([1-9][0-9]*(\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$/

function exactDecimalString(value: unknown, fieldName: string): string | null {
  if (value == null) {
    return null
  }
  if (typeof value !== 'string' || !CANONICAL_DECIMAL_TEXT.test(value)) {
    throw new Error(`${fieldName} is not a canonical exact decimal string.`)
  }
  return value
}

function exactDecimalForDisplay(value: string | null, fieldName: string): number | null {
  if (value == null) {
    return null
  }
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    throw new Error(`${fieldName} is outside the browser display range.`)
  }
  return parsed
}

function objectRecord(value: unknown, fieldName: string): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${fieldName} must be an object.`)
  }
  return value as Record<string, unknown>
}

function objectArray(value: unknown, fieldName: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${fieldName} must be an array.`)
  }
  return value
}

function requiredText(value: unknown, fieldName: string): string {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim()) {
    throw new Error(`${fieldName} must be non-empty canonical text.`)
  }
  return value
}

function optionalText(value: unknown, fieldName: string): string | null {
  return value == null ? null : requiredText(value, fieldName)
}

function requiredBoolean(value: unknown, fieldName: string): boolean {
  if (typeof value !== 'boolean') {
    throw new Error(`${fieldName} must be a boolean.`)
  }
  return value
}

function requiredInteger(value: unknown, fieldName: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new Error(`${fieldName} must be a safe integer.`)
  }
  return value
}

function normalizeSealedTaxonomyDisplayConfig(
  value: unknown,
  portfolioId: string,
): PortfolioSealedTaxonomyDisplayConfig {
  const source = objectRecord(value, 'holdings.sealed_display_config.taxonomy')
  const taxonomies = objectArray(
    source.taxonomies,
    'holdings.sealed_display_config.taxonomy.taxonomies',
  ).map((value, index): PortfolioTaxonomyRecord => {
    const fieldName = `holdings.sealed_display_config.taxonomy.taxonomies[${index}]`
    const row = objectRecord(value, fieldName)
    const rowPortfolioId = requiredText(row.portfolio_id, `${fieldName}.portfolio_id`)
    if (rowPortfolioId !== portfolioId) {
      throw new Error(`${fieldName}.portfolio_id does not match the holdings portfolio.`)
    }
    const primaryScope = requiredText(
      row.primary_assignment_scope,
      `${fieldName}.primary_assignment_scope`,
    )
    if (!['instrument', 'account', 'cash_bucket'].includes(primaryScope)) {
      throw new Error(`${fieldName}.primary_assignment_scope is invalid.`)
    }
    const targetDimension = requiredText(
      row.root_default_target_dimension,
      `${fieldName}.root_default_target_dimension`,
    )
    if (!['weight', 'risk_budget'].includes(targetDimension)) {
      throw new Error(`${fieldName}.root_default_target_dimension is invalid.`)
    }
    return {
      taxonomy_id: requiredText(row.taxonomy_id, `${fieldName}.taxonomy_id`),
      portfolio_id: rowPortfolioId,
      name: requiredText(row.name, `${fieldName}.name`),
      taxonomy_type: requiredText(row.taxonomy_type, `${fieldName}.taxonomy_type`),
      purpose: optionalText(row.purpose, `${fieldName}.purpose`),
      primary_assignment_scope: primaryScope as TaxonomyAssignmentScope,
      planning_enabled: requiredBoolean(row.planning_enabled, `${fieldName}.planning_enabled`),
      budgeting_level: optionalText(row.budgeting_level, `${fieldName}.budgeting_level`),
      root_default_target_dimension: targetDimension as 'weight' | 'risk_budget',
      status: requiredText(row.status, `${fieldName}.status`),
      source_template_ref: optionalText(row.source_template_ref, `${fieldName}.source_template_ref`),
    }
  })
  const taxonomiesById = new Map(taxonomies.map((row) => [row.taxonomy_id, row]))
  if (taxonomiesById.size !== taxonomies.length) {
    throw new Error('holdings.sealed_display_config.taxonomy has duplicate taxonomy ids.')
  }

  const taxonomyNodes = objectArray(
    source.taxonomy_nodes,
    'holdings.sealed_display_config.taxonomy.taxonomy_nodes',
  ).map((value, index): PortfolioTaxonomyNodeRecord => {
    const fieldName = `holdings.sealed_display_config.taxonomy.taxonomy_nodes[${index}]`
    const row = objectRecord(value, fieldName)
    const taxonomyId = requiredText(row.taxonomy_id, `${fieldName}.taxonomy_id`)
    if (!taxonomiesById.has(taxonomyId)) {
      throw new Error(`${fieldName}.taxonomy_id is not present in the sealed taxonomies.`)
    }
    const targetDimension = requiredText(
      row.default_target_dimension,
      `${fieldName}.default_target_dimension`,
    )
    if (!['weight', 'risk_budget'].includes(targetDimension)) {
      throw new Error(`${fieldName}.default_target_dimension is invalid.`)
    }
    return {
      taxonomy_node_id: requiredText(row.taxonomy_node_id, `${fieldName}.taxonomy_node_id`),
      taxonomy_id: taxonomyId,
      parent_taxonomy_node_id: optionalText(
        row.parent_taxonomy_node_id,
        `${fieldName}.parent_taxonomy_node_id`,
      ),
      node_name: requiredText(row.node_name, `${fieldName}.node_name`),
      node_code: optionalText(row.node_code, `${fieldName}.node_code`),
      sort_order: requiredInteger(row.sort_order, `${fieldName}.sort_order`),
      is_terminal: requiredBoolean(row.is_terminal, `${fieldName}.is_terminal`),
      default_target_dimension: targetDimension as 'weight' | 'risk_budget',
      status: requiredText(row.status, `${fieldName}.status`),
    }
  })
  const nodesById = new Map(taxonomyNodes.map((row) => [row.taxonomy_node_id, row]))
  if (nodesById.size !== taxonomyNodes.length) {
    throw new Error('holdings.sealed_display_config.taxonomy has duplicate taxonomy node ids.')
  }
  taxonomyNodes.forEach((row) => {
    if (row.parent_taxonomy_node_id == null) {
      return
    }
    const parent = nodesById.get(row.parent_taxonomy_node_id)
    if (parent?.taxonomy_id !== row.taxonomy_id) {
      throw new Error(`Sealed taxonomy node ${row.taxonomy_node_id} has an invalid parent.`)
    }
  })

  const taxonomyAssignments = objectArray(
    source.taxonomy_assignments,
    'holdings.sealed_display_config.taxonomy.taxonomy_assignments',
  ).map((value, index): PortfolioTaxonomyAssignmentRecord => {
    const fieldName = `holdings.sealed_display_config.taxonomy.taxonomy_assignments[${index}]`
    const row = objectRecord(value, fieldName)
    const taxonomyId = requiredText(row.taxonomy_id, `${fieldName}.taxonomy_id`)
    const nodeId = requiredText(row.taxonomy_node_id, `${fieldName}.taxonomy_node_id`)
    const targetScope = requiredText(row.target_scope, `${fieldName}.target_scope`)
    if (!['instrument', 'account', 'cash_bucket'].includes(targetScope)) {
      throw new Error(`${fieldName}.target_scope is invalid.`)
    }
    if (nodesById.get(nodeId)?.taxonomy_id !== taxonomyId) {
      throw new Error(`${fieldName}.taxonomy_node_id is outside its sealed taxonomy.`)
    }
    return {
      assignment_id: requiredText(row.assignment_id, `${fieldName}.assignment_id`),
      taxonomy_id: taxonomyId,
      target_scope: targetScope as TaxonomyAssignmentScope,
      target_entity_id: requiredText(row.target_entity_id, `${fieldName}.target_entity_id`),
      taxonomy_node_id: nodeId,
      status: requiredText(row.status, `${fieldName}.status`),
    }
  })
  const assignmentIds = new Set(taxonomyAssignments.map((row) => row.assignment_id))
  if (assignmentIds.size !== taxonomyAssignments.length) {
    throw new Error('holdings.sealed_display_config.taxonomy has duplicate assignment ids.')
  }

  const defaultPlanningTaxonomyId = optionalText(
    source.default_planning_taxonomy_id,
    'holdings.sealed_display_config.taxonomy.default_planning_taxonomy_id',
  )
  if (defaultPlanningTaxonomyId != null && !taxonomiesById.has(defaultPlanningTaxonomyId)) {
    throw new Error(
      'holdings.sealed_display_config.taxonomy.default_planning_taxonomy_id is not sealed.',
    )
  }
  return {
    default_planning_taxonomy_id: defaultPlanningTaxonomyId,
    taxonomies,
    taxonomy_nodes: taxonomyNodes,
    taxonomy_assignments: taxonomyAssignments,
  }
}

function normalizedExactFields(
  value: unknown,
  fieldName: string,
  nullableFields: readonly string[],
  requiredFields: readonly string[] = [],
) {
  const source = objectRecord(value, fieldName)
  const normalized: Record<string, unknown> = { ...source }
  nullableFields.forEach((key) => {
    normalized[key] = exactDecimalString(source[key], `${fieldName}.${key}`)
  })
  requiredFields.forEach((key) => {
    const exact = exactDecimalString(source[key], `${fieldName}.${key}`)
    if (exact == null) {
      throw new Error(`${fieldName}.${key} must be available.`)
    }
    normalized[key] = exact
  })
  return normalized
}

function normalizePublishedDecimalMetric(
  value: unknown,
  fieldName: string,
): PortfolioDailyPublishedDecimalMetric | null {
  if (value == null) {
    return null
  }
  return normalizedExactFields(
    value,
    fieldName,
    [],
    ['method50', 'published', 'rounding_adjustment_exact'],
  ) as PortfolioDailyPublishedDecimalMetric
}

function normalizePublishedPerformanceSummary(
  value: unknown,
): PortfolioDailyPublishedPerformanceSummary {
  const fieldName = 'performance_report.performance'
  const normalized = objectRecord(value, fieldName)
  const metricFields = [
    'cumulative_twr',
    'annualized_twr',
    'current_drawdown',
    'max_drawdown',
  ] as const
  return {
    ...normalized,
    ...Object.fromEntries(
      metricFields.map((key) => [
        key,
        normalizePublishedDecimalMetric(normalized[key], `${fieldName}.${key}`),
      ]),
    ),
  } as PortfolioDailyPublishedPerformanceSummary
}

function normalizePublishedStatistics(
  value: unknown,
): PortfolioDailyPublishedPerformanceStatistics {
  const normalized = objectRecord(value, 'performance_report.statistics')
  const metricFields = [
    'periods_per_year',
    'mean_period_return',
    'annualized_arithmetic_mean',
    'annualized_volatility',
    'annualized_downside_deviation',
  ] as const
  return {
    ...normalized,
    ...Object.fromEntries(
      metricFields.map((key) => [
        key,
        normalizePublishedDecimalMetric(
          normalized[key],
          `performance_report.statistics.${key}`,
        ),
      ]),
    ),
  } as PortfolioDailyPublishedPerformanceStatistics
}

function normalizePublishedXirr(value: unknown): PortfolioDailyPublishedXirrResult {
  const normalized = normalizedExactFields(
    value,
    'performance_report.xirr',
    ['xnpv_residual_exact'],
  )
  normalized.rate = normalizePublishedDecimalMetric(
    normalized.rate,
    'performance_report.xirr.rate',
  )
  return normalized as PortfolioDailyPublishedXirrResult
}

function normalizePublishedAttributionGroup(
  value: unknown,
  fieldName: string,
): PortfolioDailyPublishedAttributionGroup {
  return normalizedExactFields(
    value,
    fieldName,
    [],
    [
      'opening_nav_exact',
      'closing_nav_exact',
      'external_flow_in_exact',
      'external_flow_out_exact',
      'internal_flow_in_exact',
      'internal_flow_out_exact',
      'economic_pnl_exact',
      'linked_contribution_method50',
      'linking_adjustment_exact',
      'linked_contribution_effective',
      'closure_residual_exact',
    ],
  ) as PortfolioDailyPublishedAttributionGroup
}

function normalizePublishedAttributionSummary(
  value: unknown,
  fieldName: string,
): PortfolioDailyPublishedAttributionSummary {
  return normalizedExactFields(
    value,
    fieldName,
    [
      'cumulative_twr_method50',
      'total_linked_contribution_effective',
      'total_linking_adjustment_exact',
      'closure_residual_exact',
    ],
  ) as PortfolioDailyPublishedAttributionSummary
}

function normalizePublishedSnapshot(
  value: unknown,
  index: number,
): PortfolioDailyPublishedSnapshotRecord {
  return normalizedExactFields(
    value,
    `performance_report.daily_series[${index}]`,
    [
      'opening_nav',
      'closing_nav',
      'position_market_value',
      'settled_cash',
      'pending_receivable',
      'pending_payable',
      'accrual_receivable',
      'accrual_payable',
      'external_flow_in',
      'external_flow_out',
      'economic_pnl',
      'realized_pnl_daily',
      'unrealized_pnl_beginning',
      'unrealized_pnl_ending',
      'unrealized_pnl_change',
      'gross_income_daily',
      'return_of_capital_daily',
      'capitalized_fee_daily',
      'capitalized_tax_daily',
      'expensed_fee_daily',
      'expensed_tax_daily',
      'local_price_effect_daily',
      'position_fx_effect_daily',
      'cash_fx_effect_daily',
      'pending_fx_effect_daily',
      'accrual_fx_effect_daily',
      'fx_conversion_effect_daily',
      'subperiod_twr_method50',
      'subperiod_twr_published',
      'cumulative_twr_method50',
      'cumulative_twr_published',
      'wealth_index_method50',
      'wealth_index_published',
      'peak_wealth_index_method50',
      'peak_wealth_index_published',
      'drawdown_method50',
      'drawdown_published',
      'wealth_chain_rounding_adjustment_exact',
    ],
  ) as PortfolioDailyPublishedSnapshotRecord
}

function normalizePublishedReturnCalendarBucket(
  value: unknown,
  index: number,
): PortfolioDailyPublishedReturnCalendarBucket {
  const fieldName = `performance_report.return_calendar[${index}]`
  const normalized = objectRecord(value, fieldName)
  const metricFields = [
    'cumulative_twr',
    'current_drawdown',
    'max_drawdown',
  ] as const
  return {
    ...normalized,
    ...Object.fromEntries(
      metricFields.map((key) => [
        key,
        normalizePublishedDecimalMetric(normalized[key], `${fieldName}.${key}`),
      ]),
    ),
  } as PortfolioDailyPublishedReturnCalendarBucket
}

function normalizePublishedAttributionCalendarBucket(
  value: unknown,
  index: number,
): PortfolioDailyPublishedAttributionCalendarBucket {
  const fieldName = `performance_report.attribution_calendar[${index}]`
  const source = objectRecord(value, fieldName)
  return {
    ...source,
    summary: normalizePublishedAttributionSummary(
      source.summary,
      `${fieldName}.summary`,
    ),
    groups: objectArray(source.groups, `${fieldName}.groups`).map((group, groupIndex) =>
      normalizePublishedAttributionGroup(
        group,
        `${fieldName}.groups[${groupIndex}]`,
      ),
    ),
  } as PortfolioDailyPublishedAttributionCalendarBucket
}

function normalizePublishedPerformanceReport(
  value: unknown,
): PortfolioDailyPublishedPerformanceReportResponse {
  const source = objectRecord(value, 'performance_report')
  const bridge = source.portfolio_bridge
  return {
    ...source,
    performance: normalizePublishedPerformanceSummary(source.performance),
    statistics: normalizePublishedStatistics(source.statistics),
    xirr: normalizePublishedXirr(source.xirr),
    portfolio_bridge:
      bridge == null
        ? null
        : normalizePublishedAttributionGroup(
            bridge,
            'performance_report.portfolio_bridge',
          ),
    rebased_wealth_series: objectArray(
      source.rebased_wealth_series,
      'performance_report.rebased_wealth_series',
    ).map((point, index) =>
      normalizedExactFields(
        point,
        `performance_report.rebased_wealth_series[${index}]`,
        ['wealth_chain_rounding_adjustment_exact'],
        ['wealth_index_method50', 'peak_wealth_index_method50', 'drawdown_method50'],
      ) as PortfolioDailyPublishedRebasedWealthPoint,
    ),
    daily_series: objectArray(
      source.daily_series,
      'performance_report.daily_series',
    ).map(normalizePublishedSnapshot),
    attribution: normalizePublishedAttributionSummary(
      source.attribution,
      'performance_report.attribution',
    ),
    attribution_groups: objectArray(
      source.attribution_groups,
      'performance_report.attribution_groups',
    ).map((group, index) =>
      normalizePublishedAttributionGroup(
        group,
        `performance_report.attribution_groups[${index}]`,
      ),
    ),
    return_calendar: objectArray(
      source.return_calendar,
      'performance_report.return_calendar',
    ).map(normalizePublishedReturnCalendarBucket),
    attribution_calendar: objectArray(
      source.attribution_calendar,
      'performance_report.attribution_calendar',
    ).map(normalizePublishedAttributionCalendarBucket),
  } as PortfolioDailyPublishedPerformanceReportResponse
}

type PortfolioWorkspaceSummaryWire = Omit<
  PortfolioWorkspaceSummary,
  | 'nav'
  | 'nav_exact'
  | 'day_change_value'
  | 'day_change_value_exact'
  | 'day_change_pct'
  | 'day_change_pct_method50'
  | 'day_change_pct_published'
  | 'badges'
> & {
  nav: unknown
  economic_pnl: unknown
  subperiod_twr_method50: unknown
  subperiod_twr_published: unknown
  nav_coverage_state?: string
  return_coverage_state?: string
  valuation_endpoint_status?: string
  valuation_reason_codes?: string[]
  badges?: string[]
}

function normalizeWorkspaceSummary(wire: PortfolioWorkspaceSummaryWire): PortfolioWorkspaceSummary {
  const navExact = exactDecimalString(wire.nav, 'workspace.nav')
  const dayChangeValueExact = exactDecimalString(wire.economic_pnl, 'workspace.economic_pnl')
  const dayChangePctMethod50 = exactDecimalString(
    wire.subperiod_twr_method50,
    'workspace.subperiod_twr_method50',
  )
  const dayChangePctPublished = exactDecimalString(
    wire.subperiod_twr_published,
    'workspace.subperiod_twr_published',
  )
  const badges = [...(wire.badges ?? [])]
  if (wire.publication?.stale) {
    badges.push('Published calculation is stale')
  }
  if (wire.publication?.pending) {
    badges.push('Recalculation pending')
  }
  if (wire.nav_coverage_state && wire.nav_coverage_state !== 'complete') {
    badges.push(`NAV coverage: ${wire.nav_coverage_state}`)
  }
  if (wire.return_coverage_state && wire.return_coverage_state !== 'complete') {
    badges.push(`Return coverage: ${wire.return_coverage_state}`)
  }
  if (wire.valuation_endpoint_status && wire.valuation_endpoint_status !== 'fresh') {
    badges.push(`Valuation: ${wire.valuation_endpoint_status}`)
  }
  return {
    ...wire,
    operating_profile: portfolioOperatingProfile(wire.operating_profile),
    nav: exactDecimalForDisplay(navExact, 'workspace.nav'),
    nav_exact: navExact,
    day_change_value: exactDecimalForDisplay(dayChangeValueExact, 'workspace.economic_pnl'),
    day_change_value_exact: dayChangeValueExact,
    day_change_pct: exactDecimalForDisplay(
      dayChangePctPublished,
      'workspace.subperiod_twr_published',
    ),
    day_change_pct_method50: dayChangePctMethod50,
    day_change_pct_published: dayChangePctPublished,
    badges: [...new Set(badges)],
  }
}

type PortfolioEntryWire = Omit<
  PortfolioEntryRecord,
  | 'nav'
  | 'nav_exact'
  | 'day_change_value'
  | 'day_change_value_exact'
  | 'day_change_pct'
  | 'day_change_pct_method50'
  | 'day_change_pct_published'
  | 'securities_count'
  | 'calculation_status'
  | 'lifecycle_status'
> & {
  nav?: unknown
  economic_pnl?: unknown
  subperiod_twr_method50?: unknown
  subperiod_twr_published?: unknown
  holding_count?: number
  calculation_status?: PortfolioEntryRecord['calculation_status']
  lifecycle_status: unknown
}

function portfolioLifecycleStatus(value: unknown): PortfolioEntryRecord['lifecycle_status'] {
  if (value === 'active' || value === 'archived') {
    return value
  }
  throw new Error('portfolio.lifecycle_status must be active or archived')
}

function portfolioOperatingProfile(value: unknown): PortfolioOperatingProfile {
  if (value === 'standard_taxonomy' || value === 'external_etf_rotation') {
    return value
  }
  throw new Error(
    'portfolio.operating_profile must be standard_taxonomy or external_etf_rotation',
  )
}

function normalizePortfolioEntry(wire: PortfolioEntryWire): PortfolioEntryRecord {
  const navExact = exactDecimalString(wire.nav, 'portfolio.nav')
  const dayChangeValueExact = exactDecimalString(wire.economic_pnl, 'portfolio.economic_pnl')
  const dayChangePctMethod50 = exactDecimalString(
    wire.subperiod_twr_method50,
    'portfolio.subperiod_twr_method50',
  )
  const dayChangePctPublished = exactDecimalString(
    wire.subperiod_twr_published,
    'portfolio.subperiod_twr_published',
  )
  return {
    ...wire,
    operating_profile: portfolioOperatingProfile(wire.operating_profile),
    as_of_date: wire.as_of_date ?? null,
    nav: exactDecimalForDisplay(navExact, 'portfolio.nav'),
    nav_exact: navExact,
    day_change_value: exactDecimalForDisplay(dayChangeValueExact, 'portfolio.economic_pnl'),
    day_change_value_exact: dayChangeValueExact,
    day_change_pct: exactDecimalForDisplay(
      dayChangePctPublished,
      'portfolio.subperiod_twr_published',
    ),
    day_change_pct_method50: dayChangePctMethod50,
    day_change_pct_published: dayChangePctPublished,
    securities_count: wire.holding_count ?? 0,
    calculation_status: wire.calculation_status ?? 'not_ready',
    lifecycle_status: portfolioLifecycleStatus(wire.lifecycle_status),
  }
}

type PortfolioHoldingRowWire = Omit<
  PortfolioHoldingRow,
  | 'quantity'
  | 'last_price'
  | 'market_value'
  | 'market_value_base'
  | 'day_change_pct'
  | 'day_change_value'
  | 'day_change_value_base'
  | 'portfolio_return_contribution'
  | 'cost_basis'
  | 'cost_basis_base'
  | 'unrealized_pnl'
  | 'unrealized_pnl_base'
  | 'unrealized_return'
  | 'allocation'
  | 'exact_values'
> & {
  quantity: unknown
  last_price: unknown
  market_value: unknown
  market_value_base?: unknown
  day_change_pct: unknown
  day_change_value: unknown
  day_change_value_base?: unknown
  portfolio_return_contribution?: unknown
  cost_basis: unknown
  cost_basis_base?: unknown
  unrealized_pnl?: unknown
  unrealized_pnl_base?: unknown
  unrealized_return?: unknown
  allocation: unknown
}

function normalizeHoldingRow(wire: PortfolioHoldingRowWire): PortfolioHoldingRow {
  const exactValues = {
    quantity: exactDecimalString(wire.quantity, 'holding.quantity'),
    last_price: exactDecimalString(wire.last_price, 'holding.last_price'),
    market_value: exactDecimalString(wire.market_value, 'holding.market_value'),
    market_value_base: exactDecimalString(wire.market_value_base, 'holding.market_value_base'),
    day_change_pct: exactDecimalString(wire.day_change_pct, 'holding.day_change_pct'),
    day_change_value: exactDecimalString(wire.day_change_value, 'holding.day_change_value'),
    day_change_value_base: exactDecimalString(
      wire.day_change_value_base,
      'holding.day_change_value_base',
    ),
    portfolio_return_contribution: exactDecimalString(
      wire.portfolio_return_contribution,
      'holding.portfolio_return_contribution',
    ),
    cost_basis: exactDecimalString(wire.cost_basis, 'holding.cost_basis'),
    cost_basis_base: exactDecimalString(wire.cost_basis_base, 'holding.cost_basis_base'),
    unrealized_pnl: exactDecimalString(wire.unrealized_pnl, 'holding.unrealized_pnl'),
    unrealized_pnl_base: exactDecimalString(
      wire.unrealized_pnl_base,
      'holding.unrealized_pnl_base',
    ),
    unrealized_return: exactDecimalString(wire.unrealized_return, 'holding.unrealized_return'),
    allocation: exactDecimalString(wire.allocation, 'holding.allocation'),
  }
  if (exactValues.quantity == null) {
    throw new Error('holding.quantity must be available.')
  }
  return {
    ...wire,
    quantity: exactDecimalForDisplay(exactValues.quantity, 'holding.quantity') ?? 0,
    last_price: exactDecimalForDisplay(exactValues.last_price, 'holding.last_price'),
    market_value: exactDecimalForDisplay(exactValues.market_value, 'holding.market_value'),
    market_value_base: exactDecimalForDisplay(
      exactValues.market_value_base,
      'holding.market_value_base',
    ),
    day_change_pct: exactDecimalForDisplay(exactValues.day_change_pct, 'holding.day_change_pct'),
    day_change_value: exactDecimalForDisplay(exactValues.day_change_value, 'holding.day_change_value'),
    day_change_value_base: exactDecimalForDisplay(
      exactValues.day_change_value_base,
      'holding.day_change_value_base',
    ),
    portfolio_return_contribution: exactDecimalForDisplay(
      exactValues.portfolio_return_contribution,
      'holding.portfolio_return_contribution',
    ),
    cost_basis: exactDecimalForDisplay(exactValues.cost_basis, 'holding.cost_basis'),
    cost_basis_base: exactDecimalForDisplay(exactValues.cost_basis_base, 'holding.cost_basis_base'),
    unrealized_pnl: exactDecimalForDisplay(exactValues.unrealized_pnl, 'holding.unrealized_pnl'),
    unrealized_pnl_base: exactDecimalForDisplay(
      exactValues.unrealized_pnl_base,
      'holding.unrealized_pnl_base',
    ),
    unrealized_return: exactDecimalForDisplay(exactValues.unrealized_return, 'holding.unrealized_return'),
    allocation: exactDecimalForDisplay(exactValues.allocation, 'holding.allocation'),
    exact_values: { ...exactValues, quantity: exactValues.quantity },
  }
}

type HoldingsWorkspaceWire = Omit<HoldingsWorkspaceResponse, 'rows' | 'totals'> & {
  rows: PortfolioHoldingRowWire[]
  totals: {
    market_value: unknown
    day_change_pct: unknown
    day_change_value: unknown
    cost_basis: unknown
    unrealized_pnl_base?: unknown
    unrealized_return?: unknown
    allocation: unknown
  }
}

function normalizeHoldingsWorkspace(wire: HoldingsWorkspaceWire): HoldingsWorkspaceResponse {
  const portfolioId = requiredText(wire.portfolio_id, 'holdings.portfolio_id')
  const publication = objectRecord(wire.publication, 'holdings.publication')
  requiredText(publication.publication_id, 'holdings.publication.publication_id')
  const sealedDisplayConfig = objectRecord(
    wire.sealed_display_config,
    'holdings.sealed_display_config',
  )
  const sealedTaxonomy = normalizeSealedTaxonomyDisplayConfig(
    sealedDisplayConfig.taxonomy,
    portfolioId,
  )
  const exactValues = {
    market_value: exactDecimalString(wire.totals.market_value, 'holdings.totals.market_value'),
    day_change_pct: exactDecimalString(wire.totals.day_change_pct, 'holdings.totals.day_change_pct'),
    day_change_value: exactDecimalString(wire.totals.day_change_value, 'holdings.totals.day_change_value'),
    cost_basis: exactDecimalString(wire.totals.cost_basis, 'holdings.totals.cost_basis'),
    unrealized_pnl_base: exactDecimalString(
      wire.totals.unrealized_pnl_base,
      'holdings.totals.unrealized_pnl_base',
    ),
    unrealized_return: exactDecimalString(
      wire.totals.unrealized_return,
      'holdings.totals.unrealized_return',
    ),
    allocation: exactDecimalString(wire.totals.allocation, 'holdings.totals.allocation'),
  }
  return {
    ...wire,
    portfolio_id: portfolioId,
    publication: publication as PortfolioDailyPublicationMetadata,
    sealed_display_config: { taxonomy: sealedTaxonomy },
    rows: wire.rows.map(normalizeHoldingRow),
    totals: {
      ...wire.totals,
      market_value: exactDecimalForDisplay(exactValues.market_value, 'holdings.totals.market_value'),
      day_change_pct: exactDecimalForDisplay(exactValues.day_change_pct, 'holdings.totals.day_change_pct'),
      day_change_value: exactDecimalForDisplay(exactValues.day_change_value, 'holdings.totals.day_change_value'),
      cost_basis: exactDecimalForDisplay(exactValues.cost_basis, 'holdings.totals.cost_basis'),
      unrealized_pnl_base: exactDecimalForDisplay(
        exactValues.unrealized_pnl_base,
        'holdings.totals.unrealized_pnl_base',
      ),
      unrealized_return: exactDecimalForDisplay(
        exactValues.unrealized_return,
        'holdings.totals.unrealized_return',
      ),
      allocation: exactDecimalForDisplay(exactValues.allocation, 'holdings.totals.allocation'),
      exact_values: exactValues,
    },
  }
}

export function getWorkspaceSummaryForPortfolio(portfolioId: string) {
  return fetchJson<PortfolioWorkspaceSummaryWire>(
    API_BASE_URL,
    `/api/workspace/summary?portfolio_id=${encodeURIComponent(portfolioId)}`,
  ).then(normalizeWorkspaceSummary)
}

export function getHoldingsWorkspace(
  portfolioId: string,
  filters: HoldingsWorkspaceFilters = {},
) {
  const query = buildQuery({
    portfolio_id: portfolioId,
    as_of_date: filters.as_of_date,
  })
  return fetchJson<HoldingsWorkspaceWire>(API_BASE_URL, `/api/workspace/holdings${query}`).then(
    normalizeHoldingsWorkspace,
  )
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
  return fetchJson<
    Omit<PortfolioInstrumentHoldingProjectionResponse, 'row'> & {
      row: PortfolioHoldingRowWire | null
    }
  >(
    API_BASE_URL,
    `/api/workspace/holdings/instrument${query}`,
  ).then((wire) => ({
    ...wire,
    row: wire.row == null ? null : normalizeHoldingRow(wire.row),
  }))
}

export function getPortfolios(options: { includeArchived?: boolean } = {}) {
  const query = buildQuery({
    include_archived: options.includeArchived ? 'true' : undefined,
  })
  return fetchJson<PortfolioEntryWire[]>(API_BASE_URL, `/api/portfolios${query}`).then((rows) =>
    rows.map(normalizePortfolioEntry),
  )
}

export function createPortfolio(payload: PortfolioCreatePayload) {
  return fetchJson<PortfolioEntryWire>(API_BASE_URL, '/api/portfolios', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then(normalizePortfolioEntry)
}

export function copyPortfolio(portfolioId: string) {
  return fetchJson<PortfolioEntryWire>(API_BASE_URL, `/api/portfolios/${portfolioId}/copy`, {
    method: 'POST',
  }).then(normalizePortfolioEntry)
}

export type PortfolioRiskPolicyUpdatePayload = {
  covariance_model_id: PortfolioRiskCovarianceModel
  lookback_days: number
  calculation_frequency: PortfolioRiskCalculationFrequency
  missing_return_policy: PortfolioRiskMissingReturnPolicy
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

export function archivePortfolio(portfolioId: string) {
  return fetchJson<PortfolioEntryWire>(API_BASE_URL, `/api/portfolios/${portfolioId}/archive`, {
    method: 'POST',
  }).then(normalizePortfolioEntry)
}

export function restorePortfolio(portfolioId: string) {
  return fetchJson<PortfolioEntryWire>(API_BASE_URL, `/api/portfolios/${portfolioId}/restore`, {
    method: 'POST',
  }).then(normalizePortfolioEntry)
}

export function reorderPortfolios(portfolioIds: string[]) {
  return fetchJson<PortfolioEntryWire[]>(API_BASE_URL, '/api/portfolios/reorder', {
    method: 'POST',
    body: JSON.stringify({ portfolio_ids: portfolioIds }),
  }).then((rows) => rows.map(normalizePortfolioEntry))
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

export function getPortfolioPositions(portfolioId: string) {
  return fetchJson<PortfolioDailyPublishedPositionListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/positions`,
  )
}

export function getPortfolioPositionLots(
  portfolioId: string,
  filters: PortfolioDailyPublishedLotFilters = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioDailyPublishedLotListResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/position-lots${query}`,
  )
}

export function getPortfolioFxRates(portfolioId: string) {
  return fetchJson<PortfolioSharedFxRatesResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/fx-rates`,
  ).then((response) => ({
    ...response,
    rates: response.rates.map((rate, index) => ({
      ...rate,
      rate:
        exactDecimalString(rate.rate, `fx_rates.rates[${index}].rate`) ??
        (() => {
          throw new Error(`fx_rates.rates[${index}].rate is required.`)
        })(),
    })),
  }))
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
  ).then((response) => ({
    ...response,
    value: exactDecimalString(response.value, 'execution_quote.value'),
    suggested_transaction_price: exactDecimalString(
      response.suggested_transaction_price,
      'execution_quote.suggested_transaction_price',
    ),
  }))
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

export function getPortfolioPerformanceReport(
  portfolioId: string,
  filters: PortfolioPerformanceFilters & {
    axis?: PortfolioDailyPublishedAttributionAxis
    frequency?: PortfolioDailyPublishedCalendarFrequency
    group_key?: string
  } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<unknown>(
    API_BASE_URL,
    `/api/portfolios/${encodeURIComponent(portfolioId)}/performance/report${query}`,
  ).then(normalizePublishedPerformanceReport)
}

export function getPortfolioRiskWorkspace(
  portfolioId: string,
  filters: {
    as_of_date: string
    rolling_lookback_days: number
    matrix_lookback_days: number
    matrix_scope_node_id: string
    matrix_as_of_date?: string
    benchmark_instrument_id?: string
  },
) {
  const query = buildQuery({
    as_of_date: filters.as_of_date,
    rolling_lookback_days: String(filters.rolling_lookback_days),
    matrix_lookback_days: String(filters.matrix_lookback_days),
    matrix_scope_node_id: filters.matrix_scope_node_id,
    matrix_as_of_date: filters.matrix_as_of_date,
    benchmark_instrument_id: filters.benchmark_instrument_id,
  })
  return fetchJson<PortfolioRiskWorkspaceResponse>(
    API_BASE_URL,
    `/api/portfolios/${encodeURIComponent(portfolioId)}/risk/workspace${query}`,
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

export function getPortfolioAllocationResearchWorkbench(portfolioId: string, selectedRunId?: string) {
  const query = buildQuery({ selected_run_id: selectedRunId })
  return fetchJson<PortfolioAllocationResearchWorkbenchResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/allocation-research/workbench${query}`,
  )
}

export function updatePortfolioAllocationResearchSettings(
  portfolioId: string,
  payload: PortfolioAllocationResearchSettingsUpdatePayload,
) {
  return fetchJson<PortfolioAllocationResearchSettingsRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/allocation-research/settings`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function createPortfolioAllocationResearchRun(
  portfolioId: string,
  payload: PortfolioAllocationResearchRunCreatePayload = {},
) {
  return fetchJson<PortfolioAllocationResearchRunRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/allocation-research/runs`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function getPortfolioPolicyReplayBenchmarkComparison(
  portfolioId: string,
  allocationResearchRunId: string,
  benchmarkInstrumentId: string,
) {
  const query = buildQuery({ benchmark_instrument_id: benchmarkInstrumentId })
  return fetchJson<PortfolioPolicyReplayBenchmarkComparisonResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/allocation-research/runs/${allocationResearchRunId}/policy-replay/benchmark-comparison${query}`,
  )
}

export function getPortfolioAllocationResearchArtifactContent(portfolioId: string, path: string) {
  const query = buildQuery({ path })
  return fetchJson<PortfolioAllocationResearchArtifactContentResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/allocation-research/artifacts/content${query}`,
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

export function deletePortfolioTaxonomy(portfolioId: string, taxonomyId: string) {
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}`,
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

export function deletePortfolioTaxonomyNode(portfolioId: string, taxonomyId: string, taxonomyNodeId: string) {
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; taxonomy_node_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/nodes/${taxonomyNodeId}`,
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
) {
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; assignment_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/assignments/${assignmentId}`,
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

export function deletePortfolioTargetSet(portfolioId: string, taxonomyId: string, targetSetId: string) {
  return fetchJson<{ portfolio_id: string; taxonomy_id: string; target_set_id: string; deleted: boolean }>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/taxonomies/${taxonomyId}/target-sets/${targetSetId}`,
    {
      method: 'DELETE',
    },
  )
}

export function createPortfolioTransaction(
  portfolioId: string,
  payload: PortfolioTransactionCreatePayload,
) {
  return fetchJson<PortfolioTransactionRecord>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions`,
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

export function getPortfolioTransactionRevisionHistory(
  portfolioId: string,
  transactionId: string,
) {
  return fetchJson<PortfolioTransactionRevisionHistoryResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}/revisions`,
  )
}

export function deletePortfolioTransaction(
  portfolioId: string,
  transactionId: string,
  payload: PortfolioTransactionDeletePayload,
) {
  return fetchJson<PortfolioTransactionDeleteResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}`,
    {
      method: 'DELETE',
      body: JSON.stringify(payload),
    },
  )
}

export function createPortfolioInternalTransfer(
  portfolioId: string,
  payload: PortfolioInternalTransferCreatePayload,
) {
  return fetchJson<PortfolioTransactionBatchResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/internal-transfer`,
    {
      method: 'POST',
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
