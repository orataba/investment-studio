export type WatchlistRecord = {
  watchlist_id: string
  name: string
  description: string | null
  item_count: number
  owner_type: string
  owner_id: string
  is_default: boolean
  is_shared: boolean
  default_view_id: string | null
}

export type WatchlistView = {
  view_id: string
  view_key: string
  name: string
  description: string | null
  kind: string
  default_group_by: string | null
  default_sort: Array<{ field: string; direction: string }>
  default_filters: Record<string, unknown>
  default_advanced_filters: Record<string, unknown> | null
  columns: string[]
  column_meta?: Array<{
    field_key: string
    display_order: number
    width?: number | null
    is_visible?: boolean
  }>
}

export type WatchlistViewColumnPayload = {
  field_key: string
  display_order: number
  width?: number | null
  is_visible?: boolean
}

export type WatchlistViewCreatePayload = {
  name: string
  description?: string | null
  default_group_by?: string | null
  default_sort?: Array<{ field: string; direction: string }>
  default_filters?: Record<string, unknown>
  default_advanced_filters?: Record<string, unknown> | null
  columns?: WatchlistViewColumnPayload[]
}

export type WatchlistCreatePayload = {
  name: string
  description?: string | null
}

export type WatchlistDeleteResponse = {
  watchlist_id: string
  deleted: boolean
}

export type WatchlistItemsMoveResponse = {
  source_watchlist_id: string
  target_watchlist_id: string
  moved_count: number
  added_count: number
  already_present_count: number
}

export type WatchlistItemsCopyResponse = {
  source_watchlist_id: string
  target_watchlist_id: string
  copied_count: number
  added_count: number
  already_present_count: number
}

export type SharedInstrumentRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type: string
  currency: string
  identifiers: Array<{
    identifier_type: string
    identifier_value: string
    is_primary: boolean
  }>
  coverage_state?: string
}

export type GroupByOption = {
  code: string
  label: string
}

export type WatchlistDetail = WatchlistRecord & {
  views: WatchlistView[]
  available_group_bys: GroupByOption[]
  default_filters_summary: Record<string, unknown[]>
}

export type FieldCategory = {
  category_code: string
  label: string
  display_order: number
  parent_category_code: string | null
}

export type FieldRegistryRecord = {
  field_key: string
  label: string
  description: string | null
  category_code: string
  data_type: string
  formatter_code: string
  sort_mode: string
  filter_mode: string
  group_mode: string
  instrument_scope_json: string[]
  product_scope_json: string[]
  availability_rule_json: Record<string, unknown>
  source_domain: string
  source_metric_code: string
  default_width: number | null
  default_visible: boolean
}

export type FieldRegistryResponse = {
  categories: FieldCategory[]
  fields: FieldRegistryRecord[]
  total_fields: number
}

export type InstrumentAttributeDefinition = {
  attribute_key: string
  label: string
  description: string | null
  data_type: 'single_select' | 'multi_select' | 'boolean' | 'number' | 'text' | 'date'
  domain_code: 'overview' | 'research' | 'monitoring'
  group_code: string
  display_order: number
  options: string[]
  instrument_scope_json: string[]
  applicability_json: Record<string, string[]>
  rubric_json: Record<string, unknown>
  is_groupable: boolean
  is_filterable: boolean
  is_view_column: boolean
  default_visible: boolean
  required_for_monitoring: boolean
}

export type FundTaxonomyContext = {
  taxonomy_code: string
  assigned_node_id: string | null
  assigned_label: string | null
  path_labels: string[]
  path_node_ids: string[]
  depth: number
  derived_values: Record<string, string>
}

export type FundTaxonomyTreeNode = {
  node_id: string
  label: string
  parent_node_id: string | null
  level_index: number
  display_order: number
  is_leaf: boolean
  path_labels: string[]
  path_node_ids: string[]
}

export type FundTaxonomyTreeResponse = {
  taxonomy_code: string
  instrument_type: string
  instrument_types: string[]
  max_depth: number
  nodes: FundTaxonomyTreeNode[]
}

export type InstrumentAttributeValuesResponse = {
  instrument_id: string
  definitions: InstrumentAttributeDefinition[]
  values: Record<string, unknown>
  taxonomy: FundTaxonomyContext
}

export type InstrumentResolveResponse = {
  requested_instrument_id: string
  canonical_instrument_id: string | null
  instrument_name: string
  instrument_type: string
  primary_identifier: string | null
  detail_view_type: string
  detail_subject_id: string | null
  detail_supported: boolean
  support_reason: string
  corporate_actions: CorporateActionEvent[]
}

export type CorporateActionEvent = {
  corporate_action_event_id: string
  action_type: 'share_split'
  announcement_date: string | null
  record_date: string | null
  effective_date: string
  payable_date: string | null
  new_units: string | number
  old_units: string | number
  quantity_rounding: 'exact' | 'truncate' | 'round_half_up' | 'cash_in_lieu'
  quantity_precision: number
  source: string
  status: 'detected' | 'confirmed' | 'cancelled'
  provenance?: Record<string, unknown>
}

export type InstrumentAttributeDefinitionCreatePayload = {
  attribute_key: string
  label: string
  description?: string | null
  data_type: 'single_select' | 'multi_select' | 'boolean' | 'number' | 'text' | 'date'
  domain_code: 'overview' | 'research' | 'monitoring'
  group_code: string
  display_order?: number
  options?: string[]
  instrument_scope_json?: string[]
  applicability_json?: Record<string, string[]>
  rubric_json?: Record<string, unknown>
  is_groupable?: boolean
  is_filterable?: boolean
  is_view_column?: boolean
  default_visible?: boolean
  required_for_monitoring?: boolean
}

export type InstrumentAttributeUpdatePayload = {
  values: Array<{
    attribute_key: string
    value: unknown
  }>
}

export type ScreenerGroup = {
  group_value: string
  row_count: number
  group_depth?: number
  group_path?: string[]
}

export type ScreenerSnapshotMetadata = {
  as_of_date: string | null
  methodology_version: string
  market_data_input_watermark_at: string | null
  last_recalculated_at: string | null
  is_current: boolean
  advanced_filter_applied: boolean
}

export type ScreenerResponse = {
  rows: Array<Record<string, unknown>>
  groups: ScreenerGroup[]
  total_rows: number
  stale_row_count: number
  sparklines?: Record<string, FundChartPoint[]>
  snapshot_metadata: ScreenerSnapshotMetadata
}

export type MonitoringMembership = {
  watchlist_id: string
  name: string
}

export type MonitoringWatchlistSummary = {
  watchlist_id: string
  name: string
  item_count: number
  needs_refresh_count: number
  missing_quote_count: number
  missing_label_count: number
  open_recalc_job_count: number
  last_activity_at: string | null
}

export type MonitoringInstrumentRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type: string
  ticker_or_isin: string | null
  management_firm_name: string | null
  data_freshness_status: string
  latest_quote_date: string | null
  market_data_input_watermark_at: string | null
  last_recalculated_at: string | null
  last_activity_at: string | null
  staleness_reason: string | null
  primary_watchlist_id: string
  primary_watchlist_name: string
  watchlists: MonitoringMembership[]
  watchlist_count: number
  missing_attribute_keys: string[]
  missing_attribute_labels: string[]
  missing_attribute_count: number
  issue_flags: string[]
}

export type MonitoringRecalcJobRecord = {
  recalc_job_id: string
  instrument_id: string
  instrument_name: string
  job_type: string
  job_status: string
  trigger_type: string
  enqueued_at: string | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  primary_watchlist_id: string | null
  primary_watchlist_name: string | null
  watchlists: MonitoringMembership[]
}

export type MonitoringDashboardResponse = {
  generated_at: string | null
  overview: {
    watchlist_count: number
    unique_instrument_count: number
    needs_refresh_count: number
    missing_quote_count: number
    missing_label_count: number
    open_recalc_job_count: number
    failed_recalc_job_count: number
  }
  watchlists: MonitoringWatchlistSummary[]
  needs_attention_instruments: MonitoringInstrumentRecord[]
  missing_label_instruments: MonitoringInstrumentRecord[]
  open_recalc_jobs: MonitoringRecalcJobRecord[]
}

export type RecalcExecuteResponse = {
  recalc_job_id: string
  job_status: string
  result: Record<string, unknown>
}

export type FundSummaryResponse = {
  instrument_id?: string
  fund_id: string
  fund_name: string
  ticker_or_isin: string
  management_firm_name: string | null
  research_rating: ResearchRatingRecord | null
  instrument_attributes: Record<string, unknown>
  taxonomy: FundTaxonomyContext
  key_stats: Array<{ label: string; value: string | number | null }>
  freshness: {
    data_freshness_status: string
    last_recalculated_at: string | null
    last_successful_snapshot_at: string | null
    market_data_input_watermark_at: string | null
    market_data_input_watermark_status: 'known' | 'unknown'
    market_data_input_watermark_reason_code: string
    staleness_reason_codes: string[]
    staleness_reason: string | null
  }
  quick_monitoring_items: string[]
  tabs: string[]
  consumer_freshness_profile?: ConsumerFreshnessProfile
  calculation_state?: QuoteCalculationState
  quote_resolution?: CanonicalQuoteSeriesResolutionSummary
  nav_snapshot?: {
    nav_basis_type?: string | null
    nav_basis_source?: string | null
    selected_role?: string | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    selected_date_label?: string | null
    latest_nav?: number | null
    latest_nav_with_dividend?: number | null
  }
  selected_series?: SelectedQuoteSeriesMetadata
}

export type FundLibraryItem = {
  fund_id: string
  fund_name: string
  ticker_or_isin: string | null
  product_type: string
}

type RawFundLibraryItem = {
  instrument_id: string
  instrument_name: string
  instrument_type: string
  detail_view_type: string
  primary_identifier: string | null
}

export type FundChartPoint = { date: string; value: number }

export type PerformanceMetricPeriodKey =
  | '1W'
  | 'MTD'
  | 'YTD'
  | '1Y'
  | '2Y'
  | '3Y'
  | '5Y'
  | 'SI'

export type PerformanceMetricSnapshot = {
  period_return: number | null
  annualized_return: number | null
  annualized_volatility: number | null
  annualized_downside_deviation: number | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  calmar_ratio: number | null
  max_drawdown: number | null
  recovery_days: number | null
  recovery_open: boolean
}

export type PerformanceRelativeSnapshot = {
  excess_return: number | null
  information_ratio: number | null
  tracking_error: number | null
  beta: number | null
  upside_capture: number | null
  downside_capture: number | null
}

export type InvestmentAnalyticsMetricQuality = {
  status: 'available' | 'qualified' | 'unavailable' | 'not_requested'
  reason: string | null
  observation_count: number
  excluded_observation_count: number
  used_window_count: number
  excluded_window_count: number
}

export type PerformanceSnapshotQuality = {
  window: InvestmentAnalyticsMetricQuality
  annualized_return: InvestmentAnalyticsMetricQuality
  annualized_volatility: InvestmentAnalyticsMetricQuality
  sharpe_ratio: InvestmentAnalyticsMetricQuality
  downside_deviation: InvestmentAnalyticsMetricQuality
  sortino_ratio: InvestmentAnalyticsMetricQuality
  calmar_ratio: InvestmentAnalyticsMetricQuality
}

export type PerformanceRelativeQuality = {
  alignment: InvestmentAnalyticsMetricQuality
  excess_return: InvestmentAnalyticsMetricQuality
  tracking_error: InvestmentAnalyticsMetricQuality
  information_ratio: InvestmentAnalyticsMetricQuality
  beta: InvestmentAnalyticsMetricQuality
  upside_capture: InvestmentAnalyticsMetricQuality
  downside_capture: InvestmentAnalyticsMetricQuality
}

export type PerformancePeriodAnalytics = {
  period: PerformanceMetricPeriodKey
  fund: PerformanceMetricSnapshot
  benchmark: PerformanceMetricSnapshot | null
  relative: PerformanceRelativeSnapshot | null
  quality: {
    fund: PerformanceSnapshotQuality
    benchmark: PerformanceSnapshotQuality | null
    relative: PerformanceRelativeQuality
  }
}

export type InvestmentAnalyticsSeriesSummary = {
  latest: number | null
  median: number | null
  percentile: number | null
  maximum: number | null
  minimum: number | null
}

export type FundInvestmentAnalytics = {
  methodology_version: 'canonical-investment-analytics/v2'
  methodology: {
    annualized_return: 'geometric_minimum_365_calendar_days'
    calmar_ratio: 'annualized_return_over_max_drawdown_minimum_1096_calendar_days'
    annualized_risk: 'minimum_12_same_frequency_returns'
    sharpe_ratio: 'arithmetic_mean_excess_return_risk_free_rate_zero'
    downside_deviation: 'lower_partial_moment_mar_zero_all_observations'
    sortino_ratio: 'arithmetic_mean_excess_return_mar_zero'
    relative_alignment: 'exact_fund_observation_boundaries'
    monthly_volatility_annualization: 'fixed_same_frequency_annualization_252_52_12'
    rolling_beta_frequency: 'exact_contiguous_calendar_months'
    capture_ratio: 'minimum_3_exact_same_frequency_returns_per_regime'
  }
  valuation_date?: string
  quote_resolutions?: {
    fund: CanonicalQuoteSeriesResolutionSummary
    benchmark: CanonicalQuoteSeriesResolutionSummary | null
  }
  calculation_states?: {
    fund: QuoteCalculationState
    benchmark: QuoteCalculationState | null
  }
  as_of_date: string | null
  benchmark_instrument_id: string | null
  rolling_window_months: 1 | 3 | 6 | 12
  periods: PerformancePeriodAnalytics[]
  monthly_return_matrix: Array<{
    year: string
    months: Array<number | null>
    ytd: number | null
  }>
  series: {
    drawdown: FundChartPoint[]
    benchmark_drawdown: FundChartPoint[]
    monthly_drawdown: FundChartPoint[]
    monthly_annualized_volatility: FundChartPoint[]
    rolling_annualized_volatility: FundChartPoint[]
    benchmark_rolling_annualized_volatility: FundChartPoint[]
    rolling_sharpe_ratio: FundChartPoint[]
    benchmark_rolling_sharpe_ratio: FundChartPoint[]
    rolling_beta: FundChartPoint[]
  }
  statistics: {
    current_drawdown: number | null
    monthly_return: InvestmentAnalyticsSeriesSummary
    monthly_drawdown: InvestmentAnalyticsSeriesSummary
    rolling_annualized_volatility: InvestmentAnalyticsSeriesSummary
    rolling_beta: InvestmentAnalyticsSeriesSummary
    trailing_negative_month_count: number | null
  }
  source_observation_count: number
  benchmark_observation_count: number
  source_input_observation_count: number
  benchmark_input_observation_count: number
  quality: {
    fund_status: 'available' | 'unavailable'
    fund_reason: string | null
    benchmark_status: 'available' | 'unavailable' | 'not_requested'
    benchmark_reason: string | null
    series: {
      monthly_annualized_volatility: InvestmentAnalyticsMetricQuality
      rolling_annualized_volatility: InvestmentAnalyticsMetricQuality
      benchmark_rolling_annualized_volatility: InvestmentAnalyticsMetricQuality
      rolling_sharpe_ratio: InvestmentAnalyticsMetricQuality
      benchmark_rolling_sharpe_ratio: InvestmentAnalyticsMetricQuality
      rolling_beta: InvestmentAnalyticsMetricQuality
    }
  }
}

export type CalculationFrequency = 'daily' | 'weekly' | 'monthly'

export type CalculationFrequencyProfile = {
  requested_frequency: 'auto'
  resolved_frequency: CalculationFrequency | null
  inferred_frequency: CalculationFrequency | null
  source_frequency_counts: Record<CalculationFrequency | 'unknown', number>
  raw_observation_count: number
  observation_count: number
  start_date: string | null
  end_date: string | null
  annualization_periods_per_year: number | null
  largest_gap_days: number | null
  gap_count: number
  gap_status: 'aligned' | 'calendar_gaps' | 'unresolved'
  status_label: string
}

export type FundChartResponse = {
  fund_id: string
  base_series_type: string
  selected_series?: SelectedQuoteSeriesMetadata
  resolution?: CanonicalQuoteSeriesResolutionSummary
  currency: string | null
  date_range: { start: string; end: string } | null
  series: Array<{ name: string; points: FundChartPoint[] }>
  available_compare_targets: string[]
}

export type SelectedQuoteSeriesMetadata = {
  role?: string | null
  metric_family?: string | null
  quote_basis?: string | null
  series_type?: string | null
  basis_type?: string | null
  label?: string | null
  date_label?: string | null
}

export type SelectedQuotePoint = {
  date: string
  value: number
  nav: number
  metric_family?: string | null
  quote_basis?: string | null
  series_type?: string | null
}

type RawSelectedQuotePoint = Omit<Partial<SelectedQuotePoint>, 'value' | 'nav'> & {
  date: string
  nav?: number | null
  value?: number | null
}

export type FundPerformanceResponse = {
  growth_chart_series: Array<{ name: string; value: number | null }>
  annual_returns: Array<Record<string, unknown>>
  trailing_returns: Array<Record<string, unknown>>
  ranking: {
    metric_key?: string | null
    metric_label?: string | null
    quartile?: number | null
    percentile?: number | null
    rank?: number | null
    sample_count?: number | null
    peer_group?: string | null
  } | null
  peer_comparison?: {
    status: string
    taxonomy_code: string
    assigned_node_id: string | null
    assigned_path: string[]
    peer_node_id: string | null
    peer_path: string[]
    fallback_levels: number
    sample_count: number
    metrics: Array<{
      metric_key: string
      label: string
      domain: string
      format: 'percent' | 'ratio' | string
      direction: 'higher' | 'lower' | string
      value: number | null
      peer_median: number | null
      peer_p25: number | null
      peer_p75: number | null
      percentile: number | null
      quartile: number | null
      rank: number | null
      sample_count: number | null
      peer_sample_count: number | null
    }>
    summary: {
      return_percentile?: number | null
      risk_percentile?: number | null
      risk_adjusted_percentile?: number | null
      overall_percentile?: number | null
    }
  } | null
  calculation_frequency_profile: CalculationFrequencyProfile | null
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    calculated_at: string | null
    market_data_input_watermark_at: string | null
  } | null
  quote_resolution?: CanonicalQuoteSeriesResolutionSummary
  historical_quote_resolution?: CanonicalQuoteSeriesResolutionSummary | null
  calculation_state?: QuoteCalculationState
  analytics: FundInvestmentAnalytics
}

export type FundRiskResponse = {
  risk_overview: Record<string, unknown> | null
  scatter_points: Array<Record<string, unknown>>
  risk_metrics: Array<Record<string, unknown>>
  drawdown_summary: Record<string, unknown> | null
  risk_structure?: {
    rows: Array<Record<string, unknown>>
  } | null
  current_watch?: {
    overall_level: string | null
    rows: Array<Record<string, unknown>>
    note: string | null
  } | null
  change_monitor?: {
    rows: Array<Record<string, unknown>>
    note: string | null
  } | null
  calculation_frequency_profile: CalculationFrequencyProfile | null
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    calculated_at: string | null
    market_data_input_watermark_at: string | null
  } | null
  quote_resolution?: CanonicalQuoteSeriesResolutionSummary
  historical_quote_resolution?: CanonicalQuoteSeriesResolutionSummary | null
  calculation_state?: QuoteCalculationState
}

export type FundPeopleResponse = {
  overview: Record<string, unknown>
  team: Array<Record<string, unknown>>
  notes: string[]
}

export type FundStrategyResponse = {
  summary: string
  investment_objective: string
  process_bullets: string[]
  risk_controls: string[]
  notes: string[]
}

export type FundPriceResponse = {
  overview: Record<string, unknown>
  distribution_policy: string
  policy_text: string
  fee_notes: string[]
  notes: string[]
}

export type FundDocumentsResponse = {
  current_documents: Array<Record<string, unknown>>
  recent_imports: Array<Record<string, unknown>>
  extraction_reviews: Array<Record<string, unknown>>
  notes: string[]
}

export type InstrumentDocumentUploadPayload = {
  file: File
  title?: string
  document_type?: string
  as_of_date?: string
  source?: string
  status?: string
  version_label?: string
  notes?: string
  updated_by?: string
}

export type FundResearchResponse = {
  overview: Record<string, unknown>
  timeline_notes: Array<Record<string, unknown>>
}

export type ResearchRatingConfidence = 'low' | 'medium' | 'high'

export type ResearchRatingRecord = {
  rating_revision_id: string | null
  revision_number: number | null
  previous_rating_revision_id: string | null
  rating: number | null
  confidence: ResearchRatingConfidence | 'unassessed' | null
  as_of_date: string | null
  rationale: string | null
  author: string | null
  next_review_date: string | null
  created_at: string | null
}

export type ResearchRatingResponse = ResearchRatingRecord & {
  instrument_id: string
  superseded_at: string | null
  is_current: boolean
}

export type ResearchRatingsResponse = {
  items: ResearchRatingResponse[]
}

export type ResearchRatingUpdatePayload = {
  rating: number | null
  confidence: ResearchRatingConfidence
  as_of_date: string
  rationale: string
  author: string
  next_review_date?: string | null
  expected_current_revision_id: string | null
}

export type QuoteRevisionStatus = 'complete' | 'partial' | 'rejected' | 'withdrawn'

export type CanonicalQuoteSeriesObservation = {
  quote_series_id: string
  observation_id: string
  revision_id: string
  revision_number: number
  payload_hash: string
  observation_date: string
  value: string | null
  status: QuoteRevisionStatus
  source_ref: string | null
  source_published_at: string | null
  ingested_at: string | null
  ingestion_time_state:
    | 'observed'
    | 'legacy_series_upper_bound'
    | 'legacy_instrument_upper_bound'
    | 'legacy_migration_upper_bound'
    | null
}

export type CanonicalQuoteSeriesPoint = Omit<CanonicalQuoteSeriesObservation, 'status'> & {
  value: string
}

type CanonicalQuoteSeriesCalculationDependencyBase = {
  resolver_strategy_version: string
  freshness_policy_version: string
  freshness_mode: string
  max_age_days: number
  range_mode: 'bounded' | 'since_inception'
  start_date: string | null
  end_date: string
  quote_selection_policy_version: string
  quote_selection_policy_revision: string | null
  quote_series_id: string | null
  fingerprint: string
}

export type CanonicalQuoteSeriesCalculationDependency =
  CanonicalQuoteSeriesCalculationDependencyBase & {
    revision_ids: string[]
    payload_hashes: string[]
    excluded_revision_ids: string[]
    excluded_payload_hashes: string[]
  }

export type CanonicalQuoteSeriesCalculationDependencySummary =
  CanonicalQuoteSeriesCalculationDependencyBase & {
    revision_count: number
    excluded_revision_count: number
  }

export type ConsumerFreshnessProfile = {
  profile: 'periodic_fund_nav' | 'daily_market' | string
  policy_version: string
  reason_code: string
  canonical_instrument_type: string
  resolver_policy: {
    policy_version: string
    mode: string
    max_age_days: number
  }
}

export type QuoteConsumerDependency = {
  dependency_kind: 'watchlist_quote_consumer_dependency'
  dependency_version: 'v1'
  canonical_dependency_fingerprint: string
  consumer_freshness_profile: ConsumerFreshnessProfile
  fingerprint: string
}

export type QuoteCalculationState = {
  current_endpoint_state: 'resolved' | 'stale' | 'partial' | 'unavailable'
  analysis_as_of_date?: string | null
  historical_calculation_state?:
    | 'current_endpoint'
    | 'last_good_preserved'
    | 'historical_as_of_last_observation'
    | 'unavailable'
  history_as_of_date?: string | null
  reason_codes: string[]
}

type CanonicalQuoteSeriesResolutionBase = {
  resolution_status: 'resolved' | 'unavailable'
  resolver_strategy_version: string
  instrument_id: string
  role: string
  metric_family: string | null
  quote_basis: string | null
  currency: string
  range_mode: 'bounded' | 'since_inception'
  start_date: string | null
  end_date: string
  quote_selection_policy_version: string
  quote_selection_policy_revision: string | null
  freshness_policy: {
    policy_version: string
    mode: string
    max_age_days: number
  }
  quote_series_id: string | null
  start_boundary_observation: CanonicalQuoteSeriesObservation | null
  start_anchor: CanonicalQuoteSeriesPoint | null
  observation_count: number
  adopted_point_count: number
  first_observation_date: string | null
  last_observation_date: string | null
  coverage_status: 'complete' | 'partial' | 'unavailable'
  freshness_status: string
  ingestion_status: string
  reliability_status: string
  reason_codes: string[]
  consumer_freshness_profile: ConsumerFreshnessProfile
  consumer_dependency: QuoteConsumerDependency
}

export type CanonicalQuoteSeriesResolution =
  CanonicalQuoteSeriesResolutionBase & {
    observations: CanonicalQuoteSeriesObservation[]
    points: CanonicalQuoteSeriesPoint[]
    calculation_dependency: CanonicalQuoteSeriesCalculationDependency
  }

export type CanonicalQuoteSeriesResolutionSummary =
  CanonicalQuoteSeriesResolutionBase & {
    schema_version: 'watchlist_quote_resolution_summary.v1'
    calculation_dependency: CanonicalQuoteSeriesCalculationDependencySummary
  }

export type FundNavSeriesResponse = {
  fund_id: string
  valuation_date: string | null
  count: number
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  selected_role?: string | null
  selected_metric_family?: string | null
  selected_quote_basis?: string | null
  selected_series_type?: string | null
  selected_series_label?: string | null
  selected_date_label?: string | null
  resolution: CanonicalQuoteSeriesResolution | null
  consumer_freshness_profile?: ConsumerFreshnessProfile
  calculation_frequency_profile: CalculationFrequencyProfile
  basis_statistics: Record<
    'nav' | 'nav_with_dividend',
    {
      latest_date: string | null
      latest_value: number | null
      latest_change: number | null
      latest_change_percent: number | null
    }
  >
  compare_settings?: {
    default_benchmark_instrument_id: string | null
    peer_instrument_ids: string[]
  }
  refresh_status?: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: string
  }
  series: SelectedQuotePoint[]
  calculation_series: SelectedQuotePoint[]
  rows: Array<{
    as_of_date: string
    nav: number | null
    nav_with_dividend: number | null
    selected_basis_type?: string | null
    selected_value?: number | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    calculation_included?: boolean
    cumulative_distribution: number | null
    distribution_amount: number | null
    currency: string | null
    frequency: string | null
    adopted_at: string | null
    status: QuoteRevisionStatus
    observation_id: string
    revision_id: string
    revision_number: number
    payload_hash: string
    source_ref: string | null
    source_published_at: string | null
  }>
}

type RawFundNavSeriesResponse = {
  instrument_id: string
  valuation_date: string
  count: number
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  selected_role?: string | null
  selected_metric_family?: string | null
  selected_quote_basis?: string | null
  selected_series_type?: string | null
  selected_series_label?: string | null
  selected_date_label?: string | null
  resolution: CanonicalQuoteSeriesResolution
  consumer_freshness_profile?: ConsumerFreshnessProfile
  calculation_frequency_profile: CalculationFrequencyProfile
  basis_statistics: FundNavSeriesResponse['basis_statistics']
  compare_settings?: {
    default_benchmark_instrument_id: string | null
    peer_instrument_ids: string[]
  }
  refresh_status?: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: string
  }
  series?: RawSelectedQuotePoint[]
  calculation_series?: RawSelectedQuotePoint[]
  rows: Array<{
    date: string
    nav: number | null
    nav_with_dividend: number | null
    selected_basis_type?: string | null
    selected_value?: number | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    calculation_included?: boolean
    cumulative_distribution?: number | null
    distribution_amount?: number | null
    currency: string | null
    frequency: string | null
    adopted_at: string | null
    status: QuoteRevisionStatus
    observation_id: string
    revision_id: string
    revision_number: number
    payload_hash: string
    source_ref: string | null
    source_published_at: string | null
  }>
}

export type ManualProfileUpdatePayload = {
  payload: Record<string, unknown>
  updated_by?: string
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const referenceGetCache = new Map<
  string,
  { expiresAt: number; promise: Promise<unknown> }
>()

export class ApiRequestError extends Error {
  readonly status: number
  readonly responseBody: string

  constructor(status: number, responseBody: string) {
    super(responseBody || `Request failed: ${status}`)
    this.name = 'ApiRequestError'
    this.status = status
    this.responseBody = responseBody
  }
}

async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  })

  if (!response.ok) {
    const body = await response.text()
    throw new ApiRequestError(response.status, body)
  }

  return (await response.json()) as T
}

function fetchReferenceJson<T>(path: string, ttlMs: number = 5 * 60_000): Promise<T> {
  const now = Date.now()
  const cached = referenceGetCache.get(path)
  if (cached && cached.expiresAt > now) {
    return cached.promise as Promise<T>
  }
  const promise = fetchJson<T>(path).catch((error) => {
    if (referenceGetCache.get(path)?.promise === promise) {
      referenceGetCache.delete(path)
    }
    throw error
  })
  referenceGetCache.set(path, { expiresAt: now + ttlMs, promise })
  return promise
}

async function fetchForm<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init)

  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `Request failed: ${response.status}`)
  }

  return (await response.json()) as T
}

function normalizeFundLibraryItem(item: RawFundLibraryItem): FundLibraryItem {
  return {
    fund_id: item.instrument_id,
    fund_name: item.instrument_name,
    ticker_or_isin: item.primary_identifier,
    product_type: item.instrument_type || item.detail_view_type || 'fund',
  }
}

export function normalizeFundNavSeriesResponse(response: RawFundNavSeriesResponse): FundNavSeriesResponse {
  const normalizeQuotePoints = (
    points: RawSelectedQuotePoint[],
  ): SelectedQuotePoint[] =>
    points.flatMap((point) => {
      const value = point.value
      if (value == null) {
        return []
      }
      return [{
        date: point.date,
        value,
        nav: value,
        metric_family: point.metric_family,
        quote_basis: point.quote_basis,
        series_type: point.series_type,
      }]
    })

  const rows: FundNavSeriesResponse['rows'] = response.rows.map((row) => ({
    as_of_date: row.date,
    nav: row.nav,
    nav_with_dividend: row.nav_with_dividend,
    selected_basis_type: row.selected_basis_type,
    selected_value: row.selected_value,
    selected_metric_family: row.selected_metric_family,
    selected_quote_basis: row.selected_quote_basis,
    selected_series_type: row.selected_series_type,
    selected_series_label: row.selected_series_label,
    calculation_included: row.calculation_included,
    cumulative_distribution: row.cumulative_distribution ?? null,
    distribution_amount: row.distribution_amount ?? null,
    currency: row.currency,
    frequency: row.frequency,
    adopted_at: row.adopted_at,
    status: row.status,
    observation_id: row.observation_id,
    revision_id: row.revision_id,
    revision_number: row.revision_number,
    payload_hash: row.payload_hash,
    source_ref: row.source_ref,
    source_published_at: row.source_published_at,
  }))
  const pointsFromRows = (calculationOnly: boolean): SelectedQuotePoint[] =>
    rows.flatMap((row) => {
      if (calculationOnly && row.calculation_included === false) {
        return []
      }
      const value = row.selected_value
      if (value == null) {
        return []
      }
      return [{
        date: row.as_of_date,
        value,
        nav: value,
        metric_family: response.selected_metric_family ?? undefined,
        quote_basis: response.selected_quote_basis ?? undefined,
        series_type: response.selected_series_type ?? undefined,
      }]
    })

  return {
    fund_id: response.instrument_id,
    valuation_date: response.valuation_date,
    count: response.count,
    nav_basis_type: response.nav_basis_type,
    nav_basis_source: response.nav_basis_source,
    nav_basis_status: response.nav_basis_status,
    selected_role: response.selected_role,
    selected_metric_family: response.selected_metric_family,
    selected_quote_basis: response.selected_quote_basis,
    selected_series_type: response.selected_series_type,
    selected_series_label: response.selected_series_label,
    selected_date_label: response.selected_date_label,
    resolution: response.resolution,
    calculation_frequency_profile: response.calculation_frequency_profile,
    basis_statistics: response.basis_statistics,
    compare_settings: response.compare_settings,
    refresh_status: response.refresh_status,
    series: response.series ? normalizeQuotePoints(response.series) : pointsFromRows(false),
    calculation_series: response.calculation_series
      ? normalizeQuotePoints(response.calculation_series)
      : pointsFromRows(true),
    rows,
  }
}

export function getWatchlists() {
  return fetchJson<WatchlistRecord[]>('/api/watchlists')
}

export function getMonitoringDashboard() {
  return fetchJson<MonitoringDashboardResponse>('/api/monitoring/dashboard')
}

export function executeInstrumentRecalc(
  instrumentId: string,
  payload?: {
    job_type?: 'performance'
    trigger_type?: string
    trigger_ref_type?: string | null
    trigger_ref_id?: string | null
  },
) {
  return fetchJson<RecalcExecuteResponse>(`/api/recalc/instruments/${encodeURIComponent(instrumentId)}/execute`, {
    method: 'POST',
    body: JSON.stringify({
      job_type: payload?.job_type ?? 'performance',
      trigger_type: payload?.trigger_type ?? 'monitoring_dashboard',
      trigger_ref_type: payload?.trigger_ref_type ?? 'monitoring_dashboard',
      trigger_ref_id: payload?.trigger_ref_id ?? null,
    }),
  })
}

export function createWatchlist(payload: WatchlistCreatePayload) {
  return fetchJson<WatchlistRecord>('/api/watchlists', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function copyWatchlist(watchlistId: string) {
  return fetchJson<WatchlistRecord>(`/api/watchlists/${watchlistId}/copy`, {
    method: 'POST',
  })
}

export function deleteWatchlist(watchlistId: string) {
  return fetchJson<WatchlistDeleteResponse>(`/api/watchlists/${watchlistId}`, {
    method: 'DELETE',
  })
}

export function reorderWatchlists(watchlistIds: string[]) {
  return fetchJson<WatchlistRecord[]>('/api/watchlists/reorder', {
    method: 'POST',
    body: JSON.stringify({ watchlist_ids: watchlistIds }),
  })
}

export function getWatchlistDetail(watchlistId: string) {
  return fetchJson<WatchlistDetail>(`/api/watchlists/${watchlistId}`)
}

export function resolveInstrumentDetail(instrumentId: string) {
  return fetchJson<InstrumentResolveResponse>(
    `/api/instruments/${encodeURIComponent(instrumentId)}/resolve`,
  )
}

export function getSharedInstruments(options?: {
  search?: string
  instrument_type?: string
  limit?: number
}) {
  const params = new URLSearchParams()
  if (options?.search?.trim()) {
    params.set('search', options.search.trim())
  }
  if (options?.instrument_type?.trim()) {
    params.set('instrument_type', options.instrument_type.trim())
  }
  if (typeof options?.limit === 'number') {
    params.set('limit', String(options.limit))
  }
  const query = params.toString()
  return fetchJson<SharedInstrumentRecord[]>(`/api/instruments${query ? `?${query}` : ''}`)
}

export function resolveSharedInstrument(
  identifierValue: string,
  options?: { identifier_type?: string },
) {
  const params = new URLSearchParams({ identifier_value: identifierValue })
  if (options?.identifier_type?.trim()) {
    params.set('identifier_type', options.identifier_type.trim())
  }
  return fetchJson<SharedInstrumentRecord>(`/api/instruments/resolve?${params.toString()}`)
}

export function createWatchlistView(
  watchlistId: string,
  payload: WatchlistViewCreatePayload,
) {
  return fetchJson<WatchlistView>(`/api/watchlists/${watchlistId}/views`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateWatchlistView(
  watchlistId: string,
  viewId: string,
  payload: WatchlistViewCreatePayload,
) {
  return fetchJson<WatchlistView>(`/api/watchlists/${watchlistId}/views/${viewId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function addWatchlistItems(watchlistId: string, instrumentIds: string[]) {
  return fetchJson<{
    watchlist_id: string
    accepted_count: number
    pending_recalc_instrument_ids: string[]
    recalculated_instrument_ids: string[]
  }>(
    `/api/watchlists/${watchlistId}/items`,
    {
      method: 'POST',
      body: JSON.stringify({ instrument_ids: instrumentIds }),
    },
  )
}

export function deleteWatchlistItems(watchlistId: string, instrumentIds: string[]) {
  return fetchJson<{ watchlist_id: string; deleted_count: number }>(
    `/api/watchlists/${watchlistId}/items/delete`,
    {
      method: 'POST',
      body: JSON.stringify({ instrument_ids: instrumentIds }),
    },
  )
}

export function moveWatchlistItems(
  watchlistId: string,
  instrumentIds: string[],
  targetWatchlistId: string,
) {
  return fetchJson<WatchlistItemsMoveResponse>(`/api/watchlists/${watchlistId}/items/move`, {
    method: 'POST',
    body: JSON.stringify({
      instrument_ids: instrumentIds,
      target_watchlist_id: targetWatchlistId,
    }),
  })
}

export function copyWatchlistItems(
  watchlistId: string,
  instrumentIds: string[],
  targetWatchlistId: string,
) {
  return fetchJson<WatchlistItemsCopyResponse>(`/api/watchlists/${watchlistId}/items/copy`, {
    method: 'POST',
    body: JSON.stringify({
      instrument_ids: instrumentIds,
      target_watchlist_id: targetWatchlistId,
    }),
  })
}

export function getFieldRegistry(options?: {
  instrument_type?: string | string[]
  product_type?: string
  search?: string
}) {
  const params = new URLSearchParams()
  if (options?.instrument_type) {
    const instrumentType = Array.isArray(options.instrument_type)
      ? options.instrument_type.join(',')
      : options.instrument_type
    if (instrumentType.trim()) {
      params.set('instrument_type', instrumentType)
    }
  }
  if (options?.product_type?.trim()) {
    params.set('product_type', options.product_type.trim())
  }
  if (options?.search?.trim()) {
    params.set('search', options.search.trim())
  }
  const query = params.toString()
  return fetchReferenceJson<FieldRegistryResponse>(`/api/field-registry${query ? `?${query}` : ''}`)
}

export function getInstrumentAttributeDefinitions() {
  return fetchJson<InstrumentAttributeDefinition[]>('/api/instrument-attributes/definitions')
}

export function createInstrumentAttributeDefinition(
  payload: InstrumentAttributeDefinitionCreatePayload,
) {
  return fetchJson<InstrumentAttributeDefinition>('/api/instrument-attributes/definitions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getInstrumentAttributes(instrumentId: string) {
  return fetchJson<InstrumentAttributeValuesResponse>(
    `/api/instrument-attributes/instruments/${instrumentId}`,
  )
}

export function getFundTaxonomyTree() {
  return fetchReferenceJson<FundTaxonomyTreeResponse>('/api/taxonomies/fund-taxonomy')
}

export function updateFundTaxonomy(
  instrumentId: string,
  payload: {
    node_id: string | null
    updated_by?: string
  },
) {
  return fetchJson<FundTaxonomyContext & { instrument_id: string; updated: boolean }>(
    `/api/taxonomies/fund-taxonomy/instruments/${encodeURIComponent(instrumentId)}`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function updateInstrumentAttributes(
  instrumentId: string,
  payload: InstrumentAttributeUpdatePayload,
) {
  return fetchJson<InstrumentAttributeValuesResponse & { updated: boolean }>(
    `/api/instrument-attributes/instruments/${instrumentId}`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export function runScreenerQuery(payload: Record<string, unknown>) {
  return fetchJson<ScreenerResponse>('/api/screener/query', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

function buildInstrumentDetailApiPath(instrumentId: string, suffix: string) {
  return `/api/instruments/${encodeURIComponent(instrumentId)}/${suffix}`
}

export function getInstrumentSummary(instrumentId: string) {
  return fetchJson<FundSummaryResponse>(buildInstrumentDetailApiPath(instrumentId, 'summary'))
}

export function getInstrumentLibrary() {
  return fetchReferenceJson<RawFundLibraryItem[]>('/api/instruments/library').then((items) =>
    items.map(normalizeFundLibraryItem),
  )
}

export function getInstrumentChart(instrumentId: string) {
  return fetchJson<FundChartResponse>(buildInstrumentDetailApiPath(instrumentId, 'chart'))
}

export function getInstrumentPerformance(
  instrumentId: string,
  options?: {
    benchmarkInstrumentId?: string | null
    rollingWindowMonths?: 1 | 3 | 6 | 12
  },
) {
  const searchParams = new URLSearchParams()
  if (options?.benchmarkInstrumentId) {
    searchParams.set('benchmark_instrument_id', options.benchmarkInstrumentId)
  }
  if (options?.rollingWindowMonths != null) {
    searchParams.set('rolling_window_months', String(options.rollingWindowMonths))
  }
  const suffix = searchParams.size
    ? `performance?${searchParams.toString()}`
    : 'performance'
  return fetchJson<FundPerformanceResponse>(buildInstrumentDetailApiPath(instrumentId, suffix))
}

export function getInstrumentRisk(instrumentId: string) {
  return fetchJson<FundRiskResponse>(buildInstrumentDetailApiPath(instrumentId, 'risk'))
}

export function getInstrumentPeople(instrumentId: string) {
  return fetchJson<FundPeopleResponse>(buildInstrumentDetailApiPath(instrumentId, 'people'))
}

export function getInstrumentStrategy(instrumentId: string) {
  return fetchJson<FundStrategyResponse>(buildInstrumentDetailApiPath(instrumentId, 'strategy'))
}

export function getInstrumentPrice(instrumentId: string) {
  return fetchJson<FundPriceResponse>(buildInstrumentDetailApiPath(instrumentId, 'price'))
}

export function getInstrumentDocuments(instrumentId: string) {
  return fetchJson<FundDocumentsResponse>(buildInstrumentDetailApiPath(instrumentId, 'documents'))
}

export function getInstrumentResearch(instrumentId: string) {
  return fetchJson<FundResearchResponse>(buildInstrumentDetailApiPath(instrumentId, 'research'))
}

export function getInstrumentResearchRating(instrumentId: string) {
  return fetchJson<ResearchRatingResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'research-rating'),
  )
}

export function getInstrumentResearchRatings(instrumentId: string) {
  return fetchJson<ResearchRatingsResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'research-ratings'),
  )
}

export function getInstrumentNavSeries(instrumentId: string) {
  return fetchJson<RawFundNavSeriesResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'nav-series'),
  ).then(normalizeFundNavSeriesResponse)
}

export function updateInstrumentNavSettings(instrumentId: string, payload: unknown) {
  return fetchJson<Record<string, unknown>>(
    buildInstrumentDetailApiPath(instrumentId, 'nav-settings'),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function updateInstrumentPeople(
  instrumentId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundPeopleResponse>(buildInstrumentDetailApiPath(instrumentId, 'people'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentStrategy(
  instrumentId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundStrategyResponse>(buildInstrumentDetailApiPath(instrumentId, 'strategy'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentPrice(
  instrumentId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundPriceResponse>(buildInstrumentDetailApiPath(instrumentId, 'price'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentDocuments(
  instrumentId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundDocumentsResponse>(buildInstrumentDetailApiPath(instrumentId, 'documents'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function uploadInstrumentDocument(
  instrumentId: string,
  payload: InstrumentDocumentUploadPayload,
) {
  const formData = new FormData()
  formData.append('file', payload.file)
  if (payload.title) formData.append('title', payload.title)
  if (payload.document_type) formData.append('document_type', payload.document_type)
  if (payload.as_of_date) formData.append('as_of_date', payload.as_of_date)
  if (payload.source) formData.append('source', payload.source)
  if (payload.status) formData.append('status', payload.status)
  if (payload.version_label) formData.append('version_label', payload.version_label)
  if (payload.notes) formData.append('notes', payload.notes)
  if (payload.updated_by) formData.append('updated_by', payload.updated_by)

  return fetchForm<FundDocumentsResponse>(buildInstrumentDetailApiPath(instrumentId, 'documents/upload'), {
    method: 'POST',
    body: formData,
  })
}

export function updateInstrumentResearch(
  instrumentId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundResearchResponse>(buildInstrumentDetailApiPath(instrumentId, 'research'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentResearchRating(
  instrumentId: string,
  payload: ResearchRatingUpdatePayload,
) {
  return fetchJson<ResearchRatingResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'research-rating'),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}
