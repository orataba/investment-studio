import type {
  AssetCore,
  AssetIdentifier,
  DataStatus,
  MetricFamily,
  QuoteBasis,
  QuoteSelectionPolicy,
} from '../../../../../packages/asset-core/ts/src'

export type { AssetCore, AssetIdentifier } from '../../../../../packages/asset-core/ts/src'

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

export type PortfolioAssetChartRangeKey = '1m' | '3m' | '6m' | 'ytd' | '1y' | 'all'

export type PortfolioAssetPriceChartPoint = {
  date: string
  value: number
}

export type PortfolioAssetPriceChartSummary = {
  point_count: number
  change_value: number | null
  change_pct: number | null
  high: number | null
  low: number | null
}

export type PortfolioAssetPriceChartResponse = {
  portfolio_id: string
  asset_core: AssetCore
  as_of_date: string
  range_key: PortfolioAssetChartRangeKey
  chart_basis: string | null
  metric_family: string | null
  currency: string
  points: PortfolioAssetPriceChartPoint[]
  summary: PortfolioAssetPriceChartSummary
}

export type PortfolioPerformanceCoverageState = 'complete' | 'partial' | 'unavailable'

export type PortfolioDailyPerformancePoint = {
  as_of_date: string
  coverage_state: PortfolioPerformanceCoverageState
  stale_price_flag: boolean
  stale_fx_flag: boolean
  beginning_nav: number | null
  ending_nav: number | null
  realized_pnl: number | null
  unrealized_pnl: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  cash_currency_gains: number | null
  asset_currency_gains: number | null
  return_of_capital_amount: number | null
  total_pnl: number | null
  external_cash_in: number
  external_cash_out: number
  net_external_inflow: number
  absolute_change: number | null
  delta: number | null
  daily_ttwror: number | null
  cumulative_ttwror: number | null
  drawdown: number | null
}

export type PortfolioPerformanceSummary = {
  start_date: string | null
  end_date: string | null
  coverage_state: PortfolioPerformanceCoverageState
  snapshot_count: number
  return_observation_count: number
  latest_complete_as_of_date: string | null
  start_nav: number | null
  end_nav: number | null
  external_cash_in: number
  external_cash_out: number
  net_external_inflow: number
  cumulative_ttwror: number | null
  annualized_ttwror: number | null
  irr: number | null
  mwror: number | null
  absolute_change: number | null
  delta: number | null
  realized_pnl: number | null
  unrealized_pnl: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  cash_currency_gains: number | null
  asset_currency_gains: number | null
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
  coverage_state: PortfolioPerformanceCoverageState
  stale_price_flag: boolean
  stale_fx_flag: boolean
  initial_value: number | null
  final_value: number | null
  delta: number | null
  capital_gains: number | null
  realized_capital_gains: number | null
  earnings: number | null
  fees: number | null
  taxes: number | null
  cash_currency_gains: number | null
  asset_currency_gains: number | null
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

export type PortfolioPeriodBoundaryHoldingRecord = {
  position_id: string
  asset_id: string
  instrument_ref: AssetCore
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

export type PortfolioContributionAxis = 'instrument' | 'account' | 'taxonomy'

export type PortfolioContributionLineRecord = {
  axis: PortfolioContributionAxis
  group_key: string
  group_label: string
  start_value_base: number | null
  end_value_base: number | null
  average_weight: number | null
  ending_weight: number | null
  realized_pnl: number | null
  unrealized_pnl_change: number | null
  income_cash_amount: number | null
  expense_cash_amount: number | null
  fee_amount: number | null
  tax_amount: number | null
  cash_currency_gains: number | null
  asset_currency_gains: number | null
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
  coverage_state: PortfolioPerformanceCoverageState
  slice_count: number
  group_count: number
  observation_count: number
  start_nav: number | null
  end_nav: number | null
  portfolio_arithmetic_return: number | null
  portfolio_cumulative_ttwror: number | null
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
    beginning_value_base: number | null
    ending_value_base: number | null
    beginning_weight: number | null
    ending_weight: number | null
    cash_balance_base: number | null
    position_market_value_base: number | null
    open_cost_basis_base: number | null
    realized_pnl: number | null
    unrealized_pnl: number | null
    income_cash_amount: number | null
    expense_cash_amount: number | null
    fee_amount: number | null
    tax_amount: number | null
    cash_currency_gains: number | null
    asset_currency_gains: number | null
    total_pnl: number | null
    daily_return: number | null
    daily_contribution: number | null
  }>
}

export type PortfolioHoldingRow = {
  line_id: string
  asset_core: AssetCore
  quantity: number
  last_price: number | null
  market_value: number | null
  market_value_base?: number | null
  day_change_pct: number | null
  day_change_value: number | null
  cost_basis: number | null
  cost_basis_base?: number | null
  allocation: number | null
  price_chart: SparklinePoint[]
  coverage_status: string
  account_count?: number
  open_position_lot_count?: number
}

export type HoldingsWorkspaceResponse = {
  portfolio_id: string
  portfolio_name: string
  base_currency: string
  as_of_date: string
  view_label: string
  coverage_note: string
  summary_cards: HoldingsSummaryCard[]
  rows: PortfolioHoldingRow[]
  totals: {
    market_value: number | null
    day_change_pct: number | null
    day_change_value: number | null
    cost_basis: number | null
    allocation: number | null
  }
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
  provider?: string | null
  status: DataStatus
}

export type SharedInstrumentRecord = {
  asset_id: string
  asset_name: string
  asset_type: AssetCore['asset_type']
  currency: string
  identifiers: AssetIdentifier[]
  latest_market_data: SharedMarketDataPoint[]
  quote_selection_policy?: QuoteSelectionPolicy
  coverage_state: DataStatus
}

export type PortfolioSharedInstrumentsResponse = {
  portfolio_id: string
  instruments: SharedInstrumentRecord[]
}

type RawSharedInstrumentRecord = {
  asset_core: AssetCore
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
  rate: number
  as_of_date: string
  source_kind: string
  asset_id?: string | null
  source_asset_ids: string[]
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
  effective_from?: string | null
  effective_to?: string | null
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
  effective_from?: string | null
  effective_to?: string | null
  status: string
}

export type PortfolioTargetSetType = 'saa' | 'taa'

export type PortfolioTargetSetRecord = {
  target_set_id: string
  taxonomy_id: string
  comparator_taxonomy_node_id?: string | null
  target_set_type: PortfolioTargetSetType
  name: string
  effective_from?: string | null
  effective_to?: string | null
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

export type PortfolioTaxonomyCatalogResponse = {
  portfolio_id: string
  default_planning_taxonomy_id?: string | null
  taxonomies: PortfolioTaxonomyRecord[]
  taxonomy_nodes: PortfolioTaxonomyNodeRecord[]
  taxonomy_assignments: PortfolioTaxonomyAssignmentRecord[]
  target_sets: PortfolioTargetSetRecord[]
  target_set_lines: PortfolioTargetSetLineRecord[]
}

export type PortfolioDefaultPlanningTaxonomyUpdatePayload = {
  taxonomy_id?: string | null
}

export type PortfolioDefaultPlanningTaxonomyResponse = {
  portfolio_id: string
  default_planning_taxonomy_id?: string | null
}

export type PortfolioResearchRunStatus = 'running' | 'completed' | 'failed'
export type PortfolioResearchRunTemplate = 'taxonomy_backtest'
export type PortfolioResearchBenchmarkMode = 'none'
export type PortfolioResearchTargetSetMode = 'saa' | 'taa_over_saa'
export type PortfolioResearchTargetDimension = 'scope_default' | 'weight' | 'risk_budget'
export type PortfolioResearchRebalanceFrequency = 'weekly' | 'monthly' | 'quarterly'
export type PortfolioResearchCapitalMode = 'unit_notional' | 'fixed_gross' | 'target_volatility'
export type PortfolioResearchArtifactPreviewKind = 'text' | 'html' | 'binary'

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
  as_of_date?: string | null
  start_date?: string | null
  lookback_days: number
  benchmark_mode: PortfolioResearchBenchmarkMode
  run_template: PortfolioResearchRunTemplate
  target_set_mode: PortfolioResearchTargetSetMode
  target_dimension: PortfolioResearchTargetDimension
  capital_mode: PortfolioResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  rebalance_frequency: PortfolioResearchRebalanceFrequency
  notes?: string | null
  updated_at?: string | null
}

export type PortfolioResearchSettingsUpdatePayload = {
  planning_taxonomy_id?: string | null
  comparator_taxonomy_node_id?: string | null
  as_of_date?: string | null
  start_date?: string | null
  lookback_days: number
  benchmark_mode?: PortfolioResearchBenchmarkMode
  run_template?: PortfolioResearchRunTemplate
  target_set_mode?: PortfolioResearchTargetSetMode
  target_dimension?: PortfolioResearchTargetDimension
  capital_mode?: PortfolioResearchCapitalMode
  gross_exposure?: number | null
  target_volatility?: number | null
  max_gross_exposure?: number | null
  rebalance_frequency?: PortfolioResearchRebalanceFrequency
  notes?: string | null
}

export type PortfolioResearchContextSignalRecord = {
  label: string
  value: string
  tone: string
}

export type PortfolioResearchHoldingSnapshotRecord = {
  asset_id: string
  asset_name: string
  asset_type?: string | null
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

export type PortfolioResearchBacktestMetricRecord = {
  metric_id: string
  label: string
  value?: number | null
}

export type PortfolioResearchBacktestCurvePointRecord = {
  date: string
  nav: number
  drawdown: number
  portfolio_return: number
  rebalance_flag: boolean
}

export type PortfolioResearchWeightSchedulePointRecord = {
  date: string
  rebalance_flag: boolean
  nav?: number | null
  weights: Record<string, number>
}

export type PortfolioResearchBacktestMemberSummaryRecord = {
  member_type: string
  member_id: string
  label: string
  default_target_dimension?: 'weight' | 'risk_budget' | null
  selected_target_dimension?: PortfolioResearchTargetDimension | null
  source_target_set_type?: 'saa' | 'taa' | null
  start_weight?: number | null
  end_weight?: number | null
  weight_change?: number | null
  average_weight?: number | null
  min_weight?: number | null
  max_weight?: number | null
  latest_target_weight?: number | null
  latest_target_risk_share?: number | null
  latest_implementation_weight?: number | null
  selected_target_value?: number | null
  cumulative_return?: number | null
}

export type PortfolioResearchRebalanceEventRecord = {
  rebalance_date: string
  scope_label: string
  target_dimension?: PortfolioResearchTargetDimension | null
  solver_kind?: string | null
  turnover?: number | null
  pre_rebalance_weight_total?: number | null
  target_weight_total?: number | null
  max_weight_gap_before_rebalance?: number | null
  max_risk_share_gap?: number | null
  member_count: number
}

export type PortfolioResearchRebalanceSuggestionRecord = {
  member_type: string
  member_id: string
  label: string
  current_weight?: number | null
  target_weight?: number | null
  gap?: number | null
  current_value_base?: number | null
  base_currency: string
  action: string
}

export type PortfolioResearchConstructionRowRecord = {
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
  backtest_metrics: PortfolioResearchBacktestMetricRecord[]
  backtest_curve: PortfolioResearchBacktestCurvePointRecord[]
  weight_schedule: PortfolioResearchWeightSchedulePointRecord[]
  member_summaries: PortfolioResearchBacktestMemberSummaryRecord[]
  construction_assumptions: string[]
  construction_rows: PortfolioResearchConstructionRowRecord[]
  rebalance_events: PortfolioResearchRebalanceEventRecord[]
  rebalance_suggestions: PortfolioResearchRebalanceSuggestionRecord[]
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
  benchmark_mode: PortfolioResearchBenchmarkMode
  run_template: PortfolioResearchRunTemplate
  requested_by?: string | null
  headline?: string | null
  error_message?: string | null
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
  settings: PortfolioResearchSettingsRecord
  current_context: PortfolioResearchCurrentContextRecord
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
  name: string
  taxonomy_type?: string
  purpose?: string | null
  primary_assignment_scope: TaxonomyAssignmentScope
  planning_enabled?: boolean
  budgeting_level?: string | null
  root_default_target_dimension?: 'weight' | 'risk_budget'
  effective_from?: string | null
  effective_to?: string | null
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
  effective_from?: string | null
  effective_to?: string | null
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
  effective_from?: string | null
  effective_to?: string | null
  status?: string
}

export type PortfolioTaxonomyAssignmentUpdatePayload = {
  taxonomy_node_id?: string | null
  effective_from?: string | null
  effective_to?: string | null
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
  effective_from?: string | null
  effective_to?: string | null
  weight_enabled: boolean
  risk_budget_enabled: boolean
  status?: string
  notes?: string | null
  lines: PortfolioTargetSetLinePayload[]
}

export type PortfolioTargetSetUpdatePayload = {
  name?: string | null
  effective_from?: string | null
  effective_to?: string | null
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
  allowed_asset_types?: string[] | null
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
  allowed_asset_types?: string[] | null
  opened_at?: string | null
  closed_at?: string | null
  status?: string
}

export type PortfolioLedgerPostingRecord = {
  posting_id: string
  transaction_id: string
  portfolio_id: string
  account_id: string
  posting_role: string
  source_transaction_type: string
  trade_date: string
  settlement_date: string
  asset_id?: string | null
  instrument_ref?: AssetCore | null
  cash_amount_delta?: number | null
  quantity_delta?: number | null
  cost_basis_delta?: number | null
  currency: string
  transfer_group_id?: string | null
  note?: string | null
}

export type PortfolioAccountPositionRecord = {
  position_id?: string | null
  account_id: string
  asset_id: string
  instrument_ref: AssetCore
  quantity: number
  cost_basis?: number | null
  last_price?: number | null
  market_value?: number | null
  currency: string
  cost_basis_method?: 'moving_average' | 'fifo' | null
  open_position_lot_count?: number
}

export type PortfolioAccountWorkspaceAccount = {
  account: PortfolioAccountRecord
  default_settlement_cash_account_name?: string | null
  linked_transaction_count: number
  linked_posting_count: number
  derived_cash_balance: number
  derived_cash_balance_base?: number | null
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
  linked_transactions_summary?: PortfolioTransactionListResponse['summary'] | null
  linked_transactions: PortfolioTransactionRecord[]
}

export type PortfolioLedgerPostingListResponse = {
  portfolio_id: string
  summary: {
    posting_count: number
    cash_posting_count: number
    position_posting_count: number
  }
  ledger_postings: PortfolioLedgerPostingRecord[]
}

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
  asset_id?: string | null
  instrument_ref?: AssetCore | null
  quantity?: number | null
  price?: number | null
  gross_amount: number
  counter_amount?: number | null
  fx_rate?: number | null
  fees: number
  taxes: number
  currency: string
  transfer_scope?: string | null
  transfer_object_type?: string | null
  transfer_group_id?: string | null
  counterparty_account_id?: string | null
  net_cash_effect?: number | null
  note?: string | null
  created_at?: string | null
}

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
  selected_transaction_id?: string | null
  transactions: PortfolioTransactionRecord[]
  selected_transaction?: PortfolioTransactionRecord | null
  ledger_summary: PortfolioLedgerPostingListResponse['summary']
  ledger_postings: PortfolioLedgerPostingRecord[]
  related_position_lot_summary: PortfolioPositionLotListResponse['summary']
  related_position_lots: PortfolioPositionLotRecord[]
}

export type PortfolioTransactionPositionPreviewResponse = {
  portfolio_id: string
  account_id: string
  asset_id: string
  as_of_date: string
  trade_at: string
  quantity: number
}

export type PortfolioPositionRecord = {
  position_id: string
  portfolio_id: string
  asset_id: string
  instrument_ref: AssetCore
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
  asset_id: string
  instrument_ref: AssetCore
  currency: string
  cost_basis_method: 'moving_average' | 'fifo'
  opened_by_transaction_id: string
  opening_transaction_type: string
  opened_at: string
  closed_at?: string | null
  status: 'open' | 'closed'
  close_reason?: 'disposed' | 'transferred' | null
  source_position_lot_id?: string | null
  entry_quantity: number
  remaining_quantity: number
  realized_quantity: number
  transferred_quantity: number
  entry_cost_basis: number
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
  asset_id?: string
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
  asset_id?: string
  start_date?: string
  end_date?: string
}

export type PortfolioTransactionCreatePayload = {
  transaction_type: string
  trade_date: string
  trade_time?: string | null
  settlement_date?: string | null
  entitlement_date?: string | null
  acquisition_date?: string | null
  account_id: string
  settlement_cash_account_id?: string | null
  asset_id?: string | null
  quantity?: number | null
  price?: number | null
  gross_amount: number
  counter_amount?: number | null
  fx_rate?: number | null
  fees?: number
  taxes?: number
  currency: string
  transfer_scope?: string | null
  transfer_object_type?: string | null
  transfer_group_id?: string | null
  counterparty_account_id?: string | null
  note?: string | null
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
  asset_id?: string | null
  quantity?: number | null
  gross_amount?: number | null
  note?: string | null
  transfer_group_id?: string | null
}

export type PortfolioTransactionBatchResponse = {
  portfolio_id: string
  created_count: number
  transfer_group_id?: string | null
  transactions: PortfolioTransactionRecord[]
}

export type PortfolioTransactionDeleteResponse = {
  portfolio_id: string
  deleted_count: number
  deleted_transaction_ids: string[]
  transfer_group_id?: string | null
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

async function fetchJson<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  })

  if (!response.ok) {
    const body = await response.text()
    if (body) {
      try {
        const parsed = JSON.parse(body) as { detail?: string }
        throw new Error(parsed.detail || body)
      } catch {
        throw new Error(body)
      }
    }

    throw new Error(`Request failed: ${response.status}`)
  }

  return (await response.json()) as T
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
  })
  return fetchJson<HoldingsWorkspaceResponse>(API_BASE_URL, `/api/workspace/holdings${query}`)
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

export function getPortfolioAccounts(portfolioId: string) {
  return fetchJson<PortfolioAccountsResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/accounts`)
}

export function createPortfolioAccount(portfolioId: string, payload: PortfolioAccountCreatePayload) {
  return fetchJson<PortfolioAccountRecord>(API_BASE_URL, `/api/portfolios/${portfolioId}/accounts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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
    asset_id: string
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

export function getPortfolioAssetPriceChart(
  portfolioId: string,
  assetId: string,
  filters: {
    as_of_date?: string
    range?: PortfolioAssetChartRangeKey
  } = {},
) {
  const query = buildQuery(filters)
  return fetchJson<PortfolioAssetPriceChartResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/assets/${assetId}/price-chart${query}`,
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

export function getPortfolioPerformanceContribution(
  portfolioId: string,
  filters: PortfolioPerformanceFilters & { axis?: PortfolioContributionAxis } = {},
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

export function getPortfolioTaxonomyCatalog(portfolioId: string) {
  return fetchJson<PortfolioTaxonomyCatalogResponse>(API_BASE_URL, `/api/portfolios/${portfolioId}/taxonomies`)
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

export function getPortfolioResearchWorkbench(portfolioId: string, selectedRunId?: string) {
  const query = buildQuery({ selected_run_id: selectedRunId })
  return fetchJson<PortfolioResearchWorkbenchResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/research/workbench${query}`,
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
  payload: PortfolioTransactionCreatePayload,
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

export function deletePortfolioTransaction(portfolioId: string, transactionId: string) {
  return fetchJson<PortfolioTransactionDeleteResponse>(
    API_BASE_URL,
    `/api/portfolios/${portfolioId}/transactions/${transactionId}`,
    {
      method: 'DELETE',
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
        ...instrument.asset_core,
        coverage_state: instrument.coverage_state,
        latest_market_data: instrument.latest_market_data,
        quote_selection_policy: instrument.quote_selection_policy,
      })),
    }),
  )
}
