import {
  Fragment,
  startTransition,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  type FundChartPoint,
  type FundChartResponse,
  type FundDocumentsResponse,
  type FundLibraryItem,
  type FundNavSeriesResponse,
  type FundPeopleResponse,
  type FundPerformanceResponse,
  type FundPriceResponse,
  type FundRatingsResponse,
  type FundResearchResponse,
  type FundRiskResponse,
  type FundStrategyResponse,
  type FundSummaryResponse,
  type FundTaxonomyTreeNode,
  type FundTaxonomyTreeResponse,
  type FundExposureHoldingsResponse as FundPortfolioHoldingsResponse,
  type FundExposureResponse as FundPortfolioResponse,
  type InstrumentAttributeValuesResponse,
  type InstrumentAttributeDefinition,
  getFundTaxonomyTree,
  getInstrumentAttributes,
  getInstrumentChart,
  getInstrumentDocuments,
  getInstrumentExposureHoldings as getInstrumentPortfolioHoldings,
  getInstrumentExposureSummary as getInstrumentPortfolioSummary,
  getInstrumentLibrary,
  getInstrumentNavSeries,
  getInstrumentPeople,
  getInstrumentPerformance,
  getInstrumentPrice,
  getInstrumentRatings,
  getInstrumentResearch,
  getInstrumentRisk,
  getInstrumentSummary,
  getInstrumentStrategy,
  updateFundTaxonomy,
  updateInstrumentAttributes,
  updateInstrumentDocuments,
  updateInstrumentPeople,
  updateInstrumentPrice,
  updateInstrumentResearch,
  updateInstrumentStrategy,
} from '../lib/api'
import {
  formatBoolean,
  formatCompactCurrency,
  formatDate,
  formatDateTime,
  formatLabel,
  formatNumber,
  formatPercent,
} from '../lib/format'
import { PLATFORM_HOME_URL } from '../lib/navigation'

type FundDetailBundle = {
  summary: FundSummaryResponse
  library: FundLibraryItem[]
  chart: FundChartResponse
  performance: FundPerformanceResponse
  risk: FundRiskResponse
  portfolio: FundPortfolioResponse
  holdings: FundPortfolioHoldingsResponse
  ratings: FundRatingsResponse
  people: FundPeopleResponse
  strategy: FundStrategyResponse
  price: FundPriceResponse
  documents: FundDocumentsResponse
  research: FundResearchResponse
  navSeries: FundNavSeriesResponse
}

type PeerComparisonMetric = NonNullable<
  NonNullable<FundPerformanceResponse['peer_comparison']>['metrics']
>[number]

type EditableKeyValueRow = {
  id: string
  key: string
  value: string
}

type PositionedPoint = FundChartPoint & {
  x: number
  y: number
}

type ChartGeometry = {
  width: number
  height: number
  paddingLeft: number
  paddingRight: number
  paddingTop: number
  paddingBottom: number
}

type EditableListRow = {
  id: string
  value: string
}

type EditableTeamRow = {
  id: string
  name: string
  role: string
  start_date: string
}

type EditableDocumentRow = {
  id: string
  title: string
  document_type: string
  as_of_date: string
  source: string
  status: string
  version_label: string
}

type EditableImportRow = {
  id: string
  import_type: string
  received_at: string
  source: string
  status: string
  file_name: string
}

type EditableExtractionRow = {
  id: string
  document_title: string
  extract_type: string
  status: string
  adopted_version: string
  updated_at: string
}

type EditableConclusionRow = {
  id: string
  conclusion: string
  evidence_ref: string
  status: string
}

type TimelineNoteImportance = 'low' | 'medium' | 'high'

type ResearchTimelineNote = {
  note_id: string
  note_date: string
  title: string
  summary: string
  body: string
  importance: TimelineNoteImportance
  tags: string[]
}

type TimelineNoteDraft = {
  note_id: string
  note_date: string
  title: string
  summary: string
  body: string
  importance: TimelineNoteImportance
  tagsText: string
}

type PeopleDraft = {
  overviewRows: EditableKeyValueRow[]
  teamRows: EditableTeamRow[]
  noteRows: EditableListRow[]
}

type StrategyDraft = {
  summary: string
  investment_objective: string
  processRows: EditableListRow[]
  riskControlRows: EditableListRow[]
  noteRows: EditableListRow[]
}

type PriceDraft = {
  overviewRows: EditableKeyValueRow[]
  distribution_policy: string
  policy_text: string
  feeNoteRows: EditableListRow[]
  noteRows: EditableListRow[]
}

type DocumentsDraft = {
  currentDocumentRows: EditableDocumentRow[]
  importRows: EditableImportRow[]
  extractionRows: EditableExtractionRow[]
  noteRows: EditableListRow[]
}

type ResearchDraft = {
  overviewRows: EditableKeyValueRow[]
  thesis: string
  conclusionRows: EditableConclusionRow[]
  timelineNotes: ResearchTimelineNote[]
  noteRows: EditableListRow[]
}

type ChartTimelineNoteContextMenu = {
  clientX: number
  clientY: number
  anchorDate: string
}

type PriceEditSection = 'ter' | 'fees' | 'policy'

type DetailTab =
  | 'overview'
  | 'performance'
  | 'risk'
  | 'price'
  | 'exposure'
  | 'people'
  | 'strategy'
  | 'documents'
  | 'research'
  | 'monitoring'

type ChartRange = '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | '5Y' | '10Y' | 'MAX' | 'CUSTOM'
type QuoteBasis = 'nav' | 'nav_with_dividend'
type ChartFrequency = 'daily' | 'weekly' | 'monthly'
type ChartDisplayStyle = 'mountain' | 'line' | 'dot'
type ChartScale = 'linear' | 'logarithmic'
type QuoteChartMenu = 'settings'
type ChartHoverPanel = 'primary' | 'drawdown'
type ChartHoverCursor = {
  xRatio: number
  y: number
}

type PerformanceMetricPeriodKey = '1W' | '1M' | 'YTD' | '1Y' | '2Y' | '3Y' | '5Y' | 'SI'
type PerformanceMatrixMode = 'values' | 'peer_percentile' | 'peer_rank' | 'peer_median_delta'
type PerformanceMatrixRowKey =
  | 'period_return'
  | 'annualized_return'
  | 'annualized_volatility'
  | 'excess_return'
  | 'sharpe_ratio'
  | 'sortino_ratio'
  | 'calmar_ratio'
  | 'information_ratio'
  | 'tracking_error'
  | 'beta'
  | 'max_drawdown'
  | 'recovery_days'
  | 'upside_capture'
  | 'downside_capture'
type RollingReturnWindowMonths = 12 | 24 | 36
type PerformanceMetricSnapshot = {
  periodReturn: number | null
  annualizedReturn: number | null
  annualizedVolatility: number | null
  annualizedDownsideDeviation: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  maxDrawdown: number | null
  recoveryDays: number | null
  recoveryOpen: boolean
}

type PerformanceRelativeSnapshot = {
  informationRatio: number | null
  trackingError: number | null
  beta: number | null
  upsideCapture: number | null
  downsideCapture: number | null
}

type PeriodicReturnPoint = {
  startDate: string
  endDate: string
  value: number
}

const PERFORMANCE_METRIC_PERIODS: Array<{
  key: PerformanceMetricPeriodKey
  label: string
}> = [
  { key: '1W', label: '1W' },
  { key: '1M', label: '1M' },
  { key: 'YTD', label: 'YTD' },
  { key: '1Y', label: '1Y' },
  { key: '2Y', label: '2Y' },
  { key: '3Y', label: '3Y' },
  { key: '5Y', label: '5Y' },
  { key: 'SI', label: 'SI' },
]

const PERFORMANCE_MATRIX_MODE_OPTIONS: Array<{
  value: PerformanceMatrixMode
  label: string
}> = [
  { value: 'values', label: 'Values' },
  { value: 'peer_percentile', label: 'Peer Percentile' },
  { value: 'peer_rank', label: 'Peer Rank' },
  { value: 'peer_median_delta', label: 'vs Median' },
]

const RISK_MATRIX_PERIOD_KEYS = new Set<PerformanceMetricPeriodKey>(['1Y', '3Y', '5Y', 'SI'])

const ROLLING_RETURN_WINDOW_OPTIONS: Array<{
  months: RollingReturnWindowMonths
  label: string
}> = [
  { months: 12, label: 'Rolling 12M' },
  { months: 24, label: 'Rolling 24M' },
  { months: 36, label: 'Rolling 36M' },
]

const TAB_ORDER: DetailTab[] = [
  'overview',
  'performance',
  'risk',
  'price',
  'exposure',
  'people',
  'strategy',
  'documents',
  'research',
  'monitoring',
]

const CORE_TABS: DetailTab[] = ['overview', 'performance', 'risk', 'price', 'exposure', 'people', 'strategy']

const TAB_LABELS: Record<DetailTab, string> = {
  overview: 'Overview',
  performance: 'Performance',
  risk: 'Risk',
  price: 'Price',
  exposure: 'Exposure',
  people: 'People',
  strategy: 'Strategy',
  documents: 'Documents',
  research: 'Research',
  monitoring: 'Monitoring',
}

const NAV_BASIS_LABELS: Record<string, string> = {
  auto: 'Auto',
  nav_with_dividend: 'NAV with Dividends',
  nav: 'NAV',
}

const NAV_BASIS_SOURCE_LABELS: Record<string, string> = {
  nav_with_dividend_series: 'NAV with Dividend Series',
  nav_series: 'NAV Series',
  manual_nav_editor: 'Manual Editor',
}

const PEOPLE_PRIMARY_OVERVIEW_FIELDS: Array<{
  key: string
  label: string
  type: 'date' | 'number' | 'text'
}> = [
  { key: 'inception_date', label: 'Inception Date', type: 'date' },
  { key: 'number_of_managers', label: 'Number of Managers', type: 'number' },
  { key: 'average_tenure_years', label: 'Average Tenure', type: 'number' },
  { key: 'longest_tenure_years', label: 'Longest Tenure', type: 'number' },
  { key: 'advisor', label: 'Advisor', type: 'text' },
  { key: 'sub_advisor', label: 'Sub-Advisor', type: 'text' },
]

const PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS = new Set(
  PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => field.key),
)

const RESEARCH_OVERVIEW_FIELDS: Array<{
  key: string
  label: string
  type: 'date' | 'text'
}> = [
  { key: 'current_view', label: 'Current View', type: 'text' },
  { key: 'research_status', label: 'Research Status', type: 'text' },
  { key: 'dd_status', label: 'DD Status', type: 'text' },
  { key: 'odd_status', label: 'ODD Status', type: 'text' },
  { key: 'ic_status', label: 'IC Status', type: 'text' },
  { key: 'decision', label: 'Decision', type: 'text' },
  { key: 'next_review_date', label: 'Next Review Date', type: 'date' },
  { key: 'primary_analyst', label: 'Primary Analyst', type: 'text' },
]

const QUOTE_RANGE_OPTIONS: Array<{ value: ChartRange | 'YTD' | 'CUSTOM'; label: string }> = [
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: 'YTD', label: 'YTD' },
  { value: '1Y', label: '1Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: 'MAX', label: 'MAX' },
  { value: 'CUSTOM', label: 'Custom' },
]

const QUOTE_BASIS_LABELS: Record<QuoteBasis, string> = {
  nav: 'NAV',
  nav_with_dividend: 'NAV with Dividends',
}

const MONTH_SHORT_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const ROLLING_WINDOW_MONTHS = 12

const PRIMARY_CHART_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 340,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 24,
  paddingBottom: 40,
}

const DRAWDOWN_CHART_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 120,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 18,
  paddingBottom: 30,
}

const CHART_HOVER_INSET = 2
const CHART_CROSSHAIR_INSET = 1

const RISK_SCATTER_GEOMETRY: ChartGeometry = {
  width: 560,
  height: 300,
  paddingLeft: 52,
  paddingRight: 20,
  paddingTop: 18,
  paddingBottom: 34,
}

const SECONDARY_SERIES_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 220,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 18,
  paddingBottom: 34,
}

function toTitleCase(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function makeRowId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
}

function parseTimelineNoteImportance(value: unknown): TimelineNoteImportance {
  return value === 'high' || value === 'medium' || value === 'low' ? value : 'medium'
}

function normalizeResearchTimelineNotes(notes: Array<Record<string, unknown>> | undefined) {
  return (notes || [])
    .map((row) => {
      const noteDate = typeof row.note_date === 'string' ? row.note_date.slice(0, 10) : ''
      if (!noteDate) {
        return null
      }
      return {
        note_id:
          typeof row.note_id === 'string' && row.note_id.trim()
            ? row.note_id
            : makeRowId('timeline-note'),
        note_date: noteDate,
        title: typeof row.title === 'string' ? row.title : '',
        summary: typeof row.summary === 'string' ? row.summary : '',
        body: typeof row.body === 'string' ? row.body : '',
        importance: parseTimelineNoteImportance(row.importance),
        tags: Array.isArray(row.tags)
          ? row.tags
              .map((value) => (typeof value === 'string' ? value.trim() : ''))
              .filter(Boolean)
          : [],
      } satisfies ResearchTimelineNote
    })
    .filter((row): row is ResearchTimelineNote => row !== null)
    .sort(sortResearchTimelineNotes)
}

function sortResearchTimelineNotes(left: ResearchTimelineNote, right: ResearchTimelineNote) {
  const dateCompare = right.note_date.localeCompare(left.note_date)
  if (dateCompare !== 0) {
    return dateCompare
  }
  return left.title.localeCompare(right.title)
}

function createTimelineNoteDraft(
  noteDate: string,
  note?: ResearchTimelineNote | null,
): TimelineNoteDraft {
  return {
    note_id: note?.note_id || makeRowId('timeline-note'),
    note_date: note?.note_date || noteDate,
    title: note?.title || '',
    summary: note?.summary || '',
    body: note?.body || '',
    importance: note?.importance || 'medium',
    tagsText: note?.tags.join(', ') || '',
  }
}

function serializeTimelineNoteDraft(draft: TimelineNoteDraft): ResearchTimelineNote {
  return {
    note_id: draft.note_id,
    note_date: draft.note_date,
    title: draft.title.trim(),
    summary: draft.summary.trim(),
    body: draft.body.trim(),
    importance: draft.importance,
    tags: draft.tagsText
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  }
}

function formatTimelineNoteImportance(importance: TimelineNoteImportance) {
  if (importance === 'high') {
    return 'High'
  }
  if (importance === 'low') {
    return 'Low'
  }
  return 'Medium'
}

function formatStarRating(rating: number | null | undefined) {
  if (rating == null || !Number.isFinite(rating)) {
    return '—'
  }
  const normalizedRating = Math.max(0, Math.min(5, Math.round(rating)))
  return `${'★'.repeat(normalizedRating)}${'☆'.repeat(5 - normalizedRating)}`
}

function getNumber(value: unknown) {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null
}

function getString(value: unknown) {
  return typeof value === 'string' && value ? value : '—'
}

function formatPeerMetricValue(metric: PeerComparisonMetric, value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return '—'
  }
  return metric.format === 'percent' ? formatPercent(value) : formatNumber(value, 2)
}

function formatPeerRank(metric: PeerComparisonMetric) {
  if (metric.rank == null || metric.sample_count == null) {
    return '—'
  }
  return `${formatNumber(metric.rank, 0)} / ${formatNumber(metric.sample_count, 0)}`
}

function formatPeerMetricDelta(metric: PeerComparisonMetric) {
  if (
    metric.value == null ||
    metric.peer_median == null ||
    !Number.isFinite(metric.value) ||
    !Number.isFinite(metric.peer_median)
  ) {
    return '—'
  }
  const delta = metric.value - metric.peer_median
  const prefix = delta > 0 ? '+' : ''
  return metric.format === 'percent'
    ? `${prefix}${formatPercent(delta)}`
    : `${prefix}${formatNumber(delta, 2)}`
}

function getSignedMetricTone(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return 'empty'
  }
  if (value > 0) {
    return 'positive'
  }
  if (value < 0) {
    return 'negative'
  }
  return 'neutral'
}

function getPeerMetricTone(metric: PeerComparisonMetric, mode: PerformanceMatrixMode) {
  if (mode === 'peer_percentile' || mode === 'peer_rank') {
    if (metric.percentile == null || !Number.isFinite(metric.percentile)) {
      return 'neutral'
    }
    if (metric.percentile >= 75) {
      return 'positive'
    }
    if (metric.percentile < 25) {
      return 'negative'
    }
    return 'neutral'
  }
  if (
    mode === 'peer_median_delta' &&
    metric.value != null &&
    metric.peer_median != null &&
    Number.isFinite(metric.value) &&
    Number.isFinite(metric.peer_median)
  ) {
    const delta = metric.value - metric.peer_median
    const isGood = metric.direction === 'lower' ? delta < 0 : delta > 0
    const isBad = metric.direction === 'lower' ? delta > 0 : delta < 0
    return isGood ? 'positive' : isBad ? 'negative' : 'neutral'
  }
  return 'neutral'
}

function getPeerMetricKeyForMatrixCell(
  rowKey: PerformanceMatrixRowKey,
  periodKey: PerformanceMetricPeriodKey,
) {
  if (rowKey === 'period_return') {
    return (
      {
        '1W': 'return_1w',
        '1M': 'return_1m',
        YTD: 'return_ytd',
        '1Y': 'return_1y',
      } as Partial<Record<PerformanceMetricPeriodKey, string>>
    )[periodKey] || null
  }
  if (rowKey === 'annualized_return') {
    return (
      {
        '1Y': 'return_1y',
        '3Y': 'return_3y_annualized',
        '5Y': 'return_5y_annualized',
        SI: 'annualized_return',
      } as Partial<Record<PerformanceMetricPeriodKey, string>>
    )[periodKey] || null
  }
  if (rowKey === 'annualized_volatility') {
    return periodKey === 'SI' ? 'volatility' : null
  }
  if (rowKey === 'sharpe_ratio') {
    return periodKey === 'SI' ? 'sharpe_ratio' : null
  }
  if (rowKey === 'calmar_ratio') {
    return periodKey === 'SI' ? 'calmar' : null
  }
  if (rowKey === 'max_drawdown') {
    return periodKey === 'SI' ? 'max_drawdown' : null
  }
  return null
}

function formatRiskComparisonValue(value: unknown) {
  return value && typeof value === 'string' ? toTitleCase(value.replace(/_/g, ' ')) : '—'
}

function getRows(value: unknown) {
  return Array.isArray(value) ? value : []
}

function getDisplayValue(value: unknown) {
  if (typeof value === 'boolean') {
    return formatBoolean(value)
  }
  if (typeof value === 'number') {
    return formatNumber(value)
  }
  return getString(value)
}

function formatPriceOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (typeof value === 'number') {
    if (
      key.includes('ratio') ||
      key.includes('fee')
    ) {
      return `${formatNumber(value, 2)} %`
    }
    if (key.includes('investment')) {
      return formatCompactCurrency(value)
    }
    return formatNumber(value)
  }
  return String(value)
}

function formatPeopleOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (key === 'inception_date') {
    return formatDate(value)
  }
  if (typeof value === 'number') {
    if (key.includes('tenure')) {
      return `${formatNumber(value, 1)} Years`
    }
    if (key === 'number_of_managers') {
      return formatNumber(value, 0)
    }
    return formatNumber(value)
  }
  if (typeof value === 'string' && key.includes('tenure')) {
    return value.toLowerCase().includes('year') ? value : `${value} Years`
  }
  return String(value)
}

function formatResearchOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (key === 'next_review_date') {
    return formatDate(value)
  }
  return String(value)
}

function formatMonitoringStatus(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'Unknown'
  }
  return toTitleCase(value)
}

function getMonitoringStatusTone(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'status-pending'
  }
  const normalized = value.toLowerCase()
  if (
    normalized === 'fresh' ||
    normalized === 'current' ||
    normalized === 'healthy' ||
    normalized === 'ready' ||
    normalized === 'imported'
  ) {
    return 'status-fresh'
  }
  if (normalized === 'failed' || normalized === 'blocked') {
    return 'status-error'
  }
  return 'status-pending'
}

function getDocumentStatusTone(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'status-pending'
  }
  const normalized = value.toLowerCase()
  if (
    normalized.includes('adopt') ||
    normalized.includes('complete') ||
    normalized.includes('current')
  ) {
    return 'status-fresh'
  }
  if (normalized.includes('review') || normalized.includes('pending') || normalized.includes('draft')) {
    return 'status-pending'
  }
  return 'status-attribute'
}

function formatNavBasisSource(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  return NAV_BASIS_SOURCE_LABELS[value] || toTitleCase(value)
}

function cleanListRows(rows: EditableListRow[]) {
  return rows.map((row) => row.value.trim()).filter(Boolean)
}

function parseOptionalNumber(value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function toEditablePeopleDraft(people: FundPeopleResponse): PeopleDraft {
  return {
    overviewRows: Object.entries(people.overview || {}).map(([key, value]) => ({
      id: makeRowId('overview'),
      key,
      value: value == null ? '' : String(value),
    })),
    teamRows: (people.team || []).map((row) => ({
      id: makeRowId('team'),
      name: typeof row.name === 'string' ? row.name : '',
      role: typeof row.role === 'string' ? row.role : '',
      start_date: typeof row.start_date === 'string' ? row.start_date.slice(0, 10) : '',
    })),
    noteRows: (people.notes || []).map((value) => ({
      id: makeRowId('people-note'),
      value,
    })),
  }
}

function toEditableStrategyDraft(strategy: FundStrategyResponse): StrategyDraft {
  return {
    summary: strategy.summary || '',
    investment_objective: strategy.investment_objective || '',
    processRows: (strategy.process_bullets || []).map((value) => ({
      id: makeRowId('process'),
      value,
    })),
    riskControlRows: (strategy.risk_controls || []).map((value) => ({
      id: makeRowId('risk-control'),
      value,
    })),
    noteRows: (strategy.notes || []).map((value) => ({
      id: makeRowId('strategy-note'),
      value,
    })),
  }
}

function toEditablePriceDraft(price: FundPriceResponse): PriceDraft {
  return {
    overviewRows: Object.entries(price.overview || {}).map(([key, value]) => ({
      id: makeRowId('price-overview'),
      key,
      value: value == null ? '' : String(value),
    })),
    distribution_policy: price.distribution_policy || '',
    policy_text: price.policy_text || '',
    feeNoteRows: (price.fee_notes || []).map((value) => ({
      id: makeRowId('price-fee-note'),
      value,
    })),
    noteRows: (price.notes || []).map((value) => ({
      id: makeRowId('price-note'),
      value,
    })),
  }
}

function toEditableDocumentsDraft(documents: FundDocumentsResponse): DocumentsDraft {
  return {
    currentDocumentRows: (documents.current_documents || []).map((row) => ({
      id: makeRowId('document'),
      title: typeof row.title === 'string' ? row.title : '',
      document_type: typeof row.document_type === 'string' ? row.document_type : '',
      as_of_date: typeof row.as_of_date === 'string' ? row.as_of_date.slice(0, 10) : '',
      source: typeof row.source === 'string' ? row.source : '',
      status: typeof row.status === 'string' ? row.status : '',
      version_label: typeof row.version_label === 'string' ? row.version_label : '',
    })),
    importRows: (documents.recent_imports || []).map((row) => ({
      id: makeRowId('document-import'),
      import_type: typeof row.import_type === 'string' ? row.import_type : '',
      received_at: typeof row.received_at === 'string' ? row.received_at.slice(0, 16) : '',
      source: typeof row.source === 'string' ? row.source : '',
      status: typeof row.status === 'string' ? row.status : '',
      file_name: typeof row.file_name === 'string' ? row.file_name : '',
    })),
    extractionRows: (documents.extraction_reviews || []).map((row) => ({
      id: makeRowId('document-extraction'),
      document_title: typeof row.document_title === 'string' ? row.document_title : '',
      extract_type: typeof row.extract_type === 'string' ? row.extract_type : '',
      status: typeof row.status === 'string' ? row.status : '',
      adopted_version: typeof row.adopted_version === 'string' ? row.adopted_version : '',
      updated_at: typeof row.updated_at === 'string' ? row.updated_at.slice(0, 16) : '',
    })),
    noteRows: (documents.notes || []).map((value) => ({
      id: makeRowId('document-note'),
      value,
    })),
  }
}

function toEditableResearchDraft(research: FundResearchResponse): ResearchDraft {
  return {
    overviewRows: Object.entries(research.overview || {}).map(([key, value]) => ({
      id: makeRowId('research-overview'),
      key,
      value: value == null ? '' : String(value),
    })),
    thesis: research.thesis || '',
    conclusionRows: (research.conclusions || []).map((row) => ({
      id: makeRowId('research-conclusion'),
      conclusion: typeof row.conclusion === 'string' ? row.conclusion : '',
      evidence_ref: typeof row.evidence_ref === 'string' ? row.evidence_ref : '',
      status: typeof row.status === 'string' ? row.status : '',
    })),
    timelineNotes: normalizeResearchTimelineNotes(research.timeline_notes),
    noteRows: (research.notes || []).map((value) => ({
      id: makeRowId('research-note'),
      value,
    })),
  }
}

function getOverviewDraftValue(draft: PriceDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function getPeopleOverviewDraftValue(draft: PeopleDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function getResearchOverviewDraftValue(draft: ResearchDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function upsertKeyValueRows(rows: EditableKeyValueRow[], key: string, value: string) {
  const existing = rows.find((row) => row.key === key)
  if (existing) {
    return rows.map((row) => (row.key === key ? { ...row, value } : row))
  }
  return [...rows, { id: makeRowId('overview'), key, value }]
}

function normalizeTabs(sourceTabs: string[]): DetailTab[] {
  const set = new Set<DetailTab>(CORE_TABS)

  sourceTabs.forEach((tab) => {
    const normalizedTab = tab === 'quote' || tab === 'summary' ? 'overview' : tab === 'portfolio' ? 'exposure' : tab
    if (TAB_ORDER.includes(normalizedTab as DetailTab)) {
      set.add(normalizedTab as DetailTab)
    }
  })

  return TAB_ORDER.filter((tab) => set.has(tab))
}

function buildChartLinePath(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
) {
  if (points.length < 2) {
    return ''
  }

  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const values = points.map((point) => point.value)
  const resolvedMin = min ?? Math.min(...values)
  const resolvedMax = max ?? Math.max(...values)
  const range = resolvedMax - resolvedMin || 1

  return points
    .map((point, index) => {
      const x =
        paddingLeft +
        (index / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
      const y =
        height -
        paddingBottom -
        ((point.value - resolvedMin) / range) * (height - paddingTop - paddingBottom)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function buildChartAreaPath(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
  baselineValue?: number,
) {
  if (points.length < 2) {
    return ''
  }

  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const values = points.map((point) => point.value)
  const resolvedMin = min ?? Math.min(...values)
  const resolvedMax = max ?? Math.max(...values)
  const range = resolvedMax - resolvedMin || 1

  const topPath = points
    .map((point, index) => {
      const x =
        paddingLeft +
        (index / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
      const y =
        height -
        paddingBottom -
        ((point.value - resolvedMin) / range) * (height - paddingTop - paddingBottom)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')

  const lastX = width - paddingRight
  const firstX = paddingLeft
  const resolvedBaselineValue = baselineValue ?? resolvedMin
  const baseline =
    height -
    paddingBottom -
    ((resolvedBaselineValue - resolvedMin) / range) * (height - paddingTop - paddingBottom)

  return `${topPath} L ${lastX.toFixed(2)} ${baseline.toFixed(2)} L ${firstX.toFixed(2)} ${baseline.toFixed(2)} Z`
}

function filterChartPoints(points: FundChartPoint[], range: ChartRange) {
  if (range === 'MAX' || points.length < 2) {
    return points
  }

  const latestDate = new Date(points[points.length - 1].date)
  if (Number.isNaN(latestDate.getTime())) {
    return points
  }

  const cutoff = new Date(latestDate)
  if (range === '1M') {
    cutoff.setMonth(cutoff.getMonth() - 1)
  } else if (range === '3M') {
    cutoff.setMonth(cutoff.getMonth() - 3)
  } else if (range === '6M') {
    cutoff.setMonth(cutoff.getMonth() - 6)
  } else if (range === '1Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 1)
  } else if (range === '3Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 3)
  } else if (range === '5Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 5)
  } else if (range === '10Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 10)
  }

  const filtered = points.filter((point) => {
    const current = new Date(point.date)
    return !Number.isNaN(current.getTime()) && current >= cutoff
  })

  return filtered.length >= 2 ? filtered : points.slice(-Math.min(points.length, 2))
}

function buildBasisSeries(rows: FundNavSeriesResponse['rows'], basis: QuoteBasis) {
  return [...rows]
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((row) => {
      const value = basis === 'nav' ? row.nav : row.nav_with_dividend
      return value == null ? null : { date: row.as_of_date, value }
    })
    .filter((point): point is FundChartPoint => point !== null)
}

function getAvailableQuoteBases(rows: FundNavSeriesResponse['rows']) {
  return (['nav_with_dividend', 'nav'] as QuoteBasis[]).filter(
    (basis) => buildBasisSeries(rows, basis).length > 0,
  )
}

function getRowsForCurrency(rows: FundNavSeriesResponse['rows'], currency: string) {
  return rows.filter((row) => !currency || !row.currency || row.currency === currency)
}

function toSelectedBasisPoints(series: FundNavSeriesResponse['series']) {
  return [...series]
    .sort((left, right) => left.date.localeCompare(right.date))
    .map((point) => ({ date: point.date, value: point.nav }))
}

function resolvePreferredQuoteBasis(value: string | null | undefined): QuoteBasis | null {
  return value === 'nav' || value === 'nav_with_dividend' ? value : null
}

function buildQuoteSeriesContext(
  rows: FundNavSeriesResponse['rows'],
  series: FundNavSeriesResponse['series'],
  {
    currency,
    requestedBasis,
    preferredBasis,
  }: {
    currency: string
    requestedBasis: QuoteBasis
    preferredBasis: string | null | undefined
  },
) {
  const preferred = resolvePreferredQuoteBasis(preferredBasis)
  const currencyFilteredRows = getRowsForCurrency(rows, currency)
  const scopedRows = currencyFilteredRows.length ? currencyFilteredRows : rows
  const availableBases = getAvailableQuoteBases(scopedRows)
  const activeBasis =
    availableBases.includes(requestedBasis)
      ? requestedBasis
      : preferred && availableBases.includes(preferred)
        ? preferred
        : availableBases[0] || preferred
  const seriesFromRows = activeBasis ? buildBasisSeries(scopedRows, activeBasis) : []
  const fallbackSeries =
    activeBasis && preferred === activeBasis && series.length > 0 ? toSelectedBasisPoints(series) : []
  const basisSeries = seriesFromRows.length ? seriesFromRows : fallbackSeries

  return {
    rows: scopedRows,
    availableBases,
    activeBasis,
    basisSeries,
    latestRow: scopedRows[scopedRows.length - 1],
  }
}

function formatChangeSummary(change: number | null, changePct: number | null, precision = 4) {
  const parts = [
    change == null ? null : `${change >= 0 ? '+' : ''}${formatNumber(change, precision)}`,
    changePct == null ? null : `${changePct >= 0 ? '+' : ''}${formatPercent(changePct)}`,
  ].filter((value): value is string => Boolean(value))

  return parts.length ? parts.join(' | ') : '—'
}

function getChartValueTagLayout(
  point: PositionedPoint | null | undefined,
  geometry: ChartGeometry,
  text: string | null,
) {
  if (!point || !text) {
    return null
  }

  const height = 22
  const width = Math.max(42, text.length * 6.1 + 12)
  const tailWidth = 8
  const direction = point.x > geometry.width * 0.55 ? 'right' : 'left'
  const x =
    direction === 'right'
      ? Math.min(
          Math.max(point.x - width - tailWidth - 4, geometry.paddingLeft + 8),
          geometry.width - width - tailWidth - 8,
        )
      : Math.min(
          Math.max(point.x + tailWidth + 4, geometry.paddingLeft + tailWidth + 8),
          geometry.width - width - tailWidth - 8,
        )
  const y = Math.min(
    Math.max(point.y - height / 2, geometry.paddingTop + 6),
    geometry.height - geometry.paddingBottom - height - 6,
  )
  const bubbleX = direction === 'right' ? x : x + tailWidth
  const tailMidY = Math.min(Math.max(point.y, y + 7), y + height - 7)
  const tipX = direction === 'right' ? bubbleX + width + tailWidth : bubbleX - tailWidth
  const textX = bubbleX + width / 2
  const textY = y + 14.5
  const tailHalfHeight = 4
  const path =
    direction === 'right'
      ? [
          `M ${bubbleX} ${y}`,
          `H ${bubbleX + width}`,
          `V ${tailMidY - tailHalfHeight}`,
          `L ${tipX} ${tailMidY}`,
          `L ${bubbleX + width} ${tailMidY + tailHalfHeight}`,
          `V ${y + height}`,
          `H ${bubbleX}`,
          `V ${y}`,
          'Z',
        ].join(' ')
      : [
          `M ${bubbleX} ${y}`,
          `H ${bubbleX + width}`,
          `V ${y + height}`,
          `H ${bubbleX}`,
          `V ${tailMidY + tailHalfHeight}`,
          `L ${tipX} ${tailMidY}`,
          `L ${bubbleX} ${tailMidY - tailHalfHeight}`,
          `V ${y}`,
          'Z',
        ].join(' ')

  return { path, textX, textY }
}

function getChartTooltipAnchor(cursor: ChartHoverCursor | null | undefined, geometry: ChartGeometry) {
  if (!cursor) {
    return null
  }

  const plotBounds = getChartPlotBounds(geometry, CHART_CROSSHAIR_INSET)
  const x = plotBounds.left + plotBounds.width * cursor.xRatio
  const y = cursor.y

  return {
    left: `${(x / geometry.width) * 100}%`,
    top: `${(y / geometry.height) * 100}%`,
    transform: 'translate(18px, calc(-100% - 12px))',
  }
}

function getDrawdownAxisBounds(points: FundChartPoint[]) {
  if (!points.length) {
    return { min: -5, max: 0 }
  }

  const rawMin = Math.min(...points.map((point) => point.value))
  const paddedMin = Math.floor((rawMin - 0.4) / 0.5) * 0.5

  return {
    min: Math.min(paddedMin, -0.5),
    max: 0,
  }
}

function getPaddedAxisBounds(min: number, max: number, ratio = 0.08, minimumPadding = 0.01) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: 0, max: 1 }
  }
  if (min === max) {
    const padding = Math.max(Math.abs(min) * ratio, minimumPadding)
    return { min: min - padding, max: max + padding }
  }
  const padding = Math.max((max - min) * ratio, minimumPadding)
  return { min: min - padding, max: max + padding }
}

function getYAxisStubEndX(geometry: ChartGeometry) {
  return geometry.paddingLeft - 14
}

function getYAxisLabelTextY(lineY: number, geometry: ChartGeometry, placement: 'below' | 'above' = 'below') {
  if (placement === 'above') {
    return Math.max(lineY - 6, 12)
  }
  return Math.min(lineY + 18, geometry.height - 4)
}

function getChartPlotBounds(geometry: ChartGeometry, inset = 0) {
  const left = geometry.paddingLeft + inset
  const right = geometry.width - geometry.paddingRight - inset
  const top = geometry.paddingTop + inset
  const bottom = geometry.height - geometry.paddingBottom - inset

  return {
    left,
    right,
    top,
    bottom,
    width: Math.max(right - left, 1),
    height: Math.max(bottom - top, 1),
  }
}

function getPlotXFromRatio(geometry: ChartGeometry, xRatio: number, inset = CHART_CROSSHAIR_INSET) {
  const plotBounds = getChartPlotBounds(geometry, inset)
  return plotBounds.left + plotBounds.width * Math.min(Math.max(xRatio, 0), 1)
}

function applyChartScale(points: FundChartPoint[], scale: ChartScale) {
  if (!points.length) {
    return []
  }
  if (scale === 'logarithmic') {
    const canUseLogScale = points.every((point) => point.value > 0)
    if (!canUseLogScale) {
      return points
    }
    return points.map((point) => ({
      date: point.date,
      value: Math.log10(point.value),
    }))
  }
  return points
}

function buildDrawdownSeries(points: FundChartPoint[]) {
  let runningMax = 0
  return points.map((point) => {
    runningMax = Math.max(runningMax, point.value)
    const drawdown = runningMax > 0 ? ((point.value / runningMax) - 1) * 100 : 0
    return {
      date: point.date,
      value: drawdown,
    }
  })
}

function getSeriesChangeStats(points: FundChartPoint[]) {
  if (points.length < 2) {
    return { change: null, changePct: null }
  }
  const first = points[0]
  const last = points[points.length - 1]
  const change = last.value - first.value
  return {
    change,
    changePct: first.value !== 0 ? (change / first.value) * 100 : null,
  }
}

function getLatestPointChangeStats(points: FundChartPoint[]) {
  if (points.length < 2) {
    return { change: null, changePct: null }
  }
  const latest = points[points.length - 1]
  const previous = points[points.length - 2]
  const change = latest.value - previous.value
  return {
    change,
    changePct: previous.value !== 0 ? (change / previous.value) * 100 : null,
  }
}

function getRangeWindow(points: FundChartPoint[], range: ChartRange) {
  if (!points.length) {
    return { start: '', end: '' }
  }

  const end = points[points.length - 1].date
  if (range === 'MAX' || range === 'CUSTOM') {
    return { start: points[0].date, end }
  }

  const latestDate = new Date(`${end}T00:00:00`)
  if (Number.isNaN(latestDate.getTime())) {
    return { start: points[0].date, end }
  }

  const startDate = new Date(latestDate)
  if (range === '1M') {
    startDate.setMonth(startDate.getMonth() - 1)
  } else if (range === '3M') {
    startDate.setMonth(startDate.getMonth() - 3)
  } else if (range === '6M') {
    startDate.setMonth(startDate.getMonth() - 6)
  } else if (range === 'YTD') {
    startDate.setMonth(0)
    startDate.setDate(1)
  } else if (range === '1Y') {
    startDate.setFullYear(startDate.getFullYear() - 1)
  } else if (range === '3Y') {
    startDate.setFullYear(startDate.getFullYear() - 3)
  } else if (range === '5Y') {
    startDate.setFullYear(startDate.getFullYear() - 5)
  } else if (range === '10Y') {
    startDate.setFullYear(startDate.getFullYear() - 10)
  }

  return {
    start: startDate.toISOString().slice(0, 10),
    end,
  }
}

function filterSeriesByDateWindow(
  points: FundChartPoint[],
  startDate: string,
  endDate: string,
) {
  return points.filter((point) => {
    if (startDate && point.date < startDate) {
      return false
    }
    if (endDate && point.date > endDate) {
      return false
    }
    return true
  })
}

function getMonthBucket(value: string) {
  return value.slice(0, 7)
}

function formatMonthBucket(value: string) {
  const year = Number(value.slice(0, 4))
  const monthIndex = Number(value.slice(5, 7)) - 1
  if (!Number.isFinite(year) || monthIndex < 0 || monthIndex > 11) {
    return value
  }
  return `${MONTH_SHORT_LABELS[monthIndex]} ${String(year).slice(-2)}`
}

function buildMonthlyCloseSeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const monthlyPoints: FundChartPoint[] = []

  sortedPoints.forEach((point) => {
    const currentBucket = getMonthBucket(point.date)
    const previousPoint = monthlyPoints[monthlyPoints.length - 1]
    if (!previousPoint || getMonthBucket(previousPoint.date) !== currentBucket) {
      monthlyPoints.push(point)
      return
    }
    monthlyPoints[monthlyPoints.length - 1] = point
  })

  return monthlyPoints
}

function rebaseSeries(points: FundChartPoint[], baseValue = 100) {
  if (!points.length || points[0].value === 0) {
    return []
  }
  const startingValue = points[0].value
  return points.map((point) => ({
    date: point.date,
    value: (point.value / startingValue) * baseValue,
  }))
}

function buildMonthlyReturnSeries(points: FundChartPoint[]) {
  const monthlyCloses = buildMonthlyCloseSeries(points)
  const monthlyReturns: FundChartPoint[] = []

  for (let index = 1; index < monthlyCloses.length; index += 1) {
    const previousPoint = monthlyCloses[index - 1]
    const currentPoint = monthlyCloses[index]
    if (previousPoint.value === 0) {
      continue
    }
    monthlyReturns.push({
      date: currentPoint.date,
      value: ((currentPoint.value / previousPoint.value) - 1) * 100,
    })
  }

  return monthlyReturns
}

function buildRollingReturnSeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const monthlyCloses = buildMonthlyCloseSeries(points)
  const rollingReturns: FundChartPoint[] = []

  for (let index = windowMonths; index < monthlyCloses.length; index += 1) {
    const basePoint = monthlyCloses[index - windowMonths]
    const currentPoint = monthlyCloses[index]
    if (basePoint.value === 0) {
      continue
    }
    rollingReturns.push({
      date: currentPoint.date,
      value: ((currentPoint.value / basePoint.value) - 1) * 100,
    })
  }

  return rollingReturns
}

function buildRollingAnnualizedVolatilitySeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const monthlyReturns = buildMonthlyReturnSeries(points).map((point) => ({
    date: point.date,
    value: point.value / 100,
  }))
  const rollingVolatility: FundChartPoint[] = []

  for (let index = windowMonths - 1; index < monthlyReturns.length; index += 1) {
    const windowReturns = monthlyReturns.slice(index - windowMonths + 1, index + 1).map((point) => point.value)
    const stdev = getSampleStandardDeviation(windowReturns)
    if (stdev == null) {
      continue
    }
    rollingVolatility.push({
      date: monthlyReturns[index].date,
      value: stdev * Math.sqrt(12) * 100,
    })
  }

  return rollingVolatility
}

function buildRollingSharpeSeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const monthlyReturns = buildMonthlyReturnSeries(points).map((point) => ({
    date: point.date,
    value: point.value / 100,
  }))
  const rollingSharpe: FundChartPoint[] = []

  for (let index = windowMonths - 1; index < monthlyReturns.length; index += 1) {
    const windowReturns = monthlyReturns.slice(index - windowMonths + 1, index + 1).map((point) => point.value)
    const stdev = getSampleStandardDeviation(windowReturns)
    if (stdev == null || stdev === 0) {
      continue
    }
    const mean = windowReturns.reduce((sum, value) => sum + value, 0) / windowReturns.length
    rollingSharpe.push({
      date: monthlyReturns[index].date,
      value: (mean / stdev) * Math.sqrt(12),
    })
  }

  return rollingSharpe
}

function alignMonthlyReturnPairs(leftPoints: FundChartPoint[], rightPoints: FundChartPoint[]) {
  const leftMonthlyReturns = buildMonthlyReturnSeries(leftPoints)
  const rightMonthlyReturns = buildMonthlyReturnSeries(rightPoints)
  const rightMap = new Map(
    rightMonthlyReturns.map((point) => [getMonthBucket(point.date), { date: point.date, value: point.value / 100 }] as const),
  )

  return leftMonthlyReturns
    .map((point) => {
      const bucket = getMonthBucket(point.date)
      const rightPoint = rightMap.get(bucket)
      if (!rightPoint) {
        return null
      }
      return {
        date: point.date,
        left: point.value / 100,
        right: rightPoint.value,
      }
    })
    .filter(
      (point): point is { date: string; left: number; right: number } => point !== null,
    )
}

function getSampleCovariance(left: number[], right: number[]) {
  if (left.length < 2 || right.length < 2 || left.length !== right.length) {
    return null
  }
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length
  const rightMean = right.reduce((sum, value) => sum + value, 0) / right.length
  const covariance =
    left.reduce((sum, value, index) => sum + ((value - leftMean) * (right[index] - rightMean)), 0) /
    (left.length - 1)
  return covariance
}

function buildRollingBetaSeries(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
  windowMonths = ROLLING_WINDOW_MONTHS,
) {
  const alignedPairs = alignMonthlyReturnPairs(points, benchmarkPoints)
  const rollingBeta: FundChartPoint[] = []

  for (let index = windowMonths - 1; index < alignedPairs.length; index += 1) {
    const windowPairs = alignedPairs.slice(index - windowMonths + 1, index + 1)
    const leftReturns = windowPairs.map((point) => point.left)
    const rightReturns = windowPairs.map((point) => point.right)
    const covariance = getSampleCovariance(leftReturns, rightReturns)
    const variance = getSampleStandardDeviation(rightReturns)
    if (covariance == null || variance == null || variance === 0) {
      continue
    }
    rollingBeta.push({
      date: windowPairs[windowPairs.length - 1].date,
      value: covariance / (variance ** 2),
    })
  }

  return rollingBeta
}

function buildMonthlyMinimumSeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const monthlyMinimums: FundChartPoint[] = []

  sortedPoints.forEach((point) => {
    const currentBucket = getMonthBucket(point.date)
    const previousPoint = monthlyMinimums[monthlyMinimums.length - 1]
    if (!previousPoint || getMonthBucket(previousPoint.date) !== currentBucket) {
      monthlyMinimums.push(point)
      return
    }
    if (point.value <= previousPoint.value) {
      monthlyMinimums[monthlyMinimums.length - 1] = point
    }
  })

  return monthlyMinimums
}

function inferAnnualizationPeriodsPerYear(points: FundChartPoint[]) {
  const diffs: number[] = []

  for (let index = 1; index < points.length; index += 1) {
    const previousDate = new Date(`${points[index - 1].date}T00:00:00`)
    const currentDate = new Date(`${points[index].date}T00:00:00`)
    if (Number.isNaN(previousDate.getTime()) || Number.isNaN(currentDate.getTime())) {
      continue
    }
    const diffDays = (currentDate.getTime() - previousDate.getTime()) / 86_400_000
    if (diffDays > 0) {
      diffs.push(diffDays)
    }
  }

  if (!diffs.length) {
    return 252
  }

  const sortedDiffs = [...diffs].sort((left, right) => left - right)
  const medianDiff = sortedDiffs[Math.floor(sortedDiffs.length / 2)]
  if (medianDiff <= 3) {
    return 252
  }
  if (medianDiff <= 10) {
    return 52
  }
  if (medianDiff <= 20) {
    return 24
  }
  return 12
}

function getSampleStandardDeviation(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance =
    values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function buildMonthlyAnnualizedVolatilitySeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortedPoints)
  const returnsByMonth = new Map<string, { date: string; returns: number[] }>()

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const previousPoint = sortedPoints[index - 1]
    const currentPoint = sortedPoints[index]
    if (previousPoint.value === 0) {
      continue
    }
    const monthlyKey = getMonthBucket(currentPoint.date)
    const bucket = returnsByMonth.get(monthlyKey) || { date: currentPoint.date, returns: [] }
    bucket.date = currentPoint.date
    bucket.returns.push((currentPoint.value / previousPoint.value) - 1)
    returnsByMonth.set(monthlyKey, bucket)
  }

  return Array.from(returnsByMonth.entries())
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([, bucket]) => {
      const stdev = getSampleStandardDeviation(bucket.returns)
      if (stdev == null) {
        return null
      }
      return {
        date: bucket.date,
        value: stdev * Math.sqrt(periodsPerYear) * 100,
      }
    })
    .filter((point): point is FundChartPoint => point !== null)
}

function buildMonthlyReturnMatrix(points: FundChartPoint[]) {
  const monthlyReturns = buildMonthlyReturnSeries(points)
  const monthlyCloses = buildMonthlyCloseSeries(points)
  const rows = new Map<number, { year: string; months: Array<number | null>; ytd: number | null }>()
  const yearEndCloses = new Map<number, FundChartPoint>()

  monthlyCloses.forEach((point) => {
    yearEndCloses.set(Number(point.date.slice(0, 4)), point)
  })

  monthlyReturns.forEach((point) => {
    const year = Number(point.date.slice(0, 4))
    const monthIndex = Number(point.date.slice(5, 7)) - 1
    const row = rows.get(year) || {
      year: String(year),
      months: Array.from({ length: 12 }, () => null),
      ytd: null,
    }
    row.months[monthIndex] = point.value
    rows.set(year, row)
  })

  return Array.from(rows.entries())
    .sort((left, right) => right[0] - left[0])
    .map(([year, row]) => {
      const previousYearClose = yearEndCloses.get(year - 1)
      const currentYearClose = yearEndCloses.get(year)
      const ytd =
        previousYearClose && currentYearClose && previousYearClose.value !== 0
          ? ((currentYearClose.value / previousYearClose.value) - 1) * 100
          : null
      return {
        ...row,
        ytd,
      }
    })
}

function sortSeriesByDate(points: FundChartPoint[]) {
  return [...points].sort((left, right) => left.date.localeCompare(right.date))
}

function shiftIsoDate(
  value: string,
  offset: { days?: number; months?: number; years?: number },
) {
  const year = Number(value.slice(0, 4))
  const month = Number(value.slice(5, 7))
  const day = Number(value.slice(8, 10))
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null
  }

  let targetYear = year + (offset.years || 0)
  let targetMonthIndex = month - 1 + (offset.months || 0)
  while (targetMonthIndex < 0) {
    targetMonthIndex += 12
    targetYear -= 1
  }
  while (targetMonthIndex > 11) {
    targetMonthIndex -= 12
    targetYear += 1
  }

  const lastDayOfMonth = new Date(Date.UTC(targetYear, targetMonthIndex + 1, 0)).getUTCDate()
  const targetDay = Math.min(day, lastDayOfMonth)
  const shifted = new Date(Date.UTC(targetYear, targetMonthIndex, targetDay))
  if (offset.days) {
    shifted.setUTCDate(shifted.getUTCDate() + offset.days)
  }
  return shifted.toISOString().slice(0, 10)
}

function findLastPointIndexOnOrBefore(points: FundChartPoint[], targetDate: string) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].date <= targetDate) {
      return index
    }
  }
  return -1
}

function getAnchoredWindow(
  points: FundChartPoint[],
  periodKey: PerformanceMetricPeriodKey,
  referenceEndDate?: string | null,
) {
  const sortedPoints = sortSeriesByDate(points)
  if (!sortedPoints.length) {
    return []
  }

  const requestedEndDate = referenceEndDate || sortedPoints[sortedPoints.length - 1]?.date || ''
  const endIndex = findLastPointIndexOnOrBefore(sortedPoints, requestedEndDate)
  if (endIndex < 0) {
    return []
  }

  if (periodKey === 'SI') {
    return sortedPoints.slice(0, endIndex + 1)
  }

  const endPoint = sortedPoints[endIndex]
  const targetStartDate =
    periodKey === 'YTD'
      ? `${endPoint.date.slice(0, 4)}-01-01`
      : periodKey === '1W'
        ? shiftIsoDate(endPoint.date, { days: -7 })
        : periodKey === '1M'
          ? shiftIsoDate(endPoint.date, { months: -1 })
          : periodKey === '1Y'
            ? shiftIsoDate(endPoint.date, { years: -1 })
            : periodKey === '2Y'
              ? shiftIsoDate(endPoint.date, { years: -2 })
              : periodKey === '3Y'
                ? shiftIsoDate(endPoint.date, { years: -3 })
                : shiftIsoDate(endPoint.date, { years: -5 })

  if (!targetStartDate) {
    return []
  }

  const startIndex = findLastPointIndexOnOrBefore(sortedPoints, targetStartDate)
  if (startIndex < 0 || startIndex >= endIndex) {
    return []
  }
  return sortedPoints.slice(startIndex, endIndex + 1)
}

function getPeriodReturnFromPoints(points: FundChartPoint[]) {
  if (points.length < 2 || points[0].value === 0) {
    return null
  }
  return ((points[points.length - 1].value / points[0].value) - 1) * 100
}

function getAnnualizedReturnFromPoints(points: FundChartPoint[]) {
  if (points.length < 2 || points[0].value <= 0 || points[points.length - 1].value <= 0) {
    return null
  }
  const dayCount = getDateDifferenceInDays(points[0].date, points[points.length - 1].date)
  if (dayCount == null || dayCount <= 0) {
    return null
  }
  return (Math.pow(points[points.length - 1].value / points[0].value, 365.25 / dayCount) - 1) * 100
}

function getDownsideDeviation(values: number[]) {
  const downside = values.filter((value) => value < 0)
  if (!downside.length) {
    return null
  }
  const variance = downside.reduce((sum, value) => sum + (value ** 2), 0) / downside.length
  return Math.sqrt(Math.max(variance, 0))
}

function buildPeriodicReturnSeries(points: FundChartPoint[]) {
  const sortedPoints = sortSeriesByDate(points)
  const periodicReturns: PeriodicReturnPoint[] = []

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const previousPoint = sortedPoints[index - 1]
    const currentPoint = sortedPoints[index]
    if (previousPoint.value === 0) {
      continue
    }
    periodicReturns.push({
      startDate: previousPoint.date,
      endDate: currentPoint.date,
      value: (currentPoint.value / previousPoint.value) - 1,
    })
  }

  return periodicReturns
}

function getAnnualizedVolatilityFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const stdev = getSampleStandardDeviation(periodicReturns)
  if (stdev == null) {
    return null
  }
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortSeriesByDate(points))
  return stdev * Math.sqrt(periodsPerYear) * 100
}

function getAnnualizedDownsideDeviationFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const downsideDeviation = getDownsideDeviation(periodicReturns)
  if (downsideDeviation == null) {
    return null
  }
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortSeriesByDate(points))
  return downsideDeviation * Math.sqrt(periodsPerYear) * 100
}

function getSharpeRatioFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const stdev = getSampleStandardDeviation(periodicReturns)
  if (stdev == null || stdev === 0) {
    return null
  }
  const mean = periodicReturns.reduce((sum, value) => sum + value, 0) / periodicReturns.length
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortSeriesByDate(points))
  return (mean / stdev) * Math.sqrt(periodsPerYear)
}

function getSortinoRatioFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const downsideDeviation = getDownsideDeviation(periodicReturns)
  if (downsideDeviation == null || downsideDeviation === 0) {
    return null
  }
  const mean = periodicReturns.reduce((sum, value) => sum + value, 0) / periodicReturns.length
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortSeriesByDate(points))
  return (mean / downsideDeviation) * Math.sqrt(periodsPerYear)
}

function alignPeriodicReturnPairs(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
) {
  const periodicReturns = buildPeriodicReturnSeries(points)
  const sortedBenchmarkPoints = sortSeriesByDate(benchmarkPoints)

  return periodicReturns
    .map((point) => {
      const benchmarkStartIndex = findLastPointIndexOnOrBefore(sortedBenchmarkPoints, point.startDate)
      const benchmarkEndIndex = findLastPointIndexOnOrBefore(sortedBenchmarkPoints, point.endDate)
      if (
        benchmarkStartIndex < 0 ||
        benchmarkEndIndex <= benchmarkStartIndex ||
        sortedBenchmarkPoints[benchmarkStartIndex].value === 0
      ) {
        return null
      }
      return {
        date: point.endDate,
        left: point.value,
        right:
          (sortedBenchmarkPoints[benchmarkEndIndex].value /
            sortedBenchmarkPoints[benchmarkStartIndex].value) -
          1,
      }
    })
    .filter(
      (
        point,
      ): point is {
        date: string
        left: number
        right: number
      } => point !== null,
    )
}

function getAnnualizedReturnFromPeriodicValues(values: number[], periodsPerYear: number) {
  if (!values.length) {
    return null
  }
  const cumulative = values.reduce((product, value) => product * (1 + value), 1)
  if (!Number.isFinite(cumulative) || cumulative <= 0) {
    return null
  }
  return (Math.pow(cumulative, periodsPerYear / values.length) - 1) * 100
}

function getMedianValue(values: number[]) {
  if (!values.length) {
    return null
  }
  const sortedValues = [...values].sort((left, right) => left - right)
  const middleIndex = Math.floor(sortedValues.length / 2)
  if (sortedValues.length % 2 === 0) {
    return (sortedValues[middleIndex - 1] + sortedValues[middleIndex]) / 2
  }
  return sortedValues[middleIndex]
}

function getPercentileRank(values: number[], targetValue: number) {
  if (!values.length) {
    return null
  }
  const belowOrEqualCount = values.filter((value) => value <= targetValue).length
  return (belowOrEqualCount / values.length) * 100
}

function getTrailingNegativeMonthCount(points: FundChartPoint[]) {
  let count = 0
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].value >= 0) {
      break
    }
    count += 1
  }
  return count
}

function buildPerformanceRelativeSnapshot(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
): PerformanceRelativeSnapshot {
  const alignedPairs = alignPeriodicReturnPairs(points, benchmarkPoints)
  if (alignedPairs.length < 2) {
    return {
      informationRatio: null,
      trackingError: null,
      beta: null,
      upsideCapture: null,
      downsideCapture: null,
    }
  }

  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortSeriesByDate(points))
  const activeReturns = alignedPairs.map((point) => point.left - point.right)
  const activeReturnStdev = getSampleStandardDeviation(activeReturns)
  const activeReturnMean = activeReturns.reduce((sum, value) => sum + value, 0) / activeReturns.length
  const trackingError =
    activeReturnStdev == null ? null : activeReturnStdev * Math.sqrt(periodsPerYear) * 100
  const informationRatio =
    activeReturnStdev == null || activeReturnStdev === 0
      ? null
      : (activeReturnMean / activeReturnStdev) * Math.sqrt(periodsPerYear)

  const benchmarkReturns = alignedPairs.map((point) => point.right)
  const benchmarkVolatility = getSampleStandardDeviation(benchmarkReturns)
  const beta =
    benchmarkVolatility == null || benchmarkVolatility === 0
      ? null
      : (getSampleCovariance(
          alignedPairs.map((point) => point.left),
          benchmarkReturns,
        ) ?? NaN) /
        (benchmarkVolatility ** 2)

  const upPairs = alignedPairs.filter((point) => point.right > 0)
  const downPairs = alignedPairs.filter((point) => point.right < 0)
  const upsideBenchmarkReturn = getAnnualizedReturnFromPeriodicValues(
    upPairs.map((point) => point.right),
    periodsPerYear,
  )
  const upsideFundReturn = getAnnualizedReturnFromPeriodicValues(
    upPairs.map((point) => point.left),
    periodsPerYear,
  )
  const downsideBenchmarkReturn = getAnnualizedReturnFromPeriodicValues(
    downPairs.map((point) => point.right),
    periodsPerYear,
  )
  const downsideFundReturn = getAnnualizedReturnFromPeriodicValues(
    downPairs.map((point) => point.left),
    periodsPerYear,
  )

  return {
    informationRatio: Number.isFinite(informationRatio) ? informationRatio : null,
    trackingError: Number.isFinite(trackingError) ? trackingError : null,
    beta: Number.isFinite(beta) ? beta : null,
    upsideCapture:
      upsideFundReturn == null || upsideBenchmarkReturn == null || upsideBenchmarkReturn === 0
        ? null
        : (upsideFundReturn / upsideBenchmarkReturn) * 100,
    downsideCapture:
      downsideFundReturn == null || downsideBenchmarkReturn == null || downsideBenchmarkReturn === 0
        ? null
        : (downsideFundReturn / downsideBenchmarkReturn) * 100,
  }
}

function getDateDifferenceInDays(startDate: string, endDate: string) {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return null
  }
  return Math.max(Math.round((end.getTime() - start.getTime()) / 86_400_000), 0)
}

function getMaxDrawdownStatsFromPoints(points: FundChartPoint[]) {
  if (points.length < 2) {
    return {
      maxDrawdown: null,
      recoveryDays: null,
      recoveryOpen: false,
    }
  }

  let runningPeakValue = points[0].value
  let runningPeakIndex = 0
  let worstDrawdown = 0
  let worstPeakIndex = 0
  let worstTroughIndex: number | null = null

  for (let index = 1; index < points.length; index += 1) {
    const point = points[index]
    if (point.value > runningPeakValue) {
      runningPeakValue = point.value
      runningPeakIndex = index
    }
    const drawdown = runningPeakValue > 0 ? ((point.value / runningPeakValue) - 1) * 100 : 0
    if (drawdown < worstDrawdown) {
      worstDrawdown = drawdown
      worstPeakIndex = runningPeakIndex
      worstTroughIndex = index
    }
  }

  if (worstTroughIndex == null) {
    return {
      maxDrawdown: 0,
      recoveryDays: 0,
      recoveryOpen: false,
    }
  }

  const recoveryTargetValue = points[worstPeakIndex]?.value ?? null
  let recoveryDays: number | null = null
  let recoveryOpen = true

  if (recoveryTargetValue != null) {
    for (let index = worstTroughIndex + 1; index < points.length; index += 1) {
      if (points[index].value >= recoveryTargetValue) {
        recoveryDays = getDateDifferenceInDays(points[worstTroughIndex].date, points[index].date)
        recoveryOpen = false
        break
      }
    }
  }

  return {
    maxDrawdown: worstDrawdown,
    recoveryDays,
    recoveryOpen,
  }
}

function buildPerformanceMetricSnapshot(points: FundChartPoint[]): PerformanceMetricSnapshot {
  if (points.length < 2) {
    return {
      periodReturn: null,
      annualizedReturn: null,
      annualizedVolatility: null,
      annualizedDownsideDeviation: null,
      sharpe: null,
      sortino: null,
      calmar: null,
      maxDrawdown: null,
      recoveryDays: null,
      recoveryOpen: false,
    }
  }

  const periodReturn = getPeriodReturnFromPoints(points)
  const annualizedReturn = getAnnualizedReturnFromPoints(points)
  const annualizedVolatility = getAnnualizedVolatilityFromPoints(points)
  const annualizedDownsideDeviation = getAnnualizedDownsideDeviationFromPoints(points)
  const sharpe = getSharpeRatioFromPoints(points)
  const sortino = getSortinoRatioFromPoints(points)
  const drawdownStats = getMaxDrawdownStatsFromPoints(points)
  const calmar =
    annualizedReturn != null &&
    drawdownStats.maxDrawdown != null &&
    drawdownStats.maxDrawdown !== 0
      ? annualizedReturn / Math.abs(drawdownStats.maxDrawdown)
      : null

  return {
    periodReturn,
    annualizedReturn,
    annualizedVolatility,
    annualizedDownsideDeviation,
    sharpe,
    sortino,
    calmar,
    maxDrawdown: drawdownStats.maxDrawdown,
    recoveryDays: drawdownStats.recoveryDays,
    recoveryOpen: drawdownStats.recoveryOpen,
  }
}

function getHeatmapCellStyle(value: number | null, maxAbsValue: number) {
  if (value == null) {
    return undefined
  }
  const normalized = Math.min(Math.abs(value) / Math.max(maxAbsValue, 1), 1)
  if (value >= 0) {
    return {
      backgroundColor: `rgba(13, 122, 56, ${0.08 + normalized * 0.26})`,
      color: '#0d5e31',
    }
  }
  return {
    backgroundColor: `rgba(175, 0, 0, ${0.08 + normalized * 0.24})`,
    color: '#8f1d1d',
  }
}

function getWeekBucket(value: string) {
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const day = date.getDay()
  const diff = day === 0 ? -6 : 1 - day
  date.setDate(date.getDate() + diff)
  return date.toISOString().slice(0, 10)
}

function resampleSeries(points: FundChartPoint[], frequency: ChartFrequency) {
  if (frequency === 'daily') {
    return points
  }

  const buckets = new Map<string, FundChartPoint>()
  points.forEach((point) => {
    const bucket =
      frequency === 'weekly'
        ? getWeekBucket(point.date)
        : point.date.slice(0, 7)
    buckets.set(bucket, point)
  })

  return Array.from(buckets.values()).sort((left, right) => left.date.localeCompare(right.date))
}

function getChartTickValues(points: FundChartPoint[], count = 5) {
  if (!points.length) {
    return []
  }
  const values = points.map((point) => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min
  if (range === 0) {
    return [min]
  }
  return Array.from({ length: count }, (_, index) => min + (range / (count - 1)) * index)
}

function getLogTickValues(points: FundChartPoint[], count = 5) {
  if (!points.length || points.some((point) => point.value <= 0)) {
    return getChartTickValues(points, count)
  }
  const values = points.map((point) => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const logMin = Math.log10(min)
  const logMax = Math.log10(max)
  if (!Number.isFinite(logMin) || !Number.isFinite(logMax) || logMin === logMax) {
    return [min]
  }
  return Array.from(
    { length: count },
    (_, index) => 10 ** (logMin + ((logMax - logMin) / Math.max(count - 1, 1)) * index),
  )
}

function getLogTickValuesFromBounds(logMin: number, logMax: number, count = 5) {
  if (!Number.isFinite(logMin) || !Number.isFinite(logMax)) {
    return []
  }
  if (logMin === logMax) {
    return [10 ** logMin]
  }
  return Array.from(
    { length: count },
    (_, index) => 10 ** (logMin + ((logMax - logMin) / Math.max(count - 1, 1)) * index),
  )
}

function getLinearTickValues(min: number, max: number, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return []
  }
  if (min === max) {
    return [min]
  }
  return Array.from({ length: count }, (_, index) => min + ((max - min) / Math.max(count - 1, 1)) * index)
}

function formatAxisNumber(value: number) {
  const digits = Math.abs(value) >= 100 ? 2 : 4
  return value.toFixed(digits)
}

function formatChartAxisDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleDateString('en-US', {
    month: 'short',
    year: '2-digit',
  })
}

function getChartTickDates(points: FundChartPoint[], count = 5) {
  if (!points.length) {
    return []
  }
  const step = Math.max(1, Math.floor((points.length - 1) / Math.max(count - 1, 1)))
  const ticks = Array.from({ length: count }, (_, index) => {
    const candidate = points[Math.min(points.length - 1, index * step)]
    return candidate
  })
  const last = points[points.length - 1]
  if (ticks[ticks.length - 1]?.date !== last.date) {
    ticks[ticks.length - 1] = last
  }
  return ticks.filter((point, index, array) => array.findIndex((item) => item.date === point.date) === index)
}

function buildChartBands(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  segments = 10,
) {
  if (points.length < 2) {
    return []
  }
  const plottingWidth = geometry.width - geometry.paddingLeft - geometry.paddingRight
  const bandWidth = plottingWidth / segments
  return Array.from({ length: segments }, (_, index) => {
    if (index % 2 === 0) {
      return null
    }
    return {
      x: geometry.paddingLeft + bandWidth * index,
      width: bandWidth,
    }
  }).filter((band): band is { x: number; width: number } => band !== null)
}

function projectChartPoint(
  point: FundChartPoint,
  points: FundChartPoint[],
  min: number,
  max: number,
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
) {
  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const range = max - min || 1
  const index = points.findIndex((item) => item.date === point.date)
  const x =
    paddingLeft +
    (Math.max(index, 0) / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
  const y = projectChartValue(point.value, min, max, geometry)
  return { x, y }
}

function projectChartValue(
  value: number,
  min: number,
  max: number,
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
) {
  const { height, paddingTop, paddingBottom } = geometry
  const range = max - min || 1
  const y =
    height -
    paddingBottom -
    ((value - min) / range) * (height - paddingTop - paddingBottom)
  return y
}

function buildPositionedPoints(
  points: FundChartPoint[],
  min: number,
  max: number,
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
) {
  return points.map((point) => ({
    ...point,
    ...projectChartPoint(point, points, min, max, geometry),
  }))
}

function findNearestChartPoint(points: FundChartPoint[], targetDate: string | undefined) {
  if (!points.length || !targetDate) {
    return null
  }
  const targetTime = new Date(`${targetDate}T00:00:00`).getTime()
  if (Number.isNaN(targetTime)) {
    return points[points.length - 1] ?? null
  }
  return points.reduce<FundChartPoint | null>((closest, point) => {
    if (!closest) {
      return point
    }
    const pointDistance = Math.abs(new Date(`${point.date}T00:00:00`).getTime() - targetTime)
    const closestDistance = Math.abs(new Date(`${closest.date}T00:00:00`).getTime() - targetTime)
    return pointDistance < closestDistance ? point : closest
  }, null)
}

function renderStackRows(items: Record<string, unknown>) {
  return Object.entries(items).map(([key, value]) => (
    <div key={key} className="stack-item">
      <span>{formatLabel(key)}</span>
      <strong>{getDisplayValue(value)}</strong>
    </div>
  ))
}

function getFrameworkValueList(values: Record<string, unknown>, key: string) {
  const value = values[key]
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean)
  }
  if (value == null) {
    return []
  }
  const text = String(value).trim()
  return text ? [text] : []
}

function formatFrameworkValue(value: unknown) {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item).trim()).filter(Boolean)
    return items.length ? items.join(', ') : '—'
  }
  if (value == null) {
    return '—'
  }
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  const text = String(value).trim()
  return text || '—'
}

function isFrameworkOptionSelected(
  attributeValues: InstrumentAttributeValuesResponse | null,
  definition: InstrumentAttributeDefinition,
  option: string,
) {
  if (!attributeValues) {
    return false
  }
  const values = getFrameworkValueList(attributeValues.values, definition.attribute_key)
  return values.includes(option)
}

function buildNextFrameworkValue(
  attributeValues: InstrumentAttributeValuesResponse | null,
  definition: InstrumentAttributeDefinition,
  option: string,
) {
  const currentValues = attributeValues
    ? getFrameworkValueList(attributeValues.values, definition.attribute_key)
    : []
  if (definition.data_type === 'multi_select') {
    return currentValues.includes(option)
      ? currentValues.filter((value) => value !== option)
      : [...currentValues, option]
  }
  return option
}

type AttributeFrameworkDomain = Extract<
  InstrumentAttributeDefinition['domain_code'],
  'research' | 'monitoring'
>

const ATTRIBUTE_DOMAIN_ORDER: AttributeFrameworkDomain[] = [
  'research',
  'monitoring',
]

const ATTRIBUTE_DOMAIN_META: Record<
  AttributeFrameworkDomain,
  {
    title: string
    note: string
    emptyState: string
  }
> = {
  research: {
    title: 'Research Tags',
    note: '',
    emptyState: 'Complete fund taxonomy first to unlock category-specific research tags.',
  },
  monitoring: {
    title: 'Monitoring Assessment',
    note: '',
    emptyState: 'Complete fund taxonomy first to unlock category-specific monitoring labels.',
  },
}

const ATTRIBUTE_GROUP_LABELS: Record<string, string> = {
  overview_identity: 'Identity',
  research_coverage: 'Coverage',
  research_process: 'Process & Construction',
  research_style: 'Style Tags',
  research_manager: 'Manager Assessment',
  monitoring_risk: 'Risk Profile',
  monitoring_regime: 'Regime Fit',
  monitoring_operational: 'Operational Coverage',
  custom: 'Custom',
}

function hasAttributeValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.some((item) => String(item ?? '').trim())
  }
  if (typeof value === 'boolean') {
    return true
  }
  if (value == null) {
    return false
  }
  return String(value).trim().length > 0
}

function definitionHasAssignedValue(
  values: Record<string, unknown>,
  definition: InstrumentAttributeDefinition,
) {
  return hasAttributeValue(values[definition.attribute_key])
}

function definitionMatchesApplicability(
  definition: InstrumentAttributeDefinition,
  values: Record<string, unknown>,
) {
  const applicabilityEntries = Object.entries(definition.applicability_json || {})
  if (!applicabilityEntries.length) {
    return true
  }
  return applicabilityEntries.every(([attributeKey, expectedValues]) => {
    if (!expectedValues.length) {
      return true
    }
    const currentValues = getFrameworkValueList(values, attributeKey)
    if (!currentValues.length) {
      return false
    }
    return expectedValues.some((candidate) => currentValues.includes(candidate))
  })
}

function isTaxonomyComplete(attributeValues: InstrumentAttributeValuesResponse | null) {
  return Boolean(
    hasAttributeValue(attributeValues?.taxonomy?.derived_values?.fund_regime) &&
      hasAttributeValue(attributeValues?.taxonomy?.derived_values?.fund_taxonomy_leaf),
  )
}

function buildAttributeFrameworkSections(
  attributeValues: InstrumentAttributeValuesResponse | null,
) {
  if (!attributeValues) {
    return []
  }

  const values = {
    ...(attributeValues.taxonomy?.derived_values || {}),
    ...(attributeValues.values || {}),
  }
  const classificationReady = isTaxonomyComplete(attributeValues)

  return ATTRIBUTE_DOMAIN_ORDER.map((domain) => {
    const definitions = [...attributeValues.definitions]
      .filter((definition) => definition.domain_code === domain)
      .filter(
        (definition) =>
          definitionMatchesApplicability(definition, values) ||
          definitionHasAssignedValue(values, definition),
      )
      .sort((left, right) => left.display_order - right.display_order || left.label.localeCompare(right.label))

    const groups = definitions.reduce<
      Array<{ groupCode: string; label: string; definitions: InstrumentAttributeDefinition[] }>
    >((items, definition) => {
      const groupCode = definition.group_code || 'custom'
      const current = items.find((item) => item.groupCode === groupCode)
      if (current) {
        current.definitions.push(definition)
        return items
      }
      return [
        ...items,
        {
          groupCode,
          label: ATTRIBUTE_GROUP_LABELS[groupCode] || formatLabel(groupCode),
          definitions: [definition],
        },
      ]
    }, [])

    const emptyState =
      classificationReady
        ? ATTRIBUTE_DOMAIN_META[domain].emptyState
        : domain === 'research'
          ? 'Complete fund taxonomy first to unlock category-specific research tags.'
          : 'Complete fund taxonomy first to unlock category-specific monitoring labels.'

    return {
      domain,
      title: ATTRIBUTE_DOMAIN_META[domain].title,
      note: ATTRIBUTE_DOMAIN_META[domain].note,
      emptyState,
      groups,
    }
  })
}

function EmptyPanel({ title, note }: { title: string; note: string }) {
  return (
    <section className="panel">
      <div className="instrument-section-header">
        <div>
          <div className="panel-title">{title}</div>
          <div className="instrument-section-title">{title}</div>
        </div>
      </div>
      <div className="instrument-placeholder">{note}</div>
    </section>
  )
}

type FundDetailPageProps = {
  fundId?: string
}

export default function FundDetailPage({ fundId: propFundId }: FundDetailPageProps = {}) {
  const { fundId: routeFundId = 'fax' } = useParams()
  const fundId = propFundId || routeFundId
  const databaseDashboardUrl = `${PLATFORM_HOME_URL}/database-dashboard`
  const [bundle, setBundle] = useState<FundDetailBundle | null>(null)
  const [activeTab, setActiveTab] = useState<DetailTab>('overview')
  const [chartRange, setChartRange] = useState<ChartRange>('3Y')
  const [quoteBasis, setQuoteBasis] = useState<QuoteBasis>('nav_with_dividend')
  const [chartFrequency, setChartFrequency] = useState<ChartFrequency>('daily')
  const [selectedCurrency, setSelectedCurrency] = useState('USD')
  const [benchmarkFundId, setBenchmarkFundId] = useState('')
  const [benchmarkNavSeries, setBenchmarkNavSeries] = useState<FundNavSeriesResponse | null>(null)
  const [metricBenchmarkFundId, setMetricBenchmarkFundId] = useState('')
  const [metricBenchmarkNavSeries, setMetricBenchmarkNavSeries] =
    useState<FundNavSeriesResponse | null>(null)
  const [performanceMatrixMode, setPerformanceMatrixMode] =
    useState<PerformanceMatrixMode>('values')
  const [rollingReturnWindowMonths, setRollingReturnWindowMonths] =
    useState<RollingReturnWindowMonths>(12)
  const [quoteActionNotice, setQuoteActionNotice] = useState<string | null>(null)
  const [chartStartDate, setChartStartDate] = useState('')
  const [chartEndDate, setChartEndDate] = useState('')
  const [chartHoverIndex, setChartHoverIndex] = useState<number | null>(null)
  const [chartHoverPanel, setChartHoverPanel] = useState<ChartHoverPanel | null>(null)
  const [chartHoverCursor, setChartHoverCursor] = useState<ChartHoverCursor | null>(null)
  const [chartDisplayStyle, setChartDisplayStyle] = useState<ChartDisplayStyle>('mountain')
  const [chartScale, setChartScale] = useState<ChartScale>('linear')
  const [showDividendEvents, setShowDividendEvents] = useState(true)
  const [showTimelineNoteEvents, setShowTimelineNoteEvents] = useState(true)
  const [showDrawdownPanel, setShowDrawdownPanel] = useState(true)
  const [openQuoteChartMenu, setOpenQuoteChartMenu] = useState<QuoteChartMenu | null>(null)
  const [timelineNoteDraft, setTimelineNoteDraft] = useState<TimelineNoteDraft | null>(null)
  const [timelineNoteCaptureMode, setTimelineNoteCaptureMode] = useState(false)
  const [timelineNoteViewAnchorDate, setTimelineNoteViewAnchorDate] = useState<string | null>(null)
  const [chartTimelineNoteContextMenu, setChartTimelineNoteContextMenu] =
    useState<ChartTimelineNoteContextMenu | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)
  const [editingPeople, setEditingPeople] = useState(false)
  const [editingStrategy, setEditingStrategy] = useState(false)
  const [editingPriceSection, setEditingPriceSection] = useState<PriceEditSection | null>(null)
  const [editingDocuments, setEditingDocuments] = useState(false)
  const [editingResearch, setEditingResearch] = useState(false)
  const [peopleDraft, setPeopleDraft] = useState<PeopleDraft | null>(null)
  const [strategyDraft, setStrategyDraft] = useState<StrategyDraft | null>(null)
  const [priceDraft, setPriceDraft] = useState<PriceDraft | null>(null)
  const [documentsDraft, setDocumentsDraft] = useState<DocumentsDraft | null>(null)
  const [researchDraft, setResearchDraft] = useState<ResearchDraft | null>(null)
  const [savingSection, setSavingSection] = useState<string | null>(null)
  const [sectionNotice, setSectionNotice] = useState<string | null>(null)
  const [sectionError, setSectionError] = useState<string | null>(null)
  const [settingsModalOpen, setSettingsModalOpen] = useState(false)
  const [taxonomyTree, setTaxonomyTree] = useState<FundTaxonomyTreeResponse | null>(null)
  const [taxonomyDraftNodeId, setTaxonomyDraftNodeId] = useState('')
  const quoteChartMenuRef = useRef<HTMLDivElement | null>(null)
  const productFrameworkPickerRef = useRef<HTMLDivElement | null>(null)
  const timelineNoteContextMenuRef = useRef<HTMLDivElement | null>(null)
  const [productFrameworkAttributes, setProductFrameworkAttributes] =
    useState<InstrumentAttributeValuesResponse | null>(null)
  const [productFrameworkSavingKey, setProductFrameworkSavingKey] = useState<string | null>(null)
  const [openProductFrameworkPickerKey, setOpenProductFrameworkPickerKey] =
    useState<string | null>(null)

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (!quoteChartMenuRef.current?.contains(event.target as Node)) {
        setOpenQuoteChartMenu(null)
      }
      if (!productFrameworkPickerRef.current?.contains(event.target as Node)) {
        setOpenProductFrameworkPickerKey(null)
      }
      if (!timelineNoteContextMenuRef.current?.contains(event.target as Node)) {
        setChartTimelineNoteContextMenu(null)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [])

  useEffect(() => {
    let cancelled = false

    async function loadFund() {
      if (!bundle) {
        setLoading(true)
      }
      setError(null)

      try {
        const [summary, library, chart, performance, risk, portfolio, holdings, ratings, people, strategy, price, documents, research, navSeries] =
          await Promise.all([
            getInstrumentSummary(fundId),
            getInstrumentLibrary(),
            getInstrumentChart(fundId),
            getInstrumentPerformance(fundId),
            getInstrumentRisk(fundId),
            getInstrumentPortfolioSummary(fundId),
            getInstrumentPortfolioHoldings(fundId),
            getInstrumentRatings(fundId),
            getInstrumentPeople(fundId),
            getInstrumentStrategy(fundId),
            getInstrumentPrice(fundId),
            getInstrumentDocuments(fundId),
            getInstrumentResearch(fundId),
            getInstrumentNavSeries(fundId),
          ])

        if (cancelled) {
          return
        }

        const nextTabs = normalizeTabs(summary.tabs || [])

        setBundle({
          summary,
          library,
          chart,
          performance,
          risk,
          portfolio,
          holdings,
          ratings,
          people,
          strategy,
          price,
          documents,
          research,
          navSeries,
        })
        startTransition(() => {
          setActiveTab((current) => (nextTabs.includes(current) ? current : nextTabs[0] || 'overview'))
        })
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load instrument detail.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadFund()

    return () => {
      cancelled = true
    }
  }, [fundId, refreshToken])

  useEffect(() => {
    setTimelineNoteDraft(null)
    setTimelineNoteCaptureMode(false)
    setTimelineNoteViewAnchorDate(null)
    setChartTimelineNoteContextMenu(null)
  }, [fundId])

  useEffect(() => {
    let cancelled = false
    setProductFrameworkAttributes(null)
    setOpenProductFrameworkPickerKey(null)

    async function loadProductFramework() {
      try {
        const response = await getInstrumentAttributes(fundId)
        if (!cancelled) {
          setProductFrameworkAttributes(response)
        }
      } catch {
        if (!cancelled) {
          setProductFrameworkAttributes({
            asset_id: fundId,
            definitions: [],
            values: {},
            taxonomy: {
              taxonomy_code: 'fund_taxonomy',
              assigned_node_id: null,
              assigned_label: null,
              path_labels: [],
              path_node_ids: [],
              depth: 0,
              derived_values: {},
            },
          })
        }
      }
    }

    void loadProductFramework()

    return () => {
      cancelled = true
    }
  }, [fundId, refreshToken])

  useEffect(() => {
    const assignedNodeId =
      productFrameworkAttributes?.taxonomy?.assigned_node_id ||
      bundle?.summary.taxonomy?.assigned_node_id ||
      ''
    setTaxonomyDraftNodeId(assignedNodeId)
  }, [bundle?.summary.taxonomy?.assigned_node_id, productFrameworkAttributes?.taxonomy?.assigned_node_id])

  useEffect(() => {
    if (!settingsModalOpen || taxonomyTree) {
      return
    }
    let cancelled = false

    async function loadTaxonomyTree() {
      try {
        const response = await getFundTaxonomyTree()
        if (!cancelled) {
          setTaxonomyTree(response)
        }
      } catch (loadError) {
        if (!cancelled) {
          setSectionError(loadError instanceof Error ? loadError.message : 'Failed to load fund taxonomy.')
        }
      }
    }

    void loadTaxonomyTree()

    return () => {
      cancelled = true
    }
  }, [settingsModalOpen, taxonomyTree])

  useEffect(() => {
    if (!bundle) {
      return
    }
    setPeopleDraft(toEditablePeopleDraft(bundle.people))
    setStrategyDraft(toEditableStrategyDraft(bundle.strategy))
    setPriceDraft(toEditablePriceDraft(bundle.price))
    setDocumentsDraft(toEditableDocumentsDraft(bundle.documents))
    setResearchDraft(toEditableResearchDraft(bundle.research))
  }, [bundle])

  useEffect(() => {
    if (!bundle) {
      return
    }
    const currencies = Array.from(
      new Set(
        [bundle.chart.currency, ...bundle.navSeries.rows.map((row) => row.currency || '')]
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    const effectiveCurrency =
      currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0] || bundle.chart.currency || 'USD'
    const quoteContext = buildQuoteSeriesContext(bundle.navSeries.rows, bundle.navSeries.series, {
      currency: effectiveCurrency,
      requestedBasis: quoteBasis,
      preferredBasis: bundle.navSeries.nav_basis_type,
    })
    if (quoteContext.activeBasis && quoteContext.activeBasis !== quoteBasis) {
      setQuoteBasis(quoteContext.activeBasis)
    }
  }, [bundle, quoteBasis, selectedCurrency])

  useEffect(() => {
    setChartHoverIndex(null)
    setChartHoverPanel(null)
    setChartTimelineNoteContextMenu(null)
    setTimelineNoteViewAnchorDate(null)
  }, [
    chartRange,
    quoteBasis,
    chartFrequency,
    selectedCurrency,
    chartStartDate,
    chartEndDate,
    chartDisplayStyle,
    chartScale,
    showDividendEvents,
    showDrawdownPanel,
    fundId,
  ])

  useEffect(() => {
    if (!bundle) {
      return
    }
    const currencies = Array.from(
      new Set(
        [bundle.chart.currency, ...bundle.navSeries.rows.map((row) => row.currency || '')]
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    if (currencies.length && !currencies.includes(selectedCurrency)) {
      setSelectedCurrency(currencies[0])
    }
    if (!currencies.length && selectedCurrency !== 'USD') {
      setSelectedCurrency('USD')
    }
  }, [bundle, selectedCurrency])

  useEffect(() => {
    let cancelled = false

    async function loadBenchmark() {
      if (!benchmarkFundId || benchmarkFundId === fundId) {
        setBenchmarkNavSeries(null)
        return
      }

      try {
        const response = await getInstrumentNavSeries(benchmarkFundId)
        if (!cancelled) {
          setBenchmarkNavSeries(response)
        }
      } catch {
        if (!cancelled) {
          setBenchmarkNavSeries(null)
        }
      }
    }

    void loadBenchmark()

    return () => {
      cancelled = true
    }
  }, [benchmarkFundId, fundId])

  useEffect(() => {
    let cancelled = false

    async function loadMetricBenchmark() {
      if (!metricBenchmarkFundId || metricBenchmarkFundId === fundId) {
        setMetricBenchmarkNavSeries(null)
        return
      }

      try {
        const response = await getInstrumentNavSeries(metricBenchmarkFundId)
        if (!cancelled) {
          setMetricBenchmarkNavSeries(response)
        }
      } catch {
        if (!cancelled) {
          setMetricBenchmarkNavSeries(null)
        }
      }
    }

    void loadMetricBenchmark()

    return () => {
      cancelled = true
    }
  }, [metricBenchmarkFundId, fundId])

  useEffect(() => {
    setBenchmarkFundId('')
    setBenchmarkNavSeries(null)
    setMetricBenchmarkFundId('')
    setMetricBenchmarkNavSeries(null)
    setQuoteActionNotice(null)
    setOpenQuoteChartMenu(null)
  }, [fundId])

  useEffect(() => {
    if (!bundle || chartRange === 'CUSTOM') {
      return
    }

    const currencies = Array.from(
      new Set(
        [bundle.chart.currency, ...bundle.navSeries.rows.map((row) => row.currency || '')]
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    const effectiveCurrency =
      currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0] || bundle.chart.currency || 'USD'
    const quoteContext = buildQuoteSeriesContext(bundle.navSeries.rows, bundle.navSeries.series, {
      currency: effectiveCurrency,
      requestedBasis: quoteBasis,
      preferredBasis: bundle.navSeries.nav_basis_type,
    })
    const navBasisSeries = quoteContext.basisSeries
    const defaultWindow = getRangeWindow(navBasisSeries, chartRange)

    setChartStartDate(defaultWindow.start)
    setChartEndDate(defaultWindow.end)
  }, [bundle, chartRange, quoteBasis, selectedCurrency])

  async function handleSavePeople() {
    if (!peopleDraft) {
      return
    }
    setSavingSection('people')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentPeople(fundId, {
        payload: {
          overview: Object.fromEntries(
            peopleDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key, value]) => key && value),
          ),
          team: peopleDraft.teamRows
            .filter((row) => row.name.trim() || row.role.trim() || row.start_date.trim())
            .map((row) => ({
              name: row.name.trim(),
              role: row.role.trim(),
              start_date: row.start_date.trim() || null,
            })),
          notes: cleanListRows(peopleDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingPeople(false)
      setSectionNotice('People profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save people profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveStrategy() {
    if (!strategyDraft) {
      return
    }
    setSavingSection('strategy')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentStrategy(fundId, {
        payload: {
          summary: strategyDraft.summary.trim(),
          investment_objective: strategyDraft.investment_objective.trim(),
          process_bullets: cleanListRows(strategyDraft.processRows),
          risk_controls: cleanListRows(strategyDraft.riskControlRows),
          notes: cleanListRows(strategyDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingStrategy(false)
      setSectionNotice('Strategy profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save strategy profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSavePrice() {
    if (!priceDraft) {
      return
    }
    setSavingSection('price')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentPrice(fundId, {
        payload: {
          overview: Object.fromEntries(
            priceDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key]) => key),
          ),
          distribution_policy: priceDraft.distribution_policy.trim(),
          policy_text: priceDraft.policy_text.trim(),
          fee_notes: cleanListRows(priceDraft.feeNoteRows),
          notes: cleanListRows(priceDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingPriceSection(null)
      setSectionNotice('Price profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save price profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveDocuments() {
    if (!documentsDraft) {
      return
    }
    setSavingSection('documents')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentDocuments(fundId, {
        payload: {
          current_documents: documentsDraft.currentDocumentRows
            .filter((row) =>
              row.title.trim() ||
              row.document_type.trim() ||
              row.as_of_date.trim() ||
              row.source.trim() ||
              row.status.trim() ||
              row.version_label.trim(),
            )
            .map((row) => ({
              title: row.title.trim(),
              document_type: row.document_type.trim(),
              as_of_date: row.as_of_date.trim() || null,
              source: row.source.trim(),
              status: row.status.trim(),
              version_label: row.version_label.trim(),
            })),
          recent_imports: documentsDraft.importRows
            .filter((row) =>
              row.import_type.trim() ||
              row.received_at.trim() ||
              row.source.trim() ||
              row.status.trim() ||
              row.file_name.trim(),
            )
            .map((row) => ({
              import_type: row.import_type.trim(),
              received_at: row.received_at.trim() || null,
              source: row.source.trim(),
              status: row.status.trim(),
              file_name: row.file_name.trim(),
            })),
          extraction_reviews: documentsDraft.extractionRows
            .filter((row) =>
              row.document_title.trim() ||
              row.extract_type.trim() ||
              row.status.trim() ||
              row.adopted_version.trim() ||
              row.updated_at.trim(),
            )
            .map((row) => ({
              document_title: row.document_title.trim(),
              extract_type: row.extract_type.trim(),
              status: row.status.trim(),
              adopted_version: row.adopted_version.trim(),
              updated_at: row.updated_at.trim() || null,
            })),
          notes: cleanListRows(documentsDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingDocuments(false)
      setSectionNotice('Documents profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save documents profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveResearch() {
    if (!researchDraft) {
      return
    }
    setSavingSection('research')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentResearch(fundId, {
        payload: {
          overview: Object.fromEntries(
            researchDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key, value]) => key && value),
          ),
          thesis: researchDraft.thesis.trim(),
          conclusions: researchDraft.conclusionRows
            .filter((row) => row.conclusion.trim() || row.evidence_ref.trim() || row.status.trim())
            .map((row) => ({
              conclusion: row.conclusion.trim(),
              evidence_ref: row.evidence_ref.trim(),
              status: row.status.trim(),
            })),
          timeline_notes: researchDraft.timelineNotes,
          notes: cleanListRows(researchDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingResearch(false)
      setSectionNotice('Research profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save research profile.')
    } finally {
      setSavingSection(null)
    }
  }

  function buildResearchPayloadWithTimelineNotes(nextTimelineNotes: ResearchTimelineNote[]) {
    return {
      overview: bundle?.research.overview || {},
      thesis: bundle?.research.thesis || '',
      conclusions: bundle?.research.conclusions || [],
      timeline_notes: nextTimelineNotes,
      notes: bundle?.research.notes || [],
    }
  }

  function openTimelineNoteEditor(noteDate: string, note?: ResearchTimelineNote | null) {
    setTimelineNoteDraft(createTimelineNoteDraft(noteDate, note))
    setTimelineNoteViewAnchorDate(null)
    setChartTimelineNoteContextMenu(null)
    setTimelineNoteCaptureMode(false)
    setOpenQuoteChartMenu(null)
    setSectionError(null)
    setSectionNotice(null)
  }

  function focusTimelineNoteInQuote(noteDate: string) {
    setActiveTab('overview')
    setChartRange('MAX')
    setChartStartDate('')
    setChartEndDate('')
    setTimelineNoteViewAnchorDate(noteDate)
    setChartTimelineNoteContextMenu(null)
    setSectionError(null)
    setQuoteActionNotice(`Research note anchored to ${formatDate(noteDate)}.`)
  }

  async function handleSaveTimelineNote() {
    if (!bundle || !timelineNoteDraft) {
      return
    }
    const serializedNote = serializeTimelineNoteDraft(timelineNoteDraft)
    if (!serializedNote.note_date) {
      setSectionError('Timeline notes require a valid note date.')
      return
    }
    if (!serializedNote.title && !serializedNote.summary && !serializedNote.body) {
      setSectionError('Add at least a title, summary, or note body before saving.')
      return
    }

    setSavingSection('timeline_note')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const nextNotes = [...timelineNotes.filter((note) => note.note_id !== serializedNote.note_id), serializedNote]
        .sort(sortResearchTimelineNotes)
      const response = await updateInstrumentResearch(fundId, {
        payload: buildResearchPayloadWithTimelineNotes(nextNotes),
        updated_by: 'terminal_ui',
      })
      const normalizedNotes = normalizeResearchTimelineNotes(response.timeline_notes)
      setBundle((current) => (current ? { ...current, research: response } : current))
      setResearchDraft((current) =>
        current
          ? {
              ...current,
              timelineNotes: normalizedNotes,
            }
          : current,
      )
      setTimelineNoteDraft(null)
      setTimelineNoteViewAnchorDate(
        findNearestChartPoint(visibleNavSeries, serializedNote.note_date)?.date || serializedNote.note_date,
      )
      setSectionNotice(`Saved note for ${formatDate(serializedNote.note_date)}.`)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save timeline note.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleDeleteTimelineNote(noteId: string) {
    if (!bundle) {
      return
    }
    setSavingSection('timeline_note')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const nextNotes = timelineNotes.filter((note) => note.note_id !== noteId)
      const response = await updateInstrumentResearch(fundId, {
        payload: buildResearchPayloadWithTimelineNotes(nextNotes),
        updated_by: 'terminal_ui',
      })
      const normalizedNotes = normalizeResearchTimelineNotes(response.timeline_notes)
      setBundle((current) => (current ? { ...current, research: response } : current))
      setResearchDraft((current) =>
        current
          ? {
              ...current,
              timelineNotes: normalizedNotes,
            }
          : current,
      )
      setTimelineNoteDraft((current) => (current?.note_id === noteId ? null : current))
      setTimelineNoteViewAnchorDate((current) => {
        if (!current) {
          return current
        }
        const hasRemaining = normalizedNotes.some((note) => note.note_date === current)
        return hasRemaining ? current : null
      })
      setSectionNotice('Timeline note removed.')
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to delete timeline note.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveProductFrameworkValue(
    definition: InstrumentAttributeDefinition,
    value: unknown,
    options?: { closePicker?: boolean },
  ) {
    setProductFrameworkSavingKey(definition.attribute_key)
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateInstrumentAttributes(fundId, {
        values: [
          {
            attribute_key: definition.attribute_key,
            value,
          },
        ],
      })
      setProductFrameworkAttributes(response)
      setBundle((current) =>
        current
          ? {
              ...current,
              summary: {
                ...current.summary,
                instrument_attributes: {
                  ...current.summary.instrument_attributes,
                  ...response.values,
                },
              },
            }
          : current,
      )
      if (options?.closePicker !== false) {
        setOpenProductFrameworkPickerKey(null)
      }
    } catch (saveError) {
      setSectionError(
        saveError instanceof Error ? saveError.message : 'Failed to update product framework labels.',
      )
    } finally {
      setProductFrameworkSavingKey(null)
    }
  }

  async function handleSaveFundSettings() {
    setSavingSection('fund_settings')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateFundTaxonomy(fundId, {
        node_id: taxonomyDraftNodeId || null,
        updated_by: 'terminal_ui',
      })
      setProductFrameworkAttributes((current) =>
        current
          ? {
              ...current,
              taxonomy: response,
            }
          : current,
      )
      setBundle((current) =>
        current
          ? {
              ...current,
              summary: {
                ...current.summary,
                category_name: response.assigned_label || 'Unclassified',
                taxonomy: response,
              },
            }
          : current,
      )
      setSettingsModalOpen(false)
      setSectionNotice('Fund settings saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save fund settings.')
    } finally {
      setSavingSection(null)
    }
  }

  if (loading) {
    return (
      <div className="terminal-page">
        <section className="panel">
          <div className="loading-state">Loading instrument detail...</div>
        </section>
      </div>
    )
  }

  if (error || !bundle) {
    return (
      <div className="terminal-page">
        <section className="panel">
          <div className="error-state">{error || 'Instrument detail unavailable.'}</div>
        </section>
      </div>
    )
  }

  const { summary, chart, performance, risk, portfolio, holdings, ratings, people, strategy, price, documents, research, navSeries } = bundle
  const timelineNotes = normalizeResearchTimelineNotes(research.timeline_notes)
  const availableCurrencies = Array.from(
    new Set(
      [chart.currency, ...navSeries.rows.map((row) => row.currency || '')]
        .map((value) => value || '')
        .filter(Boolean),
    ),
  )
  const effectiveCurrency = availableCurrencies.includes(selectedCurrency)
    ? selectedCurrency
    : availableCurrencies[0] || chart.currency || 'USD'
  const quoteSeriesContext = buildQuoteSeriesContext(navSeries.rows, navSeries.series, {
    currency: effectiveCurrency,
    requestedBasis: quoteBasis,
    preferredBasis: navSeries.nav_basis_type,
  })
  const currencyFilteredRows = quoteSeriesContext.rows
  const availableQuoteBases = quoteSeriesContext.availableBases
  const activeQuoteBasis = quoteSeriesContext.activeBasis || resolvePreferredQuoteBasis(navSeries.nav_basis_type) || 'nav'
  const latestQuoteRow = quoteSeriesContext.latestRow || navSeries.rows[navSeries.rows.length - 1]
  const navBasisSeries = quoteSeriesContext.basisSeries
  const defaultWindow = getRangeWindow(navBasisSeries, chartRange)
  const effectiveStartDate = chartRange === 'CUSTOM' ? chartStartDate : defaultWindow.start
  const effectiveEndDate = chartRange === 'CUSTOM' ? chartEndDate : defaultWindow.end
  const zoomMaxIndex = Math.max(navBasisSeries.length - 1, 0)
  const rawZoomStartIndex = effectiveStartDate
    ? findLastPointIndexOnOrBefore(navBasisSeries, effectiveStartDate)
    : 0
  const rawZoomEndIndex = effectiveEndDate
    ? findLastPointIndexOnOrBefore(navBasisSeries, effectiveEndDate)
    : zoomMaxIndex
  const zoomStartIndex = Math.max(0, Math.min(rawZoomStartIndex < 0 ? 0 : rawZoomStartIndex, zoomMaxIndex))
  const zoomEndIndex =
    zoomMaxIndex <= 0
      ? 0
      : Math.max(
          Math.min(zoomStartIndex + 1, zoomMaxIndex),
          Math.min(rawZoomEndIndex < 0 ? zoomMaxIndex : rawZoomEndIndex, zoomMaxIndex),
        )
  const canUseZoom = navBasisSeries.length > 2
  const zoomSelectionLeftPct = zoomMaxIndex > 0 ? (zoomStartIndex / zoomMaxIndex) * 100 : 0
  const zoomSelectionRightPct =
    zoomMaxIndex > 0 ? ((zoomMaxIndex - zoomEndIndex) / zoomMaxIndex) * 100 : 0
  const visibleNavSeries = resampleSeries(
    filterSeriesByDateWindow(navBasisSeries, effectiveStartDate, effectiveEndDate),
    chartFrequency,
  )
  const benchmarkOptions = bundle.library.filter((item) => item.fund_id !== fundId)
  const selectedBenchmark = benchmarkOptions.find((item) => item.fund_id === benchmarkFundId) || null
  const selectedMetricBenchmark =
    benchmarkOptions.find((item) => item.fund_id === metricBenchmarkFundId) || null
  const benchmarkRowsByCurrency =
    getRowsForCurrency(benchmarkNavSeries?.rows || [], effectiveCurrency)
  const benchmarkSourceRows = benchmarkRowsByCurrency.length > 0 ? benchmarkRowsByCurrency : benchmarkNavSeries?.rows || []
  const benchmarkAvailableBases = getAvailableQuoteBases(benchmarkSourceRows)
  const activeBenchmarkBasis = benchmarkAvailableBases.includes(activeQuoteBasis)
    ? activeQuoteBasis
    : benchmarkAvailableBases[0] || null
  const benchmarkNavBasisSeries = activeBenchmarkBasis ? buildBasisSeries(benchmarkSourceRows, activeBenchmarkBasis) : []
  const benchmarkVisibleNavSeries = resampleSeries(
    filterSeriesByDateWindow(benchmarkNavBasisSeries, effectiveStartDate, effectiveEndDate),
    chartFrequency,
  )
  const metricBenchmarkRowsByCurrency =
    getRowsForCurrency(metricBenchmarkNavSeries?.rows || [], effectiveCurrency)
  const metricBenchmarkSourceRows =
    metricBenchmarkRowsByCurrency.length > 0
      ? metricBenchmarkRowsByCurrency
      : metricBenchmarkNavSeries?.rows || []
  const metricBenchmarkAvailableBases = getAvailableQuoteBases(metricBenchmarkSourceRows)
  const activeMetricBenchmarkBasis = metricBenchmarkAvailableBases.includes(activeQuoteBasis)
    ? activeQuoteBasis
    : metricBenchmarkAvailableBases[0] || null
  const metricBenchmarkNavBasisSeries =
    activeMetricBenchmarkBasis
      ? buildBasisSeries(metricBenchmarkSourceRows, activeMetricBenchmarkBasis)
      : []
  const shouldIndexCompareSeries = Boolean(selectedBenchmark)
  const indexedNavSeries = shouldIndexCompareSeries ? rebaseSeries(visibleNavSeries, 1) : []
  const indexedBenchmarkSeries = shouldIndexCompareSeries ? rebaseSeries(benchmarkVisibleNavSeries, 1) : []
  const chartNavSeries =
    shouldIndexCompareSeries && indexedNavSeries.length ? indexedNavSeries : visibleNavSeries
  const chartBenchmarkSeries = shouldIndexCompareSeries
    ? indexedBenchmarkSeries
    : benchmarkVisibleNavSeries
  const drawdownSeries = buildDrawdownSeries(visibleNavSeries)
  const benchmarkDrawdownSeries = buildDrawdownSeries(benchmarkVisibleNavSeries)
  const latestPoint = visibleNavSeries.length ? visibleNavSeries[visibleNavSeries.length - 1] : undefined
  const periodLow =
    visibleNavSeries.length > 0 ? Math.min(...visibleNavSeries.map((point) => point.value)) : null
  const periodHigh =
    visibleNavSeries.length > 0 ? Math.max(...visibleNavSeries.map((point) => point.value)) : null
  const maxDrawdown =
    drawdownSeries.length > 0 ? Math.min(...drawdownSeries.map((point) => point.value)) : null
  const chartQuotePeriodStats = getSeriesChangeStats(chartNavSeries)
  const chartBenchmarkPeriodStats = getSeriesChangeStats(chartBenchmarkSeries)
  const latestSeriesPoint = navBasisSeries[navBasisSeries.length - 1]
  const quoteLatestStats = getLatestPointChangeStats(navBasisSeries)
  const quoteChange = quoteLatestStats.change
  const quoteChangePct = quoteLatestStats.changePct
  const availableTabs = normalizeTabs(summary.tabs || [])
  const navBasisType = navSeries.nav_basis_type || summary.nav_snapshot?.nav_basis_type || 'auto'
  const navBasisLabel = NAV_BASIS_LABELS[navBasisType] || toTitleCase(navBasisType)
  const quoteBasisLabel = QUOTE_BASIS_LABELS[activeQuoteBasis]
  const chartSeriesBasisLabel = shouldIndexCompareSeries ? 'Indexed to 1.00' : quoteBasisLabel
  const quoteBasisOptions = availableQuoteBases.length
    ? availableQuoteBases
    : [activeQuoteBasis]
  const basisValue =
    activeQuoteBasis === 'nav_with_dividend'
      ? latestQuoteRow?.nav_with_dividend ??
        latestSeriesPoint?.value ??
        summary.nav_snapshot?.latest_nav_with_dividend ??
        latestQuoteRow?.nav
      : latestQuoteRow?.nav ??
        latestSeriesPoint?.value ??
        summary.nav_snapshot?.latest_nav ??
        latestQuoteRow?.nav_with_dividend
  const quoteToneClass =
    quoteChange == null
      ? ''
      : quoteChange > 0
        ? 'instrument-quote-change instrument-quote-change-positive'
        : quoteChange < 0
        ? 'instrument-quote-change instrument-quote-change-negative'
        : 'instrument-quote-change instrument-quote-change-neutral'
  const managementStats = people.overview || {}
  const peoplePrimaryOverviewFacts = PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => ({
    ...field,
    value: managementStats[field.key],
  }))
  const peoplePrimaryOverviewRows = peoplePrimaryOverviewFacts.map((field) => ({
    key: field.key,
    label: field.label,
    value: formatResearchOverviewValue(field.key, field.value),
  }))
  const peopleAdditionalOverviewEntries = Object.entries(managementStats).filter(
    ([key]) => !PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS.has(key),
  )
  const peopleAdditionalOverviewRows = peopleAdditionalOverviewEntries.map(([key, value]) => ({
    key,
    label: formatLabel(key),
    value: getDisplayValue(value),
  }))
  const peopleAdditionalDraftRows =
    peopleDraft?.overviewRows.filter((row) => !PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS.has(row.key.trim())) ?? []
  const combinedVisibleSeries = [...chartNavSeries, ...chartBenchmarkSeries]
  const canUseLogarithmicScale =
    combinedVisibleSeries.length > 0 && combinedVisibleSeries.every((point) => point.value > 0)
  const effectiveChartScale =
    chartScale === 'logarithmic' && canUseLogarithmicScale
      ? chartScale
      : 'linear'
  const scaledVisibleSeries = applyChartScale(chartNavSeries, effectiveChartScale)
  const scaledBenchmarkVisibleSeries = applyChartScale(chartBenchmarkSeries, effectiveChartScale)
  const combinedScaledSeries = [...scaledVisibleSeries, ...scaledBenchmarkVisibleSeries]
  const chartTickDates = getChartTickDates(chartNavSeries, 6)
  const chartBands = buildChartBands(chartNavSeries, PRIMARY_CHART_GEOMETRY, 10)
  const rawChartMin = combinedScaledSeries.length ? Math.min(...combinedScaledSeries.map((point) => point.value)) : 0
  const rawChartMax = combinedScaledSeries.length ? Math.max(...combinedScaledSeries.map((point) => point.value)) : 1
  const chartRenderBounds = getPaddedAxisBounds(rawChartMin, rawChartMax, 0.045, 0.01)
  const chartMin = chartRenderBounds.min
  const chartMax = chartRenderBounds.max
  const chartLinePath = buildChartLinePath(scaledVisibleSeries, PRIMARY_CHART_GEOMETRY, chartMin, chartMax)
  const benchmarkChartLinePath = buildChartLinePath(
    scaledBenchmarkVisibleSeries,
    PRIMARY_CHART_GEOMETRY,
    chartMin,
    chartMax,
  )
  const chartAreaPath =
    chartDisplayStyle === 'mountain'
      ? buildChartAreaPath(scaledVisibleSeries, PRIMARY_CHART_GEOMETRY, chartMin, chartMax)
      : ''
  const chartTickValues =
    effectiveChartScale === 'logarithmic'
      ? getLogTickValuesFromBounds(chartMin, chartMax, 5)
      : getLinearTickValues(chartMin, chartMax, 5)
  const combinedDrawdownSeries = [...drawdownSeries, ...benchmarkDrawdownSeries]
  const drawdownBounds = getDrawdownAxisBounds(combinedDrawdownSeries)
  const drawdownBands = buildChartBands(drawdownSeries, DRAWDOWN_CHART_GEOMETRY, 10)
  const drawdownMin = drawdownBounds.min
  const drawdownMax = drawdownBounds.max
  const drawdownTickValues =
    drawdownMin === drawdownMax
      ? [drawdownMin]
      : [drawdownMin, drawdownMax]
  const drawdownLinePath = buildChartLinePath(drawdownSeries, DRAWDOWN_CHART_GEOMETRY, drawdownMin, drawdownMax)
  const benchmarkDrawdownLinePath = buildChartLinePath(
    benchmarkDrawdownSeries,
    DRAWDOWN_CHART_GEOMETRY,
    drawdownMin,
    drawdownMax,
  )
  const drawdownAreaPath = buildChartAreaPath(
    drawdownSeries,
    DRAWDOWN_CHART_GEOMETRY,
    drawdownMin,
    drawdownMax,
    0,
  )
  const positionedChartPoints = buildPositionedPoints(
    scaledVisibleSeries,
    chartMin,
    chartMax,
    PRIMARY_CHART_GEOMETRY,
  )
  const positionedBenchmarkPoints = buildPositionedPoints(
    scaledBenchmarkVisibleSeries,
    chartMin,
    chartMax,
    PRIMARY_CHART_GEOMETRY,
  )
  const positionedDrawdownPoints = buildPositionedPoints(
    drawdownSeries,
    drawdownMin,
    drawdownMax,
    DRAWDOWN_CHART_GEOMETRY,
  )
  const positionedBenchmarkDrawdownPoints = buildPositionedPoints(
    benchmarkDrawdownSeries,
    drawdownMin,
    drawdownMax,
    DRAWDOWN_CHART_GEOMETRY,
  )
  const activeHoverIndex =
    chartHoverIndex == null || !scaledVisibleSeries.length
      ? null
      : Math.min(Math.max(chartHoverIndex, 0), scaledVisibleSeries.length - 1)
  const displayIndex =
    !scaledVisibleSeries.length ? null : activeHoverIndex ?? scaledVisibleSeries.length - 1
  const displayedDrawdownPoint = displayIndex == null ? undefined : positionedDrawdownPoints[displayIndex]
  const displayedNavPoint = displayIndex == null ? undefined : chartNavSeries[displayIndex]
  const primaryHoverGuideX = chartHoverCursor ? getPlotXFromRatio(PRIMARY_CHART_GEOMETRY, chartHoverCursor.xRatio) : null
  const drawdownHoverGuideX = chartHoverCursor ? getPlotXFromRatio(DRAWDOWN_CHART_GEOMETRY, chartHoverCursor.xRatio) : null
  const activeChartGuidePoint = activeHoverIndex == null ? null : positionedChartPoints[activeHoverIndex]
  const activeDrawdownGuidePoint = activeHoverIndex == null ? null : positionedDrawdownPoints[activeHoverIndex]
  const isPrimaryHoverActive = chartHoverPanel === 'primary' && chartHoverCursor != null && activeChartGuidePoint != null
  const isDrawdownHoverActive = chartHoverPanel === 'drawdown' && chartHoverCursor != null && activeDrawdownGuidePoint != null
  const hoveredChartPoint = isPrimaryHoverActive ? activeChartGuidePoint : null
  const hoveredDrawdownPoint = isDrawdownHoverActive ? activeDrawdownGuidePoint : null
  const hoveredNavPoint = activeHoverIndex == null ? null : chartNavSeries[activeHoverIndex]
  const displayedBenchmarkBasePoint = findNearestChartPoint(
    chartBenchmarkSeries,
    displayedNavPoint?.date || chartNavSeries[chartNavSeries.length - 1]?.date,
  )
  const displayedBenchmarkDrawdownBasePoint = findNearestChartPoint(
    benchmarkDrawdownSeries,
    displayedNavPoint?.date || chartNavSeries[chartNavSeries.length - 1]?.date,
  )
  const hoveredBenchmarkBasePoint = activeHoverIndex == null ? null : findNearestChartPoint(
    chartBenchmarkSeries,
    hoveredNavPoint?.date,
  )
  const hoveredBenchmarkDrawdownBasePoint = activeHoverIndex == null ? null : findNearestChartPoint(
    benchmarkDrawdownSeries,
    hoveredNavPoint?.date,
  )
  const hoveredBenchmarkPoint =
    hoveredBenchmarkBasePoint &&
    positionedBenchmarkPoints.find((point) => point.date === hoveredBenchmarkBasePoint.date)
  const hoveredBenchmarkDrawdownPoint =
    hoveredBenchmarkDrawdownBasePoint &&
    positionedBenchmarkDrawdownPoints.find((point) => point.date === hoveredBenchmarkDrawdownBasePoint.date)
  const distributionRows = [...currencyFilteredRows]
    .filter((row) => row.distribution_amount != null && row.distribution_amount !== 0)
    .filter((row) => (!effectiveStartDate || row.as_of_date >= effectiveStartDate) && (!effectiveEndDate || row.as_of_date <= effectiveEndDate))
    .sort((left, right) => right.as_of_date.localeCompare(left.as_of_date))
  const latestDistribution = distributionRows[0]
  const hoveredDistribution = activeHoverIndex == null ? null : distributionRows.find((row) => row.as_of_date === hoveredNavPoint?.date)
  const distributionMarkers = !showDividendEvents
    ? []
    : distributionRows
    .map((row) => {
      const point = positionedChartPoints.find((item) => item.date === row.as_of_date)
      if (!point) {
        return null
      }
      return {
        row,
        x: point.x,
      }
    })
    .filter((marker): marker is { row: FundNavSeriesResponse['rows'][number]; x: number } => marker !== null)
  const visibleTimelineNotes = !showTimelineNoteEvents
    ? []
    : timelineNotes.filter((note) => {
      const windowStart = visibleNavSeries[0]?.date
      const windowEnd = visibleNavSeries[visibleNavSeries.length - 1]?.date
      if (!windowStart || !windowEnd) {
        return false
      }
      return note.note_date >= windowStart && note.note_date <= windowEnd
    })
  const timelineNoteMarkerGroups = !showTimelineNoteEvents
    ? []
    : Array.from(
      visibleTimelineNotes.reduce(
        (
          groups,
          note,
        ) => {
          const anchorPoint = findNearestChartPoint(visibleNavSeries, note.note_date)
          if (!anchorPoint) {
            return groups
          }
          const positionedPoint = positionedChartPoints.find((point) => point.date === anchorPoint.date)
          if (!positionedPoint) {
            return groups
          }
          const existing = groups.get(anchorPoint.date)
          if (existing) {
            existing.notes.push(note)
            return groups
          }
          groups.set(anchorPoint.date, {
            anchorDate: anchorPoint.date,
            x: positionedPoint.x,
            notes: [note],
          })
          return groups
        },
        new Map<string, { anchorDate: string; x: number; notes: ResearchTimelineNote[] }>(),
      ).values(),
    ).sort((left, right) => left.anchorDate.localeCompare(right.anchorDate))
  const hoveredTimelineNoteGroup =
    hoveredNavPoint == null
      ? null
      : timelineNoteMarkerGroups.find((group) => group.anchorDate === hoveredNavPoint.date) || null
  const selectedTimelineNoteGroup =
    timelineNoteViewAnchorDate == null
      ? null
      : timelineNoteMarkerGroups.find((group) => group.anchorDate === timelineNoteViewAnchorDate) || null
  const timelineNoteContextMenuStyle = chartTimelineNoteContextMenu
    ? {
        left:
          typeof window === 'undefined'
            ? chartTimelineNoteContextMenu.clientX
            : Math.min(chartTimelineNoteContextMenu.clientX, window.innerWidth - 220),
        top:
          typeof window === 'undefined'
            ? chartTimelineNoteContextMenu.clientY
            : Math.min(chartTimelineNoteContextMenu.clientY, window.innerHeight - 160),
      }
    : undefined
  const hoverNavValue = displayedNavPoint?.value ?? null
  const hoverBenchmarkValue = displayedBenchmarkBasePoint?.value ?? null
  const hoverDrawdownValue = displayedDrawdownPoint?.value ?? null
  const hoverBenchmarkDrawdownValue = displayedBenchmarkDrawdownBasePoint?.value ?? null
  const latestChartPoint = positionedChartPoints[positionedChartPoints.length - 1] ?? null
  const latestBenchmarkChartPoint = positionedBenchmarkPoints[positionedBenchmarkPoints.length - 1] ?? null
  const latestDrawdownPoint = positionedDrawdownPoints[positionedDrawdownPoints.length - 1] ?? null
  const latestBenchmarkDrawdownPoint =
    positionedBenchmarkDrawdownPoints[positionedBenchmarkDrawdownPoints.length - 1] ?? null
  const latestChartValueLabel =
    chartNavSeries.length > 0 ? formatNumber(chartNavSeries[chartNavSeries.length - 1].value, 4) : null
  const latestBenchmarkChartValueLabel =
    chartBenchmarkSeries.length > 0
      ? formatNumber(chartBenchmarkSeries[chartBenchmarkSeries.length - 1].value, 4)
      : null
  const latestDrawdownValueLabel =
    drawdownSeries.length > 0 ? formatPercent(drawdownSeries[drawdownSeries.length - 1].value) : null
  const latestBenchmarkDrawdownValueLabel =
    benchmarkDrawdownSeries.length > 0
      ? formatPercent(benchmarkDrawdownSeries[benchmarkDrawdownSeries.length - 1].value)
      : null
  const latestChartValueTag = getChartValueTagLayout(
    latestChartPoint,
    PRIMARY_CHART_GEOMETRY,
    latestChartValueLabel,
  )
  const latestBenchmarkChartValueTag = getChartValueTagLayout(
    latestBenchmarkChartPoint,
    PRIMARY_CHART_GEOMETRY,
    latestBenchmarkChartValueLabel,
  )
  const latestDrawdownValueTag = getChartValueTagLayout(
    latestDrawdownPoint,
    DRAWDOWN_CHART_GEOMETRY,
    latestDrawdownValueLabel,
  )
  const latestBenchmarkDrawdownValueTag = getChartValueTagLayout(
    latestBenchmarkDrawdownPoint,
    DRAWDOWN_CHART_GEOMETRY,
    latestBenchmarkDrawdownValueLabel,
  )
  const chartClipId = `quote-chart-plot-${fundId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  const drawdownClipId = `quote-drawdown-plot-${fundId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  const primaryHoverPlotBounds = getChartPlotBounds(PRIMARY_CHART_GEOMETRY, CHART_CROSSHAIR_INSET)
  const drawdownHoverPlotBounds = getChartPlotBounds(DRAWDOWN_CHART_GEOMETRY, CHART_CROSSHAIR_INSET)
  const primaryTooltipAnchor = getChartTooltipAnchor(
    isPrimaryHoverActive ? chartHoverCursor : null,
    PRIMARY_CHART_GEOMETRY,
  )
  const drawdownTooltipAnchor = getChartTooltipAnchor(
    isDrawdownHoverActive ? chartHoverCursor : null,
    DRAWDOWN_CHART_GEOMETRY,
  )
  const priceOverviewMap = price.overview || {}
  const adjustedExpenseRatio = formatPriceOverviewValue(
    'adjusted_expense_ratio',
    priceOverviewMap.adjusted_expense_ratio,
  )
  const reportedExpenseRatio = formatPriceOverviewValue(
    'total_expense_ratio',
    priceOverviewMap.total_expense_ratio,
  )
  const feesAndTermsRows = [
    { label: 'Management Fee', value: formatPriceOverviewValue('management_fee', priceOverviewMap.management_fee) },
    {
      label: 'Interest Expense Fees',
      value: formatPriceOverviewValue('interest_expense_fees', priceOverviewMap.interest_expense_fees),
    },
    { label: 'Redemption Fee', value: formatPriceOverviewValue('redemption_fee', priceOverviewMap.redemption_fee) },
    {
      label: 'Minimum Initial Investment',
      value: formatPriceOverviewValue('minimum_initial_investment', priceOverviewMap.minimum_initial_investment),
    },
  ]
  const priceFeeFieldDefinitions = [
    ['management_fee', 'Management Fee'],
    ['interest_expense_fees', 'Interest Expense Fees'],
    ['redemption_fee', 'Redemption Fee'],
    ['minimum_initial_investment', 'Minimum Initial Investment'],
  ] as const
  const priceTerRows = [
    { label: 'Adjusted Expense Ratio', value: adjustedExpenseRatio },
    { label: 'Reported Expense Ratio', value: reportedExpenseRatio },
  ]
  const pricePolicyRows = [
    { label: 'Distribution Policy', value: getString(price.distribution_policy) },
    { label: 'Policy Text', value: getString(price.policy_text) },
  ]
  const latestNavRecord = navSeries.rows[navSeries.rows.length - 1]
  const navRefreshStatus = navSeries.refresh_status || null
  const monitoringOverviewRows = [
    {
      label: 'Freshness Status',
      value: formatMonitoringStatus(summary.freshness.data_freshness_status),
      tone: getMonitoringStatusTone(summary.freshness.data_freshness_status),
    },
    {
      label: 'Coverage Status',
      value: getString(summary.instrument_attributes.coverage_status),
      tone: 'status-attribute',
    },
    { label: 'Last Fact Update', value: formatDateTime(summary.freshness.last_fact_update_at), tone: null },
    { label: 'Last Recalculated', value: formatDateTime(summary.freshness.last_recalculated_at), tone: null },
    {
      label: 'Last Snapshot',
      value: formatDateTime(summary.freshness.last_successful_snapshot_at),
      tone: null,
    },
    {
      label: 'Last NAV Date',
      value: formatDate(latestNavRecord?.as_of_date || null),
      tone: null,
    },
    {
      label: 'Refresh Owner',
      value: 'Database Dashboard',
      tone: 'status-attribute',
    },
    {
      label: 'Last Update Trigger',
      value: formatDateTime(navRefreshStatus?.requested_at || null),
      tone: null,
    },
  ]
  const monitoringPipelineRows = [
    {
      domain: 'NAV Facts',
      asOf: formatDate(latestNavRecord?.as_of_date || null),
      cutoff: formatDateTime(navRefreshStatus?.requested_at || latestNavRecord?.adopted_at || summary.freshness.last_fact_update_at),
      methodology: formatNavBasisSource(navSeries.nav_basis_source),
      status: formatMonitoringStatus(navRefreshStatus?.status || navSeries.nav_basis_status || summary.freshness.data_freshness_status),
      tone: getMonitoringStatusTone(navRefreshStatus?.status || navSeries.nav_basis_status || summary.freshness.data_freshness_status),
    },
    {
      domain: 'Performance Snapshot',
      asOf: formatDate(performance.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(performance.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(performance.snapshot_metadata?.methodology_version),
      status: performance.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: performance.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Risk Snapshot',
      asOf: formatDate(risk.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(risk.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(risk.snapshot_metadata?.methodology_version),
      status: risk.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: risk.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Portfolio Snapshot',
      asOf: formatDate(portfolio.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(portfolio.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(portfolio.snapshot_metadata?.methodology_version),
      status: portfolio.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: portfolio.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Ratings',
      asOf: formatDate(summary.rating_as_of),
      cutoff: '—',
      methodology: getString(ratings.methodology_version),
      status: summary.rating_as_of ? 'Current' : 'Pending',
      tone: summary.rating_as_of ? 'status-fresh' : 'status-pending',
    },
  ]
  const productFrameworkSections = buildAttributeFrameworkSections(productFrameworkAttributes)

  const monitoringAlertRows = [
    ...(navRefreshStatus?.message ? [navRefreshStatus.message] : []),
    ...(summary.freshness.staleness_reason ? [summary.freshness.staleness_reason] : []),
    ...summary.quick_monitoring_items,
  ]
  const navSnapshotRows = [
    { label: 'Selected Basis', value: quoteBasisLabel },
    { label: 'Research Basis', value: navBasisLabel },
    { label: 'Basis Source', value: formatNavBasisSource(navSeries.nav_basis_source) },
    { label: 'Series Count', value: String(navSeries.count || navSeries.rows.length || 0) },
    { label: 'NAV Date', value: formatDate(latestPoint?.date || navSeries.rows[navSeries.rows.length - 1]?.as_of_date) },
    { label: 'Currency', value: getString(navSeries.rows[navSeries.rows.length - 1]?.currency || chart.currency) },
  ]
  const distributionRowsSummary = [
    {
      label: 'Latest Distribution',
      value: latestDistribution ? formatNumber(latestDistribution.distribution_amount, 4) : '—',
    },
    {
      label: 'Latest Distribution Date',
      value: latestDistribution ? formatDate(latestDistribution.as_of_date) : '—',
    },
    {
      label: 'Cumulative Distribution',
      value: latestDistribution ? formatNumber(latestDistribution.cumulative_distribution, 4) : '—',
    },
    {
      label: 'Adopted At',
      value: latestDistribution ? formatDateTime(latestDistribution.adopted_at) : '—',
    },
  ]
  const performanceReferenceEndDate = navBasisSeries[navBasisSeries.length - 1]?.date || null
  const performancePeriodSnapshots = PERFORMANCE_METRIC_PERIODS.map((period) => {
    const fundWindow = getAnchoredWindow(navBasisSeries, period.key, performanceReferenceEndDate)
    const benchmarkWindow = getAnchoredWindow(
      metricBenchmarkNavBasisSeries,
      period.key,
      performanceReferenceEndDate,
    )
    return {
      ...period,
      fund: buildPerformanceMetricSnapshot(fundWindow),
      benchmark: benchmarkWindow.length >= 2 ? buildPerformanceMetricSnapshot(benchmarkWindow) : null,
      relative:
        benchmarkWindow.length >= 2 ? buildPerformanceRelativeSnapshot(fundWindow, benchmarkWindow) : null,
    }
  })
  const rollingReturnSeries = buildRollingReturnSeries(navBasisSeries, rollingReturnWindowMonths).slice(-60)
  const rollingBenchmarkReturnSeries =
    selectedMetricBenchmark && metricBenchmarkNavBasisSeries.length > 0
      ? buildRollingReturnSeries(metricBenchmarkNavBasisSeries, rollingReturnWindowMonths).slice(-60)
      : []
  const combinedRollingReturnSeries = [...rollingReturnSeries, ...rollingBenchmarkReturnSeries]
  const rollingReturnBounds = combinedRollingReturnSeries.length
    ? getPaddedAxisBounds(
        Math.min(...combinedRollingReturnSeries.map((point) => point.value)),
        Math.max(...combinedRollingReturnSeries.map((point) => point.value)),
        0.12,
        0.5,
      )
    : { min: -1, max: 1 }
  const rollingReturnTickValues = getLinearTickValues(
    rollingReturnBounds.min,
    rollingReturnBounds.max,
    5,
  )
  const rollingReturnTickDates = getChartTickDates(
    rollingReturnSeries.length > 0 ? rollingReturnSeries : rollingBenchmarkReturnSeries,
    6,
  )
  const rollingReturnLinePath = buildChartLinePath(
    rollingReturnSeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingReturnBounds.min,
    rollingReturnBounds.max,
  )
  const rollingReturnAreaPath = buildChartAreaPath(
    rollingReturnSeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingReturnBounds.min,
    rollingReturnBounds.max,
    0,
  )
  const rollingBenchmarkReturnLinePath = buildChartLinePath(
    rollingBenchmarkReturnSeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingReturnBounds.min,
    rollingReturnBounds.max,
  )
  const monthlyReturnMatrixRows = buildMonthlyReturnMatrix(navBasisSeries)
  const monthlyReturnMatrixMaxAbs = monthlyReturnMatrixRows.reduce((maxAbs, row) => {
    const rowMax = Math.max(
      ...[...row.months, row.ytd]
        .filter((value): value is number => value != null)
        .map((value) => Math.abs(value)),
      0,
    )
    return Math.max(maxAbs, rowMax)
  }, 0)
  const peerComparison = performance.peer_comparison?.status === 'ready' ? performance.peer_comparison : null
  const peerComparisonPathLabel =
    peerComparison?.peer_path?.filter(Boolean).join(' / ') ||
    performance.ranking?.category_name ||
    'Taxonomy peers'
  const peerComparisonMetricByKey = new Map(
    (peerComparison?.metrics || []).map((metric) => [metric.metric_key, metric]),
  )
  const activePerformanceMatrixMode = peerComparison ? performanceMatrixMode : 'values'
  const formatRecoveryValue = (snapshot: PerformanceMetricSnapshot | null) => {
    if (!snapshot || snapshot.maxDrawdown == null) {
      return null
    }
    if (snapshot.maxDrawdown === 0) {
      return '0 d'
    }
    if (snapshot.recoveryOpen) {
      return 'Open'
    }
    if (snapshot.recoveryDays == null) {
      return '—'
    }
    return `${formatNumber(snapshot.recoveryDays, 0)} d`
  }
  const benchmarkMetricPrefix = selectedMetricBenchmark ? 'BM' : null
  const buildBenchmarkNote = (value: string | null) =>
    benchmarkMetricPrefix && value ? `${benchmarkMetricPrefix} ${value}` : null
  const buildPeerPerformanceMatrixCell = (
    rowKey: PerformanceMatrixRowKey,
    periodKey: PerformanceMetricPeriodKey,
  ) => {
    const metricKey = getPeerMetricKeyForMatrixCell(rowKey, periodKey)
    const metric = metricKey ? peerComparisonMetricByKey.get(metricKey) || null : null
    if (!metric) {
      return {
        primary: '—',
        secondary: null,
        peerAvailable: false,
        tone: 'empty',
      }
    }
    if (activePerformanceMatrixMode === 'peer_percentile') {
      return {
        primary: metric.percentile == null ? '—' : `${formatNumber(metric.percentile, 0)} pct`,
        secondary: metric.quartile == null ? null : `Q${formatNumber(metric.quartile, 0)}`,
        peerAvailable: true,
        tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
      }
    }
    if (activePerformanceMatrixMode === 'peer_rank') {
      return {
        primary: formatPeerRank(metric),
        secondary: metric.percentile == null ? null : `${formatNumber(metric.percentile, 0)} pct`,
        peerAvailable: true,
        tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
      }
    }
    return {
      primary: formatPeerMetricDelta(metric),
      secondary: `median ${formatPeerMetricValue(metric, metric.peer_median)}`,
      peerAvailable: true,
      tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
    }
  }
  const performanceMetricMatrixBaseRows = [
    {
      key: 'period_return' as const,
      label: 'Period Return',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.periodReturn == null ? '—' : formatPercent(fund.periodReturn),
        secondary:
          benchmark?.periodReturn == null ? null : buildBenchmarkNote(formatPercent(benchmark.periodReturn)),
        tone: getSignedMetricTone(fund.periodReturn),
      })),
    },
    {
      key: 'annualized_return' as const,
      label: 'Annualized Return',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedReturn == null ? '—' : formatPercent(fund.annualizedReturn),
        secondary:
          benchmark?.annualizedReturn == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedReturn)),
        tone: getSignedMetricTone(fund.annualizedReturn),
      })),
    },
    {
      key: 'annualized_volatility' as const,
      label: 'Ann. Volatility',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedVolatility == null ? '—' : formatPercent(fund.annualizedVolatility),
        secondary:
          benchmark?.annualizedVolatility == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedVolatility)),
      })),
    },
    {
      key: 'excess_return' as const,
      label: 'Excess Return',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary:
          fund.periodReturn == null || benchmark?.periodReturn == null
            ? '—'
            : formatPercent(fund.periodReturn - benchmark.periodReturn),
        secondary: null,
        tone:
          fund.periodReturn == null || benchmark?.periodReturn == null
            ? 'empty'
            : getSignedMetricTone(fund.periodReturn - benchmark.periodReturn),
      })),
    },
    {
      key: 'sharpe_ratio' as const,
      label: 'Sharpe Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.sharpe == null ? '—' : formatNumber(fund.sharpe, 2),
        secondary: benchmark?.sharpe == null ? null : buildBenchmarkNote(formatNumber(benchmark.sharpe, 2)),
      })),
    },
    {
      key: 'sortino_ratio' as const,
      label: 'Sortino Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.sortino == null ? '—' : formatNumber(fund.sortino, 2),
        secondary:
          benchmark?.sortino == null ? null : buildBenchmarkNote(formatNumber(benchmark.sortino, 2)),
      })),
    },
    {
      key: 'calmar_ratio' as const,
      label: 'Calmar Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.calmar == null ? '—' : formatNumber(fund.calmar, 2),
        secondary: benchmark?.calmar == null ? null : buildBenchmarkNote(formatNumber(benchmark.calmar, 2)),
      })),
    },
    {
      key: 'information_ratio' as const,
      label: 'Information Ratio',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.informationRatio == null ? '—' : formatNumber(relative.informationRatio, 2),
        secondary: null,
      })),
    },
    {
      key: 'tracking_error' as const,
      label: 'Tracking Error',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.trackingError == null ? '—' : formatPercent(relative.trackingError),
        secondary: null,
      })),
    },
    {
      key: 'beta' as const,
      label: 'Beta',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.beta == null ? '—' : formatNumber(relative.beta, 2),
        secondary: null,
      })),
    },
    {
      key: 'max_drawdown' as const,
      label: 'Max Drawdown',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.maxDrawdown == null ? '—' : formatPercent(fund.maxDrawdown),
        secondary:
          benchmark?.maxDrawdown == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.maxDrawdown)),
        tone: getSignedMetricTone(fund.maxDrawdown),
      })),
    },
    {
      key: 'recovery_days' as const,
      label: 'Recovery Days',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: formatRecoveryValue(fund) || '—',
        secondary: buildBenchmarkNote(formatRecoveryValue(benchmark)),
      })),
    },
    {
      key: 'upside_capture' as const,
      label: 'Upside Capture',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.upsideCapture == null ? '—' : formatPercent(relative.upsideCapture, 0),
        secondary: null,
      })),
    },
    {
      key: 'downside_capture' as const,
      label: 'Downside Capture',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.downsideCapture == null ? '—' : formatPercent(relative.downsideCapture, 0),
        secondary: null,
      })),
    },
  ]
  const performanceMetricMatrixRows = performanceMetricMatrixBaseRows
    .map((row) => {
      if (activePerformanceMatrixMode === 'values') {
        return row
      }
      return {
        ...row,
        supportsBenchmark: false,
        cells: performancePeriodSnapshots.map((period) =>
          buildPeerPerformanceMatrixCell(row.key, period.key),
        ),
      }
    })
    .filter(
      (row) =>
        activePerformanceMatrixMode === 'values' ||
        row.cells.some((cell) => 'peerAvailable' in cell && cell.peerAvailable),
    )
  const riskScatterRows = risk.scatter_points
    .map((row, index) => ({
      name: getString(row.name),
      returnValue: getNumber(row.return),
      volatilityValue: getNumber(row.volatility),
      tone:
        index === 0
          ? 'investment'
          : String(row.name || '').toLowerCase().includes('category')
            ? 'category'
            : 'index',
    }))
    .filter((row) => row.returnValue != null && row.volatilityValue != null) as Array<{
    name: string
    returnValue: number
    volatilityValue: number
    tone: 'investment' | 'category' | 'index'
  }>
  const riskVolMin = riskScatterRows.length
    ? Math.min(...riskScatterRows.map((row) => row.volatilityValue)) - 1
    : 0
  const riskVolMax = riskScatterRows.length
    ? Math.max(...riskScatterRows.map((row) => row.volatilityValue)) + 1
    : 1
  const riskReturnMin = riskScatterRows.length
    ? Math.min(...riskScatterRows.map((row) => row.returnValue)) - 2
    : 0
  const riskReturnMax = riskScatterRows.length
    ? Math.max(...riskScatterRows.map((row) => row.returnValue)) + 2
    : 1
  const riskXTickValues = getLinearTickValues(riskVolMin, riskVolMax, 5)
  const riskYTickValues = getLinearTickValues(riskReturnMin, riskReturnMax, 5)
  const riskPlotWidth =
    RISK_SCATTER_GEOMETRY.width - RISK_SCATTER_GEOMETRY.paddingLeft - RISK_SCATTER_GEOMETRY.paddingRight
  const riskPlotHeight =
    RISK_SCATTER_GEOMETRY.height - RISK_SCATTER_GEOMETRY.paddingTop - RISK_SCATTER_GEOMETRY.paddingBottom
  const positionedRiskScatterRows = riskScatterRows.map((row) => ({
    ...row,
    x:
      RISK_SCATTER_GEOMETRY.paddingLeft +
      ((row.volatilityValue - riskVolMin) / Math.max(riskVolMax - riskVolMin, 1)) * riskPlotWidth,
    y:
      RISK_SCATTER_GEOMETRY.height -
      RISK_SCATTER_GEOMETRY.paddingBottom -
      ((row.returnValue - riskReturnMin) / Math.max(riskReturnMax - riskReturnMin, 1)) * riskPlotHeight,
  }))
  const riskBenchmarkLabel = selectedMetricBenchmark
    ? selectedMetricBenchmark.ticker_or_isin || selectedMetricBenchmark.fund_name
    : 'Not selected'
  const riskMatrixSnapshots = performancePeriodSnapshots.filter(({ key }) => RISK_MATRIX_PERIOD_KEYS.has(key))
  const lifetimeRiskSnapshot =
    riskMatrixSnapshots.find(({ key }) => key === 'SI')?.fund || buildPerformanceMetricSnapshot(navBasisSeries)
  const formatRecoveryStatus = (snapshot: PerformanceMetricSnapshot | null) => {
    if (!snapshot || snapshot.maxDrawdown == null) {
      return '—'
    }
    if (snapshot.maxDrawdown === 0) {
      return 'At high watermark'
    }
    return snapshot.recoveryOpen ? 'In drawdown' : 'Recovered'
  }
  const riskProfileSeries = resampleSeries(
    buildDrawdownSeries(navBasisSeries),
    navBasisSeries.length > 260 ? 'weekly' : 'daily',
  )
  const riskProfileBounds = getDrawdownAxisBounds(riskProfileSeries)
  const riskProfileTickValues = getLinearTickValues(riskProfileBounds.min, riskProfileBounds.max, 4)
  const riskProfileTickDates = getChartTickDates(riskProfileSeries, 6)
  const riskProfileAreaPath = buildChartAreaPath(
    riskProfileSeries,
    SECONDARY_SERIES_GEOMETRY,
    riskProfileBounds.min,
    riskProfileBounds.max,
    0,
  )
  const riskProfileLinePath = buildChartLinePath(
    riskProfileSeries,
    SECONDARY_SERIES_GEOMETRY,
    riskProfileBounds.min,
    riskProfileBounds.max,
  )
  const monthlyDrawdownSeries = buildMonthlyMinimumSeries(buildDrawdownSeries(navBasisSeries)).slice(-36)
  const monthlyDrawdownBounds = getDrawdownAxisBounds(monthlyDrawdownSeries)
  const monthlyDrawdownTickValues = getLinearTickValues(monthlyDrawdownBounds.min, monthlyDrawdownBounds.max, 4)
  const monthlyDrawdownTickDates = getChartTickDates(monthlyDrawdownSeries, 6)
  const monthlyDrawdownAreaPath = buildChartAreaPath(
    monthlyDrawdownSeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyDrawdownBounds.min,
    monthlyDrawdownBounds.max,
    0,
  )
  const monthlyDrawdownLinePath = buildChartLinePath(
    monthlyDrawdownSeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyDrawdownBounds.min,
    monthlyDrawdownBounds.max,
  )
  const monthlyVolatilitySeries = buildMonthlyAnnualizedVolatilitySeries(navBasisSeries).slice(-36)
  const monthlyVolatilityBounds = monthlyVolatilitySeries.length
    ? getPaddedAxisBounds(
        Math.min(0, ...monthlyVolatilitySeries.map((point) => point.value)),
        Math.max(...monthlyVolatilitySeries.map((point) => point.value)),
        0.12,
        0.5,
      )
    : { min: 0, max: 1 }
  const monthlyVolatilityTickValues = getLinearTickValues(
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
    4,
  )
  const monthlyVolatilityTickDates = getChartTickDates(monthlyVolatilitySeries, 6)
  const monthlyVolatilityAreaPath = buildChartAreaPath(
    monthlyVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
    0,
  )
  const monthlyVolatilityLinePath = buildChartLinePath(
    monthlyVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
  )
  const riskSummaryRows = [
    {
      label: 'As Of',
      value: formatDate(risk.snapshot_metadata?.as_of_date || latestNavRecord?.as_of_date || null),
    },
    {
      label: 'Benchmark',
      value: riskBenchmarkLabel,
    },
    {
      label: 'Current Drawdown',
      value:
        drawdownSeries.length > 0
          ? formatPercent(drawdownSeries[drawdownSeries.length - 1].value)
          : '—',
    },
    {
      label: 'Max Drawdown',
      value:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : formatPercent(lifetimeRiskSnapshot.maxDrawdown),
    },
    {
      label: 'Recovery Status',
      value: formatRecoveryStatus(lifetimeRiskSnapshot),
    },
    {
      label: 'Recovery Days',
      value: formatRecoveryValue(lifetimeRiskSnapshot) || '—',
    },
  ]
  const rollingVolatilitySeries = buildRollingAnnualizedVolatilitySeries(navBasisSeries).slice(-60)
  const rollingVolatilityBounds = rollingVolatilitySeries.length
    ? getPaddedAxisBounds(
        Math.min(0, ...rollingVolatilitySeries.map((point) => point.value)),
        Math.max(...rollingVolatilitySeries.map((point) => point.value)),
        0.12,
        0.5,
      )
    : { min: 0, max: 1 }
  const rollingVolatilityTickValues = getLinearTickValues(
    rollingVolatilityBounds.min,
    rollingVolatilityBounds.max,
    4,
  )
  const rollingVolatilityTickDates = getChartTickDates(rollingVolatilitySeries, 6)
  const rollingVolatilityLinePath = buildChartLinePath(
    rollingVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingVolatilityBounds.min,
    rollingVolatilityBounds.max,
  )
  const rollingVolatilityAreaPath = buildChartAreaPath(
    rollingVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingVolatilityBounds.min,
    rollingVolatilityBounds.max,
    0,
  )
  const rollingBetaSeries =
    selectedMetricBenchmark && metricBenchmarkNavBasisSeries.length > 0
      ? buildRollingBetaSeries(navBasisSeries, metricBenchmarkNavBasisSeries).slice(-60)
      : []
  const rollingBetaBounds = rollingBetaSeries.length
    ? getPaddedAxisBounds(
        Math.min(...rollingBetaSeries.map((point) => point.value)),
        Math.max(...rollingBetaSeries.map((point) => point.value)),
        0.15,
        0.1,
      )
    : { min: 0, max: 2 }
  const rollingBetaTickValues = getLinearTickValues(rollingBetaBounds.min, rollingBetaBounds.max, 4)
  const rollingBetaTickDates = getChartTickDates(rollingBetaSeries, 6)
  const rollingBetaLinePath = buildChartLinePath(
    rollingBetaSeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingBetaBounds.min,
    rollingBetaBounds.max,
  )
  const rollingBetaAreaPath = buildChartAreaPath(
    rollingBetaSeries,
    SECONDARY_SERIES_GEOMETRY,
    rollingBetaBounds.min,
    rollingBetaBounds.max,
    0,
  )
  const rollingRiskFactRows = [
    {
      label: 'Latest Rolling Ann. Vol',
      value:
        rollingVolatilitySeries.length > 0
          ? formatPercent(rollingVolatilitySeries[rollingVolatilitySeries.length - 1].value)
          : '—',
    },
    {
      label: 'Peak Rolling Ann. Vol',
      value:
        rollingVolatilitySeries.length > 0
          ? formatPercent(Math.max(...rollingVolatilitySeries.map((point) => point.value)))
          : '—',
    },
    {
      label: 'Latest Rolling Beta',
      value:
        rollingBetaSeries.length > 0
          ? formatNumber(rollingBetaSeries[rollingBetaSeries.length - 1].value, 2)
          : selectedMetricBenchmark
            ? 'Insufficient overlap'
            : 'No benchmark selected',
    },
    {
      label: 'Beta Benchmark',
      value: riskBenchmarkLabel,
    },
  ]
  const monthlyReturnSeries = buildMonthlyReturnSeries(navBasisSeries)
  const latestMonthlyReturnValue =
    monthlyReturnSeries.length > 0 ? monthlyReturnSeries[monthlyReturnSeries.length - 1].value : null
  const medianMonthlyReturnValue = getMedianValue(monthlyReturnSeries.map((point) => point.value))
  const trailingNegativeMonthCount = getTrailingNegativeMonthCount(monthlyReturnSeries)
  const latestMonthlyDrawdownValue =
    monthlyDrawdownSeries.length > 0 ? monthlyDrawdownSeries[monthlyDrawdownSeries.length - 1].value : null
  const worstMonthlyDrawdownValue =
    monthlyDrawdownSeries.length > 0 ? Math.min(...monthlyDrawdownSeries.map((point) => point.value)) : null
  const currentDrawdownValue = drawdownSeries.length > 0 ? drawdownSeries[drawdownSeries.length - 1].value : null
  const latestRollingVolValue =
    rollingVolatilitySeries.length > 0 ? rollingVolatilitySeries[rollingVolatilitySeries.length - 1].value : null
  const rollingVolMedianValue = getMedianValue(rollingVolatilitySeries.map((point) => point.value))
  const rollingVolPercentile =
    latestRollingVolValue == null
      ? null
      : getPercentileRank(
          rollingVolatilitySeries.map((point) => point.value),
          latestRollingVolValue,
        )
  const latestRollingBetaValue =
    rollingBetaSeries.length > 0 ? rollingBetaSeries[rollingBetaSeries.length - 1].value : null
  const rollingBetaMedianValue = getMedianValue(rollingBetaSeries.map((point) => point.value))
  const rollingBetaPercentile =
    latestRollingBetaValue == null
      ? null
      : getPercentileRank(
          rollingBetaSeries.map((point) => point.value),
          latestRollingBetaValue,
        )
  const latest1WReturn = performancePeriodSnapshots.find(({ key }) => key === '1W')?.fund.periodReturn ?? null
  const latest1MReturn = performancePeriodSnapshots.find(({ key }) => key === '1M')?.fund.periodReturn ?? null
  const structuralRiskSnapshot =
    riskMatrixSnapshots.find(({ key }) => key === '3Y') ??
    riskMatrixSnapshots.find(({ key }) => key === 'SI') ??
    null
  const structuralRiskLabel = structuralRiskSnapshot?.label || 'SI'
  const structuralFundSnapshot = structuralRiskSnapshot?.fund ?? null
  const structuralRelativeSnapshot = structuralRiskSnapshot?.relative ?? null
  const buildWatchReading = (level: string, detail: string) => `${level} · ${detail}`
  const scoreWatchLevel = (level: string) => (level === 'High' ? 2 : level === 'Elevated' ? 1 : 0)
  const volatilityWatch = (() => {
    if (latestRollingVolValue == null || rollingVolMedianValue == null || rollingVolPercentile == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need more 12M rolling history',
      }
    }
    const multiple =
      rollingVolMedianValue === 0 ? null : latestRollingVolValue / rollingVolMedianValue
    const level =
      rollingVolPercentile >= 90 || (multiple != null && multiple >= 1.4)
        ? 'High'
        : rollingVolPercentile >= 75 || (multiple != null && multiple >= 1.2)
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatPercent(latestRollingVolValue)} vs median ${formatPercent(rollingVolMedianValue)} (${formatNumber(rollingVolPercentile, 0)}th pct)`,
      ),
    }
  })()
  const drawdownPressureWatch = (() => {
    if (currentDrawdownValue == null) {
      return {
        level: 'N/A',
        reading: 'N/A · No drawdown history',
      }
    }
    const worstAbs = lifetimeRiskSnapshot.maxDrawdown == null ? null : Math.abs(lifetimeRiskSnapshot.maxDrawdown)
    const ratio = worstAbs && worstAbs > 0 ? Math.abs(currentDrawdownValue) / worstAbs : 0
    const level =
      currentDrawdownValue <= -8 || ratio >= 0.6
        ? 'High'
        : currentDrawdownValue <= -4 || ratio >= 0.35
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatPercent(currentDrawdownValue)} current${worstAbs ? `, ${formatNumber(ratio * 100, 0)}% of worst` : ''}`,
      ),
    }
  })()
  const recentLossPressureWatch = (() => {
    if (latestMonthlyReturnValue == null && latest1WReturn == null && latest1MReturn == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need recent return history',
      }
    }
    const level =
      trailingNegativeMonthCount >= 3 ||
      (latestMonthlyReturnValue != null && latestMonthlyReturnValue <= -3) ||
      (latest1WReturn != null && latest1WReturn <= -2)
        ? 'High'
        : trailingNegativeMonthCount >= 2 ||
            (latestMonthlyReturnValue != null && latestMonthlyReturnValue <= -1.5) ||
            (latest1MReturn != null && latest1MReturn <= -3)
          ? 'Elevated'
          : 'Normal'
    const recentMonthlyLabel =
      latestMonthlyReturnValue == null ? '—' : formatPercent(latestMonthlyReturnValue)
    return {
      level,
      reading: buildWatchReading(
        level,
        `1W ${latest1WReturn == null ? '—' : formatPercent(latest1WReturn)}, 1M ${latest1MReturn == null ? '—' : formatPercent(latest1MReturn)}, latest month ${recentMonthlyLabel}, ${String(trailingNegativeMonthCount)} down month(s)`,
      ),
    }
  })()
  const betaDriftWatch = (() => {
    if (!selectedMetricBenchmark) {
      return {
        level: 'N/A',
        reading: 'N/A · Select a benchmark in Performance',
      }
    }
    if (latestRollingBetaValue == null || rollingBetaMedianValue == null || rollingBetaPercentile == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need enough overlapping 12M windows',
      }
    }
    const absoluteDelta = Math.abs(latestRollingBetaValue - rollingBetaMedianValue)
    const level =
      absoluteDelta >= 0.35 || rollingBetaPercentile >= 90
        ? 'High'
        : absoluteDelta >= 0.2 || rollingBetaPercentile >= 75
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatNumber(latestRollingBetaValue, 2)} vs median ${formatNumber(rollingBetaMedianValue, 2)} (${formatNumber(rollingBetaPercentile, 0)}th pct)`,
      ),
    }
  })()
  const watchScore =
    scoreWatchLevel(volatilityWatch.level) +
    scoreWatchLevel(drawdownPressureWatch.level) +
    scoreWatchLevel(recentLossPressureWatch.level) +
    scoreWatchLevel(betaDriftWatch.level)
  const overallWatchLevel =
    watchScore >= 5 ? 'High' : watchScore >= 2 ? 'Elevated' : watchScore >= 0 ? 'Normal' : 'N/A'
  const latestYtdReturn = performancePeriodSnapshots.find(({ key }) => key === 'YTD')?.fund.periodReturn ?? null
  const lifetimePerformanceSnapshot =
    performancePeriodSnapshots.find(({ key }) => key === 'SI')?.fund || buildPerformanceMetricSnapshot(navBasisSeries)
  const overviewRatingValue =
    ratings.overall_rating == null ? '—' : formatStarRating(ratings.overall_rating)
  const overviewRatingNote =
    ratings.overall_rating == null
      ? 'Pending research'
      : summary.rating_as_of
        ? `As of ${formatDate(summary.rating_as_of)}`
        : 'Research rating'
  const overviewRankingValue =
    performance.ranking
      ? [
          performance.ranking.rank == null || performance.ranking.sample_count == null
            ? null
            : `${formatNumber(performance.ranking.rank, 0)} / ${formatNumber(performance.ranking.sample_count, 0)}`,
          performance.ranking.quartile == null ? null : `Q${performance.ranking.quartile}`,
          performance.ranking.percentile == null
            ? null
            : `${formatNumber(performance.ranking.percentile, 0)} pct`,
        ]
          .filter(Boolean)
          .join(' / ') || '—'
      : '—'
  const overviewSideMetricRows = [
    {
      label: '1W Return',
      value: latest1WReturn == null ? '—' : formatPercent(latest1WReturn),
      note: 'Latest',
    },
    {
      label: 'MTD Return',
      value: latestMonthlyReturnValue == null ? '—' : formatPercent(latestMonthlyReturnValue),
      note: 'Current month',
    },
    {
      label: 'YTD Return',
      value: latestYtdReturn == null ? '—' : formatPercent(latestYtdReturn),
      note: 'Year to date',
    },
    {
      label: 'Ann. Return',
      value:
        lifetimePerformanceSnapshot.annualizedReturn == null
          ? '—'
          : formatPercent(lifetimePerformanceSnapshot.annualizedReturn),
      note: 'SI',
    },
    {
      label: 'Ann. Vol',
      value:
        lifetimePerformanceSnapshot.annualizedVolatility == null
          ? '—'
          : formatPercent(lifetimePerformanceSnapshot.annualizedVolatility),
      note: 'SI',
    },
    {
      label: 'Max Drawdown',
      value:
        lifetimeRiskSnapshot.maxDrawdown == null ? '—' : formatPercent(lifetimeRiskSnapshot.maxDrawdown),
      note: 'SI',
    },
    {
      label: 'Current Drawdown',
      value: currentDrawdownValue == null ? '—' : formatPercent(currentDrawdownValue),
      note: drawdownPressureWatch.level,
    },
    {
      label: 'SI Sharpe',
      value: lifetimePerformanceSnapshot.sharpe == null ? '—' : formatNumber(lifetimePerformanceSnapshot.sharpe, 2),
      note: 'rf = 0',
    },
    {
      label: 'Peer Rank',
      value: overviewRankingValue,
      note: peerComparisonPathLabel,
    },
    {
      label: 'Watch Level',
      value: overallWatchLevel,
      note: `${String(watchScore)} signal point(s)`,
    },
  ]
  const taxonomyPathLabel =
    productFrameworkAttributes?.taxonomy?.path_labels?.length
      ? productFrameworkAttributes.taxonomy.path_labels.join(' / ')
      : summary.taxonomy?.path_labels?.length
        ? summary.taxonomy.path_labels.join(' / ')
        : summary.category_name || 'Unclassified'
  const taxonomyNodes = taxonomyTree?.nodes || []
  const taxonomyDraftNode =
    taxonomyNodes.find((node) => node.node_id === taxonomyDraftNodeId) || null
  const taxonomyDraftPathNodeIds = taxonomyDraftNode?.path_node_ids || []
  const taxonomyLevelSelectors = (() => {
    const selectors: Array<{
      levelIndex: number
      parentNodeId: string | null
      selectedNodeId: string
      options: FundTaxonomyTreeNode[]
      disabled: boolean
    }> = []
    let parentNodeId: string | null = null
    let chainActive = true
    const maxDepth = Math.max(taxonomyTree?.max_depth || 3, taxonomyDraftPathNodeIds.length + 1)

    for (let levelIndex = 1; levelIndex <= maxDepth; levelIndex += 1) {
      const options = chainActive
        ? taxonomyNodes.filter((node) => node.parent_node_id === parentNodeId)
        : []
      const selectedNodeId: string = chainActive ? taxonomyDraftPathNodeIds[levelIndex - 1] || '' : ''
      selectors.push({
        levelIndex,
        parentNodeId,
        selectedNodeId,
        options,
        disabled: !chainActive || options.length === 0,
      })
      chainActive = Boolean(selectedNodeId)
      parentNodeId = selectedNodeId
    }

    return selectors
  })()
  const localCurrentRiskWatchRows = [
    {
      label: 'Overall Watch',
      value: buildWatchReading(overallWatchLevel, `${String(watchScore)} signal point(s)`),
    },
    {
      label: 'Volatility Regime',
      value: volatilityWatch.reading,
    },
    {
      label: 'Drawdown Pressure',
      value: drawdownPressureWatch.reading,
    },
    {
      label: 'Recent Loss Pressure',
      value: recentLossPressureWatch.reading,
    },
    {
      label: 'Benchmark Sensitivity',
      value: betaDriftWatch.reading,
    },
    {
      label: 'Methodology',
      value: 'Heuristic watch flags based on current drawdown, rolling vol, recent losses, and beta drift.',
    },
  ]
  const localRiskFallbackFacts = localCurrentRiskWatchRows.filter((row) => row.value !== '—').slice(0, 4)
  const localRiskStructureRows = [
    {
      characteristic: 'Risk Style',
      reading:
        structuralFundSnapshot?.annualizedVolatility == null && structuralFundSnapshot?.maxDrawdown == null
          ? '—'
          : `${structuralRiskLabel} vol ${structuralFundSnapshot?.annualizedVolatility == null ? '—' : formatPercent(structuralFundSnapshot.annualizedVolatility)} · max DD ${structuralFundSnapshot?.maxDrawdown == null ? '—' : formatPercent(structuralFundSnapshot.maxDrawdown)}`,
      interpretation:
        structuralFundSnapshot?.annualizedVolatility == null || structuralFundSnapshot?.maxDrawdown == null
          ? 'Insufficient history to classify the long-run risk amplitude.'
          : structuralFundSnapshot.annualizedVolatility < 8 && Math.abs(structuralFundSnapshot.maxDrawdown) < 10
            ? 'Low-amplitude path. Capital preservation matters more than benchmark capture.'
            : structuralFundSnapshot.annualizedVolatility < 15 && Math.abs(structuralFundSnapshot.maxDrawdown) < 20
              ? 'Balanced amplitude. Drawdowns matter, but the path is still broadly manageable.'
              : 'High-amplitude path. Position sizing and liquidity discipline matter.'
    },
    {
      characteristic: 'Benchmark Dependence',
      reading:
        structuralRelativeSnapshot?.beta == null && structuralRelativeSnapshot?.trackingError == null
          ? '—'
          : `${structuralRiskLabel} beta ${structuralRelativeSnapshot?.beta == null ? '—' : formatNumber(structuralRelativeSnapshot.beta, 2)} · TE ${structuralRelativeSnapshot?.trackingError == null ? '—' : formatPercent(structuralRelativeSnapshot.trackingError)}`,
      interpretation:
        !selectedMetricBenchmark
          ? 'No benchmark selected, so benchmark dependence is not fully specified.'
          : structuralRelativeSnapshot?.beta == null || structuralRelativeSnapshot?.trackingError == null
            ? 'Need more overlap with the current benchmark to characterize sensitivity.'
            : structuralRelativeSnapshot.beta < 0.35 && structuralRelativeSnapshot.trackingError < 5
              ? 'Low benchmark dependence. Risk is driven more by manager path than market beta.'
              : structuralRelativeSnapshot.beta < 0.8 && structuralRelativeSnapshot.trackingError < 10
                ? 'Moderate benchmark dependence. Market moves matter, but are not the whole story.'
                : 'High benchmark dependence. Benchmark direction and factor conditions matter a lot.'
    },
    {
      characteristic: 'Downside Shape',
      reading:
        structuralRelativeSnapshot?.upsideCapture == null && structuralRelativeSnapshot?.downsideCapture == null
          ? '—'
          : `${structuralRiskLabel} up ${structuralRelativeSnapshot?.upsideCapture == null ? '—' : formatPercent(structuralRelativeSnapshot.upsideCapture, 0)} · down ${structuralRelativeSnapshot?.downsideCapture == null ? '—' : formatPercent(structuralRelativeSnapshot.downsideCapture, 0)}`,
      interpretation:
        !selectedMetricBenchmark
          ? '—'
          : structuralRelativeSnapshot?.upsideCapture == null || structuralRelativeSnapshot?.downsideCapture == null
            ? 'Capture profile needs a longer overlapping benchmark history.'
            : structuralRelativeSnapshot.downsideCapture < structuralRelativeSnapshot.upsideCapture - 15
              ? 'Downside participation is meaningfully lighter than upside participation.'
              : structuralRelativeSnapshot.downsideCapture > structuralRelativeSnapshot.upsideCapture + 15
                ? 'Downside participation is heavy relative to upside capture.'
                : 'Upside and downside participation are broadly balanced.'
    },
    {
      characteristic: 'Recovery Profile',
      reading:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : `SI max DD ${formatPercent(lifetimeRiskSnapshot.maxDrawdown)} · recovery ${formatRecoveryValue(lifetimeRiskSnapshot) || '—'}`,
      interpretation:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? 'Insufficient history to classify recovery behavior.'
          : lifetimeRiskSnapshot.recoveryOpen
            ? 'The fund is still below its prior high watermark.'
            : lifetimeRiskSnapshot.recoveryDays != null && lifetimeRiskSnapshot.recoveryDays <= 120
              ? 'Historically, major drawdowns have healed relatively quickly.'
              : lifetimeRiskSnapshot.recoveryDays != null && lifetimeRiskSnapshot.recoveryDays > 365
                ? 'Drawdowns can take a long time to repair.'
                : 'Recovery profile is moderate rather than fast.'
    },
  ]
  const drawdownSummaryRows = [
    {
      label: 'Peak Date',
      value: formatDate(risk.drawdown_summary?.peak_date),
    },
    {
      label: 'Valley Date',
      value: formatDate(risk.drawdown_summary?.valley_date),
    },
    {
      label: 'Max Duration',
      value:
        risk.drawdown_summary?.max_duration_months == null
          ? '—'
          : `${String(risk.drawdown_summary.max_duration_months)} mo`,
    },
    {
      label: 'Worst Monthly Drawdown',
      value:
        monthlyDrawdownSeries.length > 0
          ? formatPercent(Math.min(...monthlyDrawdownSeries.map((point) => point.value)))
          : '—',
    },
  ]
  const localRiskChangeRows = [
    {
      signal: 'Rolling Ann. Vol',
      current: latestRollingVolValue == null ? '—' : formatPercent(latestRollingVolValue),
      baseline: rollingVolMedianValue == null ? '—' : `Median ${formatPercent(rollingVolMedianValue)}`,
      change:
        latestRollingVolValue == null || rollingVolMedianValue == null
          ? '—'
          : `${latestRollingVolValue >= rollingVolMedianValue ? '+' : ''}${formatPercent(latestRollingVolValue - rollingVolMedianValue)}`,
      watch: volatilityWatch.level,
    },
    {
      signal: 'Rolling Beta',
      current:
        latestRollingBetaValue == null
          ? (selectedMetricBenchmark ? '—' : 'No benchmark selected')
          : formatNumber(latestRollingBetaValue, 2),
      baseline:
        rollingBetaMedianValue == null
          ? '—'
          : `Median ${formatNumber(rollingBetaMedianValue, 2)}`,
      change:
        latestRollingBetaValue == null || rollingBetaMedianValue == null
          ? '—'
          : `${latestRollingBetaValue >= rollingBetaMedianValue ? '+' : ''}${formatNumber(latestRollingBetaValue - rollingBetaMedianValue, 2)}`,
      watch: betaDriftWatch.level,
    },
    {
      signal: 'Current Drawdown',
      current: currentDrawdownValue == null ? '—' : formatPercent(currentDrawdownValue),
      baseline:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : `Worst ${formatPercent(lifetimeRiskSnapshot.maxDrawdown)}`,
      change:
        currentDrawdownValue == null || lifetimeRiskSnapshot.maxDrawdown == null || lifetimeRiskSnapshot.maxDrawdown === 0
          ? '—'
          : `${formatNumber((Math.abs(currentDrawdownValue) / Math.abs(lifetimeRiskSnapshot.maxDrawdown)) * 100, 0)}% of worst`,
      watch: drawdownPressureWatch.level,
    },
    {
      signal: 'Latest Monthly Drawdown',
      current: latestMonthlyDrawdownValue == null ? '—' : formatPercent(latestMonthlyDrawdownValue),
      baseline:
        worstMonthlyDrawdownValue == null
          ? '—'
          : `Worst ${formatPercent(worstMonthlyDrawdownValue)}`,
      change:
        latestMonthlyDrawdownValue == null || worstMonthlyDrawdownValue == null || worstMonthlyDrawdownValue === 0
          ? '—'
          : `${formatNumber((Math.abs(latestMonthlyDrawdownValue) / Math.abs(worstMonthlyDrawdownValue)) * 100, 0)}% of worst`,
      watch: recentLossPressureWatch.level,
    },
    {
      signal: 'Recent Return Pressure',
      current: `1W ${latest1WReturn == null ? '—' : formatPercent(latest1WReturn)} · 1M ${latest1MReturn == null ? '—' : formatPercent(latest1MReturn)}`,
      baseline:
        medianMonthlyReturnValue == null
          ? '—'
          : `Median month ${formatPercent(medianMonthlyReturnValue)}`,
      change: `${String(trailingNegativeMonthCount)} trailing down month(s)`,
      watch: recentLossPressureWatch.level,
    },
  ]
  const payloadCurrentWatchRows = Array.isArray(risk.current_watch?.rows)
    ? risk.current_watch.rows
      .map((row) => {
        const label = getString(row.signal)
        const reading = getString(row.reading)
        if (label === '—' || reading === '—') {
          return null
        }
        return {
          label,
          value: reading,
        }
      })
      .filter((row): row is { label: string; value: string } => row !== null)
    : []
  const payloadRiskStructureRows = Array.isArray(risk.risk_structure?.rows)
    ? risk.risk_structure.rows
      .map((row) => {
        const characteristic = getString(row.characteristic)
        const reading = getString(row.reading)
        const interpretation = getString(row.interpretation)
        if (characteristic === '—') {
          return null
        }
        return {
          characteristic,
          reading,
          interpretation,
        }
      })
      .filter(
        (
          row,
        ): row is {
          characteristic: string
          reading: string
          interpretation: string
        } => row !== null,
      )
    : []
  const payloadRiskChangeRows = Array.isArray(risk.change_monitor?.rows)
    ? risk.change_monitor.rows
      .map((row) => {
        const signal = getString(row.signal)
        if (signal === '—') {
          return null
        }
        return {
          signal,
          current: getString(row.current),
          baseline: getString(row.baseline),
          change: getString(row.change),
          watch: getString(row.watch),
        }
      })
      .filter(
        (
          row,
        ): row is {
          signal: string
          current: string
          baseline: string
          change: string
          watch: string
        } => row !== null,
      )
    : []
  const currentRiskWatchRows = payloadCurrentWatchRows.length ? payloadCurrentWatchRows : localCurrentRiskWatchRows
  const riskFallbackFacts = currentRiskWatchRows.length ? currentRiskWatchRows.slice(0, 4) : localRiskFallbackFacts
  const riskStructureRows = payloadRiskStructureRows.length ? payloadRiskStructureRows : localRiskStructureRows
  const riskChangeRows = payloadRiskChangeRows.length ? payloadRiskChangeRows : localRiskChangeRows
  const riskMatrixRows = [
    {
      label: 'Ann. Volatility',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedVolatility == null ? '—' : formatPercent(fund.annualizedVolatility),
        secondary:
          benchmark?.annualizedVolatility == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedVolatility)),
      })),
    },
    {
      label: 'Downside Deviation',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary:
          fund.annualizedDownsideDeviation == null ? '—' : formatPercent(fund.annualizedDownsideDeviation),
        secondary:
          benchmark?.annualizedDownsideDeviation == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedDownsideDeviation)),
      })),
    },
    {
      label: 'Tracking Error',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.trackingError == null ? '—' : formatPercent(relative.trackingError),
        secondary: null,
      })),
    },
    {
      label: 'Beta',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.beta == null ? '—' : formatNumber(relative.beta, 2),
        secondary: null,
      })),
    },
    {
      label: 'Max Drawdown',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.maxDrawdown == null ? '—' : formatPercent(fund.maxDrawdown),
        secondary:
          benchmark?.maxDrawdown == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.maxDrawdown)),
      })),
    },
    {
      label: 'Recovery Days',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: formatRecoveryValue(fund) || '—',
        secondary: buildBenchmarkNote(formatRecoveryValue(benchmark)),
      })),
    },
    {
      label: 'Upside Capture',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.upsideCapture == null ? '—' : formatPercent(relative.upsideCapture, 0),
        secondary: null,
      })),
    },
    {
      label: 'Downside Capture',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.downsideCapture == null ? '—' : formatPercent(relative.downsideCapture, 0),
        secondary: null,
      })),
    },
  ]
  const evidenceReferenceOptions = Array.from(
    new Set(
      [
        ...documents.current_documents.map((row) =>
          [getString(row.title), row.version_label ? getString(row.version_label) : '']
            .filter((item) => item && item !== '—')
            .join(' · '),
        ),
        ...documents.extraction_reviews.map((row) =>
          [getString(row.document_title), row.adopted_version ? getString(row.adopted_version) : '']
            .filter((item) => item && item !== '—')
            .join(' · '),
        ),
      ].filter((item) => item && item !== '—'),
    ),
  )
  const evidenceReferenceSet = new Set(evidenceReferenceOptions.map((item) => item.toLowerCase()))

  function resolveChartPointerSelection(
    event: ReactMouseEvent<SVGSVGElement>,
    geometry: ChartGeometry,
  ) {
    if (scaledVisibleSeries.length < 2) {
      return null
    }
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width) {
      return null
    }
    const relativeX = ((event.clientX - rect.left) / rect.width) * geometry.width
    const relativeY = ((event.clientY - rect.top) / Math.max(rect.height, 1)) * geometry.height
    const plotBounds = getChartPlotBounds(geometry, CHART_HOVER_INSET)
    if (
      relativeX <= plotBounds.left ||
      relativeX >= plotBounds.right ||
      relativeY <= plotBounds.top ||
      relativeY >= plotBounds.bottom
    ) {
      return null
    }
    const xRatio = (relativeX - plotBounds.left) / Math.max(plotBounds.width, 1)
    const nextIndex = Math.round(xRatio * (scaledVisibleSeries.length - 1))
    const resolvedIndex = Math.min(Math.max(nextIndex, 0), scaledVisibleSeries.length - 1)
    return {
      index: resolvedIndex,
      point: scaledVisibleSeries[resolvedIndex],
      cursor: {
        xRatio: Math.min(Math.max(xRatio, 0), 1),
        y: relativeY,
      },
    }
  }

  function updateChartHoverFromPointer(
    event: ReactMouseEvent<SVGSVGElement>,
    geometry: ChartGeometry,
    panel: ChartHoverPanel,
  ) {
    const nextSelection = resolveChartPointerSelection(event, geometry)
    if (!nextSelection) {
      clearChartHover()
      return
    }
    setChartHoverPanel(panel)
    setChartHoverCursor(nextSelection.cursor)
    setChartHoverIndex(nextSelection.index)
  }

  function clearChartHover() {
    setChartHoverCursor(null)
    setChartHoverIndex(null)
    setChartHoverPanel(null)
  }

  function handlePrimaryChartClick(event: ReactMouseEvent<SVGSVGElement>) {
    if (!timelineNoteCaptureMode) {
      return
    }
    const selection = resolveChartPointerSelection(event, PRIMARY_CHART_GEOMETRY)
    if (!selection) {
      return
    }
    setChartHoverPanel('primary')
    setChartHoverCursor(selection.cursor)
    setChartHoverIndex(selection.index)
    openTimelineNoteEditor(selection.point.date)
  }

  function handlePrimaryChartContextMenu(event: ReactMouseEvent<SVGSVGElement>) {
    const selection = resolveChartPointerSelection(event, PRIMARY_CHART_GEOMETRY)
    if (!selection) {
      return
    }
    event.preventDefault()
    setChartHoverPanel('primary')
    setChartHoverCursor(selection.cursor)
    setChartHoverIndex(selection.index)
    setChartTimelineNoteContextMenu({
      clientX: event.clientX,
      clientY: event.clientY,
      anchorDate: selection.point.date,
    })
  }

  const chartSettingsMenu =
    openQuoteChartMenu === 'settings' ? (
      <div className="instrument-chart-menu-panel instrument-chart-settings-panel">
        <div className="instrument-chart-settings-layout">
          <section className="instrument-chart-settings-block">
            <div className="instrument-chart-settings-block-head">
              <span>Series</span>
              <strong>{chartSeriesBasisLabel}</strong>
            </div>
            <div className="instrument-chart-settings-control-group">
              <span>Data Type</span>
              <div className="instrument-chart-settings-option-grid">
                {quoteBasisOptions.map((basis) => (
                  <button
                    key={basis}
                    type="button"
                    className={
                      activeQuoteBasis === basis
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() => setQuoteBasis(basis)}
                  >
                    {QUOTE_BASIS_LABELS[basis]}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="instrument-chart-settings-block">
            <div className="instrument-chart-settings-block-head">
              <span>Display</span>
              <strong>{effectiveCurrency}</strong>
            </div>
            <div className="instrument-chart-settings-field-grid">
              <label className="instrument-chart-settings-field">
                <span>Frequency</span>
                <select
                  value={chartFrequency}
                  onChange={(event) => setChartFrequency(event.target.value as ChartFrequency)}
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </label>
              <label className="instrument-chart-settings-field">
                <span>Currency</span>
                <select value={effectiveCurrency} onChange={(event) => setSelectedCurrency(event.target.value)}>
                  {(availableCurrencies.length ? availableCurrencies : [effectiveCurrency]).map((currency) => (
                    <option key={currency} value={currency}>
                      {currency}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="instrument-chart-settings-control-group">
              <span>Chart Style</span>
              <div className="instrument-chart-settings-option-grid">
                {(['mountain', 'line', 'dot'] as ChartDisplayStyle[]).map((style) => (
                  <button
                    key={style}
                    type="button"
                    className={
                      chartDisplayStyle === style
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() => setChartDisplayStyle(style)}
                  >
                    {toTitleCase(style)}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="instrument-chart-settings-block instrument-chart-settings-block-data">
            <div className="instrument-chart-settings-block-head">
              <span>Events & Data</span>
              <strong>Chart overlays</strong>
            </div>
            <div className="instrument-chart-settings-option-grid">
              <button
                type="button"
                className={
                  showDividendEvents
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowDividendEvents((current) => !current)}
              >
                Dividends
              </button>
              <button
                type="button"
                className={
                  showTimelineNoteEvents
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowTimelineNoteEvents((current) => !current)}
              >
                Research Notes
              </button>
              <button
                type="button"
                className={
                  showDrawdownPanel
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowDrawdownPanel((current) => !current)}
              >
                Drawdown
              </button>
            </div>
          </section>
        </div>
      </div>
    ) : null

  return (
    <div className="terminal-page">
      <section className="panel instrument-detail-shell">
        <div className="instrument-detail-topbar">
          <div className="instrument-detail-breadcrumbs">
            <Link to="/watchlists" className="instrument-detail-backlink">
              <span aria-hidden="true">‹</span>
              <span>Watchlists</span>
            </Link>
            <span className="instrument-detail-breadcrumb-separator">/</span>
            <span className="instrument-detail-breadcrumb-current">{summary.ticker_or_isin}</span>
          </div>
          <div className="instrument-detail-actions">
            <button type="button" onClick={() => setSettingsModalOpen(true)}>
              Settings
            </button>
            <button type="button">Download PDF</button>
          </div>
        </div>
        <div className="instrument-detail-hero">
          <div className="instrument-detail-headline">
            <div className="instrument-detail-eyebrow">Instrument Detail</div>
            <h1 className="instrument-detail-title">
              {summary.fund_name} <span>{summary.ticker_or_isin}</span>
            </h1>
            <div className="instrument-detail-badges">
              <span className="context-chip">{summary.category_name}</span>
              <span className="context-chip">Basis: {navBasisLabel}</span>
              <span className="context-chip">Analyst Stance: {summary.analyst_stance}</span>
            </div>
          </div>
        </div>

        <div className="instrument-detail-tabs-row">
          <div className="instrument-detail-tabs">
            {availableTabs.map((tab) => (
              <button
                key={tab}
                type="button"
                className={tab === activeTab ? 'instrument-detail-tab instrument-detail-tab-active' : 'instrument-detail-tab'}
                onClick={() => setActiveTab(tab)}
              >
                {TAB_LABELS[tab]}
              </button>
            ))}
          </div>
        </div>

        {sectionError ? <div className="inline-notice inline-notice-error">{sectionError}</div> : null}
        {sectionNotice ? <div className="inline-notice inline-notice-success">{sectionNotice}</div> : null}
      </section>

      {settingsModalOpen ? (
        <div
          className="instrument-modal-backdrop"
          onClick={() => setSettingsModalOpen(false)}
        >
          <div
            className="instrument-modal instrument-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Fund Settings"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="instrument-modal-header">
              <div>
                <div className="panel-title">Settings</div>
                <div className="instrument-quote-source-title">Taxonomy Settings</div>
              </div>
              <div className="toolbar">
                <button type="button" onClick={() => setSettingsModalOpen(false)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => void handleSaveFundSettings()}
                  disabled={savingSection === 'fund_settings'}
                >
                  {savingSection === 'fund_settings' ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
            <div className="instrument-settings-body">
              <section className="instrument-settings-section">
                <div className="instrument-settings-section-header">
                  <div>
                    <div className="instrument-settings-title">Classification Path</div>
                    <div className="instrument-settings-current-path">
                      <span>Current Path</span>
                      <strong>{taxonomyPathLabel}</strong>
                    </div>
                  </div>
                  {taxonomyDraftNodeId ? (
                    <button
                      type="button"
                      className="instrument-settings-text-action"
                      onClick={() => setTaxonomyDraftNodeId('')}
                      disabled={!taxonomyTree}
                    >
                      Unclassify
                    </button>
                  ) : null}
                </div>
                <div className="instrument-settings-taxonomy-stack">
                  {taxonomyTree ? (
                    taxonomyLevelSelectors.map((selector) => (
                      <label key={`taxonomy-level-${selector.levelIndex}`} className="instrument-settings-taxonomy-row">
                        <span className="instrument-settings-taxonomy-label">
                          {selector.levelIndex === 1 ? 'Regime' : `Level ${selector.levelIndex - 1}`}
                        </span>
                        <select
                          className="instrument-settings-taxonomy-select"
                          value={selector.selectedNodeId}
                          disabled={selector.disabled}
                          onChange={(event) =>
                            setTaxonomyDraftNodeId(event.target.value || selector.parentNodeId || '')
                          }
                        >
                          <option value="">
                            {selector.disabled
                              ? 'Select parent first'
                              : selector.parentNodeId
                                ? 'Stop here'
                                : 'Unclassified'}
                          </option>
                          {selector.options.map((node) => (
                            <option key={node.node_id} value={node.node_id}>
                              {node.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))
                  ) : (
                    <div className="instrument-settings-loading">Loading taxonomy...</div>
                  )}
                </div>
              </section>
            </div>
          </div>
        </div>
      ) : null}

      {activeTab === 'overview' ? (
        <>
          <section className="panel instrument-quote-panel">
            <div className="instrument-chart-shell">
              <div className="instrument-chart-header">
                <div className="instrument-quote-summary-main instrument-quote-summary-main-compact">
                  <div className="instrument-quote-summary-topline">
                    <div className="instrument-quote-primary-block">
                      <div className="instrument-quote-value">
                        {basisValue != null ? formatNumber(basisValue, 4) : '—'}
                      </div>
                      <div className={quoteToneClass}>
                        {formatChangeSummary(quoteChange, quoteChangePct)}
                      </div>
                    </div>
                    <div className="instrument-quote-rating-block">
                      <span>Rating</span>
                      <strong>{overviewRatingValue}</strong>
                      <em>{overviewRatingNote}</em>
                    </div>
                  </div>
                  <div className="instrument-quote-meta">
                    <div className="instrument-quote-asof">
                      As of {formatDate(latestSeriesPoint?.date || navSeries.rows[navSeries.rows.length - 1]?.as_of_date)}
                    </div>
                  </div>
                </div>
                <div className="instrument-quote-facts">
                  <div className="instrument-quote-facts-grid">
                    {overviewSideMetricRows.map((row) => (
                      <div key={row.label} className="instrument-quote-fact">
                        <span>{row.label}</span>
                        <strong>{row.value}</strong>
                        <em>{row.note}</em>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="instrument-chart-main">
                <div className="instrument-quote-control-bar">
                  <div className="instrument-quote-toolbar-primary">
                    <div className="instrument-chart-compare">
                      <div className="instrument-chart-compare-select-wrap">
                        <select
                          aria-label="Compare benchmark"
                          value={benchmarkFundId}
                          onChange={(event) => setBenchmarkFundId(event.target.value)}
                        >
                          <option value="">Compare...</option>
                          {benchmarkOptions.map((item) => (
                            <option key={item.fund_id} value={item.fund_id}>
                              {item.ticker_or_isin ? `${item.ticker_or_isin} · ${item.fund_name}` : item.fund_name}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </div>
                </div>

                {quoteActionNotice ? <div className="instrument-quote-action-notice">{quoteActionNotice}</div> : null}

                <div className="instrument-chart-stage instrument-chart-stage-interactive">
                  {scaledVisibleSeries.length > 1 ? (
                    <>
                    <div className="instrument-chart-series-head">
                      <div className="instrument-series-legend">
                        <div className="instrument-series-label">
                          <strong>{summary.ticker_or_isin}</strong>
                          <span>{chartSeriesBasisLabel}</span>
                          <em>
                            {formatChangeSummary(chartQuotePeriodStats.change, chartQuotePeriodStats.changePct)}
                          </em>
                        </div>
                        {selectedBenchmark ? (
                          <div className="instrument-series-label instrument-series-label-benchmark-row">
                            <strong>{selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}</strong>
                            <span>Compare</span>
                            <em>
                              {formatChangeSummary(chartBenchmarkPeriodStats.change, chartBenchmarkPeriodStats.changePct)}
                            </em>
                          </div>
                        ) : null}
                      </div>
                      <div className="instrument-chart-series-meta">
                        <span>{effectiveCurrency}</span>
                        <div className="instrument-chart-menu instrument-chart-settings-menu" ref={quoteChartMenuRef}>
                          <button
                            type="button"
                            className={
                              openQuoteChartMenu === 'settings'
                                ? 'instrument-chart-settings-trigger instrument-chart-settings-trigger-active'
                                : 'instrument-chart-settings-trigger'
                            }
                            aria-label="Chart settings"
                            onClick={() =>
                              setOpenQuoteChartMenu((current) => (current === 'settings' ? null : 'settings'))
                            }
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M4 7h4" />
                              <path d="M14 7h6" />
                              <circle cx="11" cy="7" r="2.25" />
                              <path d="M4 17h7" />
                              <path d="M17 17h3" />
                              <circle cx="14" cy="17" r="2.25" />
                            </svg>
                          </button>
                          {chartSettingsMenu}
                        </div>
                      </div>
                    </div>

                    <div className="instrument-chart-plot-shell">
                    <svg
                      viewBox={`0 0 ${PRIMARY_CHART_GEOMETRY.width} ${PRIMARY_CHART_GEOMETRY.height}`}
                      className="instrument-line-chart"
                      style={{ cursor: timelineNoteCaptureMode || isPrimaryHoverActive ? 'crosshair' : 'default' }}
                      role="img"
                      aria-label="Interactive NAV chart"
                      onMouseMove={(event) =>
                        updateChartHoverFromPointer(event, PRIMARY_CHART_GEOMETRY, 'primary')
                      }
                      onClick={handlePrimaryChartClick}
                      onContextMenu={handlePrimaryChartContextMenu}
                      onMouseLeave={clearChartHover}
                    >
                      <defs>
                        <clipPath id={chartClipId}>
                          <rect
                            x={primaryHoverPlotBounds.left}
                            y={primaryHoverPlotBounds.top}
                            width={primaryHoverPlotBounds.width}
                            height={primaryHoverPlotBounds.height}
                          />
                        </clipPath>
                      </defs>
                      {chartBands.map((band) => (
                        <rect
                          key={`band-${band.x.toFixed(2)}`}
                          className="instrument-chart-band"
                          x={band.x}
                          y={PRIMARY_CHART_GEOMETRY.paddingTop}
                          width={band.width}
                          height={
                            PRIMARY_CHART_GEOMETRY.height -
                            PRIMARY_CHART_GEOMETRY.paddingTop -
                            PRIMARY_CHART_GEOMETRY.paddingBottom
                          }
                        />
                      ))}

                      {chartTickValues.map((tick, index) => {
                        const projectedY = projectChartValue(
                          effectiveChartScale === 'logarithmic' ? Math.log10(tick) : tick,
                          chartMin,
                          chartMax,
                          PRIMARY_CHART_GEOMETRY,
                        )
                        const isBottomTick = index === 0
                        return (
                          <g key={`y-${tick.toFixed(6)}`}>
                            <line
                              className={
                                isBottomTick
                                  ? 'instrument-gridline instrument-gridline-axis-stub instrument-gridline-emphasis'
                                  : 'instrument-gridline instrument-gridline-axis-stub'
                              }
                              x1="8"
                              y1={projectedY}
                              x2={String(getYAxisStubEndX(PRIMARY_CHART_GEOMETRY))}
                              y2={projectedY}
                            />
                            <line
                              className={
                                isBottomTick
                                  ? 'instrument-gridline instrument-gridline-emphasis'
                                  : 'instrument-gridline'
                              }
                              x1={String(PRIMARY_CHART_GEOMETRY.paddingLeft)}
                              y1={projectedY}
                              x2={String(PRIMARY_CHART_GEOMETRY.width - PRIMARY_CHART_GEOMETRY.paddingRight)}
                              y2={projectedY}
                            />
                            <text
                              className="instrument-y-axis-label"
                              x={String(getYAxisStubEndX(PRIMARY_CHART_GEOMETRY))}
                              y={getYAxisLabelTextY(projectedY, PRIMARY_CHART_GEOMETRY, isBottomTick ? 'above' : 'below')}
                            >
                              {formatAxisNumber(tick)}
                            </text>
                          </g>
                        )
                      })}

                      {chartTickDates.map((point) => {
                        const scaledPoint = scaledVisibleSeries.find((item) => item.date === point.date)
                        const projected = scaledPoint && projectChartPoint(
                          scaledPoint,
                          scaledVisibleSeries,
                          chartMin,
                          chartMax,
                          PRIMARY_CHART_GEOMETRY,
                        )
                        if (!projected) {
                          return null
                        }
                        return (
                          <g key={`x-${point.date}`}>
                            <text
                              className="instrument-x-axis-label"
                              x={projected.x}
                              y={PRIMARY_CHART_GEOMETRY.height - 8}
                            >
                              {formatChartAxisDate(point.date)}
                            </text>
                          </g>
                        )
                      })}

                      {chartDisplayStyle === 'mountain' ? <path d={chartAreaPath} className="instrument-line-area" /> : null}
                      {chartDisplayStyle !== 'dot' ? <path d={chartLinePath} className="instrument-line-path" /> : null}
                      {chartDisplayStyle === 'dot'
                        ? positionedChartPoints.map((point) => (
                            <circle
                              key={`dot-${point.date}`}
                              className="instrument-line-dot"
                              cx={point.x}
                              cy={point.y}
                              r="2.2"
                            />
                          ))
                        : null}
                      {scaledBenchmarkVisibleSeries.length > 1 ? (
                        <path d={benchmarkChartLinePath} className="instrument-line-path instrument-line-path-benchmark" />
                      ) : null}

                      {distributionMarkers.map((marker) => (
                        <g key={`dist-${marker.row.as_of_date}`}>
                          <circle
                            className="instrument-distribution-dot"
                            cx={marker.x}
                            cy={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 14}
                            r="7"
                          />
                          <text
                            className="instrument-distribution-label"
                            x={marker.x}
                            y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 10.5}
                          >
                            D
                          </text>
                        </g>
                      ))}
                      {timelineNoteMarkerGroups.map((group) => (
                        <g
                          key={`note-${group.anchorDate}`}
                          className="instrument-chart-note-marker"
                          onClick={(event) => {
                            event.stopPropagation()
                            setTimelineNoteViewAnchorDate(group.anchorDate)
                            setChartTimelineNoteContextMenu(null)
                          }}
                        >
                          <circle
                            className="instrument-chart-note-dot"
                            cx={group.x}
                            cy={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 34}
                            r="7"
                          />
                          <text
                            className="instrument-chart-note-label"
                            x={group.x}
                            y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 29.5}
                          >
                            ★
                          </text>
                          {group.notes.length > 1 ? (
                            <text
                              className="instrument-chart-note-count"
                              x={group.x + 8}
                              y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 38}
                            >
                              {group.notes.length}
                            </text>
                          ) : null}
                        </g>
                      ))}

                      {primaryHoverGuideX != null ? (
                        <g clipPath={`url(#${chartClipId})`}>
                          <line
                            className="instrument-hover-line"
                            x1={primaryHoverGuideX}
                            y1={String(primaryHoverPlotBounds.top)}
                            x2={primaryHoverGuideX}
                            y2={String(primaryHoverPlotBounds.bottom)}
                          />
                          {isPrimaryHoverActive && chartHoverCursor ? (
                            <line
                              className="instrument-hover-line"
                              x1={String(primaryHoverPlotBounds.left)}
                              y1={chartHoverCursor.y}
                              x2={String(primaryHoverPlotBounds.right)}
                              y2={chartHoverCursor.y}
                            />
                          ) : null}
                          {isPrimaryHoverActive && hoveredChartPoint ? (
                            <circle
                              className="instrument-hover-point"
                              cx={hoveredChartPoint.x}
                              cy={hoveredChartPoint.y}
                              r="4"
                            />
                          ) : null}
                          {isPrimaryHoverActive && hoveredBenchmarkPoint ? (
                            <circle
                              className="instrument-hover-point instrument-hover-point-benchmark"
                              cx={hoveredBenchmarkPoint.x}
                              cy={hoveredBenchmarkPoint.y}
                              r="4"
                            />
                          ) : null}
                        </g>
                      ) : null}

                      {latestChartValueTag && latestChartValueLabel ? (
                        <g className="instrument-value-tag instrument-value-tag-primary">
                          <path d={latestChartValueTag.path} />
                          <text
                            x={latestChartValueTag.textX}
                            y={latestChartValueTag.textY}
                          >
                            {latestChartValueLabel}
                          </text>
                        </g>
                      ) : null}
                      {latestBenchmarkChartValueTag && latestBenchmarkChartValueLabel ? (
                        <g className="instrument-value-tag instrument-value-tag-benchmark">
                          <path d={latestBenchmarkChartValueTag.path} />
                          <text
                            x={latestBenchmarkChartValueTag.textX}
                            y={latestBenchmarkChartValueTag.textY}
                          >
                            {latestBenchmarkChartValueLabel}
                          </text>
                        </g>
                      ) : null}
                    </svg>
                    {primaryTooltipAnchor && hoveredNavPoint ? (
                      <div className="instrument-chart-tooltip-layer">
                        <div className="instrument-chart-tooltip" style={primaryTooltipAnchor}>
                          <div className="instrument-chart-tooltip-date">
                            {formatDate(hoveredNavPoint.date)}
                          </div>
                          <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                            <span className="instrument-chart-tooltip-series-label">
                              <i className="instrument-chart-tooltip-swatch" />
                                {chartSeriesBasisLabel}
                            </span>
                            <strong>{formatNumber(hoverNavValue, 4)}</strong>
                          </div>
                          {selectedBenchmark && hoverBenchmarkValue != null ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}
                              </span>
                              <strong>{formatNumber(hoverBenchmarkValue, 4)}</strong>
                            </div>
                          ) : null}
                          {showDrawdownPanel ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                Drawdown
                              </span>
                              <strong>{formatPercent(hoverDrawdownValue)}</strong>
                            </div>
                          ) : null}
                          {selectedBenchmark && hoverBenchmarkDrawdownValue != null && showDrawdownPanel ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name} Drawdown
                              </span>
                              <strong>{formatPercent(hoverBenchmarkDrawdownValue)}</strong>
                            </div>
                          ) : null}
                          {hoveredDistribution ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-event">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                Dividend
                              </span>
                              <strong>{formatNumber(hoveredDistribution.distribution_amount, 4)}</strong>
                            </div>
                          ) : null}
                          {hoveredTimelineNoteGroup?.notes.length ? (
                            <div className="instrument-chart-tooltip-events">
                              {hoveredTimelineNoteGroup.notes.slice(0, 3).map((note) => (
                                <div
                                  key={note.note_id}
                                  className="instrument-chart-tooltip-row instrument-chart-tooltip-row-note"
                                >
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {note.title || note.summary || 'Research note'}
                                  </span>
                                  <strong>{formatTimelineNoteImportance(note.importance)}</strong>
                                </div>
                              ))}
                              {hoveredTimelineNoteGroup.notes.length > 3 ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-note instrument-chart-tooltip-row-note-more">
                                  <span>{`+${hoveredTimelineNoteGroup.notes.length - 3} more notes`}</span>
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                    </div>

                    {chartTimelineNoteContextMenu ? (
                      <div
                        ref={timelineNoteContextMenuRef}
                        className="instrument-chart-context-menu"
                        style={timelineNoteContextMenuStyle}
                      >
                        <button
                          type="button"
                          className="instrument-chart-context-menu-item"
                          onClick={() => openTimelineNoteEditor(chartTimelineNoteContextMenu.anchorDate)}
                        >
                          Add note at {formatDate(chartTimelineNoteContextMenu.anchorDate)}
                        </button>
                        {timelineNoteMarkerGroups.some(
                          (group) => group.anchorDate === chartTimelineNoteContextMenu.anchorDate,
                        ) ? (
                          <button
                            type="button"
                            className="instrument-chart-context-menu-item"
                            onClick={() => {
                              setTimelineNoteViewAnchorDate(chartTimelineNoteContextMenu.anchorDate)
                              setChartTimelineNoteContextMenu(null)
                            }}
                          >
                            View notes on this date
                          </button>
                        ) : null}
                      </div>
                    ) : null}

                    {selectedTimelineNoteGroup?.notes.length ? (
                      <div className="instrument-chart-note-panel">
                        <div className="instrument-chart-note-panel-header">
                          <div>
                            <strong>Research Notes</strong>
                            <span>{formatDate(selectedTimelineNoteGroup.anchorDate)}</span>
                          </div>
                          <div className="toolbar">
                            <button
                              type="button"
                              onClick={() => openTimelineNoteEditor(selectedTimelineNoteGroup.anchorDate)}
                            >
                              Add Note
                            </button>
                            <button
                              type="button"
                              onClick={() => setTimelineNoteViewAnchorDate(null)}
                            >
                              Close
                            </button>
                          </div>
                        </div>
                        <div className="table-shell instrument-research-table-shell">
                          <table className="terminal-table terminal-table-compact instrument-research-table instrument-chart-note-table">
                            <thead>
                              <tr>
                                <th>Date</th>
                                <th>Importance</th>
                                <th>Title</th>
                                <th>Summary</th>
                                <th className="instrument-table-action-col" aria-label="Note actions" />
                              </tr>
                            </thead>
                            <tbody>
                              {selectedTimelineNoteGroup.notes.map((note) => (
                                <tr key={note.note_id}>
                                  <td>{formatDate(note.note_date)}</td>
                                  <td>{formatTimelineNoteImportance(note.importance)}</td>
                                  <td>{note.title || 'Untitled'}</td>
                                  <td>{note.summary || note.body || '—'}</td>
                                  <td className="instrument-table-row-action-cell">
                                    <div className="instrument-table-inline-actions instrument-table-inline-actions-compact">
                                      <button
                                        type="button"
                                        className="table-action"
                                        onClick={() => openTimelineNoteEditor(note.note_date, note)}
                                      >
                                        Edit
                                      </button>
                                      <button
                                        type="button"
                                        className="table-action"
                                        onClick={() => void handleDeleteTimelineNote(note.note_id)}
                                        disabled={savingSection === 'timeline_note'}
                                      >
                                        Delete
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : null}

                    {showDrawdownPanel ? (
                      <div className="instrument-drawdown-shell">
                        <div className="instrument-drawdown-header">
                          <span>Drawdown</span>
                          <div className="instrument-drawdown-header-values">
                            <strong className="instrument-drawdown-header-value instrument-drawdown-header-value-primary">
                              {hoverDrawdownValue == null ? '—' : `${formatPercent(hoverDrawdownValue)}`}
                            </strong>
                            {selectedBenchmark ? (
                              <strong className="instrument-drawdown-header-value instrument-drawdown-header-value-benchmark">
                                {hoverBenchmarkDrawdownValue == null ? '—' : `${formatPercent(hoverBenchmarkDrawdownValue)}`}
                              </strong>
                            ) : null}
                          </div>
                        </div>
                        <div className="instrument-chart-plot-shell instrument-chart-plot-shell-drawdown">
                        <svg
                          viewBox={`0 0 ${DRAWDOWN_CHART_GEOMETRY.width} ${DRAWDOWN_CHART_GEOMETRY.height}`}
                          className="instrument-drawdown-chart"
                          style={{ cursor: isDrawdownHoverActive ? 'crosshair' : 'default' }}
                          role="img"
                          aria-label="Drawdown chart"
                          onMouseMove={(event) =>
                            updateChartHoverFromPointer(event, DRAWDOWN_CHART_GEOMETRY, 'drawdown')
                          }
                          onMouseLeave={clearChartHover}
                        >
                          <defs>
                            <clipPath id={drawdownClipId}>
                              <rect
                                x={drawdownHoverPlotBounds.left}
                                y={drawdownHoverPlotBounds.top}
                                width={drawdownHoverPlotBounds.width}
                                height={drawdownHoverPlotBounds.height}
                              />
                            </clipPath>
                          </defs>
                          {drawdownBands.map((band) => (
                            <rect
                              key={`drawdown-band-${band.x.toFixed(2)}`}
                              className="instrument-chart-band"
                              x={band.x}
                              y={DRAWDOWN_CHART_GEOMETRY.paddingTop}
                              width={band.width}
                              height={
                                DRAWDOWN_CHART_GEOMETRY.height -
                                DRAWDOWN_CHART_GEOMETRY.paddingTop -
                                DRAWDOWN_CHART_GEOMETRY.paddingBottom
                              }
                            />
                          ))}
                          {drawdownTickValues.map((tick, index) => {
                            const projectedY = projectChartValue(
                              tick,
                              drawdownMin,
                              drawdownMax,
                              DRAWDOWN_CHART_GEOMETRY,
                            )
                            const isBottomTick = index === 0
                            return (
                              <g key={`dd-${tick.toFixed(6)}`}>
                                <line
                                  className={
                                    isBottomTick
                                      ? 'instrument-gridline instrument-gridline-axis-stub instrument-gridline-emphasis'
                                      : 'instrument-gridline instrument-gridline-axis-stub'
                                  }
                                  x1="8"
                                  y1={projectedY}
                                  x2={String(getYAxisStubEndX(DRAWDOWN_CHART_GEOMETRY))}
                                  y2={projectedY}
                                />
                                <line
                                  className={
                                    isBottomTick
                                      ? 'instrument-gridline instrument-gridline-emphasis'
                                      : 'instrument-gridline'
                                  }
                                  x1={String(DRAWDOWN_CHART_GEOMETRY.paddingLeft)}
                                  y1={projectedY}
                                  x2={String(DRAWDOWN_CHART_GEOMETRY.width - DRAWDOWN_CHART_GEOMETRY.paddingRight)}
                                  y2={projectedY}
                                />
                                <text
                                  className="instrument-y-axis-label"
                                  x={String(getYAxisStubEndX(DRAWDOWN_CHART_GEOMETRY))}
                                  y={getYAxisLabelTextY(
                                    projectedY,
                                    DRAWDOWN_CHART_GEOMETRY,
                                    isBottomTick ? 'above' : 'below',
                                  )}
                                >
                                  {formatPercent(tick, 1)}
                                </text>
                              </g>
                            )
                          })}
                          <path d={drawdownAreaPath} className="instrument-drawdown-area" />
                          <path d={drawdownLinePath} className="instrument-drawdown-line" />
                          {benchmarkDrawdownSeries.length > 1 ? (
                            <path
                              d={benchmarkDrawdownLinePath}
                              className="instrument-drawdown-line instrument-drawdown-line-benchmark"
                            />
                          ) : null}
                          {drawdownHoverGuideX != null ? (
                            <g clipPath={`url(#${drawdownClipId})`}>
                              <line
                                className="instrument-hover-line instrument-hover-line-drawdown"
                                x1={drawdownHoverGuideX}
                                y1={String(drawdownHoverPlotBounds.top)}
                                x2={drawdownHoverGuideX}
                                y2={String(drawdownHoverPlotBounds.bottom)}
                              />
                              {isDrawdownHoverActive && chartHoverCursor ? (
                                <line
                                  className="instrument-hover-line instrument-hover-line-drawdown"
                                  x1={String(drawdownHoverPlotBounds.left)}
                                  y1={chartHoverCursor.y}
                                  x2={String(drawdownHoverPlotBounds.right)}
                                  y2={chartHoverCursor.y}
                                />
                              ) : null}
                              {isDrawdownHoverActive && hoveredDrawdownPoint ? (
                                <circle
                                  className="instrument-hover-point instrument-hover-point-drawdown"
                                  cx={hoveredDrawdownPoint.x}
                                  cy={hoveredDrawdownPoint.y}
                                  r="3.5"
                                />
                              ) : null}
                              {isDrawdownHoverActive && hoveredBenchmarkDrawdownPoint ? (
                                <circle
                                  className="instrument-hover-point instrument-hover-point-benchmark"
                                  cx={hoveredBenchmarkDrawdownPoint.x}
                                  cy={hoveredBenchmarkDrawdownPoint.y}
                                  r="3.5"
                                />
                              ) : null}
                            </g>
                          ) : null}

                          {latestDrawdownValueTag && latestDrawdownValueLabel ? (
                            <g className="instrument-value-tag instrument-value-tag-drawdown">
                              <path d={latestDrawdownValueTag.path} />
                              <text
                                x={latestDrawdownValueTag.textX}
                                y={latestDrawdownValueTag.textY}
                              >
                                {latestDrawdownValueLabel}
                              </text>
                            </g>
                          ) : null}
                          {latestBenchmarkDrawdownValueTag && latestBenchmarkDrawdownValueLabel ? (
                            <g className="instrument-value-tag instrument-value-tag-benchmark">
                              <path d={latestBenchmarkDrawdownValueTag.path} />
                              <text
                                x={latestBenchmarkDrawdownValueTag.textX}
                                y={latestBenchmarkDrawdownValueTag.textY}
                              >
                                {latestBenchmarkDrawdownValueLabel}
                              </text>
                            </g>
                          ) : null}
                        </svg>
                        {drawdownTooltipAnchor && hoveredNavPoint ? (
                          <div className="instrument-chart-tooltip-layer">
                            <div className="instrument-chart-tooltip" style={drawdownTooltipAnchor}>
                              <div className="instrument-chart-tooltip-date">
                                {formatDate(hoveredNavPoint.date)}
                              </div>
                              <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                                <span className="instrument-chart-tooltip-series-label">
                                  <i className="instrument-chart-tooltip-swatch" />
                                    {chartSeriesBasisLabel}
                                </span>
                                <strong>{formatNumber(hoverNavValue, 4)}</strong>
                              </div>
                              {selectedBenchmark && hoverBenchmarkValue != null ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}
                                  </span>
                                  <strong>{formatNumber(hoverBenchmarkValue, 4)}</strong>
                                </div>
                              ) : null}
                              {showDrawdownPanel ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    Drawdown
                                  </span>
                                  <strong>{formatPercent(hoverDrawdownValue)}</strong>
                                </div>
                              ) : null}
                              {selectedBenchmark && hoverBenchmarkDrawdownValue != null && showDrawdownPanel ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name} Drawdown
                                  </span>
                                  <strong>{formatPercent(hoverBenchmarkDrawdownValue)}</strong>
                                </div>
                              ) : null}
                              {hoveredDistribution ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-event">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    Dividend
                                  </span>
                                  <strong>{formatNumber(hoveredDistribution.distribution_amount, 4)}</strong>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                        </div>
                      </div>
                    ) : null}
                    {canUseZoom ? (
                      <div className="instrument-chart-zoom">
                        <div className="instrument-chart-zoom-meta">
                          <span>Period</span>
                          <strong>
                            {formatDate(effectiveStartDate)} - {formatDate(effectiveEndDate)}
                          </strong>
                        </div>
                        <div className="instrument-chart-zoom-track">
                          <div
                            className="instrument-chart-zoom-selection"
                            style={{
                              left: `${zoomSelectionLeftPct}%`,
                              right: `${zoomSelectionRightPct}%`,
                            }}
                          />
                          <input
                            type="range"
                            min={0}
                            max={zoomMaxIndex}
                            value={zoomStartIndex}
                            aria-label="Chart period start"
                            onChange={(event) => {
                              const nextIndex = Math.min(Number(event.target.value), zoomEndIndex - 1)
                              const nextPoint = navBasisSeries[Math.max(nextIndex, 0)]
                              if (nextPoint) {
                                setChartRange('CUSTOM')
                                setChartStartDate(nextPoint.date)
                              }
                            }}
                          />
                          <input
                            type="range"
                            min={0}
                            max={zoomMaxIndex}
                            value={zoomEndIndex}
                            aria-label="Chart period end"
                            onChange={(event) => {
                              const nextIndex = Math.max(Number(event.target.value), zoomStartIndex + 1)
                              const nextPoint = navBasisSeries[Math.min(nextIndex, zoomMaxIndex)]
                              if (nextPoint) {
                                setChartRange('CUSTOM')
                                setChartEndDate(nextPoint.date)
                              }
                            }}
                          />
                        </div>
                      </div>
                    ) : null}
                    </>
                  ) : (
                    <div className="instrument-placeholder">
                      {navSeries.rows.length
                        ? `${quoteBasisLabel} is unavailable for the current currency or date window. Switch Data Type, Currency, or range.`
                        : 'No NAV history is available yet. Add shared market data in Database Dashboard to materialize the quote curve.'}
                    </div>
                  )}
                </div>
              </div>

            </div>
          </section>

          {timelineNoteDraft ? (
            <div
              className="instrument-modal-backdrop"
              onClick={() => setTimelineNoteDraft(null)}
            >
              <div
                className="instrument-modal instrument-timeline-note-modal"
                role="dialog"
                aria-modal="true"
                aria-label="Timeline Note"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="instrument-modal-header">
                  <div>
                    <div className="panel-title">Research Note</div>
                    <div className="instrument-quote-source-title">
                      Anchored to {formatDate(timelineNoteDraft.note_date)}
                    </div>
                  </div>
                  <div className="toolbar">
                    {timelineNotes.some((note) => note.note_id === timelineNoteDraft.note_id) ? (
                      <button
                        type="button"
                        onClick={() => void handleDeleteTimelineNote(timelineNoteDraft.note_id)}
                        disabled={savingSection === 'timeline_note'}
                      >
                        Delete
                      </button>
                    ) : null}
                    <button type="button" onClick={() => setTimelineNoteDraft(null)}>
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSaveTimelineNote()}
                      disabled={savingSection === 'timeline_note'}
                    >
                      {savingSection === 'timeline_note' ? 'Saving...' : 'Save Note'}
                    </button>
                  </div>
                </div>
                <div className="form-grid form-grid-2 instrument-quote-source-grid">
                  <label className="form-field">
                    <span>Date</span>
                    <input
                      type="date"
                      value={timelineNoteDraft.note_date}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                note_date: event.target.value,
                              }
                            : current,
                        )
                      }
                    />
                  </label>
                  <label className="form-field">
                    <span>Importance</span>
                    <select
                      value={timelineNoteDraft.importance}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                importance: parseTimelineNoteImportance(event.target.value),
                              }
                            : current,
                        )
                      }
                    >
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                    </select>
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Title</span>
                    <input
                      value={timelineNoteDraft.title}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                title: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Brief headline for what mattered on this date"
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Summary</span>
                    <textarea
                      rows={3}
                      value={timelineNoteDraft.summary}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                summary: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Short takeaway shown in the chart tooltip."
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Body</span>
                    <textarea
                      rows={6}
                      value={timelineNoteDraft.body}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                body: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Longer context, supporting evidence, or follow-up items."
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Tags</span>
                    <input
                      value={timelineNoteDraft.tagsText}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                tagsText: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="event, manager change, liquidity, drawdown"
                    />
                  </label>
                </div>
              </div>
            </div>
          ) : null}

        </>
      ) : null}

      {activeTab === 'performance' ? (
        <section className="panel instrument-performance-shell">
          <div className="instrument-price-topline" />

          <section className="instrument-performance-section instrument-performance-section-metrics">
            <div className="instrument-performance-section-header">
              <div>
                <div className="panel-title">Performance</div>
                <div className="instrument-section-title">Metrics Matrix</div>
              </div>
              <div className="instrument-performance-matrix-controls">
                <div className="instrument-performance-view-toggle" role="group" aria-label="Metrics matrix view">
                  {PERFORMANCE_MATRIX_MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={option.value === activePerformanceMatrixMode ? 'instrument-performance-toggle-active' : undefined}
                      disabled={option.value !== 'values' && !peerComparison}
                      onClick={() => setPerformanceMatrixMode(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                {activePerformanceMatrixMode === 'values' ? (
                  <div className="instrument-chart-compare instrument-performance-benchmark-select">
                    <div className="instrument-chart-compare-select-wrap">
                      <select
                        aria-label="Performance benchmark"
                        value={metricBenchmarkFundId}
                        onChange={(event) => setMetricBenchmarkFundId(event.target.value)}
                      >
                        <option value="">Compare...</option>
                        {benchmarkOptions.map((item) => (
                          <option key={item.fund_id} value={item.fund_id}>
                            {item.ticker_or_isin ? `${item.ticker_or_isin} · ${item.fund_name}` : item.fund_name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                ) : (
                  <div className="instrument-peer-context">
                    <span>{peerComparisonPathLabel}</span>
                    {peerComparison?.sample_count ? (
                      <strong>n={formatNumber(peerComparison.sample_count, 0)}</strong>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
            <div className="instrument-performance-section-body">
              <div className="table-shell instrument-performance-table-shell">
                <table className="terminal-table terminal-table-compact instrument-metrics-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      {PERFORMANCE_METRIC_PERIODS.map((period) => (
                        <th key={period.key}>{period.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {performanceMetricMatrixRows.map((row) => (
                      <tr
                        key={row.key}
                        className={
                          row.supportsBenchmark
                            ? 'instrument-metrics-row-with-note'
                            : 'instrument-metrics-row-single'
                        }
                      >
                        <td className="instrument-metrics-row-label">{row.label}</td>
                        {row.cells.map((cell, index) => (
                          <td key={`${row.key}-${PERFORMANCE_METRIC_PERIODS[index]?.key || index}`}>
                            <div
                              className={`instrument-metrics-cell${
                                'tone' in cell ? ` instrument-metrics-cell-${cell.tone}` : ''
                              }`}
                            >
                              <strong>{cell.primary}</strong>
                              {cell.secondary ? (
                                <span className="instrument-metrics-cell-note">{cell.secondary}</span>
                              ) : null}
                            </div>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-performance-section instrument-performance-section-monthly">
            <div className="instrument-performance-section-header">
              <div>
                <div className="panel-title">Performance</div>
                <div className="instrument-section-title">Monthly Return Matrix</div>
              </div>
            </div>
            <div className="instrument-performance-section-body">
              {monthlyReturnMatrixRows.length ? (
                <div className="table-shell instrument-performance-table-shell">
                  <table className="terminal-table terminal-table-compact instrument-heatmap-table">
                    <thead>
                      <tr>
                        <th>Year</th>
                        {MONTH_SHORT_LABELS.map((label) => (
                          <th key={label}>{label}</th>
                        ))}
                        <th>Yearly / YTD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyReturnMatrixRows.map((row) => (
                        <tr key={row.year}>
                          <td className="instrument-heatmap-row-label">{row.year}</td>
                          {row.months.map((value, index) => (
                            <td
                              key={`${row.year}-${MONTH_SHORT_LABELS[index]}`}
                              className={`instrument-heatmap-cell${value == null ? ' instrument-heatmap-cell-empty' : ''}`}
                              style={getHeatmapCellStyle(value, monthlyReturnMatrixMaxAbs)}
                            >
                              {value == null ? '—' : formatPercent(value, 1)}
                            </td>
                          ))}
                          <td
                            className={`instrument-heatmap-cell${row.ytd == null ? ' instrument-heatmap-cell-empty' : ''}`}
                            style={getHeatmapCellStyle(row.ytd, monthlyReturnMatrixMaxAbs)}
                          >
                            {row.ytd == null ? '—' : formatPercent(row.ytd, 1)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="instrument-fallback-block">
                  <div className="instrument-fallback-copy">
                    Monthly return matrix will appear once month-end NAV history is available.
                  </div>
                </div>
              )}
            </div>
          </section>

          <section className="instrument-performance-section instrument-performance-section-rolling">
            <div className="instrument-performance-section-header">
              <div>
                <div className="panel-title">Performance</div>
                <div className="instrument-section-title">Rolling Return</div>
              </div>
              <div className="toolbar instrument-performance-window-toolbar">
                {ROLLING_RETURN_WINDOW_OPTIONS.map((option) => (
                  <button
                    key={option.months}
                    type="button"
                    className={option.months === rollingReturnWindowMonths ? 'instrument-performance-toggle-active' : undefined}
                    onClick={() => setRollingReturnWindowMonths(option.months)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="instrument-performance-section-body">
              {rollingReturnSeries.length > 1 ? (
                <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                  <svg
                    viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                    className="instrument-line-chart"
                    role="img"
                    aria-label="Rolling return chart"
                  >
                    {rollingReturnTickValues.map((tick) => {
                      const y = projectChartValue(
                        tick,
                        rollingReturnBounds.min,
                        rollingReturnBounds.max,
                        SECONDARY_SERIES_GEOMETRY,
                      )
                      return (
                        <g key={`rolling-return-y-${tick.toFixed(4)}`}>
                          <line
                            x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                            y1={String(y)}
                            x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                            y2={String(y)}
                            className="instrument-gridline"
                          />
                          <text
                            className="instrument-y-axis-label"
                            x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                            y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                          >
                            {formatPercent(tick)}
                          </text>
                        </g>
                      )
                    })}
                    {rollingReturnTickDates.map((point) => {
                      const projected = projectChartPoint(
                        point,
                        rollingReturnSeries,
                        rollingReturnBounds.min,
                        rollingReturnBounds.max,
                        SECONDARY_SERIES_GEOMETRY,
                      )
                      return (
                        <text
                          key={`rolling-return-x-${point.date}`}
                          className="instrument-x-axis-label"
                          x={projected.x}
                          y={SECONDARY_SERIES_GEOMETRY.height - 8}
                        >
                          {formatMonthBucket(getMonthBucket(point.date))}
                        </text>
                      )
                    })}
                    <path d={rollingReturnAreaPath} className="instrument-line-area" />
                    <path d={rollingReturnLinePath} className="instrument-line-path" />
                    {rollingBenchmarkReturnSeries.length > 1 ? (
                      <path
                        d={rollingBenchmarkReturnLinePath}
                        className="instrument-line-path instrument-line-path-benchmark"
                      />
                    ) : null}
                  </svg>
                </div>
              ) : (
                <div className="instrument-fallback-block">
                  <div className="instrument-fallback-copy">
                    Rolling return will appear once enough month-end NAV history is available for the selected window.
                  </div>
                </div>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'risk' ? (
        <section className="panel instrument-risk-shell">
          <div className="instrument-price-topline" />
          <div className="instrument-risk-page-header">
            <div>
              <div className="panel-title">Risk</div>
              <div className="instrument-section-title">Risk</div>
            </div>
          </div>

          <section className="instrument-risk-section">
            <div className="instrument-risk-section-header">
              <div>
                <div className="panel-title">Risk</div>
                <div className="instrument-section-title">Risk Summary</div>
              </div>
            </div>
            <div className="instrument-risk-section-body">
              <div className="instrument-risk-summary-grid">
                <div className="table-shell instrument-risk-table-shell">
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <tbody>
                      {riskSummaryRows.map((row) => (
                        <tr key={row.label}>
                          <td>{row.label}</td>
                          <td className="instrument-data-table-value">{row.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="table-shell instrument-risk-table-shell">
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <tbody>
                      {currentRiskWatchRows.map((row) => (
                        <tr key={row.label}>
                          <td>{row.label}</td>
                          <td className="instrument-data-table-value">{row.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </section>

          <section className="instrument-risk-section">
            <div className="instrument-risk-section-header">
              <div>
                <div className="panel-title">Risk</div>
                <div className="instrument-section-title">Risk Structure</div>
              </div>
            </div>
            <div className="instrument-risk-section-body">
              <div className="table-shell instrument-risk-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Characteristic</th>
                      <th>Current Reading</th>
                      <th>Interpretation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {riskStructureRows.map((row) => (
                      <tr key={row.characteristic}>
                        <td>{row.characteristic}</td>
                        <td>{row.reading}</td>
                        <td>{row.interpretation}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-shell instrument-performance-table-shell">
                <table className="terminal-table terminal-table-compact instrument-metrics-table instrument-metrics-table-risk">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      {riskMatrixSnapshots.map((period) => (
                        <th key={`risk-metric-period-${period.key}`}>{period.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {riskMatrixRows.map((row) => (
                      <tr
                        key={row.label}
                        className={
                          row.supportsBenchmark
                            ? 'instrument-metrics-row-with-note'
                            : 'instrument-metrics-row-single'
                        }
                      >
                        <td className="instrument-metrics-row-label">{row.label}</td>
                        {row.cells.map((cell, index) => (
                          <td key={`${row.label}-${riskMatrixSnapshots[index]?.key || index}`}>
                            <div className="instrument-metrics-cell">
                              <strong>{cell.primary}</strong>
                              {cell.secondary ? (
                                <span className="instrument-metrics-cell-note">{cell.secondary}</span>
                              ) : null}
                            </div>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-risk-section">
            <div className="instrument-risk-section-header">
              <div>
                <div className="panel-title">Risk</div>
                <div className="instrument-section-title">Drawdown &amp; Recovery</div>
              </div>
            </div>
            <div className="instrument-risk-section-body">
              <div className="instrument-risk-visual-grid">
                <section className="instrument-risk-series-block">
                  <div className="instrument-risk-visual-column-header">
                    <div className="panel-title">Risk</div>
                    <div className="instrument-section-title">Drawdown Profile</div>
                  </div>
                  {riskProfileSeries.length > 1 ? (
                    <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                      <svg
                        viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                        className="instrument-drawdown-chart"
                        role="img"
                        aria-label="Risk drawdown profile chart"
                      >
                        {riskProfileTickValues.map((tick) => {
                          const y = projectChartValue(
                            tick,
                            riskProfileBounds.min,
                            riskProfileBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <g key={`risk-profile-y-${tick.toFixed(4)}`}>
                              <line
                                x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                                y1={String(y)}
                                x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                                y2={String(y)}
                                className="instrument-gridline"
                              />
                              <text
                                className="instrument-y-axis-label"
                                x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                                y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                              >
                                {formatPercent(tick)}
                              </text>
                            </g>
                          )
                        })}
                        {riskProfileTickDates.map((point) => {
                          const projected = projectChartPoint(
                            point,
                            riskProfileSeries,
                            riskProfileBounds.min,
                            riskProfileBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <text
                              key={`risk-profile-x-${point.date}`}
                              className="instrument-x-axis-label"
                              x={projected.x}
                              y={SECONDARY_SERIES_GEOMETRY.height - 8}
                            >
                              {formatChartAxisDate(point.date)}
                            </text>
                          )
                        })}
                        <path d={riskProfileAreaPath} className="instrument-drawdown-area" />
                        <path d={riskProfileLinePath} className="instrument-drawdown-line" />
                      </svg>
                    </div>
                  ) : (
                    <div className="instrument-fallback-block">
                      <div className="instrument-fallback-copy">
                        Drawdown profile will appear once a continuous NAV history is available.
                      </div>
                    </div>
                  )}
                </section>

                <section className="instrument-risk-series-block">
                  <div className="instrument-risk-visual-column-header">
                    <div className="panel-title">Risk</div>
                    <div className="instrument-section-title">Monthly Drawdown</div>
                  </div>
                  {monthlyDrawdownSeries.length > 1 ? (
                    <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                      <svg
                        viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                        className="instrument-drawdown-chart"
                        role="img"
                        aria-label="Monthly drawdown chart"
                      >
                        {monthlyDrawdownTickValues.map((tick) => {
                          const y = projectChartValue(
                            tick,
                            monthlyDrawdownBounds.min,
                            monthlyDrawdownBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <g key={`monthly-dd-y-${tick.toFixed(4)}`}>
                              <line
                                x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                                y1={String(y)}
                                x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                                y2={String(y)}
                                className="instrument-gridline"
                              />
                              <text
                                className="instrument-y-axis-label"
                                x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                                y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                              >
                                {formatPercent(tick)}
                              </text>
                            </g>
                          )
                        })}
                        {monthlyDrawdownTickDates.map((point) => {
                          const projected = projectChartPoint(
                            point,
                            monthlyDrawdownSeries,
                            monthlyDrawdownBounds.min,
                            monthlyDrawdownBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <text
                              key={`monthly-dd-x-${point.date}`}
                              className="instrument-x-axis-label"
                              x={projected.x}
                              y={SECONDARY_SERIES_GEOMETRY.height - 8}
                            >
                              {formatMonthBucket(getMonthBucket(point.date))}
                            </text>
                          )
                        })}
                        <path d={monthlyDrawdownAreaPath} className="instrument-drawdown-area" />
                        <path d={monthlyDrawdownLinePath} className="instrument-drawdown-line" />
                      </svg>
                    </div>
                  ) : (
                    <div className="instrument-fallback-block">
                      <div className="instrument-fallback-copy">
                        Monthly drawdown series will appear once enough month-level history is available.
                      </div>
                    </div>
                  )}
                </section>
              </div>

              <div className="table-shell instrument-risk-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <tbody>
                    {drawdownSummaryRows.map((row) => (
                      <tr key={row.label}>
                        <td>{row.label}</td>
                        <td className="instrument-data-table-value">{row.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-risk-section">
            <div className="instrument-risk-section-header">
              <div>
                <div className="panel-title">Risk</div>
                <div className="instrument-section-title">Risk Change &amp; Sensitivity</div>
              </div>
            </div>
            <div className="instrument-risk-section-body">
              <div className="instrument-risk-visual-grid">
                <section className="instrument-risk-series-block">
                  <div className="instrument-risk-visual-column-header">
                    <div className="panel-title">Risk</div>
                    <div className="instrument-section-title">Monthly Annualized Volatility</div>
                  </div>
                  {monthlyVolatilitySeries.length > 1 ? (
                    <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                      <svg
                        viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                        className="instrument-line-chart"
                        role="img"
                        aria-label="Monthly annualized volatility chart"
                      >
                        {monthlyVolatilityTickValues.map((tick) => {
                          const y = projectChartValue(
                            tick,
                            monthlyVolatilityBounds.min,
                            monthlyVolatilityBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <g key={`monthly-vol-y-${tick.toFixed(4)}`}>
                              <line
                                x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                                y1={String(y)}
                                x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                                y2={String(y)}
                                className="instrument-gridline"
                              />
                              <text
                                className="instrument-y-axis-label"
                                x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                                y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                              >
                                {formatPercent(tick)}
                              </text>
                            </g>
                          )
                        })}
                        {monthlyVolatilityTickDates.map((point) => {
                          const projected = projectChartPoint(
                            point,
                            monthlyVolatilitySeries,
                            monthlyVolatilityBounds.min,
                            monthlyVolatilityBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <text
                              key={`monthly-vol-x-${point.date}`}
                              className="instrument-x-axis-label"
                              x={projected.x}
                              y={SECONDARY_SERIES_GEOMETRY.height - 8}
                            >
                              {formatMonthBucket(getMonthBucket(point.date))}
                            </text>
                          )
                        })}
                        <path d={monthlyVolatilityAreaPath} className="instrument-line-area" />
                        <path d={monthlyVolatilityLinePath} className="instrument-line-path" />
                      </svg>
                    </div>
                  ) : (
                    <div className="instrument-fallback-block">
                      <div className="instrument-fallback-copy">
                        Monthly annualized volatility will appear once enough NAV observations are available.
                      </div>
                    </div>
                  )}
                </section>

                <section className="instrument-risk-series-block">
                  <div className="instrument-risk-visual-column-header">
                    <div className="panel-title">Risk</div>
                    <div className="instrument-section-title">Rolling Annualized Volatility</div>
                  </div>
                  {rollingVolatilitySeries.length > 1 ? (
                    <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                      <svg
                        viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                        className="instrument-line-chart"
                        role="img"
                        aria-label="Rolling annualized volatility chart"
                      >
                        {rollingVolatilityTickValues.map((tick) => {
                          const y = projectChartValue(
                            tick,
                            rollingVolatilityBounds.min,
                            rollingVolatilityBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <g key={`rolling-vol-y-${tick.toFixed(4)}`}>
                              <line
                                x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                                y1={String(y)}
                                x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                                y2={String(y)}
                                className="instrument-gridline"
                              />
                              <text
                                className="instrument-y-axis-label"
                                x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                                y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                              >
                                {formatPercent(tick)}
                              </text>
                            </g>
                          )
                        })}
                        {rollingVolatilityTickDates.map((point) => {
                          const projected = projectChartPoint(
                            point,
                            rollingVolatilitySeries,
                            rollingVolatilityBounds.min,
                            rollingVolatilityBounds.max,
                            SECONDARY_SERIES_GEOMETRY,
                          )
                          return (
                            <text
                              key={`rolling-vol-x-${point.date}`}
                              className="instrument-x-axis-label"
                              x={projected.x}
                              y={SECONDARY_SERIES_GEOMETRY.height - 8}
                            >
                              {formatMonthBucket(getMonthBucket(point.date))}
                            </text>
                          )
                        })}
                        <path d={rollingVolatilityAreaPath} className="instrument-line-area" />
                        <path d={rollingVolatilityLinePath} className="instrument-line-path" />
                      </svg>
                    </div>
                  ) : (
                    <div className="instrument-fallback-block">
                      <div className="instrument-fallback-copy">
                        Rolling volatility will appear once at least 12 monthly return observations are available.
                      </div>
                    </div>
                  )}
                </section>
              </div>

              <section className="instrument-risk-series-block instrument-risk-series-block-wide">
                <div className="instrument-risk-visual-column-header">
                  <div className="panel-title">Risk</div>
                  <div className="instrument-section-title">Rolling Beta</div>
                </div>
                {rollingBetaSeries.length > 1 ? (
                  <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                    <svg
                      viewBox={`0 0 ${SECONDARY_SERIES_GEOMETRY.width} ${SECONDARY_SERIES_GEOMETRY.height}`}
                      className="instrument-line-chart"
                      role="img"
                      aria-label="Rolling beta chart"
                    >
                      {rollingBetaTickValues.map((tick) => {
                        const y = projectChartValue(
                          tick,
                          rollingBetaBounds.min,
                          rollingBetaBounds.max,
                          SECONDARY_SERIES_GEOMETRY,
                        )
                        return (
                          <g key={`rolling-beta-y-${tick.toFixed(4)}`}>
                            <line
                              x1={String(SECONDARY_SERIES_GEOMETRY.paddingLeft)}
                              y1={String(y)}
                              x2={String(SECONDARY_SERIES_GEOMETRY.width - SECONDARY_SERIES_GEOMETRY.paddingRight)}
                              y2={String(y)}
                              className="instrument-gridline"
                            />
                            <text
                              className="instrument-y-axis-label"
                              x={String(getYAxisStubEndX(SECONDARY_SERIES_GEOMETRY))}
                              y={getYAxisLabelTextY(y, SECONDARY_SERIES_GEOMETRY)}
                            >
                              {formatNumber(tick, 2)}
                            </text>
                          </g>
                        )
                      })}
                      {rollingBetaTickDates.map((point) => {
                        const projected = projectChartPoint(
                          point,
                          rollingBetaSeries,
                          rollingBetaBounds.min,
                          rollingBetaBounds.max,
                          SECONDARY_SERIES_GEOMETRY,
                        )
                        return (
                          <text
                            key={`rolling-beta-x-${point.date}`}
                            className="instrument-x-axis-label"
                            x={projected.x}
                            y={SECONDARY_SERIES_GEOMETRY.height - 8}
                          >
                            {formatMonthBucket(getMonthBucket(point.date))}
                          </text>
                        )
                      })}
                      <path d={rollingBetaAreaPath} className="instrument-line-area" />
                      <path d={rollingBetaLinePath} className="instrument-line-path" />
                    </svg>
                  </div>
                ) : (
                  <div className="instrument-fallback-block">
                    <div className="instrument-fallback-copy">
                      {selectedMetricBenchmark
                        ? 'Rolling beta will appear once at least 12 overlapping monthly return observations are available.'
                        : '—'}
                    </div>
                  </div>
                )}
              </section>

              <div className="table-shell instrument-risk-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Signal</th>
                      <th>Current</th>
                      <th>Baseline</th>
                      <th>Change</th>
                      <th>Watch</th>
                    </tr>
                  </thead>
                  <tbody>
                    {riskChangeRows.map((row) => (
                      <tr key={row.signal}>
                        <td>{row.signal}</td>
                        <td>{row.current}</td>
                        <td>{row.baseline}</td>
                        <td>{row.change}</td>
                        <td className="instrument-data-table-value">{row.watch}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-risk-section">
            <div className="instrument-risk-section-header">
              <div>
                <div className="panel-title">Risk</div>
                <div className="instrument-section-title">Peer Context</div>
              </div>
            </div>
            <div className="instrument-risk-section-body">
              {positionedRiskScatterRows.length ? (
                <div className="instrument-chart-plot-shell instrument-performance-visual-shell">
                  <svg
                    viewBox={`0 0 ${RISK_SCATTER_GEOMETRY.width} ${RISK_SCATTER_GEOMETRY.height}`}
                    className="instrument-risk-scatter"
                    role="img"
                    aria-label="Risk return scatter plot"
                  >
                    {riskYTickValues.map((tick) => {
                      const y =
                        RISK_SCATTER_GEOMETRY.height -
                        RISK_SCATTER_GEOMETRY.paddingBottom -
                        ((tick - riskReturnMin) / Math.max(riskReturnMax - riskReturnMin, 1)) * riskPlotHeight
                      return (
                        <g key={`risk-y-${tick}`}>
                          <line
                            x1={String(RISK_SCATTER_GEOMETRY.paddingLeft)}
                            y1={String(y)}
                            x2={String(RISK_SCATTER_GEOMETRY.width - RISK_SCATTER_GEOMETRY.paddingRight)}
                            y2={String(y)}
                            className="instrument-gridline"
                          />
                          <text x={8} y={y + 4} className="instrument-axis-label">
                            {formatNumber(tick, 2)}
                          </text>
                        </g>
                      )
                    })}
                    {riskXTickValues.map((tick) => {
                      const x =
                        RISK_SCATTER_GEOMETRY.paddingLeft +
                        ((tick - riskVolMin) / Math.max(riskVolMax - riskVolMin, 1)) * riskPlotWidth
                      return (
                        <g key={`risk-x-${tick}`}>
                          <line
                            x1={String(x)}
                            y1={String(RISK_SCATTER_GEOMETRY.paddingTop)}
                            x2={String(x)}
                            y2={String(RISK_SCATTER_GEOMETRY.height - RISK_SCATTER_GEOMETRY.paddingBottom)}
                            className="instrument-gridline instrument-gridline-vertical"
                          />
                          <text
                            x={x}
                            y={RISK_SCATTER_GEOMETRY.height - 8}
                            textAnchor="middle"
                            className="instrument-axis-label"
                          >
                            {formatNumber(tick, 2)}
                          </text>
                        </g>
                      )
                    })}
                    {positionedRiskScatterRows.map((row) => (
                      <g key={row.name}>
                        <circle
                          cx={row.x}
                          cy={row.y}
                          r={8}
                          className={`instrument-risk-point instrument-risk-point-${row.tone}`}
                        />
                        <text x={row.x + 12} y={row.y + 4} className="instrument-risk-point-label">
                          {row.name}
                        </text>
                      </g>
                    ))}
                  </svg>
                </div>
              ) : (
                <div className="instrument-fallback-block">
                  <div className="instrument-fallback-copy">
                    Risk map will appear once peer comparison coordinates are available.
                  </div>
                  {riskFallbackFacts.length ? (
                    <div className="stack-list stack-list-compact">
                      {riskFallbackFacts.map((row) => (
                        <div key={row.label} className="stack-item">
                          <span>{row.label}</span>
                          <strong>{row.value}</strong>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'price' ? (
        <section className="panel instrument-price-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-price-page-header">
            <div>
              <div className="panel-title">Price</div>
              <div className="instrument-section-title">Price</div>
            </div>
          </div>

          <section className="instrument-price-section">
            <div className="instrument-price-section-header">
              <div>
                <div className="panel-title">Price</div>
                <div className="instrument-section-title">Expense Ratios</div>
              </div>
              <div className="toolbar">
                {editingPriceSection === 'ter' ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceDraft(toEditablePriceDraft(bundle.price))
                        setEditingPriceSection(null)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSavePrice()}
                      disabled={savingSection === 'price'}
                    >
                      {savingSection === 'price' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setPriceDraft(toEditablePriceDraft(bundle.price))
                      setEditingPriceSection('ter')
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                    disabled={editingPriceSection !== null}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-price-section-body">
              <div className="table-shell instrument-price-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Adjusted Expense Ratio</td>
                      <td className="instrument-data-table-value">
                        {editingPriceSection === 'ter' && priceDraft ? (
                          <input
                            className="table-input"
                            value={getOverviewDraftValue(priceDraft, 'adjusted_expense_ratio')}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      overviewRows: current.overviewRows.map((row) =>
                                        row.key === 'adjusted_expense_ratio'
                                          ? { ...row, value: event.target.value }
                                          : row,
                                      ),
                                    }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          priceTerRows[0].value
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td>Reported Expense Ratio</td>
                      <td className="instrument-data-table-value">
                        {editingPriceSection === 'ter' && priceDraft ? (
                          <input
                            className="table-input"
                            value={getOverviewDraftValue(priceDraft, 'total_expense_ratio')}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      overviewRows: current.overviewRows.map((row) =>
                                        row.key === 'total_expense_ratio'
                                          ? { ...row, value: event.target.value }
                                          : row,
                                      ),
                                    }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          priceTerRows[1].value
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-price-section">
            <div className="instrument-price-section-header">
              <div>
                <div className="panel-title">Price</div>
                <div className="instrument-section-title">Fees &amp; Terms</div>
              </div>
              <div className="toolbar">
                {editingPriceSection === 'fees' ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceDraft(toEditablePriceDraft(bundle.price))
                        setEditingPriceSection(null)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSavePrice()}
                      disabled={savingSection === 'price'}
                    >
                      {savingSection === 'price' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setPriceDraft(toEditablePriceDraft(bundle.price))
                      setEditingPriceSection('fees')
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                    disabled={editingPriceSection !== null}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-price-section-body">
              <div className="table-shell instrument-price-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {priceFeeFieldDefinitions.map(([key, label], index) => (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="instrument-data-table-value">
                          {editingPriceSection === 'fees' && priceDraft ? (
                            <input
                              className="table-input"
                              value={getOverviewDraftValue(priceDraft, key)}
                              onChange={(event) =>
                                setPriceDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        overviewRows: current.overviewRows.map((row) =>
                                          row.key === key ? { ...row, value: event.target.value } : row,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          ) : (
                            feesAndTermsRows[index]?.value || '—'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="table-shell instrument-price-table-shell">
                {editingPriceSection === 'fees' && priceDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Fee Note</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {priceDraft.feeNoteRows.length ? (
                        priceDraft.feeNoteRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <input
                                className="table-input"
                                value={row.value}
                                onChange={(event) =>
                                  setPriceDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          feeNoteRows: current.feeNoteRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setPriceDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          feeNoteRows: current.feeNoteRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No fee notes yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : price.fee_notes.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Fee Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {price.fee_notes.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-price-placeholder">No fee notes yet.</div>
                )}
              </div>

              {editingPriceSection === 'fees' && priceDraft ? (
                <div className="editor-actions">
                  <button
                    type="button"
                    onClick={() =>
                      setPriceDraft((current) =>
                        current
                          ? {
                              ...current,
                              feeNoteRows: [...current.feeNoteRows, { id: makeRowId('price-fee-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Fee Note
                  </button>
                </div>
              ) : null}
            </div>
          </section>

          <section className="instrument-price-section">
            <div className="instrument-price-section-header">
              <div>
                <div className="panel-title">Price</div>
                <div className="instrument-section-title">Distribution Policy</div>
              </div>
              <div className="toolbar">
                {editingPriceSection === 'policy' ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceDraft(toEditablePriceDraft(bundle.price))
                        setEditingPriceSection(null)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSavePrice()}
                      disabled={savingSection === 'price'}
                    >
                      {savingSection === 'price' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setPriceDraft(toEditablePriceDraft(bundle.price))
                      setEditingPriceSection('policy')
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                    disabled={editingPriceSection !== null}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-price-section-body">
              <div className="table-shell instrument-price-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Distribution Policy</td>
                      <td className={editingPriceSection === 'policy' ? '' : 'instrument-data-table-prose'}>
                        {editingPriceSection === 'policy' && priceDraft ? (
                          <input
                            className="table-input"
                            value={priceDraft.distribution_policy}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current ? { ...current, distribution_policy: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          pricePolicyRows[0].value
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td>Policy Text</td>
                      <td className={editingPriceSection === 'policy' ? '' : 'instrument-data-table-prose'}>
                        {editingPriceSection === 'policy' && priceDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={5}
                            value={priceDraft.policy_text}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current ? { ...current, policy_text: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          pricePolicyRows[1].value
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="table-shell instrument-price-table-shell">
                {editingPriceSection === 'policy' && priceDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {priceDraft.noteRows.length ? (
                        priceDraft.noteRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setPriceDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setPriceDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No policy notes yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : price.notes.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {price.notes.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-price-placeholder">No policy notes yet.</div>
                )}
              </div>

              {editingPriceSection === 'policy' && priceDraft ? (
                <div className="editor-actions">
                  <button
                    type="button"
                    onClick={() =>
                      setPriceDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('price-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'exposure' ? (
        <section className="detail-grid">
          <section className="panel">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Exposure</div>
                <div className="instrument-section-title">Allocation</div>
              </div>
            </div>
            <div className="stack-list">
              {getRows(portfolio.allocation_blocks.asset_allocation).length ? (
                getRows(portfolio.allocation_blocks.asset_allocation).map((item, index) => (
                  <div key={`${String(item.name)}-${index}`} className="stack-item">
                    <span>{getString(item.name)}</span>
                    <strong>{formatPercent(getNumber(item.investment))}</strong>
                  </div>
                ))
              ) : (
                <div className="instrument-placeholder">
                  Exposure analytics will be rebuilt by product type later.
                </div>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Exposure</div>
                <div className="instrument-section-title">Style & Holdings</div>
              </div>
            </div>
            <div className="stack-list">
              {portfolio.style_box ? renderStackRows(portfolio.style_box) : null}
              {portfolio.holdings_summary ? renderStackRows(portfolio.holdings_summary) : null}
              {!portfolio.style_box && !portfolio.holdings_summary ? (
                <div className="instrument-placeholder">No exposure summary available.</div>
              ) : null}
            </div>
          </section>

          <section className="panel detail-span-2">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Holdings</div>
                <div className="instrument-section-title">Current Positions</div>
              </div>
            </div>
            <div className="table-shell">
              <table className="terminal-table terminal-table-compact">
                <thead>
                  <tr>
                    <th>Holding</th>
                    <th>Issuer</th>
                    <th>Weight</th>
                    <th>Market Value</th>
                    <th>Change</th>
                    <th>Maturity</th>
                    <th>Rating</th>
                    <th>Eff. Dur.</th>
                    <th>YTW</th>
                    <th>Sector</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.rows.length ? (
                    holdings.rows.slice(0, 12).map((row, index) => (
                      <tr key={`${String(row.holding_name)}-${index}`}>
                        <td>{getString(row.holding_name)}</td>
                        <td>{getString(row.issuer_name)}</td>
                        <td>{formatPercent(getNumber(row.portfolio_weight))}</td>
                        <td>{formatCompactCurrency(getNumber(row.market_value))}</td>
                        <td>{formatPercent(getNumber(row.share_change_pct))}</td>
                        <td>{formatDate(row.maturity_date)}</td>
                        <td>{getString(row.credit_rating)}</td>
                        <td>{formatNumber(getNumber(row.effective_duration))}</td>
                        <td>{formatPercent(getNumber(row.yield_to_worst))}</td>
                        <td>{getString(row.sector)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={10} className="empty-state">
                        No holdings available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'people' ? (
        <section className="instrument-people-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-people-page-header">
            <div>
              <div className="panel-title">People</div>
              <div className="instrument-section-title">People</div>
            </div>
            <div className="toolbar">
              {editingPeople ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setPeopleDraft(toEditablePeopleDraft(bundle.people))
                      setEditingPeople(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSavePeople()}
                    disabled={savingSection === 'people'}
                  >
                    {savingSection === 'people' ? 'Saving...' : 'Save People'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setPeopleDraft(toEditablePeopleDraft(bundle.people))
                    setEditingPeople(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit People
                </button>
              )}
            </div>
          </div>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Overview</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              overviewRows: [...current.overviewRows, { id: makeRowId('overview'), key: '', value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Field
                  </button>
                </div>
              ) : null}
            </div>

            <div className="instrument-people-section-body">
              <div className="detail-grid detail-grid-tight instrument-people-columns">
                <div>
                  <div className="table-shell instrument-people-table-shell">
                    <table className="terminal-table terminal-table-compact instrument-data-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {editingPeople && peopleDraft
                          ? PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => (
                              <tr key={field.key}>
                                <td>{field.label}</td>
                                <td className="instrument-data-table-value">
                                  <input
                                    className="table-input"
                                    type={field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'}
                                    step={
                                      field.key === 'number_of_managers' ? '1' : field.type === 'number' ? '0.1' : undefined
                                    }
                                    value={getPeopleOverviewDraftValue(peopleDraft, field.key)}
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: upsertKeyValueRows(
                                                current.overviewRows,
                                                field.key,
                                                event.target.value,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                              </tr>
                            ))
                          : peoplePrimaryOverviewRows.map((row) => (
                              <tr key={row.key}>
                                <td>{row.label}</td>
                                <td className="instrument-data-table-value">{row.value}</td>
                              </tr>
                            ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <div className="table-shell instrument-people-table-shell">
                    {editingPeople && peopleDraft ? (
                      <table className="terminal-table terminal-table-compact instrument-data-table">
                        <thead>
                          <tr>
                            <th>Field Key</th>
                            <th>Value</th>
                            <th className="instrument-table-action-col">Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peopleAdditionalDraftRows.length ? (
                            peopleAdditionalDraftRows.map((row) => (
                              <tr key={row.id}>
                                <td>
                                  <input
                                    className="table-input"
                                    value={row.key}
                                    placeholder="field_key"
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.map((item) =>
                                                item.id === row.id ? { ...item, key: event.target.value } : item,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                                <td>
                                  <input
                                    className="table-input"
                                    value={row.value}
                                    placeholder="value"
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.map((item) =>
                                                item.id === row.id ? { ...item, value: event.target.value } : item,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                                <td className="instrument-table-row-action-cell">
                                  <button
                                    type="button"
                                    className="table-action"
                                    onClick={() =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.filter((item) => item.id !== row.id),
                                            }
                                          : current,
                                      )
                                    }
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={3} className="empty-state">
                                No additional people fields.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    ) : peopleAdditionalOverviewRows.length ? (
                      <table className="terminal-table terminal-table-compact instrument-data-table">
                        <thead>
                          <tr>
                            <th>Field</th>
                            <th>Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peopleAdditionalOverviewRows.map((row) => (
                            <tr key={row.key}>
                              <td>{row.label}</td>
                              <td className="instrument-data-table-value">{row.value}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <div className="instrument-placeholder instrument-people-placeholder">
                        No additional people fields.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Management Team</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              teamRows: [
                                ...current.teamRows,
                                { id: makeRowId('team'), name: '', role: '', start_date: '' },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    Add Team Member
                  </button>
                </div>
              ) : null}
            </div>
            <div className="table-shell instrument-people-table-shell">
              <table className="terminal-table terminal-table-compact instrument-data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Start Date</th>
                    {editingPeople ? <th className="instrument-table-action-col">Action</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {editingPeople && peopleDraft ? (
                    peopleDraft.teamRows.length ? (
                      peopleDraft.teamRows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <input
                              className="table-input"
                              value={row.name}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, name: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td>
                            <input
                              className="table-input"
                              value={row.role}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, role: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td>
                            <input
                              className="table-input"
                              type="date"
                              value={row.start_date}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, start_date: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={4} className="empty-state">
                          No management team rows yet.
                        </td>
                      </tr>
                    )
                  ) : people.team.length ? (
                    people.team.map((row, index) => (
                      <tr key={`${String(row.name)}-${index}`}>
                        <td>{getString(row.name)}</td>
                        <td>{getString(row.role)}</td>
                        <td>{formatDate(row.start_date)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={3} className="empty-state">
                        No management team recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('people-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            <div className="table-shell instrument-people-table-shell">
              {editingPeople && peopleDraft ? (
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Note</th>
                      <th className="instrument-table-action-col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {peopleDraft.noteRows.length ? (
                      peopleDraft.noteRows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <textarea
                              className="table-input instrument-data-table-textarea"
                              value={row.value}
                              rows={2}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        noteRows: current.noteRows.map((item) =>
                                          item.id === row.id ? { ...item, value: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={2} className="empty-state">
                          No notes yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              ) : people.notes.length ? (
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {people.notes.map((item) => (
                      <tr key={item}>
                        <td className="instrument-data-table-prose">{item}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="instrument-placeholder instrument-people-placeholder">No people notes yet.</div>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'strategy' ? (
        <section className="instrument-strategy-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-strategy-page-header">
            <div>
              <div className="panel-title">Strategy</div>
              <div className="instrument-section-title">Strategy</div>
            </div>
            <div className="toolbar">
              {editingStrategy ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setStrategyDraft(toEditableStrategyDraft(bundle.strategy))
                      setEditingStrategy(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveStrategy()}
                    disabled={savingSection === 'strategy'}
                  >
                    {savingSection === 'strategy' ? 'Saving...' : 'Save Strategy'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setStrategyDraft(toEditableStrategyDraft(bundle.strategy))
                    setEditingStrategy(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit Strategy
                </button>
              )}
            </div>
          </div>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Core Statements</div>
              </div>
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Investment Thesis</td>
                      <td className={editingStrategy ? '' : 'instrument-data-table-prose'}>
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={6}
                            value={strategyDraft.summary}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current ? { ...current, summary: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          strategy.summary || 'No strategy summary yet.'
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td>Investment Objective</td>
                      <td className={editingStrategy ? '' : 'instrument-data-table-prose'}>
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={5}
                            value={strategyDraft.investment_objective}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current ? { ...current, investment_objective: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          strategy.investment_objective || 'No investment objective yet.'
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Process</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              processRows: [...current.processRows, { id: makeRowId('process'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Bullet
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Process Item</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft.processRows.length ? (
                        strategyDraft.processRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          processRows: current.processRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          processRows: current.processRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No process bullets yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.process_bullets.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Process Item</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.process_bullets.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No process bullets yet.</div>
                )}
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Risk Controls</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              riskControlRows: [
                                ...current.riskControlRows,
                                { id: makeRowId('risk-control'), value: '' },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    Add Control
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Risk Control</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft.riskControlRows.length ? (
                        strategyDraft.riskControlRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          riskControlRows: current.riskControlRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          riskControlRows: current.riskControlRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No risk controls yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.risk_controls.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Risk Control</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.risk_controls.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No risk controls yet.</div>
                )}
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('strategy-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft.noteRows.length ? (
                        strategyDraft.noteRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No notes yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.notes.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.notes.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No strategy notes yet.</div>
                )}
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'documents' ? (
        <section className="instrument-documents-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-documents-page-header">
            <div>
              <div className="panel-title">Documents</div>
            </div>
            <div className="toolbar">
              {editingDocuments ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setDocumentsDraft(toEditableDocumentsDraft(bundle.documents))
                      setEditingDocuments(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveDocuments()}
                    disabled={savingSection === 'documents'}
                  >
                    {savingSection === 'documents' ? 'Saving...' : 'Save Documents'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setDocumentsDraft(toEditableDocumentsDraft(bundle.documents))
                    setEditingDocuments(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit Documents
                </button>
              )}
            </div>
          </div>

          <section className="instrument-documents-section">
            <div className="instrument-documents-section-header">
              <div>
                <div className="panel-title">Documents</div>
                <div className="instrument-section-title">Current Adopted Documents</div>
              </div>
            </div>
            <div className="table-shell instrument-documents-table-shell">
              <table className="terminal-table terminal-table-compact instrument-documents-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Type</th>
                    <th>As Of</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Version</th>
                    {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {editingDocuments && documentsDraft ? (
                    documentsDraft.currentDocumentRows.length ? (
                      documentsDraft.currentDocumentRows.map((row) => (
                        <tr key={row.id}>
                          <td><input className="table-input" value={row.title} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, title: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.document_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, document_type: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" type="date" value={row.as_of_date} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, as_of_date: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.source} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, source: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.version_label} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, version_label: event.target.value } : item) } : current)} /></td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="instrument-row-remove"
                              aria-label="Remove document row"
                              title="Remove row"
                              onClick={() =>
                                setDocumentsDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        currentDocumentRows: current.currentDocumentRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={7} className="empty-state">No adopted documents yet.</td></tr>
                    )
                  ) : documents.current_documents.length ? (
                    documents.current_documents.map((row, index) => (
                      <tr key={`${String(row.title)}-${index}`}>
                        <td>{getString(row.title)}</td>
                        <td>{getString(row.document_type)}</td>
                        <td>{formatDate(row.as_of_date)}</td>
                        <td>{getString(row.source)}</td>
                        <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                        <td>{getString(row.version_label)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan={6} className="empty-state">No adopted documents yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {editingDocuments ? (
              <div className="instrument-table-inline-actions">
                <button
                  type="button"
                  className="table-action"
                  onClick={() =>
                    setDocumentsDraft((current) =>
                      current
                        ? {
                            ...current,
                            currentDocumentRows: [
                              ...current.currentDocumentRows,
                              {
                                id: makeRowId('document'),
                                title: '',
                                document_type: '',
                                as_of_date: '',
                                source: '',
                                status: '',
                                version_label: '',
                              },
                            ],
                          }
                        : current,
                    )
                  }
                >
                  + Add document row
                </button>
              </div>
            ) : null}
          </section>

          <div className="instrument-documents-columns">
            <section className="instrument-documents-section">
              <div className="instrument-documents-section-header">
                <div>
                  <div className="panel-title">Documents</div>
                  <div className="instrument-section-title">Recent Imports</div>
                </div>
              </div>
              <div className="table-shell instrument-documents-table-shell">
                <table className="terminal-table terminal-table-compact instrument-documents-table">
                  <thead>
                    <tr>
                      <th>Import Type</th>
                      <th>Received At</th>
                      <th>Source</th>
                      <th>Status</th>
                      <th>File</th>
                      {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {editingDocuments && documentsDraft ? (
                      documentsDraft.importRows.length ? (
                        documentsDraft.importRows.map((row) => (
                          <tr key={row.id}>
                            <td><input className="table-input" value={row.import_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, import_type: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" type="datetime-local" value={row.received_at} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, received_at: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.source} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, source: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.file_name} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, file_name: event.target.value } : item) } : current)} /></td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="instrument-row-remove"
                                aria-label="Remove import row"
                                title="Remove row"
                                onClick={() =>
                                  setDocumentsDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          importRows: current.importRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr><td colSpan={6} className="empty-state">No import records yet.</td></tr>
                      )
                    ) : documents.recent_imports.length ? (
                      documents.recent_imports.map((row, index) => (
                        <tr key={`${String(row.file_name)}-${index}`}>
                          <td>{getString(row.import_type)}</td>
                          <td>{formatDateTime(row.received_at)}</td>
                          <td>{getString(row.source)}</td>
                          <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                          <td>{getString(row.file_name)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={5} className="empty-state">No import records yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {editingDocuments ? (
                <div className="instrument-table-inline-actions">
                  <button
                    type="button"
                    className="table-action"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              importRows: [
                                ...current.importRows,
                                {
                                  id: makeRowId('document-import'),
                                  import_type: '',
                                  received_at: '',
                                  source: '',
                                  status: '',
                                  file_name: '',
                                },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    + Add import row
                  </button>
                </div>
              ) : null}
            </section>

            <section className="instrument-documents-section">
              <div className="instrument-documents-section-header">
                <div>
                  <div className="panel-title">Documents</div>
                  <div className="instrument-section-title">Extraction Review</div>
                </div>
              </div>
              <div className="table-shell instrument-documents-table-shell">
                <table className="terminal-table terminal-table-compact instrument-documents-table">
                  <thead>
                    <tr>
                      <th>Document</th>
                      <th>Extract Type</th>
                      <th>Status</th>
                      <th>Adopted Version</th>
                      <th>Updated At</th>
                      {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {editingDocuments && documentsDraft ? (
                      documentsDraft.extractionRows.length ? (
                        documentsDraft.extractionRows.map((row) => (
                          <tr key={row.id}>
                            <td><input className="table-input" value={row.document_title} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, document_title: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.extract_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, extract_type: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.adopted_version} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, adopted_version: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" type="datetime-local" value={row.updated_at} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, updated_at: event.target.value } : item) } : current)} /></td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="instrument-row-remove"
                                aria-label="Remove extraction review row"
                                title="Remove row"
                                onClick={() =>
                                  setDocumentsDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          extractionRows: current.extractionRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr><td colSpan={6} className="empty-state">No extraction reviews yet.</td></tr>
                      )
                    ) : documents.extraction_reviews.length ? (
                      documents.extraction_reviews.map((row, index) => (
                        <tr key={`${String(row.document_title)}-${index}`}>
                          <td>{getString(row.document_title)}</td>
                          <td>{getString(row.extract_type)}</td>
                          <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                          <td>{getString(row.adopted_version)}</td>
                          <td>{formatDateTime(row.updated_at)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={5} className="empty-state">No extraction reviews yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {editingDocuments ? (
                <div className="instrument-table-inline-actions">
                  <button
                    type="button"
                    className="table-action"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              extractionRows: [
                                ...current.extractionRows,
                                {
                                  id: makeRowId('document-extraction'),
                                  document_title: '',
                                  extract_type: '',
                                  status: '',
                                  adopted_version: '',
                                  updated_at: '',
                                },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    + Add review row
                  </button>
                </div>
              ) : null}
            </section>
          </div>

          <section className="instrument-documents-section">
            <div className="instrument-documents-section-header">
              <div>
                <div className="panel-title">Documents</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingDocuments ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('document-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            {editingDocuments && documentsDraft ? (
              <div className="instrument-documents-notes-editor">
                {documentsDraft.noteRows.length ? (
                  <div className="instrument-documents-notes instrument-inline-list">
                    {documentsDraft.noteRows.map((row) => (
                      <div key={row.id} className="instrument-inline-list-row">
                        <textarea
                          className="form-textarea instrument-inline-list-textarea"
                          value={row.value}
                          rows={2}
                          onChange={(event) =>
                            setDocumentsDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    noteRows: current.noteRows.map((item) =>
                                      item.id === row.id ? { ...item, value: event.target.value } : item,
                                    ),
                                  }
                                : current,
                            )
                          }
                        />
                        <button
                          type="button"
                          className="table-action"
                          onClick={() =>
                            setDocumentsDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                  }
                                : current,
                            )
                          }
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="instrument-placeholder instrument-documents-placeholder">No notes yet.</div>
                )}
              </div>
            ) : documents.notes.length ? (
              <div className="instrument-documents-notes">
                <ul className="bullet-list">
                  {documents.notes.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-documents-placeholder">No document notes yet.</div>
            )}
          </section>
        </section>
      ) : null}

      {activeTab === 'research' ? (
        <section className="instrument-research-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-research-page-header">
            <div>
              <div className="panel-title">Research</div>
            </div>
            <div className="toolbar">
              {editingResearch ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setResearchDraft(toEditableResearchDraft(bundle.research))
                      setEditingResearch(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveResearch()}
                    disabled={savingSection === 'research'}
                  >
                    {savingSection === 'research' ? 'Saving...' : 'Save Research'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setResearchDraft(toEditableResearchDraft(bundle.research))
                    setEditingResearch(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit Research
                </button>
              )}
            </div>
          </div>

          {productFrameworkAttributes === null ? (
            <section className="instrument-research-section">
              <div className="instrument-research-section-header">
                <div>
                  <div className="panel-title">Product Framework</div>
                  <div className="instrument-section-title">Classification</div>
                </div>
              </div>
              <div className="instrument-placeholder instrument-research-placeholder">
                Loading product framework...
              </div>
            </section>
          ) : (
            productFrameworkSections.map((section) => (
              <section key={section.domain} className="instrument-research-section">
                <div className="instrument-research-section-header">
                  <div>
                    <div className="panel-title">Product Framework</div>
                    <div className="instrument-section-title">{section.title}</div>
                  </div>
                </div>
                {section.groups.length ? (
                  <div className="table-shell instrument-research-table-shell">
                    <table className="terminal-table terminal-table-compact instrument-research-table instrument-product-tags-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Notes</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {section.groups.map((group) => (
                          <Fragment key={`${section.domain}-${group.groupCode}`}>
                            <tr className="instrument-product-tags-group-row">
                              <td colSpan={3}>
                                <div className="instrument-product-tags-group-label">
                                  {group.label}
                                </div>
                              </td>
                            </tr>
                            {group.definitions.map((definition) => (
                              <tr key={definition.attribute_key}>
                                <td className="instrument-product-tags-table-label-cell">
                                  {definition.label}
                                </td>
                                <td className="instrument-product-tags-table-description-cell">
                                  {definition.description || definition.attribute_key}
                                </td>
                                <td className="instrument-product-tags-table-value-cell">
                                  <div
                                    className="instrument-product-tags-picker"
                                    ref={
                                      openProductFrameworkPickerKey === definition.attribute_key
                                        ? productFrameworkPickerRef
                                        : undefined
                                    }
                                  >
                                    <button
                                      type="button"
                                      className={`instrument-product-tags-picker-trigger${
                                        openProductFrameworkPickerKey === definition.attribute_key
                                          ? ' instrument-product-tags-picker-trigger-active'
                                          : ''
                                      }`}
                                      disabled={productFrameworkSavingKey === definition.attribute_key}
                                      onClick={() =>
                                        setOpenProductFrameworkPickerKey((current) =>
                                          current === definition.attribute_key
                                            ? null
                                            : definition.attribute_key,
                                        )
                                      }
                                    >
                                      <span>
                                        {productFrameworkSavingKey === definition.attribute_key
                                          ? 'Saving...'
                                          : formatFrameworkValue(
                                              productFrameworkAttributes.values[definition.attribute_key],
                                            )}
                                      </span>
                                    </button>
                                    {openProductFrameworkPickerKey === definition.attribute_key ? (
                                      <div className="instrument-product-tags-picker-panel">
                                        <div className="instrument-product-tags-picker-meta">
                                          {definition.data_type === 'multi_select'
                                            ? 'Select one or more'
                                            : 'Select one'}
                                        </div>
                                        <div className="instrument-product-tags-picker-options">
                                          <button
                                            type="button"
                                            className="instrument-product-tags-picker-option"
                                            disabled={productFrameworkSavingKey === definition.attribute_key}
                                            onClick={() =>
                                              void handleSaveProductFrameworkValue(
                                                definition,
                                                definition.data_type === 'multi_select' ? [] : null,
                                                {
                                                  closePicker:
                                                    definition.data_type !== 'multi_select',
                                                },
                                              )
                                            }
                                          >
                                            <span className="instrument-product-tags-picker-check" />
                                            <span className="instrument-product-tags-picker-label">
                                              Clear
                                            </span>
                                          </button>
                                          {definition.options.map((option) => {
                                            const selected = isFrameworkOptionSelected(
                                              productFrameworkAttributes,
                                              definition,
                                              option,
                                            )
                                            return (
                                              <button
                                                key={option}
                                                type="button"
                                                className={`instrument-product-tags-picker-option${
                                                  selected
                                                    ? ' instrument-product-tags-picker-option-selected'
                                                    : ''
                                                }`}
                                                disabled={productFrameworkSavingKey === definition.attribute_key}
                                                onClick={() =>
                                                  void handleSaveProductFrameworkValue(
                                                    definition,
                                                    buildNextFrameworkValue(
                                                      productFrameworkAttributes,
                                                      definition,
                                                      option,
                                                    ),
                                                    {
                                                      closePicker:
                                                        definition.data_type !== 'multi_select',
                                                    },
                                                  )
                                                }
                                              >
                                                <span className="instrument-product-tags-picker-check">
                                                  {selected ? '✓' : ''}
                                                </span>
                                                <span className="instrument-product-tags-picker-label">
                                                  {option}
                                                </span>
                                              </button>
                                            )
                                          })}
                                        </div>
                                      </div>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="instrument-placeholder instrument-research-placeholder">
                    {section.emptyState}
                  </div>
                )}
              </section>
            ))
          )}

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">Research</div>
                <div className="instrument-section-title">Current Research View</div>
              </div>
            </div>
            {editingResearch && researchDraft ? (
              <div className="instrument-research-section-body">
                <div className="instrument-research-facts-grid instrument-research-facts-grid-edit">
                  {RESEARCH_OVERVIEW_FIELDS.map((field) => (
                    <label key={field.key} className="instrument-research-fact instrument-research-fact-edit">
                      <span>{field.label}</span>
                      <input
                        className="form-input instrument-inline-value-input"
                        type={field.type === 'date' ? 'date' : 'text'}
                        value={getResearchOverviewDraftValue(researchDraft, field.key)}
                        onChange={(event) =>
                          setResearchDraft((current) =>
                            current
                              ? {
                                  ...current,
                                  overviewRows: upsertKeyValueRows(
                                    current.overviewRows,
                                    field.key,
                                    event.target.value,
                                  ),
                                }
                              : current,
                          )
                        }
                      />
                    </label>
                  ))}
                </div>
              </div>
            ) : (
              <div className="instrument-research-facts-grid">
                {RESEARCH_OVERVIEW_FIELDS.map((field) => (
                  <div key={field.key} className="instrument-research-fact">
                    <span>{field.label}</span>
                    <strong>{formatResearchOverviewValue(field.key, bundle.research.overview?.[field.key])}</strong>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">Research</div>
                <div className="instrument-section-title">Thesis</div>
              </div>
            </div>
            {editingResearch && researchDraft ? (
              <div className="instrument-research-prose instrument-inline-prose-block">
                <textarea
                  className="form-textarea instrument-research-thesis-input instrument-inline-prose-textarea"
                  rows={6}
                  value={researchDraft.thesis}
                  onChange={(event) =>
                    setResearchDraft((current) =>
                      current ? { ...current, thesis: event.target.value } : current,
                    )
                  }
                />
              </div>
            ) : (
              <div className="instrument-research-prose">
                <p>{bundle.research.thesis || 'No thesis recorded yet.'}</p>
              </div>
            )}
          </section>

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">Research</div>
                <div className="instrument-section-title">Timeline Notes</div>
              </div>
              <div className="toolbar">
                <button
                  type="button"
                  onClick={() => openTimelineNoteEditor(latestPoint?.date || latestNavRecord?.as_of_date || '')}
                >
                  Add Note
                </button>
              </div>
            </div>
            {timelineNotes.length ? (
              <div className="table-shell instrument-research-table-shell">
                <table className="terminal-table terminal-table-compact instrument-research-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Importance</th>
                      <th>Title</th>
                      <th>Summary</th>
                      <th>Tags</th>
                      <th className="instrument-table-action-col" aria-label="Timeline note actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {timelineNotes.map((note) => (
                      <tr key={note.note_id}>
                        <td>{formatDate(note.note_date)}</td>
                        <td>{formatTimelineNoteImportance(note.importance)}</td>
                        <td>{note.title || 'Untitled'}</td>
                        <td>{note.summary || note.body || '—'}</td>
                        <td>{note.tags.length ? note.tags.join(', ') : '—'}</td>
                        <td className="instrument-table-row-action-cell">
                          <div className="instrument-table-inline-actions instrument-table-inline-actions-compact">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => focusTimelineNoteInQuote(note.note_date)}
                            >
                              Open in Overview
                            </button>
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => openTimelineNoteEditor(note.note_date, note)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => void handleDeleteTimelineNote(note.note_id)}
                              disabled={savingSection === 'timeline_note'}
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-research-placeholder">
                No timeline notes yet. Add one from Overview or create one here.
              </div>
            )}
          </section>

          <div className="instrument-research-columns">
            <section className="instrument-research-section">
              <div className="instrument-research-section-header">
                <div>
                  <div className="panel-title">Research</div>
                  <div className="instrument-section-title">Evidence-Linked Conclusions</div>
                </div>
              </div>
              <div className="table-shell instrument-research-table-shell">
                <table className="terminal-table terminal-table-compact instrument-research-table">
                  <thead>
                    <tr>
                      <th>Conclusion</th>
                      <th>Evidence Ref</th>
                      <th>Status</th>
                      {editingResearch ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {editingResearch && researchDraft ? (
                      researchDraft.conclusionRows.length ? (
                        researchDraft.conclusionRows.map((row) => (
                          <tr key={row.id}>
                            <td><textarea className="form-textarea instrument-research-table-textarea" rows={2} value={row.conclusion} onChange={(event) => setResearchDraft((current) => current ? { ...current, conclusionRows: current.conclusionRows.map((item) => item.id === row.id ? { ...item, conclusion: event.target.value } : item) } : current)} /></td>
                            <td>
                              <input
                                className="table-input"
                                list="research-evidence-options"
                                value={row.evidence_ref}
                                onChange={(event) =>
                                  setResearchDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          conclusionRows: current.conclusionRows.map((item) =>
                                            item.id === row.id ? { ...item, evidence_ref: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td><input className="table-input" value={row.status} onChange={(event) => setResearchDraft((current) => current ? { ...current, conclusionRows: current.conclusionRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="instrument-row-remove"
                                aria-label="Remove conclusion row"
                                title="Remove row"
                                onClick={() =>
                                  setResearchDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          conclusionRows: current.conclusionRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr><td colSpan={4} className="empty-state">No conclusions yet.</td></tr>
                      )
                    ) : bundle.research.conclusions.length ? (
                      bundle.research.conclusions.map((row, index) => (
                        <tr key={`${String(row.conclusion)}-${index}`}>
                          <td>{getString(row.conclusion)}</td>
                          <td>
                            <span className={evidenceReferenceSet.has(String(row.evidence_ref || '').toLowerCase()) ? 'instrument-evidence-link' : ''}>
                              {getString(row.evidence_ref)}
                            </span>
                          </td>
                          <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={3} className="empty-state">No conclusions yet.</td></tr>
                    )}
                  </tbody>
                </table>
                {editingResearch && evidenceReferenceOptions.length ? (
                  <datalist id="research-evidence-options">
                    {evidenceReferenceOptions.map((item) => (
                      <option key={item} value={item} />
                    ))}
                  </datalist>
                ) : null}
              </div>
              {editingResearch ? (
                <div className="instrument-table-inline-actions">
                  <button
                    type="button"
                    className="table-action"
                    onClick={() =>
                      setResearchDraft((current) =>
                        current
                          ? {
                              ...current,
                              conclusionRows: [
                                ...current.conclusionRows,
                                { id: makeRowId('research-conclusion'), conclusion: '', evidence_ref: '', status: '' },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    + Add conclusion row
                  </button>
                </div>
              ) : null}
            </section>

            <section className="instrument-research-section">
              <div className="instrument-research-section-header">
                <div>
                  <div className="panel-title">Research</div>
                  <div className="instrument-section-title">Notes</div>
                </div>
                {editingResearch ? (
                  <div className="toolbar">
                    <button
                      type="button"
                      onClick={() =>
                        setResearchDraft((current) =>
                          current
                            ? {
                                ...current,
                                noteRows: [...current.noteRows, { id: makeRowId('research-note'), value: '' }],
                              }
                            : current,
                        )
                      }
                    >
                      Add Note
                    </button>
                  </div>
                ) : null}
              </div>
            {editingResearch && researchDraft ? (
              <div className="instrument-research-notes-editor">
                {researchDraft.noteRows.length ? (
                    <div className="instrument-research-notes instrument-inline-list">
                      {researchDraft.noteRows.map((row) => (
                        <div key={row.id} className="instrument-inline-list-row">
                          <textarea
                            className="form-textarea instrument-inline-list-textarea"
                            rows={2}
                            value={row.value}
                            onChange={(event) =>
                              setResearchDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      noteRows: current.noteRows.map((item) =>
                                        item.id === row.id ? { ...item, value: event.target.value } : item,
                                      ),
                                    }
                                  : current,
                              )
                            }
                          />
                          <button
                            type="button"
                            className="table-action"
                            onClick={() =>
                              setResearchDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                    }
                                  : current,
                              )
                            }
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="instrument-placeholder instrument-research-placeholder">No notes yet.</div>
                  )}
                </div>
              ) : bundle.research.notes.length ? (
                <div className="instrument-research-notes">
                  <ul className="bullet-list">
                    {bundle.research.notes.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="instrument-placeholder instrument-research-placeholder">No research notes yet.</div>
              )}
            </section>
          </div>
        </section>
      ) : null}

      {activeTab === 'monitoring' ? (
        <section className="instrument-monitoring-shell">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-monitoring-page-header">
            <div>
              <div className="panel-title">Monitoring</div>
            </div>
          </div>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Status Overview</div>
              </div>
            </div>
            <div className="instrument-monitoring-facts-grid">
              {monitoringOverviewRows.map((row) => (
                <div key={row.label} className="instrument-monitoring-fact">
                  <span>{row.label}</span>
                  {row.tone ? (
                    <strong>
                      <span className={`status-badge ${row.tone}`}>{row.value}</span>
                    </strong>
                  ) : (
                    <strong>{row.value}</strong>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Pipeline Status</div>
              </div>
            </div>
            <div className="table-shell instrument-monitoring-table-shell">
              <table className="terminal-table terminal-table-compact instrument-monitoring-table">
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>As Of</th>
                    <th>Source Cutoff</th>
                    <th>Methodology</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {monitoringPipelineRows.map((row) => (
                    <tr key={row.domain}>
                      <td>{row.domain}</td>
                      <td>{row.asOf}</td>
                      <td>{row.cutoff}</td>
                      <td>{row.methodology}</td>
                      <td>
                        <span className={`status-badge ${row.tone}`}>{row.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Open Items</div>
              </div>
            </div>
            {monitoringAlertRows.length ? (
              <div className="instrument-monitoring-notes">
                <ul className="bullet-list">
                  {monitoringAlertRows.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-monitoring-placeholder">No monitoring items yet.</div>
            )}
          </section>
        </section>
      ) : null}

    </div>
  )
}
