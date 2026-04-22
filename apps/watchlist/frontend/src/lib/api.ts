export type WatchlistRecord = {
  watchlist_id: string
  name: string
  description: string | null
  item_count: number
  owner_type: string
  owner_id: string
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
  asset_id: string
  asset_name: string
  asset_type: string
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
  asset_scope_json: string[]
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
  domain_code: 'classification' | 'research' | 'monitoring'
  group_code: string
  display_order: number
  options: string[]
  asset_scope_json: string[]
  applicability_json: Record<string, string[]>
  rubric_json: Record<string, unknown>
  is_groupable: boolean
  is_filterable: boolean
  is_view_column: boolean
  default_visible: boolean
  required_for_monitoring: boolean
}

export type InstrumentAttributeValuesResponse = {
  asset_id: string
  definitions: InstrumentAttributeDefinition[]
  values: Record<string, unknown>
}

export type InstrumentResolveResponse = {
  requested_asset_id: string
  canonical_asset_id: string | null
  asset_name: string
  asset_type: string
  primary_identifier: string | null
  detail_view_type: string
  detail_subject_id: string | null
  detail_supported: boolean
  support_reason: string
}

export type InstrumentAttributeDefinitionCreatePayload = {
  attribute_key: string
  label: string
  description?: string | null
  data_type: 'single_select' | 'multi_select' | 'boolean' | 'number' | 'text' | 'date'
  domain_code: 'classification' | 'research' | 'monitoring'
  group_code: string
  display_order?: number
  options?: string[]
  asset_scope_json?: string[]
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
}

export type ScreenerSnapshotMetadata = {
  as_of_date: string | null
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

export type MonitoringAssetRecord = {
  asset_id: string
  asset_name: string
  asset_type: string
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
}

export type MonitoringRecalcJobRecord = {
  recalc_job_id: string
  asset_id: string
  asset_name: string
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
    unique_asset_count: number
    needs_refresh_count: number
    missing_quote_count: number
    missing_label_count: number
    open_recalc_job_count: number
    failed_recalc_job_count: number
  }
  watchlists: MonitoringWatchlistSummary[]
  needs_attention_assets: MonitoringAssetRecord[]
  missing_label_assets: MonitoringAssetRecord[]
  open_recalc_jobs: MonitoringRecalcJobRecord[]
}

export type RecalcExecuteResponse = {
  recalc_job_id: string
  job_status: string
  result: Record<string, unknown>
}

export type FundSummaryResponse = {
  fund_id: string
  fund_name: string
  ticker_or_isin: string
  rating_as_of: string | null
  category_name: string
  overall_rating: number | null
  analyst_stance: string
  instrument_attributes: Record<string, unknown>
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
  nav_snapshot?: {
    nav_basis_type?: string | null
    nav_basis_source?: string | null
    latest_nav?: number | null
    latest_nav_with_dividend?: number | null
  }
}

export type FundLibraryItem = {
  fund_id: string
  fund_name: string
  ticker_or_isin: string | null
  product_type: string
}

type RawFundLibraryItem = {
  asset_id: string
  asset_name: string
  asset_type: string
  detail_view_type: string
  primary_identifier: string | null
}

export type FundChartPoint = { date: string; value: number }

export type FundChartResponse = {
  fund_id: string
  base_series_type: string
  currency: string
  date_range: { start: string; end: string } | null
  series: Array<{ name: string; points: FundChartPoint[] }>
  available_compare_targets: string[]
}

export type FundPerformanceResponse = {
  growth_chart_series: Array<{ name: string; value: number | null }>
  annual_returns: Array<Record<string, unknown>>
  trailing_returns: Array<Record<string, unknown>>
  ranking: {
    quartile?: number | null
    percentile?: number | null
    sample_count?: number | null
    category_name?: string | null
  } | null
  snapshot_metadata: {
    as_of_date: string | null
    methodology_version: string
    source_cutoff_at: string | null
  } | null
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
}

export type FundRatingsResponse = {
  overall_rating: number | null
  overall_score: number | null
  analyst_stance: string
  methodology_version: string
  dimension_scores: Array<{
    dimension_code: string
    score: number | null
    confidence_score: number | null
  }>
  override_info: {
    has_override: boolean
    approved_by: string | null
    approved_at: string | null
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

export type FundResearchResponse = {
  overview: Record<string, unknown>
  thesis: string
  conclusions: Array<Record<string, unknown>>
  timeline_notes?: Array<Record<string, unknown>>
  notes: string[]
}

export type FundNavSeriesResponse = {
  fund_id: string
  count: number
  nav_basis_preference: 'auto' | 'nav_with_dividend' | 'nav'
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  source_settings?: {
    source_mode: 'manual' | 'email' | 'api'
    source_email: string
    source_location: string
    source_api_profile: string
  }
  compare_settings?: {
    default_benchmark_asset_id: string | null
    peer_asset_ids: string[]
  }
  refresh_status?: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: string
  }
  series: Array<{ date: string; nav: number }>
  rows: Array<{
    as_of_date: string
    nav: number | null
    nav_with_dividend: number | null
    cumulative_distribution: number | null
    distribution_amount: number | null
    currency: string | null
    frequency: string | null
    adopted_at: string | null
  }>
}

type RawFundNavSeriesResponse = {
  asset_id: string
  count: number
  nav_basis_preference: 'auto' | 'nav_with_dividend' | 'nav'
  nav_basis_type: string | null
  nav_basis_source: string
  nav_basis_status: string
  source_settings?: {
    source_mode: 'manual' | 'email' | 'api'
    source_email: string
    source_location: string
    source_api_profile: string
  }
  compare_settings?: {
    default_benchmark_asset_id: string | null
    peer_asset_ids: string[]
  }
  refresh_status?: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: string
  }
  series: Array<{ date: string; nav: number }>
  rows: Array<{
    date: string
    nav: number | null
    nav_with_dividend: number | null
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

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

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
    throw new Error(body || `Request failed: ${response.status}`)
  }

  return (await response.json()) as T
}

function normalizeFundLibraryItem(item: RawFundLibraryItem): FundLibraryItem {
  return {
    fund_id: item.asset_id,
    fund_name: item.asset_name,
    ticker_or_isin: item.primary_identifier,
    product_type: item.asset_type || item.detail_view_type || 'fund',
  }
}

function normalizeFundNavSeriesResponse(response: RawFundNavSeriesResponse): FundNavSeriesResponse {
  return {
    fund_id: response.asset_id,
    count: response.count,
    nav_basis_preference: response.nav_basis_preference,
    nav_basis_type: response.nav_basis_type,
    nav_basis_source: response.nav_basis_source,
    nav_basis_status: response.nav_basis_status,
    source_settings: response.source_settings,
    compare_settings: response.compare_settings,
    refresh_status: response.refresh_status,
    series: response.series,
    rows: response.rows.map((row) => ({
      as_of_date: row.date,
      nav: row.nav,
      nav_with_dividend: row.nav_with_dividend,
      cumulative_distribution: row.cumulative_distribution ?? null,
      distribution_amount: row.distribution_amount ?? null,
      currency: row.currency,
      frequency: row.frequency,
      adopted_at: row.adopted_at,
    })),
  }
}

export function getWatchlists() {
  return fetchJson<WatchlistRecord[]>('/api/watchlists')
}

export function getMonitoringDashboard() {
  return fetchJson<MonitoringDashboardResponse>('/api/monitoring/dashboard')
}

export function executeAssetRecalc(
  assetId: string,
  payload?: {
    job_type?: 'performance' | 'exposure' | 'ratings' | 'all'
    trigger_type?: string
    trigger_ref_type?: string | null
    trigger_ref_id?: string | null
  },
) {
  return fetchJson<RecalcExecuteResponse>(`/api/recalc/assets/${encodeURIComponent(assetId)}/execute`, {
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

export function resolveInstrumentDetail(assetId: string) {
  return fetchJson<InstrumentResolveResponse>(
    `/api/instruments/${encodeURIComponent(assetId)}/resolve`,
  )
}

export function getSharedInstruments(options?: {
  search?: string
  asset_type?: string
  limit?: number
}) {
  const params = new URLSearchParams()
  if (options?.search?.trim()) {
    params.set('search', options.search.trim())
  }
  if (options?.asset_type?.trim()) {
    params.set('asset_type', options.asset_type.trim())
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

export function addWatchlistItems(watchlistId: string, assetIds: string[]) {
  return fetchJson<{ watchlist_id: string; accepted_count: number; pending_recalc_asset_ids: string[] }>(
    `/api/watchlists/${watchlistId}/items`,
    {
      method: 'POST',
      body: JSON.stringify({ asset_ids: assetIds }),
    },
  )
}

export function deleteWatchlistItems(watchlistId: string, assetIds: string[]) {
  return fetchJson<{ watchlist_id: string; deleted_count: number }>(
    `/api/watchlists/${watchlistId}/items/delete`,
    {
      method: 'POST',
      body: JSON.stringify({ asset_ids: assetIds }),
    },
  )
}

export function moveWatchlistItems(
  watchlistId: string,
  assetIds: string[],
  targetWatchlistId: string,
) {
  return fetchJson<WatchlistItemsMoveResponse>(`/api/watchlists/${watchlistId}/items/move`, {
    method: 'POST',
    body: JSON.stringify({
      asset_ids: assetIds,
      target_watchlist_id: targetWatchlistId,
    }),
  })
}

export function copyWatchlistItems(
  watchlistId: string,
  assetIds: string[],
  targetWatchlistId: string,
) {
  return fetchJson<WatchlistItemsCopyResponse>(`/api/watchlists/${watchlistId}/items/copy`, {
    method: 'POST',
    body: JSON.stringify({
      asset_ids: assetIds,
      target_watchlist_id: targetWatchlistId,
    }),
  })
}

export function getFieldRegistry(options?: {
  asset_type?: string | string[]
  product_type?: string
  search?: string
}) {
  const params = new URLSearchParams()
  if (options?.asset_type) {
    const assetType = Array.isArray(options.asset_type)
      ? options.asset_type.join(',')
      : options.asset_type
    if (assetType.trim()) {
      params.set('asset_type', assetType)
    }
  }
  if (options?.product_type?.trim()) {
    params.set('product_type', options.product_type.trim())
  }
  if (options?.search?.trim()) {
    params.set('search', options.search.trim())
  }
  const query = params.toString()
  return fetchJson<FieldRegistryResponse>(`/api/field-registry${query ? `?${query}` : ''}`)
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

export function getInstrumentAttributes(assetId: string) {
  return fetchJson<InstrumentAttributeValuesResponse>(
    `/api/instrument-attributes/assets/${assetId}`,
  )
}

export function updateInstrumentAttributes(
  assetId: string,
  payload: InstrumentAttributeUpdatePayload,
) {
  return fetchJson<InstrumentAttributeValuesResponse & { updated: boolean }>(
    `/api/instrument-attributes/assets/${assetId}`,
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

function buildInstrumentDetailApiPath(assetId: string, suffix: string) {
  return `/api/instruments/${encodeURIComponent(assetId)}/${suffix}`
}

export function getInstrumentSummary(assetId: string) {
  return fetchJson<FundSummaryResponse>(buildInstrumentDetailApiPath(assetId, 'summary'))
}

export function getInstrumentLibrary() {
  return fetchJson<RawFundLibraryItem[]>('/api/instruments/library').then((items) =>
    items.map(normalizeFundLibraryItem),
  )
}

export function getInstrumentChart(assetId: string) {
  return fetchJson<FundChartResponse>(buildInstrumentDetailApiPath(assetId, 'chart'))
}

export function getInstrumentPerformance(assetId: string) {
  return fetchJson<FundPerformanceResponse>(buildInstrumentDetailApiPath(assetId, 'performance'))
}

export function getInstrumentRisk(assetId: string) {
  return fetchJson<FundRiskResponse>(buildInstrumentDetailApiPath(assetId, 'risk'))
}

export function getInstrumentExposureSummary(assetId: string) {
  return fetchJson<FundExposureResponse>(
    buildInstrumentDetailApiPath(assetId, 'exposure/summary'),
  )
}

export function getInstrumentExposureHoldings(assetId: string) {
  return fetchJson<FundExposureHoldingsResponse>(
    buildInstrumentDetailApiPath(assetId, 'exposure/holdings'),
  )
}

export function getInstrumentRatings(assetId: string) {
  return fetchJson<FundRatingsResponse>(buildInstrumentDetailApiPath(assetId, 'ratings'))
}

export function getInstrumentPeople(assetId: string) {
  return fetchJson<FundPeopleResponse>(buildInstrumentDetailApiPath(assetId, 'people'))
}

export function getInstrumentStrategy(assetId: string) {
  return fetchJson<FundStrategyResponse>(buildInstrumentDetailApiPath(assetId, 'strategy'))
}

export function getInstrumentPrice(assetId: string) {
  return fetchJson<FundPriceResponse>(buildInstrumentDetailApiPath(assetId, 'price'))
}

export function getInstrumentDocuments(assetId: string) {
  return fetchJson<FundDocumentsResponse>(buildInstrumentDetailApiPath(assetId, 'documents'))
}

export function getInstrumentResearch(assetId: string) {
  return fetchJson<FundResearchResponse>(buildInstrumentDetailApiPath(assetId, 'research'))
}

export function getInstrumentNavSeries(assetId: string) {
  return fetchJson<RawFundNavSeriesResponse>(
    buildInstrumentDetailApiPath(assetId, 'nav-series'),
  ).then(normalizeFundNavSeriesResponse)
}

export function updateInstrumentNavSettings(assetId: string, payload: unknown) {
  return fetchJson<Record<string, unknown>>(
    buildInstrumentDetailApiPath(assetId, 'nav-settings'),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export function updateInstrumentPeople(
  assetId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundPeopleResponse>(buildInstrumentDetailApiPath(assetId, 'people'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentStrategy(
  assetId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundStrategyResponse>(buildInstrumentDetailApiPath(assetId, 'strategy'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentPrice(
  assetId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundPriceResponse>(buildInstrumentDetailApiPath(assetId, 'price'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentDocuments(
  assetId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundDocumentsResponse>(buildInstrumentDetailApiPath(assetId, 'documents'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function updateInstrumentResearch(
  assetId: string,
  payload: ManualProfileUpdatePayload,
) {
  return fetchJson<FundResearchResponse>(buildInstrumentDetailApiPath(assetId, 'research'), {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}
