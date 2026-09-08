
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
  exchange_code?: string | null
}

export type GroupByOption = {
  code: string
  label: string
}

export type WatchlistDetail = WatchlistRecord & {
  instrument_types: string[]
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
  column_field_keys: string[]
  filter_field_keys: string[]
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

export type InstrumentTaxonomyContext = {
  taxonomy_code: string
  assigned_node_id: string | null
  assigned_label: string | null
  path_labels: string[]
  path_node_ids: string[]
  depth: number
  derived_values: Record<string, string>
}

export type InstrumentTaxonomyTreeNode = {
  node_id: string
  label: string
  instrument_type: string
  parent_node_id: string | null
  level_index: number
  display_order: number
  is_leaf: boolean
  path_labels: string[]
  path_node_ids: string[]
}

export type InstrumentTaxonomyTreeResponse = {
  taxonomy_code: string
  instrument_types: string[]
  max_depth: number
  nodes: InstrumentTaxonomyTreeNode[]
}

export type InstrumentAttributeValuesResponse = {
  instrument_id: string
  definitions: InstrumentAttributeDefinition[]
  values: Record<string, unknown>
  taxonomy: InstrumentTaxonomyContext
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

export type InstrumentReferenceData = {
  instrument_id: string
  instrument_type: 'public_fund' | 'private_fund' | 'etf' | 'equity' | 'index'
  provider: string
  provider_symbol: string | null
  fetched_at: string | null
  source: {
    source_mode?: string | null
    source_location?: string | null
    source_api_profile?: string | null
    expected_frequency?: string | null
    market_calendar?: string | null
    release_lag_days?: number | null
    refresh_status?: string | null
    refresh_message?: string | null
    market_data_updated_at?: string | null
  }
  sections: Record<string, unknown>
  section_errors: Record<string, string>
}

export type InstrumentPriceBar = {
  date: string
  open: string
  high: string
  low: string
  close: string
  previous_close: string | null
  volume: string | null
  turnover: string | null
  adjustment_factor: string | null
  currency: string
  volume_unit: string | null
  turnover_unit: string | null
  provider: string
  status: 'complete' | 'partial'
}

export type InstrumentPriceBarsResponse = {
  instrument_id: string
  instrument_type: 'etf' | 'equity' | 'index'
  currency: string
  adjustment_mode: 'raw_with_factor' | 'raw'
  factor_coverage: number
  source_refresh_status: string
  source_refresh_message: string
  count: number
  bars: InstrumentPriceBar[]
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

export type InstrumentSettingsUpdatePayload = {
  taxonomy_node_id: string | null
  coverage_status: string | null
  updated_by?: string
}

export type ScreenerGroup = {
  group_value: string
  row_count: number
  group_depth?: number
  group_path?: string[]
}

export type ScreenerSnapshotMetadata = {
  /** Latest row metric endpoint; descriptive only, never a shared Watchlist as-of. */
  as_of_date: string | null
  as_of_date_min: string | null
  as_of_date_max: string | null
  has_mixed_as_of_dates: boolean
  as_of_date_missing_count: number
  methodology_version: string
  source_cutoff_at: string | null
  is_current: boolean
  advanced_filter_applied: boolean
}

export type ScreenerResponse = {
  rows: Array<Record<string, unknown>>
  groups: ScreenerGroup[]
  total_rows: number
  stale_row_count: number
  sparklines?: Record<string, Record<string, ReturnSparklineSeries>>
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
  missing_required_metadata_count: number
  research_issue_count: number
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
  research: {
    current_view: string
    manual_rating: number | null
    primary_analyst: string
    next_review_date: string | null
    last_updated_at: string | null
    active_note_count: number
    next_follow_up_date: string | null
    issue_flags: string[]
  }
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
    missing_required_metadata_count: number
    research_review_due_count: number
    research_follow_up_due_count: number
    missing_investment_view_count: number
    open_recalc_job_count: number
    failed_recalc_job_count: number
  }
  watchlists: MonitoringWatchlistSummary[]
  instruments: MonitoringInstrumentRecord[]
  needs_attention_instruments: MonitoringInstrumentRecord[]
  missing_required_metadata_instruments: MonitoringInstrumentRecord[]
  research_queue: MonitoringInstrumentRecord[]
  open_recalc_jobs: MonitoringRecalcJobRecord[]
}

export type InstrumentMonitoringResponse = {
  generated_at: string | null
  instrument: MonitoringInstrumentRecord
  open_recalc_jobs: MonitoringRecalcJobRecord[]
}

export type RecalcExecuteResponse = {
  recalc_job_id: string
  job_status: string
  result: Record<string, unknown>
}

export type InstrumentSummaryResponse = {
  instrument_id: string
  instrument_name: string
  ticker_or_isin: string
  management_firm_name: string | null
  instrument_attributes: Record<string, unknown>
  taxonomy: InstrumentTaxonomyContext
  key_stats: Array<{ label: string; value: string | number | null }>
  freshness: {
    data_freshness_status: string
    last_fact_update_at: string | null
    last_recalculated_at: string | null
    last_successful_snapshot_at: string | null
    staleness_reason: string | null
  }
  quick_monitoring_items: string[]
  tabs: string[]
  series_snapshot?: {
    nav_basis_type?: string | null
    nav_basis_source?: string | null
    selected_role?: string | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    selected_date_label?: string | null
    return_kind?: ReturnKind | null
    return_series_status?: string | null
    return_anchor_date?: string | null
    return_segment_breaks?: Array<Record<string, unknown>>
    latest_nav?: number | null
    latest_nav_date?: string | null
    latest_nav_with_dividend?: number | null
    latest_nav_with_dividend_date?: string | null
  }
  selected_series?: SelectedQuoteSeriesMetadata
}

export type InstrumentLibraryItem = {
  instrument_id: string
  instrument_name: string
  primary_identifier: string | null
  instrument_type: string
}

type RawInstrumentLibraryItem = {
  instrument_id: string
  instrument_name: string
  instrument_type: string
  detail_view_type: string
  primary_identifier: string | null
}

export type InstrumentChartPoint = { date: string; value: number }

export type ReturnKind = 'total_return' | 'price_return' | 'unit_nav_return'

export type ReturnSparklineSeries = {
  points: InstrumentChartPoint[]
  return_kind: ReturnKind
  label: string
  status: 'ready' | 'partial' | 'unavailable'
  policy_version?: string
  anchor_mode?: 'on_or_before' | 'strictly_before'
  requested_start_date: string
  requested_end_date: string
  anchor_date: string | null
  end_date: string | null
}

export type CalculationFrequency = 'daily'

export type CalculationFrequencyProfile = {
  requested_frequency: 'daily'
  expected_frequency?: CalculationFrequency | null
  resolved_frequency: CalculationFrequency
  inferred_frequency: CalculationFrequency
  source_frequency_counts: Record<CalculationFrequency | 'unknown', number>
  frequency_source?: 'daily_policy'
  raw_observation_count: number
  observation_count: number
  start_date: string | null
  end_date: string | null
  annualization_periods_per_year: number | null
  largest_gap_days: number | null
  gap_count: number
  gap_status: 'aligned' | 'calendar_gaps'
  gap_detection_basis?: string
  missing_observation_date_sample?: string[]
  status_label: string
}

export type InstrumentChartResponse = {
  instrument_id: string
  base_series_type: string
  selected_series?: SelectedQuoteSeriesMetadata
  latest_values?: {
    valuation?: LatestQuoteValueSnapshot | null
    total_return?: LatestQuoteValueSnapshot | null
  }
  currency: string
  date_range: { start: string; end: string } | null
  series: Array<{ name: string; points: InstrumentChartPoint[] }>
  available_compare_targets: string[]
}

export type LatestQuoteValueSnapshot = SelectedQuoteSeriesMetadata & {
  date: string
  value: number
  currency?: string | null
}

export type SelectedQuoteSeriesMetadata = {
  role?: string | null
  metric_family?: string | null
  quote_basis?: string | null
  series_type?: string | null
  basis_type?: string | null
  label?: string | null
  date_label?: string | null
  return_kind?: ReturnKind | null
  return_series_status?: string | null
  return_anchor_date?: string | null
  return_segment_breaks?: Array<Record<string, unknown>>
}

export type SelectedQuotePoint = {
  date: string
  value: number
  metric_family?: string | null
  quote_basis?: string | null
  series_type?: string | null
}

export type InstrumentPerformanceResponse = {
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
    comparison_policy_version?: string
    as_of_date?: string | null
    taxonomy_code: string
    assigned_node_id: string | null
    assigned_path: string[]
    peer_node_id: string | null
    peer_path: string[]
    fallback_levels: number
    candidate_count?: number
    sample_count: number
    excluded_mismatched_as_of_count?: number
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
      as_of_date?: string | null
      excluded_mismatched_as_of_count?: number
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
    source_cutoff_at: string | null
    calculated_at?: string | null
  } | null
}

export type InstrumentRiskResponse = {
  risk_overview: Record<string, unknown> | null
  scatter_points: Array<Record<string, unknown>>
  risk_metrics: Array<Record<string, unknown>>
  drawdown_summary: Record<string, unknown> | null
  current_drawdown: number | null
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
  data_quality?: {
    status: 'ready' | 'withheld_missing_observations' | string
    gap_count: number
    gap_detection_basis?: string | null
  }
  calculation_frequency_profile: CalculationFrequencyProfile | null
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    source_cutoff_at: string | null
  } | null
}

export type FundExposureResponse = {
  allocation_blocks: Record<string, Array<Record<string, unknown>>>
  style_box: Record<string, unknown> | null
  liquidity_leverage: Record<string, unknown> | null
  valuation_statistics: Record<string, unknown> | null
  holdings_summary: Record<string, unknown> | null
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    source_cutoff_at: string | null
  } | null
}

export type FundExposureHoldingsResponse = {
  rows: Array<Record<string, unknown>>
  page: number
  page_size: number
  total_rows: number
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    source_cutoff_at: string | null
    calculated_at?: string | null
  } | null
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

export type InstrumentResearchProfile = {
  research_stage: string
  thesis: string
  current_view: string
  why_now: string
  edge_assessment: string
  valuation_framework: string
  catalysts: string
  key_risks: string
  disconfirming_evidence: string
  open_questions: string
  monitoring_plan: string
  people_assessment: string
  portfolio_role: string
  time_horizon: string
  decision_rationale: string
  primary_analyst: string
  next_review_date: string | null
  dd_status: string
  odd_status: string
  ic_status: string
  manual_rating: number | null
  created_at: string | null
  updated_at: string | null
  updated_by: string | null
  revision_number: number
}

export type InstrumentResearchNoteType =
  | 'research_update'
  | 'thesis_update'
  | 'evidence'
  | 'meeting'
  | 'event'
  | 'risk'
  | 'decision'
  | 'review'

export type InstrumentResearchNote = {
  completed_at?: string | null
  note_id: string
  note_date: string
  note_type: InstrumentResearchNoteType
  title: string
  summary: string
  body: string
  importance: 'low' | 'medium' | 'high'
  tags: string[]
  source_refs: string
  people: string
  author: string
  follow_up_date: string | null
  created_at: string
  updated_at: string
  updated_by: string | null
  revision_number: number
  readonly author_user_id?: string | null
  research_context?: InvestmentOpinionResearchContext | null
}

export type InvestmentOpinionResearchContextInput = {
  theme_id?: string | null
  related_note_id?: string | null
  related_revision?: number | null
  relationship?: 'initial' | 'update' | 'review' | 'lesson'
  background?: string
  horizon?: string
  verification?: string
  invalidation?: string
  outcome?: string
  mechanism_assessment?: string
  alternative_explanations?: string[]
  lesson?: string
  applicability?: string
  limitations?: string
  source_ids?: string[]
}

export type InvestmentOpinionResearchContext = InvestmentOpinionResearchContextInput & {
  readonly author_role?: string
  readonly source_run_id?: string
  readonly recorded_via?: string
  readonly source_quote?: string
  readonly research_snapshot?: Record<string, unknown>
  readonly information_cutoff?: string
}

export type InstrumentResearchResponse = {
  profile: InstrumentResearchProfile
  notes: InstrumentResearchNote[]
}

export type InstrumentResearchProfileInput = Omit<
  InstrumentResearchProfile,
  'created_at' | 'updated_at' | 'updated_by' | 'revision_number'
>

export type InstrumentResearchNoteInput = Omit<
  InstrumentResearchNote,
  'note_id' | 'created_at' | 'updated_at' | 'updated_by' | 'revision_number' | 'author_user_id' | 'research_context'
> & { research_context?: InvestmentOpinionResearchContextInput | null }

export type InstrumentResearchProfileRevision = InstrumentResearchProfileInput & {
  revision_number: number
  recorded_at: string
  recorded_by: string | null
}

export type InstrumentResearchNoteRevision = InstrumentResearchNoteInput & {
  note_id: string
  revision_number: number
  change_type: 'create' | 'update' | 'delete'
  recorded_at: string
  recorded_by: string | null
}

export type InstrumentResearchHistoryResponse = {
  profile_revisions: InstrumentResearchProfileRevision[]
  note_revisions: InstrumentResearchNoteRevision[]
}

export type InstrumentResearchProfileUpdatePayload = {
  profile: InstrumentResearchProfileInput
  updated_by?: string
}

export type InstrumentResearchNoteUpdatePayload = {
  note: InstrumentResearchNoteInput
  updated_by?: string
  source_entry_id?: string
}

export function emptyInstrumentResearchResponse(): InstrumentResearchResponse {
  return {
    profile: {
      research_stage: 'watching',
      thesis: '',
      current_view: '',
      why_now: '',
      edge_assessment: '',
      valuation_framework: '',
      catalysts: '',
      key_risks: '',
      disconfirming_evidence: '',
      open_questions: '',
      monitoring_plan: '',
      people_assessment: '',
      portfolio_role: '',
      time_horizon: '',
      decision_rationale: '',
      primary_analyst: '',
      next_review_date: null,
      dd_status: '',
      odd_status: '',
      ic_status: '',
      manual_rating: null,
      created_at: null,
      updated_at: null,
      updated_by: null,
      revision_number: 0,
    },
    notes: [],
  }
}

export type FundNavSeriesResponse = {
  fund_id: string
  count: number
  nav_basis_preference: 'auto' | 'nav_with_dividend'
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  selected_role?: string | null
  selected_metric_family?: string | null
  selected_quote_basis?: string | null
  selected_series_type?: string | null
  selected_series_label?: string | null
  selected_date_label?: string | null
  return_kind?: ReturnKind | null
  return_series_status?: string | null
  return_anchor_date?: string | null
  return_segment_breaks: Array<Record<string, unknown>>
  calculation_frequency_profile: CalculationFrequencyProfile
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
    selected_basis_type: string | null
    selected_value: number | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    calculation_included: boolean
    cumulative_distribution: number | null
    distribution_amount: number | null
    currency: string | null
    frequency: string | null
    adopted_at: string | null
  }>
}

type RawFundNavSeriesResponse = {
  instrument_id: string
  count: number
  nav_basis_preference: 'auto' | 'nav_with_dividend'
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  selected_role?: string | null
  selected_metric_family?: string | null
  selected_quote_basis?: string | null
  selected_series_type?: string | null
  selected_series_label?: string | null
  selected_date_label?: string | null
  return_kind?: ReturnKind | null
  return_series_status?: string | null
  return_anchor_date?: string | null
  return_segment_breaks?: Array<Record<string, unknown>>
  calculation_frequency_profile: CalculationFrequencyProfile
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
  rows: Array<{
    date: string
    nav: number | null
    nav_with_dividend: number | null
    selected_basis_type: string | null
    selected_value: number | null
    selected_metric_family?: string | null
    selected_quote_basis?: string | null
    selected_series_type?: string | null
    selected_series_label?: string | null
    calculation_included: boolean
    cumulative_distribution?: number | null
    distribution_amount?: number | null
    currency: string | null
    frequency: string | null
    adopted_at: string | null
  }>
}

export type ManualProfileUpdatePayload = {
  payload: Record<string, unknown>
  updated_by?: string
}

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const referenceGetCache = new Map<
  string,
  { expiresAt: number; promise: Promise<unknown> }
>()

async function responseErrorMessage(response: Response): Promise<string> {
  const fallback = `Request failed: ${response.status}`
  const body = await response.text()
  if (!body) {
    return fallback
  }
  try {
    const parsed = JSON.parse(body) as { detail?: unknown; message?: unknown }
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
      return parsed.detail
    }
    if (typeof parsed.message === 'string' && parsed.message.trim()) {
      return parsed.message
    }
    if (Array.isArray(parsed.detail)) {
      const messages = parsed.detail.flatMap((item) => {
        if (!item || typeof item !== 'object') return []
        const message = (item as { msg?: unknown }).msg
        return typeof message === 'string' && message.trim() ? [message] : []
      })
      if (messages.length) {
        return messages.join('; ')
      }
    }
  } catch {
    // Non-JSON error bodies are already suitable for display.
  }
  return body
}

export async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    credentials: 'include',
    ...init,
  })

  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('studio:unauthorized'))
    throw new Error(await responseErrorMessage(response))
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
    throw new Error(await responseErrorMessage(response))
  }

  return (await response.json()) as T
}

function normalizeInstrumentLibraryItem(item: RawInstrumentLibraryItem): InstrumentLibraryItem {
  return {
    instrument_id: item.instrument_id,
    instrument_name: item.instrument_name,
    primary_identifier: item.primary_identifier,
    instrument_type: item.instrument_type || item.detail_view_type || 'unknown',
  }
}

function normalizeFundNavSeriesResponse(response: RawFundNavSeriesResponse): FundNavSeriesResponse {
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
  }))
  const pointsFromRows = (calculationOnly: boolean): SelectedQuotePoint[] =>
    rows.flatMap((row) => {
      if (calculationOnly && row.calculation_included === false) {
        return []
      }
      const value = row.selected_value
      if (value == null || !Number.isFinite(value)) {
        return []
      }
      return [{
        date: row.as_of_date,
        value,
        metric_family: response.selected_metric_family ?? undefined,
        quote_basis: response.selected_quote_basis ?? undefined,
        series_type: response.selected_series_type ?? undefined,
      }]
    })

  return {
    fund_id: response.instrument_id,
    count: response.count,
    nav_basis_preference: response.nav_basis_preference,
    nav_basis_type: response.nav_basis_type,
    nav_basis_source: response.nav_basis_source,
    nav_basis_status: response.nav_basis_status,
    selected_role: response.selected_role,
    selected_metric_family: response.selected_metric_family,
    selected_quote_basis: response.selected_quote_basis,
    selected_series_type: response.selected_series_type,
    selected_series_label: response.selected_series_label,
    selected_date_label: response.selected_date_label,
    return_kind: response.return_kind,
    return_series_status: response.return_series_status,
    return_anchor_date: response.return_anchor_date,
    return_segment_breaks: response.return_segment_breaks || [],
    calculation_frequency_profile: response.calculation_frequency_profile,
    compare_settings: response.compare_settings,
    refresh_status: response.refresh_status,
    series: pointsFromRows(false),
    calculation_series: pointsFromRows(true),
    rows,
  }
}

export function getWatchlists() {
  return fetchJson<WatchlistRecord[]>('/api/watchlists')
}

export function getMonitoringDashboard() {
  return fetchJson<MonitoringDashboardResponse>('/api/monitoring/dashboard')
}

export function getInstrumentMonitoring(instrumentId: string) {
  return fetchJson<InstrumentMonitoringResponse>(
    `/api/monitoring/instruments/${encodeURIComponent(instrumentId)}`,
  )
}

export function executeInstrumentRecalc(
  instrumentId: string,
  payload?: {
    job_type?: 'performance' | 'exposure' | 'all'
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
    { method: 'POST' },
  )
}

export function getInstrumentReferenceData(instrumentId: string) {
  return fetchJson<InstrumentReferenceData>(
    `/api/instruments/${encodeURIComponent(instrumentId)}/reference-data`,
  )
}

export function getInstrumentPriceBars(
  instrumentId: string,
  options?: { start_date?: string; end_date?: string; limit?: number },
) {
  const params = new URLSearchParams()
  if (options?.start_date) {
    params.set('start_date', options.start_date)
  }
  if (options?.end_date) {
    params.set('end_date', options.end_date)
  }
  if (typeof options?.limit === 'number') {
    params.set('limit', String(options.limit))
  }
  const query = params.toString()
  return fetchJson<InstrumentPriceBarsResponse>(
    `${buildInstrumentDetailApiPath(instrumentId, 'price-bars')}${query ? `?${query}` : ''}`,
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

export function resolveSharedInstrumentsBulk(identifiers: string[]) {
  return fetchJson<{
    results: Array<{
      identifier: string
      status: 'resolved' | 'not_found'
      instrument: SharedInstrumentRecord | null
    }>
  }>('/api/instruments/resolve-bulk', {
    method: 'POST',
    body: JSON.stringify({ identifiers }),
  })
}

export function resolveSharedInstrumentsFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return fetchForm<{
    results: Array<{
      identifier: string
      status: 'resolved' | 'not_found'
      instrument: SharedInstrumentRecord | null
    }>
  }>('/api/instruments/resolve-file', {
    method: 'POST',
    body: form,
  })
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

export function getInstrumentTaxonomyTree() {
  return fetchReferenceJson<InstrumentTaxonomyTreeResponse>('/api/taxonomies/instrument-taxonomy')
}

export function updateInstrumentTaxonomy(
  instrumentId: string,
  payload: {
    node_id: string | null
    updated_by?: string
  },
) {
  return fetchJson<InstrumentTaxonomyContext & { instrument_id: string; updated: boolean }>(
    `/api/taxonomies/instrument-taxonomy/instruments/${encodeURIComponent(instrumentId)}`,
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

export function updateInstrumentSettings(
  instrumentId: string,
  payload: InstrumentSettingsUpdatePayload,
) {
  return fetchJson<
    InstrumentAttributeValuesResponse & {
      updated: boolean
      taxonomy_updated: boolean
      status_updated: boolean
    }
  >(`/api/instrument-attributes/instruments/${encodeURIComponent(instrumentId)}/settings`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
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
  return fetchJson<InstrumentSummaryResponse>(buildInstrumentDetailApiPath(instrumentId, 'summary'))
}

export function getInstrumentLibrary() {
  return fetchReferenceJson<RawInstrumentLibraryItem[]>('/api/instruments/library').then((items) =>
    items.map(normalizeInstrumentLibraryItem),
  )
}

export function getInstrumentChart(instrumentId: string) {
  return fetchJson<InstrumentChartResponse>(buildInstrumentDetailApiPath(instrumentId, 'chart'))
}

export function getInstrumentPerformance(instrumentId: string) {
  return fetchJson<InstrumentPerformanceResponse>(buildInstrumentDetailApiPath(instrumentId, 'performance'))
}

export function getInstrumentRisk(instrumentId: string) {
  return fetchJson<InstrumentRiskResponse>(buildInstrumentDetailApiPath(instrumentId, 'risk'))
}

export function getInstrumentExposureSummary(instrumentId: string) {
  return fetchJson<FundExposureResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'exposure/summary'),
  )
}

export function getInstrumentExposureHoldings(instrumentId: string) {
  return fetchJson<FundExposureHoldingsResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'exposure/holdings'),
  )
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
  return fetchJson<InstrumentResearchResponse>(buildInstrumentDetailApiPath(instrumentId, 'research'))
}

export function getInstrumentResearchHistory(instrumentId: string) {
  return fetchJson<InstrumentResearchHistoryResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'research/history'),
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

export function updateInstrumentResearchProfile(
  instrumentId: string,
  payload: InstrumentResearchProfileUpdatePayload,
) {
  return fetchJson<InstrumentResearchResponse>(buildInstrumentDetailApiPath(instrumentId, 'research'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function createInstrumentResearchNote(
  instrumentId: string,
  payload: InstrumentResearchNoteUpdatePayload,
) {
  return fetchJson<InstrumentResearchResponse>(
    buildInstrumentDetailApiPath(instrumentId, 'research/notes'),
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export function updateInstrumentResearchNote(
  instrumentId: string,
  noteId: string,
  payload: InstrumentResearchNoteUpdatePayload,
) {
  return fetchJson<InstrumentResearchResponse>(
    buildInstrumentDetailApiPath(
      instrumentId,
      `research/notes/${encodeURIComponent(noteId)}`,
    ),
    { method: 'PUT', body: JSON.stringify(payload) },
  )
}

export function deleteInstrumentResearchNote(instrumentId: string, noteId: string) {
  return fetchJson<InstrumentResearchResponse>(
    `${buildInstrumentDetailApiPath(
      instrumentId,
      `research/notes/${encodeURIComponent(noteId)}`,
    )}?deleted_by=terminal_ui`,
    { method: 'DELETE' },
  )
}
