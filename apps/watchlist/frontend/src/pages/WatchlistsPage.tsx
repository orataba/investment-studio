import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import React, { startTransition, useEffect, useMemo, useRef, useState } from 'react'
import { useCanWriteTeam } from '../components/AccountBoundary'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { LanguageSelector, matchesSystemLabel, useLanguage } from '../../../../../packages/ui/src/i18n'

import {
  copyWatchlistItems,
  copyWatchlist,
  type FieldRegistryRecord,
  type ReturnSparklineSeries,
  type InstrumentTaxonomyTreeNode,
  type InstrumentTaxonomyTreeResponse,
  type ScreenerResponse,
  type SharedInstrumentRecord,
  type WatchlistDetail,
  type WatchlistRecord,
  type WatchlistView,
  addWatchlistItems,
  createWatchlist,
  createWatchlistView,
  deleteWatchlist,
  deleteWatchlistItems,
  getFieldRegistry,
  getInstrumentTaxonomyTree,
  getWatchlistDetail,
  getWatchlists,
  moveWatchlistItems,
  resolveSharedInstrumentsFile,
  updateWatchlistView,
  runScreenerQuery,
} from '../lib/api'
import { searchWatchlistInstrumentCandidates } from '../lib/watchlistInstrumentSearch'
import { useWatchlistForegroundRefresh } from '../lib/useWatchlistForegroundRefresh'
import {
  buildInstrumentDetailPath,
  buildWatchlistPath,
  HOME_URL,
} from '../lib/navigation'
import { clampColumnWidth, nextSortAction } from '../lib/tableControls'
import {
  isMetricAsOfSensitiveField,
  summarizeMetricAsOfDates,
} from '../lib/watchlistMetricSemantics'
import LoadingOverlay from '../components/LoadingOverlay'
import RenameWatchlistDialog from '../components/RenameWatchlistDialog'
import ResearchPage from './ResearchPage'
import { setRiskReferenceParams, type ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'
import WatchlistRiskDrawer from '../components/WatchlistRiskDrawer'
import WorkspaceTools, { WorkspaceToolIcon } from '../../../../../packages/ui/src/WorkspaceTools'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import InfoHint from '../../../../../packages/ui/src/InfoHint'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
import { downloadTable, type TableCell, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import { SerialTaskQueue } from '../../../../../packages/ui/src/serialTaskQueue'
import {
  formatBoolean,
  formatCompactCurrency,
  formatDate,
  formatDateTime,
  formatLabel,
  formatNumber,
  formatPercent,
  signedValueClass,
} from '../lib/format'

type ModalKind =
  | 'add'
  | 'columns'
  | 'save-view'
  | 'create-watchlist'
  | 'copy-items'
  | 'move-items'
  | null
type FilterState = Record<string, unknown[]>
type WatchlistViewSettings = {
  columns: string[]
  groupBy: string
  sortRules: Array<{ field: string; direction: string }>
  filters: FilterState
  columnWidths: Record<string, number>
}
type FilterOption = { key: string; label: string; value: unknown }
type WatchlistRowGroup = {
  key: string
  label: string | null
  rows: Array<Record<string, unknown>>
  summaryRows: Array<Record<string, unknown>>
  rowCount: number
  depth: number
  taxonomyPath?: string[]
  isInstrumentTypeSection?: boolean
}
type GroupAverageCell = {
  value: number | null
  count: number
  total: number
  asOfDate: string | null
  unavailableReason: string | null
}
type ActiveFilterEntry = {
  fieldKey: string
  value: unknown
  label: string
  valueLabel: string
  isTaxonomy: boolean
}
type PendingDeleteItems = {
  watchlistId: string
  watchlistName: string
  instrumentIds: string[]
}
const WATCHLIST_INITIAL_RENDER_ROWS = 80
const WATCHLIST_SUPPORTED_INSTRUMENT_TYPES = [
  'public_fund',
  'private_fund',
  'etf',
  'equity',
  'index',
  'crypto',
] as const
const INSTRUMENT_TYPE_LABELS: Record<string, string> = {
  public_fund: 'Public Fund',
  private_fund: 'Private Fund',
  etf: 'ETF',
  equity: 'Equity',
  index: 'Index',
  crypto: 'Crypto',
}
const EMPTY_INSTRUMENT_TYPES: string[] = []
const TAXONOMY_FILTER_FIELD_KEY = 'taxonomy'
const TAXONOMY_GROUP_BY_CODE = 'taxonomy'
const TAXONOMY_GROUP_FIELD_KEYS = [
  'attr.instrument_taxonomy_level_1',
  'attr.instrument_taxonomy_level_2',
  'attr.instrument_taxonomy_level_3',
  'attr.instrument_taxonomy_level_4',
  'attr.instrument_taxonomy_level_5',
  'attr.instrument_taxonomy_level_6',
  'attr.instrument_taxonomy_level_7',
]
const TAXONOMY_ASSIGNMENT_FIELD_KEYS = [
  ...TAXONOMY_GROUP_FIELD_KEYS,
  'attr.instrument_taxonomy_path',
  'attr.instrument_taxonomy_leaf',
]
const TAXONOMY_GROUP_DEPTH_INDENT_PX = 18
const TAXONOMY_GROUP_LABEL_OFFSET_PX = 26
const WATCHLIST_SELECT_COLUMN_WIDTH = 44
const WATCHLIST_DEFAULT_COLUMN_WIDTH = 140
const WATCHLIST_VIEW_AUTOSAVE_DELAY_MS = 250
// Full names in the chooser distinguish return values from small trend charts.
const COLUMN_CHOOSER_LABELS: Record<string, [string, string]> = {
  ticker_or_isin: ['标的代码 / ISIN', 'Code / ISIN'],
  'attr.instrument_taxonomy_path': ['分类', 'Classification'],
  latest_quote: ['最新价格 / 净值', 'Latest price / NAV'],
  latest_quote_date: ['价格 / 净值日期', 'Price / NAV date'],
  return_1w: ['近一周收益率', '1-week return'],
  return_1m: ['近一月收益率', '1-month return'],
  return_3m: ['近三月收益率', '3-month return'],
  return_ytd: ['今年以来收益率', 'Year-to-date return'],
  return_1y: ['近一年收益率', '1-year return'],
  return_chart_1m: ['近一月收益走势', '1-month return chart'],
}
const COLUMN_CHOOSER_DESCRIPTIONS: Record<string, [string, string]> = {
  'attr.risk_attention': [
    '当前风险事项的状态：重点关注、监测受限或暂无触发。暂无触发不代表已完成全面风险评估。',
    'Current risk cases: attention, limited monitoring, or no trigger. No trigger does not mean a full risk assessment is complete.',
  ],
  data_freshness_status: [
    '当前指标数据的更新状态；请结合价格 / 净值日期与指标截至日期判断。',
    'Update status of the current metrics. Read alongside the price / NAV date and metric as-of date.',
  ],
  return_chart_1m: [
    '近一个月的累计收益走势，以一个月前或之前最近的有效观测为起点。低频净值可能只有少量观测点。',
    'Cumulative return over the past month, starting at the last valid observation on or before the one-month boundary. Infrequent NAVs may provide only a few points.',
  ],
}
const COLUMN_STATUS_FIELDS = new Set([
  'attr.current_drawdown', 'attr.coverage_status', 'attr.risk_attention', 'data_freshness_status',
])
const TAXONOMY_FILTER_FIELD: FieldRegistryRecord = {
  field_key: TAXONOMY_FILTER_FIELD_KEY,
  label: 'Taxonomy',
  description: 'Choose one taxonomy node; descendants under that node remain included.',
  category_code: 'instrument_taxonomy',
  data_type: 'string',
  formatter_code: 'text',
  sort_mode: 'none',
  filter_mode: 'multi_select',
  group_mode: 'none',
  instrument_scope_json: [...WATCHLIST_SUPPORTED_INSTRUMENT_TYPES],
  product_scope_json: [],
  availability_rule_json: {},
  source_domain: 'taxonomy',
  source_metric_code: 'instrument_taxonomy.tree',
  default_width: null,
  default_visible: false,
}

function watchlistFieldDisplayLabel(
  field: FieldRegistryRecord,
  instrumentTypes: string[],
) {
  if (field.field_key !== 'latest_quote' && field.field_key !== 'latest_quote_date') {
    return field.label
  }
  const types = new Set(instrumentTypes.map((value) => value.trim().toLowerCase()).filter(Boolean))
  const allTypesMatch = (allowed: Set<string>) =>
    types.size > 0 && [...types].every((instrumentType) => allowed.has(instrumentType))
  const isDate = field.field_key === 'latest_quote_date'
  if (allTypesMatch(new Set(['public_fund', 'private_fund']))) {
    return isDate ? 'NAV Date' : 'Unit NAV'
  }
  if (allTypesMatch(new Set(['equity', 'etf']))) {
    return isDate ? 'Price Date' : 'Latest Price'
  }
  if (allTypesMatch(new Set(['index']))) {
    return isDate ? 'Index Date' : 'Index Level'
  }
  return field.label
}

function compactTableColumnWidths<T extends string>(
  columns: T[],
  getRequestedWidth: (column: T) => number,
  getMinimumWidth: (column: T) => number,
  availableWidth: number,
  fixedWidth = 0,
) {
  const specs = columns.map((column) => {
    const minWidth = getMinimumWidth(column)
    const requestedWidth = Math.max(getRequestedWidth(column), minWidth)
    return { column, minWidth, requestedWidth }
  })
  const requestedDataWidth = specs.reduce((total, spec) => total + spec.requestedWidth, 0)
  const minimumDataWidth = specs.reduce((total, spec) => total + spec.minWidth, 0)
  const targetDataWidth =
    availableWidth > fixedWidth && requestedDataWidth + fixedWidth > availableWidth
      ? Math.max(minimumDataWidth, availableWidth - fixedWidth)
      : requestedDataWidth
  const widths = {} as Record<T, number>

  if (targetDataWidth >= requestedDataWidth || requestedDataWidth <= minimumDataWidth) {
    specs.forEach((spec) => {
      widths[spec.column] = Math.round(spec.requestedWidth)
    })
  } else {
    const flexibleWidth = requestedDataWidth - minimumDataWidth
    specs.forEach((spec) => {
      const share = (spec.requestedWidth - spec.minWidth) / flexibleWidth
      widths[spec.column] = Math.round(spec.minWidth + (targetDataWidth - minimumDataWidth) * share)
    })
  }

  const totalWidth = fixedWidth + columns.reduce((total, column) => total + widths[column], 0)
  return { widths, totalWidth }
}

function isSystemWatchlist(watchlist?: WatchlistRecord | WatchlistDetail | null) {
  return Boolean(
    watchlist && watchlist.is_default && watchlist.owner_type === 'system',
  )
}

function filterValueKey(value: unknown) {
  if (typeof value === 'string') {
    return `string:${value}`
  }
  return JSON.stringify(value) ?? String(value)
}

function normalizeFilterState(value: Record<string, unknown> | null | undefined): FilterState {
  if (!value || typeof value !== 'object') {
    return {}
  }

  const normalized: FilterState = {}
  Object.entries(value).forEach(([fieldKey, rawValue]) => {
    const values = Array.isArray(rawValue)
      ? rawValue.filter((item) => item != null && item !== '')
      : rawValue == null || rawValue === ''
        ? []
        : [rawValue]
    if (!values.length) {
      return
    }
    const deduped = Array.from(new Map(values.map((item) => [filterValueKey(item), item])).values())
    if (deduped.length) {
      normalized[fieldKey] = deduped
    }
  })
  return normalized
}

function serializeFilterState(filters: FilterState) {
  return JSON.stringify(
    Object.keys(filters)
      .sort()
      .map((fieldKey) => ({
        fieldKey,
        values: [...(filters[fieldKey] || [])].sort((left, right) =>
          filterValueKey(left).localeCompare(filterValueKey(right)),
        ),
      })),
  )
}

function isTaxonomyFieldKey(fieldKey: string) {
  return TAXONOMY_GROUP_FIELD_KEYS.includes(fieldKey)
}

function taxonomyFieldKeyForPathIndex(index: number) {
  return `attr.instrument_taxonomy_level_${index + 1}`
}

function taxonomyPathKey(pathLabels: string[]) {
  return pathLabels.join(' / ')
}

function instrumentTypeLabel(instrumentType: string) {
  return INSTRUMENT_TYPE_LABELS[instrumentType] || formatLabel(instrumentType)
}

function taxonomyPathFromFilters(filters: FilterState) {
  const path: string[] = []
  for (let index = 0; index < TAXONOMY_GROUP_FIELD_KEYS.length; index += 1) {
    const values = filters[taxonomyFieldKeyForPathIndex(index)] || []
    if (values.length !== 1 || typeof values[0] !== 'string' || !values[0].trim()) {
      break
    }
    path.push(values[0])
  }
  return path
}

function taxonomyPathFromRow(row: Record<string, unknown>) {
  const path: string[] = []
  for (const fieldKey of TAXONOMY_GROUP_FIELD_KEYS) {
    const value = row[fieldKey]
    if (value == null || value === '') {
      break
    }
    path.push(String(value))
  }
  return path
}

function removeTaxonomyFilters(filters: FilterState) {
  const next = { ...filters }
  delete next.instrument_type
  TAXONOMY_GROUP_FIELD_KEYS.forEach((fieldKey) => {
    delete next[fieldKey]
  })
  return next
}

function formatFilterOptionLabel(value: unknown) {
  if (typeof value === 'boolean') {
    return formatBoolean(value)
  }
  if (value == null || value === '') {
    return '—'
  }
  return String(value)
}

function buildFilterOptions(
  fieldKey: string,
  rows: Array<Record<string, unknown>>,
  selectedValues: unknown[] = [],
): FilterOption[] {
  const byKey = new Map<string, FilterOption>()
  const addOption = (value: unknown) => {
    if (value == null || value === '') {
      return
    }
    const key = filterValueKey(value)
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        label: formatFilterOptionLabel(value),
        value,
      })
    }
  }

  rows.forEach((row) => {
    const rawValue = row[fieldKey]
    if (Array.isArray(rawValue)) {
      rawValue.forEach(addOption)
      return
    }
    addOption(rawValue)
  })
  selectedValues.forEach(addOption)

  return [...byKey.values()].sort((left, right) => left.label.localeCompare(right.label, 'zh-Hans-CN'))
}

async function loadAllScreenerRows(
  payload: Record<string, unknown>,
) {
  return (await loadCompleteScreenerResult(payload)).rows
}

async function loadCompleteScreenerResult(
  payload: Record<string, unknown>,
): Promise<ScreenerResponse> {
  return runScreenerQuery({
    ...payload,
    fetch_all: true,
  })
}

function statusClass(value: unknown) {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'fresh') {
    return 'status-badge status-fresh'
  }
  if (normalized.includes('pending') || normalized.includes('stale')) {
    return 'status-badge status-pending'
  }
  return 'status-badge status-attribute'
}

function asNumber(value: unknown) {
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (!Number.isNaN(parsed)) {
      return parsed
    }
  }
  return null
}

function isReturnMetricField(fieldKey: string) {
  return (
    fieldKey.startsWith('return_') ||
    fieldKey === 'annualized_return' ||
    fieldKey === 'max_drawdown' ||
    fieldKey === 'attr.current_drawdown' ||
    fieldKey === 'ytd' ||
    fieldKey === 'oneYear' ||
    fieldKey === 'threeYear' ||
    fieldKey === 'fiveYear' ||
    fieldKey.endsWith('_return') ||
    fieldKey.endsWith('_return_pct') ||
    fieldKey.endsWith('_change_pct')
  )
}

function getWatchlistCompactMinWidth(fieldKey: string, field: FieldRegistryRecord | undefined) {
  if (fieldKey === 'instrument_name') {
    return 180
  }
  if (fieldKey === 'instrument_type') return 88

  if (fieldKey === 'ticker_or_isin') {
    return 96
  }
  if (isChartFieldKey(fieldKey)) {
    return 104
  }
  if (fieldKey.endsWith('_date') || fieldKey.endsWith('_at')) {
    return 104
  }
  if (fieldKey.includes('status')) {
    return 112
  }
  if (isTaxonomyFieldKey(fieldKey) || fieldKey.startsWith('attr.')) {
    return 112
  }
  if (
    isReturnMetricField(fieldKey) ||
    field?.formatter_code === 'percent' ||
    field?.formatter_code === 'decimal' ||
    field?.data_type === 'integer' ||
    field?.data_type === 'number'
  ) {
    return 88
  }
  if (field?.formatter_code === 'currency_compact') {
    return 104
  }
  return 104
}

function isChartFieldKey(fieldKey: string) {
  return fieldKey.startsWith('return_chart_')
}

function isAverageSummaryField(fieldKey: string, field: FieldRegistryRecord | undefined) {
  if (!field || !['number', 'integer'].includes(field.data_type)) {
    return false
  }
  if (
    fieldKey === 'aum' ||
    fieldKey === 'latest_quote' ||
    fieldKey === 'latest_cumulative_nav' ||
    fieldKey === 'attr.peer_sample_count' ||
    field.formatter_code === 'currency_compact'
  ) {
    return false
  }
  return field.sort_mode === 'numeric' || field.formatter_code === 'percent' || field.formatter_code === 'decimal'
}

function buildGroupAverageCell(
  fieldKey: string,
  field: FieldRegistryRecord | undefined,
  rows: Array<Record<string, unknown>>,
): GroupAverageCell | null {
  if (!isAverageSummaryField(fieldKey, field) || !rows.length) {
    return null
  }
  const values = rows
    .map((row) => asNumber(row[fieldKey]))
    .filter((value): value is number => value != null)
  if (!values.length) {
    return null
  }
  const asOfSummary = summarizeMetricAsOfDates(fieldKey, rows)
  if (!asOfSummary.comparable) {
    return {
      value: null,
      count: values.length,
      total: rows.length,
      asOfDate: null,
      unavailableReason:
        asOfSummary.missingDateCount > 0
          ? 'Average withheld because one or more populated rows have no metric as-of date.'
          : `Average withheld because rows use different metric endpoints (${asOfSummary.dates.join(', ')}).`,
    }
  }
  return {
    value: values.reduce((sum, value) => sum + value, 0) / values.length,
    count: values.length,
    total: rows.length,
    asOfDate: asOfSummary.asOfDate,
    unavailableReason: null,
  }
}

function formatGroupAverageCell(fieldKey: string, field: FieldRegistryRecord | undefined, value: number) {
  if (fieldKey.includes('_percentile')) {
    return `${formatNumber(value, 0)} pct`
  }
  if (field?.formatter_code === 'percent' || isReturnMetricField(fieldKey)) {
    return formatPercent(value)
  }
  if (field?.formatter_code === 'decimal' || field?.data_type === 'integer') {
    return formatNumber(value, field.data_type === 'integer' ? 1 : 2)
  }
  return formatNumber(value)
}

function renderCell(
  fieldKey: string,
  value: unknown,
  instrumentId: string,
  watchlistId: string,
  sparkline: ReturnSparklineSeries | undefined,
  field: FieldRegistryRecord | undefined,
  row: Record<string, unknown>,
  openRisk: (id: string) => void,
  zh: boolean,
) {
  if (fieldKey === 'instrument_name') {
    return (
      <span className="watchlist-instrument-name"><Link translate="no" to={buildInstrumentDetailPath(instrumentId, watchlistId)} className="table-link watchlists-instrument-link">
        {typeof value === 'string' && value ? value : instrumentId.toUpperCase()}
      </Link>{row['attr.risk_attention'] === 'attention' && <button className="watchlist-risk-indicator" onClick={() => openRisk(instrumentId)}
        aria-label={zh ? `${value || instrumentId} 有关注事项，查看风险提示` : `View risk alerts for ${value || instrumentId}`}
        title={zh ? '有重点事项仍需关注 · 点击查看' : 'Open risk alerts'}><WorkspaceToolIcon kind="risk" /></button>}</span>
    )
  }

  if (fieldKey === 'instrument_type') {
    const labels: Record<string, string> = zh
      ? { private_fund: '私募基金', public_fund: '公募基金', equity: '股票', etf: 'ETF', index: '指数', crypto: '加密资产' }
      : { private_fund: 'Private Fund', public_fund: 'Public Fund', equity: 'Stock', etf: 'ETF', index: 'Index', crypto: 'Crypto' }
    return labels[String(value)] || String(value || '—')
  }

  if (fieldKey === 'ticker_or_isin') {
    return <span translate="no" className="ticker-pill">{String(value || '—')}</span>
  }

  if (fieldKey === 'attr.research_stage') {
    const labels: Record<string, string> = { watching: '观察中', researching: '研究中', candidate: '候选', paused: '暂缓', archived: '归档' }
    return labels[String(value)] || '观察中'
  }
  if (fieldKey === 'attr.risk_attention') {
    const labels: Record<string, string> = { attention: '重点关注', limited: '监测受限', no_trigger: '暂无触发' }
    return <button className="watchlist-risk-cell" onClick={() => openRisk(instrumentId)}>{labels[String(value)] || '待核查'}</button>
  }
  if (fieldKey === 'attr.coverage_status') {
    return value == null || value === '' ? '—' : <span className="status-badge status-attribute">{String(value)}</span>
  }

  if (fieldKey === 'data_freshness_status') {
    return <span className={statusClass(value)}>{formatLabel(String(value || 'Unknown'))}</span>
  }

  if (isChartFieldKey(fieldKey)) {
    const anchorLabel = sparkline?.anchor_date
      ? `${sparkline.anchor_date} to ${sparkline.end_date || 'latest'}`
      : 'unavailable window'
    return (
      <Sparkline
        values={sparkline?.points}
        ariaLabel={`${sparkline?.label || 'Cumulative return'}; ${anchorLabel}`}
      />
    )
  }

  if (fieldKey === 'aum') {
    return formatCompactCurrency(asNumber(value))
  }

  if (fieldKey === 'latest_quote' || fieldKey === 'latest_cumulative_nav') {
    return formatNumber(asNumber(value), 4)
  }

  if (fieldKey.includes('_percentile')) {
    const numericValue = asNumber(value)
    return numericValue == null ? '—' : `${formatNumber(numericValue, 0)} pct`
  }

  if (isReturnMetricField(fieldKey) || field?.formatter_code === 'percent') {
    const numericValue = asNumber(value)
    const renderedValue = formatPercent(numericValue)
    const metricAsOfDate = String(row.metric_as_of_date || '').slice(0, 10)
    const title =
      isMetricAsOfSensitiveField(fieldKey) && metricAsOfDate
        ? `${field?.label || formatLabel(fieldKey)} through ${metricAsOfDate}; each Watchlist row uses that instrument's own latest calculation date.`
        : undefined
    return isReturnMetricField(fieldKey) ? (
      <span className={signedValueClass(numericValue)} title={title}>{renderedValue}</span>
    ) : (
      <span title={title}>{renderedValue}</span>
    )
  }

  if (field?.formatter_code === 'decimal' || fieldKey === 'duration' || fieldKey === 'sharpe_ratio') {
    const metricAsOfDate = String(row.metric_as_of_date || '').slice(0, 10)
    return (
      <span
        title={
          isMetricAsOfSensitiveField(fieldKey) && metricAsOfDate
            ? `${field?.label || formatLabel(fieldKey)} through ${metricAsOfDate}; each Watchlist row uses that instrument's own latest calculation date.`
            : undefined
        }
      >
        {formatNumber(asNumber(value), 2)}
      </span>
    )
  }

  if (fieldKey.endsWith('_date')) {
    return formatDate(typeof value === 'string' ? value : String(value || ''))
  }

  if (fieldKey.endsWith('_at')) {
    return formatDateTime(value)
  }

  if (typeof value === 'boolean') {
    return formatBoolean(value)
  }

  if (Array.isArray(value)) {
    return value.length ? value.map((item, index) => <React.Fragment key={index}>{index ? ', ' : ''}<span>{String(item)}</span></React.Fragment>) : '—'
  }

  if (value == null || value === '') {
    return '—'
  }

  return <span translate={fieldKey.match(/(?:notes?|description|manager|issuer|analyst|name)$/) ? 'no' : undefined}>{String(value)}</span>
}

function watchlistExportCell(value: unknown): TableCell {
  if (value == null) {
    return null
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value
  }
  return String(value)
}

function fileNameSlug(value: string) {
  return value
    .normalize('NFKC')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'view'
}

function downloadWatchlistRows(
  filenameBase: string,
  columns: string[],
  rows: Array<Record<string, unknown>>,
  format: TableExportFormat,
) {
  downloadTable(
    filenameBase,
    [
      columns,
      ...rows.map((row) =>
        columns.map((column) => watchlistExportCell(row[column] ?? row[column.replace(/^attr\./, '')])),
      ),
    ],
    format,
    'Watchlist',
  )
}

function primarySharedIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

function rowValueMatchesSearch(value: unknown, query: string): boolean {
  if (value == null) {
    return false
  }
  if (Array.isArray(value)) {
    return value.some((item) => rowValueMatchesSearch(item, query))
  }
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).some((item) =>
      rowValueMatchesSearch(item, query),
    )
  }
  return String(value).toLowerCase().includes(query)
}

function rowMatchesWatchlistSearch(row: Record<string, unknown>, query: string): boolean {
  if (!query) {
    return true
  }
  return Object.values(row).some((value) => rowValueMatchesSearch(value, query))
}

function parseJsonSearchParam<T>(value: string | null, fallback: T): T {
  if (!value) {
    return fallback
  }
  try {
    return JSON.parse(value) as T
  } catch {
    return fallback
  }
}

export default function WatchlistsPage() {
  const canWriteTeam = useCanWriteTeam()
  const { language, t } = useLanguage()
  const zh = language === 'zh-Hans'
  const primaryDisplayColumn = 'instrument_name'
  const requiredColumns = [primaryDisplayColumn]
  const navigate = useNavigate()
  const { watchlistId = '' } = useParams()
  const [watchlistSearchParams, setWatchlistSearchParams] = useSearchParams()
  const [watchlists, setWatchlists] = useState<WatchlistRecord[]>([])
  const [fieldRegistry, setFieldRegistry] = useState<FieldRegistryRecord[]>([])
  const [columnFieldKeys, setColumnFieldKeys] = useState<string[]>([])
  const [filterFieldKeys, setFilterFieldKeys] = useState<string[]>([])
  const [instrumentTaxonomy, setInstrumentTaxonomy] = useState<InstrumentTaxonomyTreeResponse | null>(null)
  const [activeViewId, setActiveViewId] = useState(
    () => watchlistSearchParams.get('view') || '',
  )
  const [watchlistDetail, setWatchlistDetail] = useState<WatchlistDetail | null>(null)
  const [watchlistDetailOwnerId, setWatchlistDetailOwnerId] = useState('')
  const [screenerResult, setScreenerResult] = useState<ScreenerResponse | null>(null)
  const [screenerResultOwnerId, setScreenerResultOwnerId] = useState('')
  const [screenerLoading, setScreenerLoading] = useState(false)
  const [renderRowLimit, setRenderRowLimit] = useState(WATCHLIST_INITIAL_RENDER_ROWS)
  const [workingColumns, setWorkingColumns] = useState<string[]>([])
  const [columnDraft, setColumnDraft] = useState<string[]>([])
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({})
  const [columnDropTarget, setColumnDropTarget] = useState('')
  const [columnResizing, setColumnResizing] = useState(false)
  const [workingGroupBy, setWorkingGroupBy] = useState(
    () => watchlistSearchParams.get('group') || 'none',
  )
  const [workingFilters, setWorkingFilters] = useState<FilterState>(() =>
    normalizeFilterState(
      parseJsonSearchParam<Record<string, unknown>>(watchlistSearchParams.get('filters'), {}),
    ),
  )
  const [fieldSearch, setFieldSearch] = useState('')
  const [watchlistSearch, setWatchlistSearch] = useState(
    () => watchlistSearchParams.get('q') || '',
  )
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [modalKind, setModalKind] = useState<ModalKind>(null)
  const [filterMenuOpen, setFilterMenuOpen] = useState(false)
  const [groupMenuOpen, setGroupMenuOpen] = useState(false)
  const [selectorMenuOpen, setSelectorMenuOpen] = useState(false)
  const [selectedFilterField, setSelectedFilterField] = useState('')
  const [filterOptionRows, setFilterOptionRows] = useState<Array<Record<string, unknown>>>([])
  const [saveViewName, setSaveViewName] = useState('')
  const [saveViewDescription, setSaveViewDescription] = useState('')
  const [isSavingView, setIsSavingView] = useState(false)
  const [createWatchlistName, setCreateWatchlistName] = useState('')
  const [createWatchlistDescription, setCreateWatchlistDescription] = useState('')
  const [isCreatingWatchlist, setIsCreatingWatchlist] = useState(false)
  const [copyTargetWatchlistId, setCopyTargetWatchlistId] = useState('')
  const [isCopyingItems, setIsCopyingItems] = useState(false)
  const [moveTargetWatchlistId, setMoveTargetWatchlistId] = useState('')
  const [isMovingItems, setIsMovingItems] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [instrumentSearch, setInstrumentSearch] = useState('')
  const [sharedInstrumentResults, setSharedInstrumentResults] = useState<SharedInstrumentRecord[]>([])
  const [selectedInstrumentId, setSelectedInstrumentId] = useState('')
  const [isSearchingInstruments, setIsSearchingInstruments] = useState(false)
  const [isAdding, setIsAdding] = useState(false)
  const [isBatchAdding, setIsBatchAdding] = useState(false)
  const [sortRules, setSortRules] = useState<Array<{ field: string; direction: string }>>(() => {
    const parsed = parseJsonSearchParam<unknown>(watchlistSearchParams.get('sort'), [])
    return Array.isArray(parsed)
      ? parsed.filter(
          (item): item is { field: string; direction: string } =>
            Boolean(item) &&
            typeof item === 'object' &&
            typeof (item as { field?: unknown }).field === 'string' &&
            ['asc', 'desc'].includes(String((item as { direction?: unknown }).direction)),
        )
      : []
  })
  const [viewToast, setViewToast] = useState<NoticeToastMessage | null>(null)
  function setNotice(message: string | null) {
    setViewToast(message ? { id: Date.now(), message } : null)
  }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalError, setModalError] = useState<string | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [pendingDeleteWatchlist, setPendingDeleteWatchlist] = useState<WatchlistRecord | null>(null)
  const [renamingWatchlist, setRenamingWatchlist] = useState<WatchlistRecord | null>(null)
  const [deletingWatchlist, setDeletingWatchlist] = useState(false)
  const [pendingDeleteItems, setPendingDeleteItems] = useState<PendingDeleteItems | null>(null)
  const [deletingItems, setDeletingItems] = useState(false)
  const [sparklineMap, setSparklineMap] = useState<
    Record<string, Record<string, ReturnSparklineSeries>>
  >({})
  const [collapsedGroupKeys, setCollapsedGroupKeys] = useState<Set<string>>(new Set())
  const [reloadToken, setReloadToken] = useState(0)
  useWatchlistForegroundRefresh(
    !loading && !renamingWatchlist && watchlistDetailOwnerId === watchlistId ? watchlistId : null,
    (records) => {
      setWatchlists(records)
      setError(null)
      setReloadToken((value) => value + 1)
    },
    setError,
  )
  const filterMenuRef = useRef<HTMLDivElement | null>(null)
  const groupMenuRef = useRef<HTMLDivElement | null>(null)
  const selectorMenuRef = useRef<HTMLDivElement | null>(null)
  const tableShellRef = useRef<HTMLDivElement | null>(null)
  const [tableShellWidth, setTableShellWidth] = useState(0)
  const resizeState = useRef<{
    column: string
    startX: number
    startWidth: number
  } | null>(null)
  const resizeFrame = useRef<number | null>(null)
  const pendingResize = useRef<{ column: string; width: number } | null>(null)
  const batchFileInputRef = useRef<HTMLInputElement | null>(null)
  const activeWatchlistIdRef = useRef(watchlistId)
  const viewSaveQueueRef = useRef(new SerialTaskQueue())
  const viewSaveSequenceRef = useRef(0)
  const latestViewSaveSequenceRef = useRef(new Map<string, number>())
  activeWatchlistIdRef.current = watchlistId
  const watchlistSearchKey = watchlistSearchParams.toString()

  const modalBusy = isSavingView || isCreatingWatchlist || isCopyingItems || isMovingItems || isAdding || isBatchAdding
  function closeActiveModal() {
    if (!modalBusy) {
      setModalError(null)
      setModalKind(null)
    }
  }

  const modalDialogRef = useModalDialog(Boolean(modalKind), closeActiveModal)
  const activeInstrumentTypes = useMemo(() => {
    const all = watchlistDetail?.instrument_types || EMPTY_INSTRUMENT_TYPES
    const selected = workingFilters.instrument_type
    return Array.isArray(selected) && selected.length ? all.filter(type => selected.includes(type)) : all
  }, [watchlistDetail?.instrument_types, workingFilters.instrument_type])
  const displayFieldRegistry = useMemo(
    () =>
      fieldRegistry.map((field) => ({
        ...field,
        label: watchlistFieldDisplayLabel(field, activeInstrumentTypes),
      })),
    [fieldRegistry, activeInstrumentTypes],
  )
  const fieldKeys = useMemo(
    () => new Set(fieldRegistry.map((field) => field.field_key)),
    [fieldRegistry],
  )
  const columnKeys = useMemo(() => new Set(columnFieldKeys), [columnFieldKeys])
  const filterKeys = useMemo(
    () => new Set([...filterFieldKeys, ...TAXONOMY_GROUP_FIELD_KEYS, TAXONOMY_FILTER_FIELD_KEY]),
    [filterFieldKeys],
  )
  const columnFieldRegistry = useMemo(
    () => columnFieldKeys
      .map((key) => fieldRegistry.find((field) => field.field_key === key))
      .filter((field): field is FieldRegistryRecord => Boolean(field)),
    [fieldRegistry, columnFieldKeys],
  )
  const availableGroupByCodes = useMemo(
    () => new Set((watchlistDetail?.available_group_bys || []).map((item) => item.code)),
    [watchlistDetail?.available_group_bys],
  )

  const baseScreenerPayload = useMemo(() => {
    if (!watchlistId || watchlistDetailOwnerId !== watchlistId || !columnFieldKeys.length) {
      return null
    }

    const requestedFields = (workingColumns.length ? [...workingColumns] : [primaryDisplayColumn])
      .filter((fieldKey) => columnKeys.has(fieldKey))
    if (fieldKeys.has('attr.risk_attention') && !requestedFields.includes('attr.risk_attention')) {
      requestedFields.push('attr.risk_attention')
    }
    const effectiveGroupBy = availableGroupByCodes.has(workingGroupBy) ? workingGroupBy : 'none'
    if (effectiveGroupBy === TAXONOMY_GROUP_BY_CODE) {
      TAXONOMY_ASSIGNMENT_FIELD_KEYS.forEach((fieldKey) => {
        if (fieldKeys.has(fieldKey) && !requestedFields.includes(fieldKey)) {
          requestedFields.push(fieldKey)
        }
      })
    } else if (effectiveGroupBy !== 'none' && !requestedFields.includes(effectiveGroupBy)) {
      requestedFields.push(effectiveGroupBy)
    }

    const effectiveFilters = Object.fromEntries(
      Object.entries(workingFilters).filter(
        ([fieldKey]) => filterKeys.has(fieldKey),
      ),
    )
    const effectiveSortRules = sortRules.filter((rule) => columnKeys.has(rule.field))

    return {
      watchlist_id: watchlistId,
      view_id: activeViewId || null,
      selected_fields: requestedFields,
      filters: effectiveFilters,
      sort: effectiveSortRules,
      group_by: effectiveGroupBy,
    }
  }, [activeViewId, availableGroupByCodes, primaryDisplayColumn, fieldKeys, columnKeys, columnFieldKeys.length, filterKeys, sortRules, watchlistDetailOwnerId, watchlistId, workingColumns, workingFilters, workingGroupBy])
  const screenerCriteriaKey = useMemo(
    () => JSON.stringify(baseScreenerPayload || {}),
    [baseScreenerPayload],
  )

  useEffect(() => {
    const element = tableShellRef.current
    if (!element) {
      return undefined
    }

    const updateWidth = () => setTableShellWidth(Math.floor(element.clientWidth))
    updateWidth()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateWidth)
      return () => window.removeEventListener('resize', updateWidth)
    }

    const observer = new ResizeObserver(updateWidth)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let cancelled = false

    async function loadShell() {
      setLoading(true)
      setError(null)

      try {
        const [watchlistData, fieldRegistryData, taxonomyData] = await Promise.all([
          getWatchlists(),
          getFieldRegistry(),
          getInstrumentTaxonomyTree(),
        ])

        if (cancelled) {
          return
        }

        setWatchlists(watchlistData)
        setFieldRegistry(fieldRegistryData.fields)
        setColumnFieldKeys(fieldRegistryData.column_field_keys)
        setFilterFieldKeys(fieldRegistryData.filter_field_keys)
        setInstrumentTaxonomy(taxonomyData)
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load watchlists.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadShell()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (loading || !watchlists.length) {
      return
    }
    if (watchlists.some((item) => item.watchlist_id === watchlistId)) {
      return
    }
    const fallbackWatchlistId = watchlists[0]?.watchlist_id
    if (fallbackWatchlistId) {
      startTransition(() => {
        navigate(buildWatchlistPath(fallbackWatchlistId), { replace: true })
      })
    }
  }, [loading, navigate, watchlistId, watchlists])

  useEffect(() => {
    if (!watchlistId) {
      setWatchlistDetail(null)
      setWatchlistDetailOwnerId('')
      setScreenerResult(null)
      setScreenerResultOwnerId('')
      setSelectedRows([])
      return
    }

    let cancelled = false
    setWatchlistDetail(null)
    setWatchlistDetailOwnerId('')
    setScreenerResult(null)
    setScreenerResultOwnerId('')
    setSelectedRows([])
    setModalKind(null)
    setModalError(null)
    setConfirmError(null)
    setPendingDeleteItems(null)

    async function loadWatchlistDetail() {
      setError(null)

      try {
        const detail = await getWatchlistDetail(watchlistId)
        if (cancelled) {
          return
        }

        setWatchlistDetail(detail)
        setWatchlistDetailOwnerId(watchlistId)
        startTransition(() => {
          const requestedViewId = watchlistSearchParams.get('view') || ''
          const nextViewId =
            detail.views.some((view) => view.view_id === requestedViewId)
              ? requestedViewId
              : detail.default_view_id || detail.views[0]?.view_id || ''
          setActiveViewId(nextViewId)
          const nextView =
            detail.views.find((item) => item.view_id === nextViewId) || detail.views[0] || null
          applyWatchlistView(nextView)
          if (watchlistSearchParams.has('group')) {
            setWorkingGroupBy(watchlistSearchParams.get('group') || 'none')
          }
          if (watchlistSearchParams.has('filters')) {
            setWorkingFilters(
              normalizeFilterState(
                parseJsonSearchParam<Record<string, unknown>>(
                  watchlistSearchParams.get('filters'),
                  {},
                ),
              ),
            )
          }
          if (watchlistSearchParams.has('sort')) {
            const requestedSort = parseJsonSearchParam<unknown>(
              watchlistSearchParams.get('sort'),
              [],
            )
            if (Array.isArray(requestedSort)) {
              setSortRules(
                requestedSort.filter(
                  (item): item is { field: string; direction: string } =>
                    Boolean(item) &&
                    typeof item === 'object' &&
                    typeof (item as { field?: unknown }).field === 'string' &&
                    ['asc', 'desc'].includes(
                      String((item as { direction?: unknown }).direction),
                    ),
                ),
              )
            }
          }
          setWatchlistSearch(watchlistSearchParams.get('q') || '')
          setSelectedRows([])
          setNotice(null)
        })
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load watchlist detail.')
        }
      }
    }

    void loadWatchlistDetail()

    return () => {
      cancelled = true
    }
  }, [watchlistId])

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      const target = event.target as Node | null
      if (filterMenuOpen && filterMenuRef.current && target && !filterMenuRef.current.contains(target)) {
        setFilterMenuOpen(false)
      }
      if (groupMenuOpen && groupMenuRef.current && target && !groupMenuRef.current.contains(target)) {
        setGroupMenuOpen(false)
      }
      if (selectorMenuOpen && selectorMenuRef.current && target && !selectorMenuRef.current.contains(target)) {
        setSelectorMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [filterMenuOpen, groupMenuOpen, selectorMenuOpen])

  useEffect(() => {
    if (modalKind !== 'add') {
      return
    }

    let cancelled = false
    setIsSearchingInstruments(true)
    setModalError(null)

    const timeoutId = window.setTimeout(() => {
      searchWatchlistInstrumentCandidates(instrumentSearch, 12)
        .then(({ results }) => {
          if (cancelled) {
            return
          }
          setSharedInstrumentResults(results)
          setSelectedInstrumentId((current) => {
            if (current && results.some((item) => item.instrument_id === current)) {
              return current
            }
            return results[0]?.instrument_id || ''
          })
        })
        .catch((loadError) => {
          if (!cancelled) {
            setModalError(
              loadError instanceof Error
                ? loadError.message
                : 'Failed to load registered assets.',
            )
            setSharedInstrumentResults([])
            setSelectedInstrumentId('')
          }
        })
        .finally(() => {
          if (!cancelled) {
            setIsSearchingInstruments(false)
          }
        })
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timeoutId)
    }
  }, [instrumentSearch, modalKind])

  useEffect(() => {
    if (
      !watchlistDetail ||
      watchlistDetailOwnerId !== watchlistId ||
      !baseScreenerPayload
    ) {
      setScreenerResult(null)
      setScreenerResultOwnerId('')
      return
    }

    let cancelled = false
    const payload = baseScreenerPayload

    async function loadRows() {
      setScreenerLoading(true)
      try {
        const result = await loadCompleteScreenerResult(payload)

        if (!cancelled) {
          setScreenerResult(result)
          setScreenerResultOwnerId(watchlistId)
          setSparklineMap(result.sparklines || {})
          setSelectedRows((current) =>
            current.filter((instrumentId) => result.rows.some((row) => String(row.instrument_id) === instrumentId)),
          )
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load watchlist rows.')
        }
      } finally {
        if (!cancelled) {
          setScreenerLoading(false)
        }
      }
    }

    void loadRows()

    return () => {
      cancelled = true
    }
  }, [reloadToken, screenerCriteriaKey, watchlistDetailOwnerId, watchlistId])

  const detailIsCurrent = watchlistDetailOwnerId === watchlistId
  const rowsAreCurrent = detailIsCurrent && screenerResultOwnerId === watchlistId
  const activeView = detailIsCurrent
    ? watchlistDetail?.views.find((item) => item.view_id === activeViewId) ||
      watchlistDetail?.views[0] ||
      null
    : null

  useEffect(() => {
    if (!detailIsCurrent || !watchlistDetail) {
      return
    }
    const requestedViewId = watchlistSearchParams.get('view') || ''
    const requestedView =
      watchlistDetail.views.find((view) => view.view_id === requestedViewId) ||
      watchlistDetail.views.find((view) => view.view_id === activeViewId) ||
      watchlistDetail.views[0] ||
      null
    if (requestedView && requestedView.view_id !== activeViewId) {
      setActiveViewId(requestedView.view_id)
    }
    applyWatchlistView(requestedView)
    if (watchlistSearchParams.has('group')) {
      setWorkingGroupBy(watchlistSearchParams.get('group') || 'none')
    }
    if (watchlistSearchParams.has('filters')) {
      setWorkingFilters(
        normalizeFilterState(
          parseJsonSearchParam<Record<string, unknown>>(
            watchlistSearchParams.get('filters'),
            {},
          ),
        ),
      )
    }
    if (watchlistSearchParams.has('sort')) {
      const requestedSort = parseJsonSearchParam<unknown>(
        watchlistSearchParams.get('sort'),
        [],
      )
      setSortRules(
        Array.isArray(requestedSort)
          ? requestedSort.filter(
              (item): item is { field: string; direction: string } =>
                Boolean(item) &&
                typeof item === 'object' &&
                typeof (item as { field?: unknown }).field === 'string' &&
                ['asc', 'desc'].includes(
                  String((item as { direction?: unknown }).direction),
                ),
            )
          : [],
      )
    }
    setWatchlistSearch(watchlistSearchParams.get('q') || '')
  }, [detailIsCurrent, fieldRegistry.length, watchlistDetail, watchlistSearchKey])

  async function handleBatchAddFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    const sourceWatchlistId = watchlistDetailOwnerId === watchlistId ? watchlistId : ''
    if (!file || !sourceWatchlistId) {
      return
    }

    setIsBatchAdding(true)
    setModalError(null)
    setNotice(null)

    try {
      const resolution = await resolveSharedInstrumentsFile(file)
      const identifierCount = resolution.results.length
      const missingIdentifiers = resolution.results
        .filter((result) => result.status === 'not_found' || !result.instrument)
        .map((result) => result.identifier)
      const unsupportedIdentifiers = resolution.results
        .filter(
          (result) =>
            result.instrument &&
            !WATCHLIST_SUPPORTED_INSTRUMENT_TYPES.includes(
              result.instrument.instrument_type as typeof WATCHLIST_SUPPORTED_INSTRUMENT_TYPES[number],
            ),
        )
        .map(
          (result) =>
            `${result.identifier} (${result.instrument?.instrument_type || 'unknown'})`,
        )
      const resolvedInstrumentIds = new Set<string>()
      resolution.results.forEach((result) => {
        if (
          result.instrument &&
          WATCHLIST_SUPPORTED_INSTRUMENT_TYPES.includes(
            result.instrument.instrument_type as typeof WATCHLIST_SUPPORTED_INSTRUMENT_TYPES[number],
          )
        ) {
          resolvedInstrumentIds.add(result.instrument.instrument_id)
        }
      })

      if (missingIdentifiers.length) {
        throw new Error(
          `These identifiers were not found among registered assets: ${missingIdentifiers.join(', ')}.`,
        )
      }
      if (unsupportedIdentifiers.length) {
        throw new Error(
          `These identifiers resolve outside Watchlist coverage: ${unsupportedIdentifiers.join(', ')}.`,
        )
      }

      const addResult = await addWatchlistItems(sourceWatchlistId, [...resolvedInstrumentIds])
      await refreshWatchlistDetail(undefined, sourceWatchlistId)
      setReloadToken(Date.now())
      setModalKind(null)
      const skippedCount = Math.max(identifierCount - addResult.accepted_count, 0)
      setNotice(
        skippedCount > 0
          ? `Processed ${identifierCount} unique identifiers. Added ${addResult.accepted_count}; ${skippedCount} already existed in this watchlist.`
          : `Processed ${identifierCount} unique identifiers. Added ${addResult.accepted_count} registered assets.`,
      )
    } catch (batchError) {
      setModalError(batchError instanceof Error ? batchError.message : 'Failed to add instruments from file.')
    } finally {
      setIsBatchAdding(false)
      event.target.value = ''
    }
  }

  const activeWatchlist = watchlists.find((item) => item.watchlist_id === watchlistId) || null
  const activeWatchlistIsSystem = isSystemWatchlist(
    activeWatchlist || (detailIsCurrent ? watchlistDetail : null),
  )

  async function handleDeleteWatchlist() {
    if (!pendingDeleteWatchlist || deletingWatchlist) {
      return
    }
    setDeletingWatchlist(true)
    setConfirmError(null)
    try {
      await deleteWatchlist(pendingDeleteWatchlist.watchlist_id)
      setWatchlists((current) =>
        current.filter((item) => item.watchlist_id !== pendingDeleteWatchlist.watchlist_id),
      )
      setPendingDeleteWatchlist(null)
      navigate('/watchlists')
    } catch (requestError) {
      setConfirmError(requestError instanceof Error ? requestError.message : 'Failed to delete watchlist.')
    } finally {
      setDeletingWatchlist(false)
    }
  }

  async function handleDeleteSelectedItems() {
    const pending = pendingDeleteItems
    if (!pending || deletingItems) {
      return
    }
    setDeletingItems(true)
    setConfirmError(null)
    try {
      await deleteWatchlistItems(pending.watchlistId, pending.instrumentIds)
      if (activeWatchlistIdRef.current === pending.watchlistId) {
        setSelectedRows([])
        await refreshWatchlistDetail(undefined, pending.watchlistId)
        setReloadToken(Date.now())
      }
      setPendingDeleteItems(null)
      setNotice(
        zh
          ? `已从“${pending.watchlistName}”移除 ${pending.instrumentIds.length} 个标的。`
          : `Removed ${pending.instrumentIds.length} instruments from "${pending.watchlistName}".`,
      )
    } catch (deleteError) {
      setConfirmError(
        deleteError instanceof Error ? deleteError.message : 'Failed to delete instruments.',
      )
    } finally {
      setDeletingItems(false)
    }
  }
  const moveTargetOptions = useMemo(
    () => watchlists.filter((item) => item.watchlist_id !== watchlistId && !isSystemWatchlist(item)),
    [watchlists, watchlistId],
  )
  const copyTargetWatchlist =
    moveTargetOptions.find((item) => item.watchlist_id === copyTargetWatchlistId) || moveTargetOptions[0] || null
  const moveTargetWatchlist =
    moveTargetOptions.find((item) => item.watchlist_id === moveTargetWatchlistId) || moveTargetOptions[0] || null
  const selectedSharedInstrument =
    sharedInstrumentResults.find((item) => item.instrument_id === selectedInstrumentId) || null
  const mergedFieldRegistry = displayFieldRegistry
  const applicableTaxonomyNodes = useMemo(
    () =>
      (instrumentTaxonomy?.nodes || []).filter(
        (node) => activeInstrumentTypes.includes(node.instrument_type),
      ),
    [activeInstrumentTypes, instrumentTaxonomy],
  )
  const taxonomyInstrumentTypes = WATCHLIST_SUPPORTED_INSTRUMENT_TYPES.filter((instrumentType) =>
    applicableTaxonomyNodes.some((node) => node.instrument_type === instrumentType),
  )
  const taxonomyNodesByParent = useMemo(() => {
    const map = new Map<string | null, InstrumentTaxonomyTreeNode[]>()
    applicableTaxonomyNodes.forEach((node) => {
      const key = node.parent_node_id || null
      map.set(key, [...(map.get(key) || []), node])
    })
    map.forEach((nodes) => {
      nodes.sort((left, right) => left.display_order - right.display_order || left.label.localeCompare(right.label, 'zh-Hans-CN'))
    })
    return map
  }, [applicableTaxonomyNodes])
  const taxonomyNodeByPath = useMemo(() => {
    const map = new Map<string, InstrumentTaxonomyTreeNode>()
    applicableTaxonomyNodes.forEach((node) => {
      map.set(`${node.instrument_type}::${taxonomyPathKey(node.path_labels)}`, node)
    })
    return map
  }, [applicableTaxonomyNodes])
  const taxonomyDisplayOrderByTypeAndPath = useMemo(() => {
    const map = new Map<string, number>()
    let index = 0
    const visit = (parentId: string | null) => {
      ;(taxonomyNodesByParent.get(parentId) || []).forEach((node) => {
        map.set(`${node.instrument_type}::${taxonomyPathKey(node.path_labels)}`, index)
        index += 1
        visit(node.node_id)
      })
    }
    visit(null)
    return map
  }, [taxonomyNodesByParent])
  const ensureRequiredColumns = (columns: string[]) => {
    const seen = new Set<string>()
    const normalized: string[] = []
    ;[...requiredColumns, ...columns].forEach((field) => {
      if (!field || seen.has(field)) {
        return
      }
      seen.add(field)
      normalized.push(field)
    })
    return normalized
  }
  function viewColumnWidths(view: WatchlistView | null) {
    const nextWidths: Record<string, number> = {}
    view?.column_meta?.forEach((item) => {
      if (typeof item.width === 'number') {
        nextWidths[item.field_key] = item.width
      }
    })
    return nextWidths
  }

  function applyWatchlistView(view: WatchlistView | null) {
    const baseColumns = view?.columns?.length ? view.columns : [primaryDisplayColumn]
    const enforced = ensureRequiredColumns(baseColumns)
    setWorkingColumns(enforced)
    setColumnDraft(enforced)
    setWorkingGroupBy(view?.default_group_by || 'none')
    setWorkingFilters(normalizeFilterState(view?.default_filters))
    setSortRules(view?.default_sort?.length ? view.default_sort : [])
    setColumnWidths(viewColumnWidths(view))
  }

  const fieldLabelByKey = useMemo(
    () => new Map([...mergedFieldRegistry.map((field) => [field.field_key, field.label] as const), [TAXONOMY_GROUP_BY_CODE, 'Taxonomy']]),
    [mergedFieldRegistry],
  )
  const fieldByKey = useMemo(
    () => new Map(mergedFieldRegistry.map((field) => [field.field_key, field] as const)),
    [mergedFieldRegistry],
  )
  const defaultWidthByKey = useMemo(() => {
    const map = new Map<string, number>()
    mergedFieldRegistry.forEach((field) => {
      if (typeof field.default_width === 'number') {
        map.set(field.field_key, field.default_width)
      }
    })
    return map
  }, [mergedFieldRegistry])
  const visibleColumns = ensureRequiredColumns(
    (workingColumns.length ? workingColumns : [primaryDisplayColumn]).filter((fieldKey) =>
      columnKeys.has(fieldKey),
    ),
  )
  const baseColumns = ensureRequiredColumns(
    (activeView?.columns || []).filter((fieldKey) => fieldKeys.has(fieldKey)),
  )
  const baseGroupBy = activeView?.default_group_by || 'none'
  const baseSort = activeView?.default_sort || []
  const baseFilters = useMemo(
    () => normalizeFilterState(activeView?.default_filters),
    [activeView?.default_filters],
  )
  const baseWidthByKey = useMemo(() => {
    const map = new Map<string, number>()
    activeView?.column_meta?.forEach((item) => {
      if (typeof item.width === 'number') {
        map.set(item.field_key, item.width)
      }
    })
    return map
  }, [activeView?.column_meta])
  const serializeViewColumnWidths = (columns: string[], widths: Map<string, number> | Record<string, number>) =>
    JSON.stringify(
      columns.map((fieldKey) => [
        fieldKey,
        widths instanceof Map
          ? widths.get(fieldKey) ?? defaultWidthByKey.get(fieldKey) ?? null
          : widths[fieldKey] ?? defaultWidthByKey.get(fieldKey) ?? null,
      ]),
    )
  const viewColumnWidthsEdited =
    serializeViewColumnWidths(visibleColumns, columnWidths) !== serializeViewColumnWidths(baseColumns, baseWidthByKey)
  const viewEdited =
    JSON.stringify(visibleColumns) !== JSON.stringify(baseColumns) ||
    workingGroupBy !== baseGroupBy ||
    serializeFilterState(workingFilters) !== serializeFilterState(baseFilters) ||
    JSON.stringify(sortRules) !== JSON.stringify(baseSort) ||
    viewColumnWidthsEdited
  const workingViewSignature = JSON.stringify({
    columns: visibleColumns,
    groupBy: workingGroupBy,
    filters: workingFilters,
    sortRules,
    columnWidths: visibleColumns.map((fieldKey) => [
      fieldKey,
      columnWidths[fieldKey] ?? defaultWidthByKey.get(fieldKey) ?? null,
    ]),
  })

  useEffect(() => {
    if (!detailIsCurrent || !activeView) {
      return
    }
    setWatchlistSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        next.set('view', activeView.view_id)
        if (watchlistSearch) {
          next.set('q', watchlistSearch)
        } else {
          next.delete('q')
        }
        if (workingGroupBy !== baseGroupBy) {
          next.set('group', workingGroupBy)
        } else {
          next.delete('group')
        }
        if (serializeFilterState(workingFilters) !== serializeFilterState(baseFilters)) {
          next.set('filters', JSON.stringify(workingFilters))
        } else {
          next.delete('filters')
        }
        if (JSON.stringify(sortRules) !== JSON.stringify(baseSort)) {
          next.set('sort', JSON.stringify(sortRules))
        } else {
          next.delete('sort')
        }
        return next.toString() === current.toString() ? current : next
      },
      { replace: true },
    )
  }, [
    activeView,
    baseFilters,
    baseGroupBy,
    baseSort,
    detailIsCurrent,
    setWatchlistSearchParams,
    sortRules,
    watchlistSearch,
    workingFilters,
    workingGroupBy,
  ])
  const compactWatchlistColumns = useMemo(
    () =>
      compactTableColumnWidths(
        visibleColumns,
        (column) => columnWidths[column] ?? defaultWidthByKey.get(column) ?? WATCHLIST_DEFAULT_COLUMN_WIDTH,
        (column) => getWatchlistCompactMinWidth(column, fieldByKey.get(column)),
        tableShellWidth,
        WATCHLIST_SELECT_COLUMN_WIDTH,
      ),
    [columnWidths, defaultWidthByKey, fieldByKey, tableShellWidth, visibleColumns],
  )
  const displayColumnWidths = compactWatchlistColumns.widths
  const watchlistTableMinWidth = compactWatchlistColumns.totalWidth
  const activeGroupBy = workingGroupBy && workingGroupBy !== 'none' ? workingGroupBy : null
  const usesFixedInstrumentTypeSections =
    activeGroupBy === TAXONOMY_GROUP_BY_CODE && activeInstrumentTypes.length > 1
  const showGroupHeaders = Boolean(activeGroupBy) || usesFixedInstrumentTypeSections
  const sortField = sortRules[0]?.field || null
  const sortDirection = sortRules[0]?.direction || 'asc'

  function toggleSort(column: string) {
    setSortRules((current) => {
      const currentRule = current[0]
      if (!currentRule || currentRule.field !== column) {
        return [{ field: column, direction: 'asc' }]
      }
      if (currentRule.direction === 'asc') {
        return [{ field: column, direction: 'desc' }]
      }
      return []
    })
  }

  function moveWorkingColumn(sourceColumn: string, targetColumn: string) {
    if (!sourceColumn || sourceColumn === targetColumn || requiredColumns.includes(sourceColumn)) {
      return
    }
    setWorkingColumns((current) => {
      const normalized = ensureRequiredColumns(current.length ? current : visibleColumns)
      const sourceIndex = normalized.indexOf(sourceColumn)
      const targetIndex = normalized.indexOf(targetColumn)
      if (sourceIndex < 0 || targetIndex < 0) {
        return normalized
      }
      const next = [...normalized]
      const [moved] = next.splice(sourceIndex, 1)
      let insertIndex = requiredColumns.includes(targetColumn) ? requiredColumns.length : targetIndex
      if (sourceIndex < targetIndex) {
        insertIndex = targetIndex
      }
      next.splice(insertIndex, 0, moved)
      return ensureRequiredColumns(next)
    })
  }

  function handleColumnDragStart(event: React.DragEvent<HTMLTableCellElement>, column: string) {
    if (requiredColumns.includes(column)) {
      event.preventDefault()
      return
    }
    event.dataTransfer.setData('text/plain', column)
    event.dataTransfer.effectAllowed = 'move'
  }

  function handleColumnDragOver(event: React.DragEvent<HTMLTableCellElement>, column: string) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setColumnDropTarget(column)
  }

  function handleColumnDrop(event: React.DragEvent<HTMLTableCellElement>, targetColumn: string) {
    event.preventDefault()
    moveWorkingColumn(event.dataTransfer.getData('text/plain'), targetColumn)
    setColumnDropTarget('')
  }

  useEffect(() => {
    setCollapsedGroupKeys(new Set())
  }, [activeGroupBy, screenerCriteriaKey])

  const filteredFieldRegistry = columnFieldRegistry
    .filter((field) => field.field_key !== primaryDisplayColumn)
    .map((field) => ({
      ...field,
      label: COLUMN_CHOOSER_LABELS[field.field_key]?.[zh ? 0 : 1] || t(field.label),
      description: COLUMN_CHOOSER_DESCRIPTIONS[field.field_key]?.[zh ? 0 : 1] || t(field.description || ''),
      section: COLUMN_STATUS_FIELDS.has(field.field_key)
        ? 'status'
        : field.field_key.startsWith('return_') ? 'returns' : 'identity',
    }))
    .filter((field) => !fieldSearch.trim()
      || matchesSystemLabel(field.label, fieldSearch)
      || field.field_key.toLowerCase().includes(fieldSearch.trim().toLowerCase()))
  const columnSections = [
    { key: 'identity', label: zh ? '基本信息' : 'Basic information' },
    { key: 'returns', label: zh ? '收益表现' : 'Returns' },
    { key: 'status', label: zh ? '风险与状态' : 'Risk and status' },
  ]
  const availableGroupByOptions = watchlistDetail?.available_group_bys || []

  useEffect(() => {
    if (!detailIsCurrent || !watchlistDetail || !columnFieldKeys.length) {
      return
    }
    const selectedColumns = (columns: string[]) =>
      ensureRequiredColumns(columns.filter((fieldKey) => columnKeys.has(fieldKey)))
    setWorkingColumns((current) => {
      const next = selectedColumns(current)
      return JSON.stringify(next) === JSON.stringify(current) ? current : next
    })
    setColumnDraft((current) => {
      const next = selectedColumns(current)
      return JSON.stringify(next) === JSON.stringify(current) ? current : next
    })
    setWorkingFilters((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([fieldKey]) => filterKeys.has(fieldKey)),
      )
      return JSON.stringify(next) === JSON.stringify(current) ? current : next
    })
    setSortRules((current) => {
      const next = current.filter((rule) => columnKeys.has(rule.field))
      return JSON.stringify(next) === JSON.stringify(current) ? current : next
    })
    setWorkingGroupBy((current) => (availableGroupByCodes.has(current) ? current : 'none'))
  }, [availableGroupByCodes, detailIsCurrent, columnFieldKeys.length, columnKeys, filterKeys, watchlistDetail])

  const filterableFields = useMemo(
    () => [
      TAXONOMY_FILTER_FIELD,
      ...fieldRegistry.filter((field) => filterFieldKeys.includes(field.field_key)),
    ],
    [fieldRegistry, filterFieldKeys],
  )
  const optionFilterFields = useMemo(
    () => filterableFields.filter((field) => field.field_key !== TAXONOMY_FILTER_FIELD_KEY),
    [filterableFields],
  )

  useEffect(() => {
    if (selectedFilterField && filterableFields.some((field) => field.field_key === selectedFilterField)) {
      return
    }
    setSelectedFilterField(filterableFields[0]?.field_key || '')
  }, [filterableFields, selectedFilterField])

  const filterFieldKeySignature = useMemo(
    () => filterableFields.map((field) => field.field_key).join('|'),
    [filterableFields],
  )

  useEffect(() => {
    if (!watchlistId || !fieldRegistry.length) {
      setFilterOptionRows([])
      return
    }

    let cancelled = false
    loadAllScreenerRows({
      watchlist_id: watchlistId,
      selected_fields: Array.from(
        new Set([
          ...optionFilterFields.map((field) => field.field_key),
          ...TAXONOMY_GROUP_FIELD_KEYS,
        ]),
      ),
      filters: {},
      advanced_filters: null,
      sort: [],
      group_by: 'none',
    })
      .then((result) => {
        if (!cancelled) {
          setFilterOptionRows(result)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFilterOptionRows([])
        }
      })

    return () => {
      cancelled = true
    }
  }, [watchlistId, fieldRegistry.length, filterFieldKeySignature, reloadToken, optionFilterFields])

  const filterOptionsByField = useMemo(() => {
    const options = new Map<string, FilterOption[]>()
    optionFilterFields.forEach((field) => {
      options.set(
        field.field_key,
        buildFilterOptions(field.field_key, filterOptionRows, workingFilters[field.field_key] || []),
      )
    })
    return options
  }, [filterOptionRows, optionFilterFields, workingFilters])

  const activeTaxonomyFilterPath = useMemo(
    () => taxonomyPathFromFilters(workingFilters),
    [workingFilters],
  )
  const activeTaxonomyFilterInstrumentType =
    workingFilters.instrument_type?.length === 1 &&
    typeof workingFilters.instrument_type[0] === 'string'
      ? String(workingFilters.instrument_type[0])
      : null
  const activeTaxonomyFilterNode = activeTaxonomyFilterPath.length && activeTaxonomyFilterInstrumentType
    ? taxonomyNodeByPath.get(
        `${activeTaxonomyFilterInstrumentType}::${taxonomyPathKey(activeTaxonomyFilterPath)}`,
      ) || null
    : null
  const activeTaxonomyFilterLabel = activeTaxonomyFilterPath.length
    ? taxonomyPathKey(activeTaxonomyFilterPath)
    : null
  const activeFilterCount = useMemo(() => {
    const nonTaxonomyCount = Object.entries(workingFilters).filter(
      ([fieldKey, values]) =>
        !isTaxonomyFieldKey(fieldKey) &&
        !(activeTaxonomyFilterPath.length && fieldKey === 'instrument_type') &&
        values.length,
    ).length
    return nonTaxonomyCount + (activeTaxonomyFilterPath.length ? 1 : 0)
  }, [workingFilters, activeTaxonomyFilterPath])

  const activeFilterEntries = useMemo(
    () => {
      const entries: ActiveFilterEntry[] = Object.entries(workingFilters).flatMap(([fieldKey, values]) => {
        if (
          isTaxonomyFieldKey(fieldKey) ||
          (activeTaxonomyFilterPath.length && fieldKey === 'instrument_type')
        ) {
          return []
        }
        return values.map((value) => ({
          fieldKey,
          value,
          label: fieldLabelByKey.get(fieldKey) || formatLabel(fieldKey),
          valueLabel: formatFilterOptionLabel(value),
          isTaxonomy: false,
        }))
      })
      if (activeTaxonomyFilterLabel) {
        entries.unshift({
          fieldKey: TAXONOMY_FILTER_FIELD_KEY,
          value: activeTaxonomyFilterNode?.node_id || activeTaxonomyFilterLabel,
          label: 'Taxonomy',
          valueLabel: activeTaxonomyFilterLabel,
          isTaxonomy: true,
        })
      }
      return entries
    },
    [activeTaxonomyFilterLabel, activeTaxonomyFilterNode, fieldLabelByKey, workingFilters],
  )
  const taxonomyFilterCountByPath = useMemo(() => {
    const counts = new Map<string, number>()
    filterOptionRows.forEach((row) => {
      const path = taxonomyPathFromRow(row)
      const instrumentType = String(row.instrument_type || '')
      path.forEach((_, index) => {
        const key = `${instrumentType}::${taxonomyPathKey(path.slice(0, index + 1))}`
        counts.set(key, (counts.get(key) || 0) + 1)
      })
    })
    return counts
  }, [filterOptionRows])
  function isVisibleTaxonomyFilterNode(node: InstrumentTaxonomyTreeNode) {
    return taxonomyFilterCountByPath.has(`${node.instrument_type}::${taxonomyPathKey(node.path_labels)}`)
      || Boolean(activeTaxonomyFilterNode?.path_node_ids.includes(node.node_id))
  }
  const taxonomyFilterInstrumentTypes = taxonomyInstrumentTypes.filter((instrumentType) =>
    applicableTaxonomyNodes.some((node) => node.instrument_type === instrumentType && isVisibleTaxonomyFilterNode(node)),
  )
  const selectedFilterFieldRecord =
    filterableFields.find((field) => field.field_key === selectedFilterField) || null
  const selectedFilterOptions = selectedFilterField
    ? filterOptionsByField.get(selectedFilterField) || []
    : []

  useEffect(() => {
    if (!workingGroupBy || workingGroupBy === 'none') {
      return
    }
    if (!availableGroupByOptions.some((option) => option.code === workingGroupBy)) {
      setWorkingGroupBy('none')
    }
  }, [availableGroupByOptions, workingGroupBy])

  const watchlistSearchQuery = watchlistSearch.trim().toLowerCase()
  useEffect(() => {
    setRenderRowLimit(WATCHLIST_INITIAL_RENDER_ROWS)
  }, [screenerCriteriaKey, watchlistSearchQuery])
  const searchedRows = useMemo(() => {
    const rows = screenerResult?.rows || []
    if (!watchlistSearchQuery) {
      return rows
    }
    return rows.filter((row) => rowMatchesWatchlistSearch(row, watchlistSearchQuery))
  }, [screenerResult, watchlistSearchQuery])

  const groupedRows = useMemo(() => {
    const rows = searchedRows
    const buildInnerGroups = (
      innerRows: Array<Record<string, unknown>>,
      instrumentType: string,
    ): WatchlistRowGroup[] => {
      if (!activeGroupBy) {
        return [{ key: 'all', label: null, rows: innerRows, summaryRows: innerRows, rowCount: innerRows.length, depth: 0 }]
      }
      if (activeGroupBy !== TAXONOMY_GROUP_BY_CODE) {
        const bucketMap = new Map<string, Array<Record<string, unknown>>>()
        innerRows.forEach((row) => {
          const rawValue = row[activeGroupBy]
          const key = rawValue == null || rawValue === '' ? 'Unspecified' : String(rawValue)
          bucketMap.set(key, [...(bucketMap.get(key) || []), row])
        })
        const orderedKeys = screenerResult?.groups.length
          ? screenerResult.groups.map((group) => group.group_value || 'Unspecified')
          : Array.from(bucketMap.keys())
        const seen = new Set<string>()
        const groups = orderedKeys
          .filter((key) => {
            if (!bucketMap.has(key) || seen.has(key)) {
              return false
            }
            seen.add(key)
            return true
          })
          .map((key) => ({
            key,
            label: key,
            rows: bucketMap.get(key) || [],
            summaryRows: bucketMap.get(key) || [],
            rowCount: bucketMap.get(key)?.length || 0,
            depth: 0,
          }))
        bucketMap.forEach((value, key) => {
          if (!seen.has(key)) {
            groups.push({ key, label: key, rows: value, summaryRows: value, rowCount: value.length, depth: 0 })
          }
        })
        return groups
      }

      type TreeNode = {
        key: string
        label: string
        depth: number
        rowCount: number
        rows: Array<Record<string, unknown>>
        summaryRows: Array<Record<string, unknown>>
        children: Map<string, TreeNode>
      }
      const root = new Map<string, TreeNode>()
      const getOrCreate = (
        map: Map<string, TreeNode>,
        path: string[],
        label: string,
        depth: number,
      ) => {
        const key = taxonomyPathKey(path)
        const existing = map.get(key)
        if (existing) {
          return existing
        }
        const node = {
          key,
          label,
          depth,
          rowCount: 0,
          rows: [],
          summaryRows: [],
          children: new Map<string, TreeNode>(),
        }
        map.set(key, node)
        return node
      }

      innerRows.forEach((row) => {
        const path = taxonomyPathFromRow(row)
        if (!path.length) {
          const node = getOrCreate(root, ['Unspecified'], 'Unspecified', 0)
          node.rowCount += 1
          node.rows.push(row)
          node.summaryRows.push(row)
          return
        }
        let currentMap = root
        let currentNode: TreeNode | null = null
        for (const [index, label] of path.entries()) {
          currentNode = getOrCreate(currentMap, path.slice(0, index + 1), label, index)
          currentNode.rowCount += 1
          currentNode.summaryRows.push(row)
          currentMap = currentNode.children
        }
        currentNode?.rows.push(row)
      })

      const sortNodes = (nodes: TreeNode[]) =>
        nodes.sort((left, right) => {
          const leftOrder = taxonomyDisplayOrderByTypeAndPath.get(`${instrumentType}::${left.key}`) ?? Number.MAX_SAFE_INTEGER
          const rightOrder = taxonomyDisplayOrderByTypeAndPath.get(`${instrumentType}::${right.key}`) ?? Number.MAX_SAFE_INTEGER
          return leftOrder - rightOrder || left.label.localeCompare(right.label, 'zh-Hans-CN')
        })
      const flattened: WatchlistRowGroup[] = []
      const visit = (nodes: TreeNode[]) => {
        sortNodes(nodes).forEach((node) => {
          flattened.push({
            key: node.key,
            label: node.label,
            rows: node.children.size ? [] : node.rows,
            summaryRows: node.summaryRows,
            rowCount: node.rowCount,
            depth: node.depth,
            taxonomyPath: node.key === 'Unspecified' ? [] : node.key.split(' / '),
          })
          visit([...node.children.values()])
          if (node.children.size && node.rows.length) {
            flattened.push({
              key: `${node.key}::direct`,
              label: `${node.label} · Direct`,
              rows: node.rows,
              summaryRows: node.rows,
              rowCount: node.rows.length,
              depth: node.depth + 1,
              taxonomyPath: node.key === 'Unspecified' ? [] : node.key.split(' / '),
            })
          }
        })
      }
      visit([...root.values()])
      return flattened.length
        ? flattened
        : [{ key: 'all', label: null, rows: innerRows, summaryRows: innerRows, rowCount: innerRows.length, depth: 0 }]
    }

    if (!usesFixedInstrumentTypeSections) {
      const instrumentType = String(rows[0]?.instrument_type || activeInstrumentTypes[0] || '')
      return buildInnerGroups(rows, instrumentType)
    }

    const rowsByInstrumentType = new Map<string, Array<Record<string, unknown>>>()
    rows.forEach((row) => {
      const instrumentType = String(row.instrument_type || 'other')
      rowsByInstrumentType.set(instrumentType, [
        ...(rowsByInstrumentType.get(instrumentType) || []),
        row,
      ])
    })
    const orderedTypes = [
      ...WATCHLIST_SUPPORTED_INSTRUMENT_TYPES.filter((instrumentType) => rowsByInstrumentType.has(instrumentType)),
      ...Array.from(rowsByInstrumentType.keys())
        .filter((instrumentType) => !WATCHLIST_SUPPORTED_INSTRUMENT_TYPES.includes(instrumentType as typeof WATCHLIST_SUPPORTED_INSTRUMENT_TYPES[number]))
        .sort(),
    ]
    return orderedTypes.flatMap((instrumentType) => {
      const typeRows = rowsByInstrumentType.get(instrumentType) || []
      const sectionKey = `instrument-type:${instrumentType}`
      const section: WatchlistRowGroup = {
        key: sectionKey,
        label: instrumentTypeLabel(instrumentType),
        rows: activeGroupBy ? [] : typeRows,
        summaryRows: typeRows,
        rowCount: typeRows.length,
        depth: 0,
        isInstrumentTypeSection: true,
      }
      if (!activeGroupBy) {
        return [section]
      }
      return [
        section,
        ...buildInnerGroups(typeRows, instrumentType).map((group) => ({
          ...group,
          key: `${sectionKey}/${group.key}`,
          depth: group.depth + 1,
        })),
      ]
    })
  }, [activeGroupBy, activeInstrumentTypes, screenerResult?.groups, searchedRows, taxonomyDisplayOrderByTypeAndPath, usesFixedInstrumentTypeSections])

  const visibleGroupedRows = useMemo(() => {
    let collapsedDepth: number | null = null
    return groupedRows.flatMap((group) => {
      if (collapsedDepth != null) {
        if (group.depth > collapsedDepth) {
          return []
        }
        collapsedDepth = null
      }

      const collapsed = collapsedGroupKeys.has(group.key)
      if (collapsed) {
        collapsedDepth = group.depth
      }
      return [{ ...group, rows: collapsed ? [] : group.rows }]
    })
  }, [collapsedGroupKeys, groupedRows])

  const renderedGroupedRows = useMemo(() => {
    const rendered: WatchlistRowGroup[] = []
    let remaining = renderRowLimit
    for (const group of visibleGroupedRows) {
      if (remaining <= 0) {
        break
      }
      const rows = group.rows.slice(0, remaining)
      rendered.push({ ...group, rows })
      remaining -= rows.length
    }
    return rendered
  }, [renderRowLimit, visibleGroupedRows])
  const renderedInstrumentCount = useMemo(
    () => renderedGroupedRows.reduce((total, group) => total + group.rows.length, 0),
    [renderedGroupedRows],
  )
  const metricAsOfRangeLabel = (() => {
    const metadata = screenerResult?.snapshot_metadata
    const missingSuffix = metadata?.as_of_date_missing_count
      ? ` · ${metadata.as_of_date_missing_count} unavailable`
      : ''
    if (!metadata?.as_of_date_max) {
      return `Metric as-of unavailable${missingSuffix}`
    }
    if (
      metadata.has_mixed_as_of_dates &&
      metadata.as_of_date_min &&
      metadata.as_of_date_min !== metadata.as_of_date_max
    ) {
      return `Per-instrument metric as-of ${metadata.as_of_date_min} to ${metadata.as_of_date_max}${missingSuffix}`
    }
    return `Metrics as of ${metadata.as_of_date_max}${missingSuffix}`
  })()

  const sortabilityByKey = useMemo(() => {
    const map = new Map<string, string>()
    mergedFieldRegistry.forEach((field) => {
      map.set(field.field_key, field.sort_mode)
    })
    return map
  }, [mergedFieldRegistry])

  const buildViewPayload = (
    name: string,
    description: string | null,
    settings: Partial<WatchlistViewSettings> = {},
  ) => {
    const advancedFilters =
      activeView?.default_advanced_filters &&
      typeof activeView.default_advanced_filters === 'object'
        ? activeView.default_advanced_filters
        : null
    const enforcedColumns = ensureRequiredColumns(settings.columns ?? visibleColumns)
    const selectedColumnWidths = settings.columnWidths ?? columnWidths
    return {
      name,
      description,
      default_group_by: settings.groupBy ?? workingGroupBy,
      default_sort: settings.sortRules ?? sortRules,
      default_filters: settings.filters ?? workingFilters,
      default_advanced_filters: advancedFilters,
      columns: enforcedColumns.map((field_key, index) => ({
        field_key,
        display_order: index,
        width: selectedColumnWidths[field_key] ?? defaultWidthByKey.get(field_key),
        is_visible: true,
      })),
    }
  }
  const allVisibleInstrumentIds = rowsAreCurrent
    ? renderedGroupedRows.flatMap((group) =>
        group.rows.map((row) => String(row.instrument_id)),
      )
    : []
  const allRowsSelected =
    allVisibleInstrumentIds.length > 0 && allVisibleInstrumentIds.every((instrumentId) => selectedRows.includes(instrumentId))

  async function refreshWatchlistDetail(nextViewId?: string, targetWatchlistId: string = watchlistId) {
    if (!targetWatchlistId) {
      return
    }
    try {
      const detail = await getWatchlistDetail(targetWatchlistId)
      if (activeWatchlistIdRef.current !== targetWatchlistId) {
        return
      }
      setWatchlistDetail(detail)
      setWatchlistDetailOwnerId(targetWatchlistId)
      const nextId =
        nextViewId || detail.default_view_id || detail.views[0]?.view_id || activeViewId || ''
      setActiveViewId(nextId)
    } catch (loadError) {
      if (activeWatchlistIdRef.current === targetWatchlistId) {
        setError(loadError instanceof Error ? loadError.message : 'Failed to refresh watchlist.')
      }
    }
  }

  function openSaveViewModal() {
    const defaultName = activeView?.name ? `${activeView.name} Copy` : 'Custom View'
    setSaveViewName(defaultName)
    setSaveViewDescription('')
    setModalKind('save-view')
    setModalError(null)
    setFilterMenuOpen(false)
    setGroupMenuOpen(false)
    setNotice(null)
  }

  async function persistActiveWatchlistView(settings: WatchlistViewSettings) {
    const sourceWatchlistId = detailIsCurrent ? watchlistDetailOwnerId : ''
    if (!sourceWatchlistId || !activeView) {
      return
    }
    const viewId = activeView.view_id
    const viewKey = `${sourceWatchlistId}\u0000${viewId}`
    const saveSequence = viewSaveSequenceRef.current + 1
    viewSaveSequenceRef.current = saveSequence
    latestViewSaveSequenceRef.current.set(viewKey, saveSequence)
    const payload = buildViewPayload(activeView.name, activeView.description, settings)
    try {
      const updated = await viewSaveQueueRef.current.enqueue(() =>
        updateWatchlistView(sourceWatchlistId, viewId, payload),
      )
      if (
        activeWatchlistIdRef.current !== sourceWatchlistId ||
        latestViewSaveSequenceRef.current.get(viewKey) !== saveSequence
      ) {
        return
      }
      setWatchlistDetail((current) => {
        if (!current || current.watchlist_id !== sourceWatchlistId) {
          return current
        }
        return {
          ...current,
          views: current.views.map((view) =>
            view.view_id === viewId || view.view_id === updated.view_id ? updated : view,
          ),
        }
      })
      if (updated.view_id !== viewId) setActiveViewId(updated.view_id)
      setError(null)
    } catch (saveError) {
      if (
        activeWatchlistIdRef.current === sourceWatchlistId &&
        latestViewSaveSequenceRef.current.get(viewKey) === saveSequence
      ) {
        setError(saveError instanceof Error ? saveError.message : 'Failed to update view.')
      }
    }
  }

  useEffect(() => {
    if (loading || !detailIsCurrent || !activeView || columnResizing || !viewEdited) {
      return undefined
    }
    const settings: WatchlistViewSettings = {
      columns: visibleColumns,
      groupBy: workingGroupBy,
      sortRules,
      filters: workingFilters,
      columnWidths,
    }
    const timeoutId = window.setTimeout(
      () => void persistActiveWatchlistView(settings),
      WATCHLIST_VIEW_AUTOSAVE_DELAY_MS,
    )
    return () => window.clearTimeout(timeoutId)
  }, [activeView?.view_id, columnResizing, detailIsCurrent, loading, viewEdited, workingViewSignature])

  async function handleDownloadCurrentView(format: TableExportFormat) {
    if (!baseScreenerPayload || !screenerResult?.total_rows) {
      setNotice('No visible rows to export.')
      return
    }

    setIsExporting(true)
    setError(null)
    setNotice(null)
    try {
      const exportRows = await loadAllScreenerRows(baseScreenerPayload)
      const includesEndpointSensitiveMetrics = visibleColumns.some(isMetricAsOfSensitiveField)
      const semanticColumns = includesEndpointSensitiveMetrics
        ? ['metric_as_of_date', 'metric_return_kind', 'metric_quote_basis', 'metric_series_type']
        : []
      downloadWatchlistRows(
        `watchlist-${fileNameSlug(activeWatchlist?.name || watchlistDetail?.name || watchlistId)}-${fileNameSlug(activeView?.name || 'default')}`,
        [...new Set(['instrument_id', ...visibleColumns, ...semanticColumns])],
        exportRows,
        format,
      )
      setNotice(`Exported ${exportRows.length} rows from the current watchlist view.`)
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : 'Failed to export watchlist view.')
    } finally {
      setIsExporting(false)
    }
  }

  function resetCreateWatchlistForm() {
    setCreateWatchlistName('')
    setCreateWatchlistDescription('')
    setIsCreatingWatchlist(false)
    setModalError(null)
  }

  function resetCopyItemsForm() {
    setCopyTargetWatchlistId(moveTargetOptions[0]?.watchlist_id || '')
    setIsCopyingItems(false)
    setModalError(null)
  }

  function resetMoveItemsForm() {
    setMoveTargetWatchlistId(moveTargetOptions[0]?.watchlist_id || '')
    setIsMovingItems(false)
    setModalError(null)
  }

  function toggleFilterValue(fieldKey: string, value: unknown) {
    setWorkingFilters((current) => {
      const currentValues = current[fieldKey] || []
      const valueKey = filterValueKey(value)
      const exists = currentValues.some((item) => filterValueKey(item) === valueKey)
      const nextValues = exists
        ? currentValues.filter((item) => filterValueKey(item) !== valueKey)
        : [...currentValues, value]
      const next = { ...current }
      if (nextValues.length) {
        next[fieldKey] = nextValues
      } else {
        delete next[fieldKey]
      }
      return next
    })
  }

  function setTaxonomyFilter(node: InstrumentTaxonomyTreeNode) {
    setWorkingFilters((current) => {
      const next = removeTaxonomyFilters(current)
      next.instrument_type = [node.instrument_type]
      node.path_labels.forEach((label, index) => {
        next[taxonomyFieldKeyForPathIndex(index)] = [label]
      })
      return next
    })
  }

  function clearTaxonomyFilter() {
    setWorkingFilters((current) => removeTaxonomyFilters(current))
  }

  function clearFilterField(fieldKey: string) {
    if (fieldKey === TAXONOMY_FILTER_FIELD_KEY) {
      clearTaxonomyFilter()
      return
    }
    setWorkingFilters((current) => {
      if (!(fieldKey in current)) {
        return current
      }
      const next = { ...current }
      delete next[fieldKey]
      return next
    })
  }

  function renderTaxonomyFilterNodes(
    parentNodeId: string | null = null,
    rootInstrumentType?: string,
  ): React.ReactNode {
    const nodes = (taxonomyNodesByParent.get(parentNodeId) || []).filter(
      (node) => (parentNodeId !== null || !rootInstrumentType || node.instrument_type === rootInstrumentType)
        && isVisibleTaxonomyFilterNode(node),
    )
    if (!nodes.length) {
      return null
    }
    return nodes.map((node) => {
      const pathKey = `${node.instrument_type}::${taxonomyPathKey(node.path_labels)}`
      const selected = activeTaxonomyFilterNode?.node_id === node.node_id
      const ancestor =
        !!activeTaxonomyFilterNode &&
        activeTaxonomyFilterNode.path_node_ids.includes(node.node_id) &&
        !selected
      const count = taxonomyFilterCountByPath.get(pathKey)
      return (
        <React.Fragment key={node.node_id}>
          <button
            type="button"
            className={[
              'watchlists-taxonomy-node',
              selected ? 'watchlists-taxonomy-node-active' : '',
              ancestor ? 'watchlists-taxonomy-node-ancestor' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            style={{ paddingLeft: `${10 + node.level_index * 18}px` }}
            onClick={() => setTaxonomyFilter(node)}
          >
            <span className="watchlists-taxonomy-node-label">{node.label}</span>
            {typeof count === 'number' ? (
              <span className="watchlists-taxonomy-node-count">{count}</span>
            ) : null}
          </button>
          {renderTaxonomyFilterNodes(node.node_id)}
        </React.Fragment>
      )
    })
  }

  useEffect(() => {
    function handleResizeMove(event: MouseEvent) {
      if (!resizeState.current) {
        return
      }
      const { column, startX, startWidth } = resizeState.current
      const delta = event.clientX - startX
      const nextWidth = Math.min(Math.max(startWidth + delta, 90), 420)
      pendingResize.current = { column, width: nextWidth }
      if (resizeFrame.current == null) {
        resizeFrame.current = window.requestAnimationFrame(() => {
          if (pendingResize.current) {
            const { column: pendingColumn, width } = pendingResize.current
            setColumnWidths((current) => ({ ...current, [pendingColumn]: width }))
          }
          pendingResize.current = null
          resizeFrame.current = null
        })
      }
    }

    function handleResizeEnd() {
      const wasResizing = Boolean(resizeState.current)
      resizeState.current = null
      if (resizeFrame.current != null) {
        window.cancelAnimationFrame(resizeFrame.current)
        resizeFrame.current = null
      }
      if (pendingResize.current) {
        const { column, width } = pendingResize.current
        setColumnWidths((current) => ({ ...current, [column]: width }))
      }
      pendingResize.current = null
      if (wasResizing) {
        setColumnResizing(false)
      }
      document.body.style.cursor = ''
    }

    window.addEventListener('mousemove', handleResizeMove)
    window.addEventListener('mouseup', handleResizeEnd)
    return () => {
      window.removeEventListener('mousemove', handleResizeMove)
      window.removeEventListener('mouseup', handleResizeEnd)
    }
  }, [])


  function openColumns() {
    setColumnDraft(ensureRequiredColumns(visibleColumns))
    setModalError(null)
    setModalKind('columns')
    setFilterMenuOpen(false)
    setGroupMenuOpen(false)
    setSelectorMenuOpen(false)
    setNotice(null)
  }

  function openRisk(id?: string) {
    setSelectorMenuOpen(false)
    const next = new URLSearchParams(watchlistSearchParams)
    next.set('risk', '1')
    if (id) next.set('risk_instrument', id)
    else next.delete('risk_instrument')
    for (const key of ['assistant', 'topic', 'instruments', 'question']) next.delete(key)
    setRiskReferenceParams(next)
    setWatchlistSearchParams(next)
  }

  function openAssistant(id?: string, question?: string, reference?: ResearchAssistantReference) {
    setSelectorMenuOpen(false)
    const next = new URLSearchParams(watchlistSearchParams)
    next.set('assistant', '1')
    next.delete('risk'); next.delete('risk_instrument'); next.delete('topic')
    const ids = id || (rowsAreCurrent ? selectedRows.join(',') : '')
    if (ids) next.set('instruments', ids)
    else next.delete('instruments')
    if (question) next.set('question', question)
    else next.delete('question')
    setRiskReferenceParams(next, reference)
    setWatchlistSearchParams(next)
  }

  if (loading) {
    return (
      <div className="watchlists-page">
        <LoadingOverlay />
      </div>
    )
  }

  if (error && !watchlistDetail) {
    return (
      <div className="watchlists-page">
        <div className="panel">
          <div className="error-state">{error}</div>
        </div>
      </div>
    )
  }

  return (
    <>
      {watchlistSearchParams.get('assistant') === '1' && <ResearchPage watchlistId={watchlistId} onClose={() => {
        const next = new URLSearchParams(watchlistSearchParams)
        next.delete('assistant'); next.delete('topic'); next.delete('instruments'); next.delete('question')
        setRiskReferenceParams(next)
        setWatchlistSearchParams(next)
      }} />}
      {watchlistSearchParams.get('risk') === '1' && <WatchlistRiskDrawer
        key={watchlistId}
        watchlistId={watchlistId}
        watchlistName={activeWatchlist?.name || '当前列表'}
        focusInstrumentId={watchlistSearchParams.get('risk_instrument') || undefined}
        onClose={() => {
          const next = new URLSearchParams(watchlistSearchParams)
          next.delete('risk'); next.delete('risk_instrument')
          setWatchlistSearchParams(next)
        }}
        onAskAssistant={openAssistant}
        onChanged={() => setReloadToken((value) => value + 1)}
      />}
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <div className="watchlists-page">
      <div className="watchlists-pagehead">
        <div className="watchlists-topbar">
          <div className="watchlist-breadcrumbs">
            <a data-workspace-link href={HOME_URL} className="watchlist-breadcrumb-link">
              Home
            </a>
            <span className="watchlist-breadcrumb-separator">/</span>
            <Link to="/watchlists" className="watchlist-breadcrumb-link">
              Watchlist
            </Link>
            <span className="watchlist-breadcrumb-separator">/</span>
            <span translate={activeWatchlist && activeWatchlist.owner_type !== 'system' ? 'no' : undefined} className="watchlist-breadcrumb-current">{activeWatchlist?.name || 'Watchlists'}</span>
          </div>
          <LanguageSelector />
        </div>

        <div className="watchlist-heading-row">
          <h1 className="watchlist-app-title" translate={activeWatchlist && activeWatchlist.owner_type !== 'system' ? 'no' : undefined}>{activeWatchlist?.name || 'Watchlist'}</h1>
          <div className="watchlist-heading-tools" ref={selectorMenuRef}>
            <WorkspaceTools
              settings={{
                label: zh ? '列表设置' : 'Watchlist settings',
                disabled: !activeWatchlist || !detailIsCurrent,
                onClick: () => {
                  setSelectorMenuOpen((current) => !current)
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                },
              }}
              risk={{ onClick: () => openRisk() }}
              assistant={{ onClick: () => openAssistant(), count: rowsAreCurrent ? selectedRows.length : 0 }}
            />
            {selectorMenuOpen && activeWatchlist ? (
              <div className="watchlist-menu" role="group" aria-label={zh ? '列表设置菜单' : 'Watchlist settings menu'}>
                <button type="button" onClick={openColumns}>{zh ? '列设置' : 'Column settings'}</button>
                <button
                  type="button"
                  disabled={!canWriteTeam}
                  onClick={async () => {
                    try {
                      const copied = await copyWatchlist(activeWatchlist.watchlist_id)
                      setWatchlists((current) => [...current, copied])
                      setNotice(`Copied watchlist "${activeWatchlist.name}".`)
                    } catch (requestError) {
                      setError(requestError instanceof Error ? requestError.message : 'Failed to copy watchlist.')
                    } finally {
                      setSelectorMenuOpen(false)
                    }
                  }}
                >
                  Copy Watchlist
                </button>
                {!activeWatchlistIsSystem ? (
                  <button type="button" disabled={!canWriteTeam} onClick={() => {
                    setRenamingWatchlist(activeWatchlist)
                    setSelectorMenuOpen(false)
                  }}>{zh ? '重命名关注列表' : 'Rename Watchlist'}</button>
                ) : null}
                {!activeWatchlistIsSystem ? (
                  <button
                    type="button"
                    disabled={!canWriteTeam}
                    onClick={() => {
                      setConfirmError(null)
                      setPendingDeleteWatchlist(activeWatchlist)
                      setSelectorMenuOpen(false)
                    }}
                  >
                    Delete Watchlist
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="watchlists-switch-row">
          <Link to="/watchlists" className="watchlist-switcher-chip watchlist-switcher-chip-inactive watchlist-switcher-chip-home">
            <span className="watchlist-switcher-home-icon" aria-hidden="true">
              <svg viewBox="0 0 16 16">
                <path d="M2.5 7.2 8 2.8l5.5 4.4v5.5H9.8V9.5H6.2v3.2H2.5Z" fill="currentColor" />
              </svg>
            </span>
            <span className="watchlist-switcher-chip-label">All</span>
          </Link>
          {watchlists.map((watchlist) =>
            watchlist.watchlist_id === watchlistId ? (
              <div className="watchlist-switcher-chip watchlist-switcher-chip-active" key={watchlist.watchlist_id}>
                <Link
                  className="watchlist-switcher-chip-label watchlist-switcher-chip-label-active"
                  translate={watchlist.owner_type === 'system' ? undefined : 'no'}
                  to={buildWatchlistPath(watchlist.watchlist_id)}
                >
                  {watchlist.name}
                </Link>
              </div>
            ) : (
              <button
                type="button"
                key={watchlist.watchlist_id}
                className="watchlist-switcher-chip watchlist-switcher-chip-inactive"
                translate={watchlist.owner_type === 'system' ? undefined : 'no'}
                onClick={() => navigate(buildWatchlistPath(watchlist.watchlist_id))}
              >
                {watchlist.name}
              </button>
            ),
          )}

          <button
            type="button"
            className="watchlist-create-link"
            disabled={!canWriteTeam}
            onClick={() => {
              resetCreateWatchlistForm()
              setModalKind('create-watchlist')
              setNotice(null)
              setError(null)
            }}
          >
            + Create Watchlist
          </button>
        </div>

      </div>

      <section className="watchlists-main panel">
        <div className="watchlists-toolbar">
          <div className="watchlists-toolbar-left">
            <label className="watchlists-current-search">
              <span className="sr-only">Search this watchlist</span>
              <input
                type="search"
                value={watchlistSearch}
                onChange={(event) => {
                  setWatchlistSearch(event.target.value)
                  setSelectedRows([])
                }}
                placeholder="Search this watchlist"
              />
            </label>

            {!activeWatchlistIsSystem ? (
              <button
                type="button"
                className="watchlists-toolbar-button"
                disabled={!canWriteTeam || !detailIsCurrent}
                onClick={() => {
                  setInstrumentSearch('')
                  setSharedInstrumentResults([])
                  setSelectedInstrumentId('')
                  setModalError(null)
                  setModalKind('add')
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                  setNotice(null)
                  setError(null)
                }}
              >
                Add
              </button>
            ) : null}

            <div className="watchlists-view-group">
              <select
                className="watchlists-inline-select watchlists-inline-select-group"
                value={activeView?.view_id || ''}
                onChange={(event) => {
                  const nextViewId = event.target.value
                  const nextView =
                    watchlistDetail?.views.find((item) => item.view_id === nextViewId) || null
                  setActiveViewId(nextViewId)
                  applyWatchlistView(nextView)
                  setNotice(null)
                }}
              >
                {watchlistDetail?.views.map((view: WatchlistView) => (
                  <option key={view.view_key} value={view.view_id}>
                    {`View\u00A0: ${view.name}`}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="watchlists-plus-button"
                disabled={isSavingView}
                aria-label="Create view"
                title="Create view"
                onClick={openSaveViewModal}
              >
                <span className="watchlists-plus-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path
                      d="M12 5v14M5 12h14"
                      fill="none"
                      stroke="currentColor"
                      strokeLinecap="square"
                      strokeWidth="2"
                    />
                  </svg>
                </span>
              </button>
            </div>

            <button
              type="button"
              className="watchlists-toolbar-button"
              onClick={openColumns}
            >
              Columns
            </button>

            <div className="watchlists-dropdown" ref={groupMenuRef}>
              <button
                type="button"
                className="watchlists-toolbar-button"
                onClick={() => {
                  setGroupMenuOpen((current) => !current)
                  setFilterMenuOpen(false)
                  setModalKind(null)
                }}
              >
                Group By{'\u00A0: '}
                {workingGroupBy && workingGroupBy !== 'none'
                  ? fieldLabelByKey.get(workingGroupBy) || formatLabel(workingGroupBy)
                  : 'None'}
              </button>
              {groupMenuOpen ? (
                <div className="watchlists-menu">
                  {availableGroupByOptions.map((option) => (
                    <button
                      key={option.code}
                      type="button"
                      className={
                        option.code === workingGroupBy
                          ? 'watchlists-menu-item watchlists-menu-item-active'
                          : 'watchlists-menu-item'
                      }
                      onClick={() => {
                        setWorkingGroupBy(option.code)
                        setGroupMenuOpen(false)
                      }}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="watchlists-dropdown" ref={filterMenuRef}>
              <button
                type="button"
                className={
                  activeFilterCount
                    ? 'watchlists-toolbar-button watchlists-toolbar-button-active'
                    : 'watchlists-toolbar-button'
                }
                onClick={() => {
                  setFilterMenuOpen((current) => !current)
                  setGroupMenuOpen(false)
                  setModalKind(null)
                }}
              >
                Filter
                {activeFilterCount ? (
                  <span className="watchlists-toolbar-count">{activeFilterCount}</span>
                ) : null}
              </button>
              {filterMenuOpen ? (
                <div className="watchlists-menu watchlists-filter-menu">
                  <div className="watchlists-filter-header">
                    <div>
                      <div className="watchlists-filter-title">Filters</div>
                      <div className="watchlists-filter-subtitle">
                        {zh
                          ? '所有列表使用相同的筛选字段，具体值来自当前列表。'
                          : 'Filter fields are shared across watchlists; values come from the current list.'}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="watchlists-filter-clear"
                      disabled={!activeFilterCount}
                      onClick={() => setWorkingFilters({})}
                    >
                      Clear All
                    </button>
                  </div>

                  {filterableFields.length ? (
                    <div className="watchlists-filter-shell">
                      <div className="watchlists-filter-fields">
                        {filterableFields.map((field) => {
                          const fieldCount = workingFilters[field.field_key]?.length || 0
                          return (
                            <button
                              key={field.field_key}
                              type="button"
                              className={
                                field.field_key === selectedFilterField
                                  ? 'watchlists-filter-field watchlists-filter-field-active'
                                  : 'watchlists-filter-field'
                              }
                              onClick={() => setSelectedFilterField(field.field_key)}
                            >
                              <span>{field.label}</span>
                              {fieldCount ? (
                                <span className="watchlists-filter-field-count">{fieldCount}</span>
                              ) : null}
                            </button>
                          )
                        })}
                      </div>

                      <div className="watchlists-filter-values">
                        {selectedFilterFieldRecord ? (
                          <>
                            <div className="watchlists-filter-values-header">
                              <div>
                                <div className="watchlists-filter-values-title">
                                  {selectedFilterFieldRecord.label}
                                </div>
                                {selectedFilterFieldRecord.description && <div className="watchlists-filter-values-subtitle">
                                  {selectedFilterFieldRecord.description}
                                </div>}
                              </div>
                              <button
                                type="button"
                                className="watchlists-filter-clear"
                                disabled={
                                  selectedFilterFieldRecord.field_key === TAXONOMY_FILTER_FIELD_KEY
                                    ? !activeTaxonomyFilterPath.length
                                    : !workingFilters[selectedFilterFieldRecord.field_key]?.length
                                }
                                onClick={() => clearFilterField(selectedFilterFieldRecord.field_key)}
                              >
                                Clear
                              </button>
                            </div>

                            {selectedFilterFieldRecord.field_key === TAXONOMY_FILTER_FIELD_KEY ? (
                              <div className="watchlists-taxonomy-filter-list">
                                <button
                                  type="button"
                                  className={
                                    activeTaxonomyFilterPath.length
                                      ? 'watchlists-taxonomy-node'
                                      : 'watchlists-taxonomy-node watchlists-taxonomy-node-active'
                                  }
                                  onClick={clearTaxonomyFilter}
                                >
                                  <span className="watchlists-taxonomy-node-label">All Taxonomy</span>
                                  <span className="watchlists-taxonomy-node-count">{filterOptionRows.length}</span>
                                </button>
                                {taxonomyFilterInstrumentTypes.length ? (
                                  taxonomyFilterInstrumentTypes.map((instrumentType) => (
                                    <div key={instrumentType} className="watchlists-taxonomy-type-section">
                                      <div className="watchlists-taxonomy-type-heading">
                                        {instrumentTypeLabel(instrumentType)}
                                      </div>
                                      {renderTaxonomyFilterNodes(null, instrumentType)}
                                    </div>
                                  ))
                                ) : (
                                  <div className="watchlists-filter-empty">
                                    {zh ? '当前列表暂无已分类标的。' : 'No classified instruments in this watchlist.'}
                                  </div>
                                )}
                              </div>
                            ) : (
                              <div className="watchlists-filter-option-list">
                                {selectedFilterOptions.length ? (
                                  selectedFilterOptions.map((option) => {
                                    const checked = (workingFilters[selectedFilterFieldRecord.field_key] || []).some(
                                      (item) => filterValueKey(item) === option.key,
                                    )
                                    return (
                                      <label key={option.key} className="watchlists-filter-option">
                                        <input
                                          type="checkbox"
                                          checked={checked}
                                          onChange={() =>
                                            toggleFilterValue(selectedFilterFieldRecord.field_key, option.value)
                                          }
                                        />
                                        <span>{option.label}</span>
                                      </label>
                                    )
                                  })
                                ) : (
                                  <div className="watchlists-filter-empty">No values available for this field.</div>
                                )}
                              </div>
                            )}
                          </>
                        ) : (
                          <div className="watchlists-filter-empty">No filterable fields are available in this view.</div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="watchlists-filter-empty">No discrete filters are available for the current rows.</div>
                  )}
                </div>
              ) : null}
            </div>

            <DownloadFormatMenu
              wrapperClassName="watchlists-dropdown"
              buttonClassName="watchlists-toolbar-button"
              menuClassName="watchlists-menu"
              itemClassName="watchlists-menu-item"
              buttonLabel={isExporting ? 'Exporting...' : 'Download'}
              disabled={isExporting}
              onBeforeOpen={() => {
                setFilterMenuOpen(false)
                setGroupMenuOpen(false)
                setSelectorMenuOpen(false)
                setModalKind(null)
              }}
              onSelect={(format) => void handleDownloadCurrentView(format)}
            />
            {canWriteTeam && selectedRows.length && rowsAreCurrent ? (
              <button
                type="button"
                className="watchlists-toolbar-button"
                disabled={!moveTargetOptions.length}
                onClick={() => {
                  resetCopyItemsForm()
                  setModalKind('copy-items')
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                  setNotice(null)
                }}
              >
                Copy
              </button>
            ) : null}
            {canWriteTeam && selectedRows.length && rowsAreCurrent && !activeWatchlistIsSystem ? (
              <button
                type="button"
                className="watchlists-toolbar-button"
                disabled={!moveTargetOptions.length}
                onClick={() => {
                  resetMoveItemsForm()
                  setModalKind('move-items')
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                  setNotice(null)
                }}
              >
                Move
              </button>
            ) : null}
            {canWriteTeam && selectedRows.length && rowsAreCurrent && !activeWatchlistIsSystem ? (
              <button
                type="button"
                className="watchlists-toolbar-button watchlists-danger"
                disabled={deletingItems}
                onClick={() => {
                  if (!rowsAreCurrent || !watchlistId || !selectedRows.length) {
                    return
                  }
                  setConfirmError(null)
                  setPendingDeleteItems({
                    watchlistId,
                    watchlistName: activeWatchlist?.name || watchlistDetail?.name || watchlistId,
                    instrumentIds: [...selectedRows],
                  })
                }}
              >
                Remove From List
              </button>
            ) : null}
          </div>
        </div>

        {activeFilterEntries.length ? (
          <div className="watchlists-filter-strip">
            {activeFilterEntries.map((entry) => (
              <button
                key={`${entry.fieldKey}:${filterValueKey(entry.value)}`}
                type="button"
                className="watchlists-filter-chip"
                onClick={() =>
                  entry.isTaxonomy ? clearTaxonomyFilter() : toggleFilterValue(entry.fieldKey, entry.value)
                }
              >
                <span className="watchlists-filter-chip-label">{entry.label}</span>
                <span className="watchlists-filter-chip-value">{entry.valueLabel}</span>
                <span className="watchlists-filter-chip-remove">×</span>
              </button>
            ))}
            <button type="button" className="watchlists-filter-reset" onClick={() => setWorkingFilters({})}>
              Clear filters
            </button>
          </div>
        ) : null}

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}

        <HorizontalTableScroll className="table-shell" ref={tableShellRef}>
          <table className="terminal-table watchlists-table" style={{ minWidth: `${watchlistTableMinWidth}px` }}>
            <colgroup>
              <col style={{ width: `${WATCHLIST_SELECT_COLUMN_WIDTH}px` }} />
              {visibleColumns.map((column) => {
                const width = displayColumnWidths[column]
                return (
                  <col
                    key={column}
                    style={width ? { width: `${width}px` } : undefined}
                  />
                )
              })}
            </colgroup>
            <thead>
              <tr>
                <th className="watchlists-select-col">
                  <input
                    type="checkbox"
                    aria-label="Select all visible instruments"
                    checked={allRowsSelected}
                    disabled={!rowsAreCurrent}
                    onChange={(event) =>
                      setSelectedRows(event.target.checked ? allVisibleInstrumentIds : [])
                    }
                  />
                </th>
                {visibleColumns.map((column) => {
                  const width = displayColumnWidths[column]
                  const sortMode = sortabilityByKey.get(column) || 'none'
                  const sortAction = nextSortAction(sortField === column, sortDirection)
                  const columnLabel = t(fieldLabelByKey.get(column) || formatLabel(column))
                  return (
                    <th
                      key={column}
                      scope="col"
                      aria-sort={
                        sortMode === 'none'
                          ? undefined
                          : sortField === column
                            ? sortDirection.toLowerCase() === 'desc'
                              ? 'descending'
                              : 'ascending'
                            : 'none'
                      }
                      className={[
                        requiredColumns.includes(column) ? '' : 'watchlists-column-draggable',
                        isChartFieldKey(column) ? 'chart-cell' : '',
                        columnDropTarget === column ? 'watchlists-column-drop-target' : '',
                      ]
                        .filter(Boolean)
                        .join(' ') || undefined}
                      draggable={!requiredColumns.includes(column)}
                      style={width ? { width: `${width}px` } : undefined}
                      onDragStart={(event) => handleColumnDragStart(event, column)}
                      onDragOver={(event) => handleColumnDragOver(event, column)}
                      onDragLeave={() => setColumnDropTarget('')}
                      onDrop={(event) => handleColumnDrop(event, column)}
                      onDragEnd={() => setColumnDropTarget('')}
                    >
                      <button
                        type="button"
                        draggable={false}
                        className={
                          sortMode !== 'none'
                            ? 'watchlists-th-label watchlists-th-sortable'
                            : 'watchlists-th-label'
                        }
                        disabled={sortMode === 'none'}
                        aria-label={
                          sortMode === 'none'
                            ? undefined
                            : sortAction === 'clear'
                              ? `Clear sort for ${columnLabel}`
                              : `Sort ${columnLabel} ${sortAction}`
                        }
                        onClick={() => {
                          if (sortMode !== 'none') {
                            toggleSort(column)
                          }
                        }}
                      >
                        <span>
                          {column === primaryDisplayColumn
                            ? 'Name'
                            : fieldLabelByKey.get(column) || formatLabel(column)}
                        </span>
                        {sortField === column ? (
                          <span className="watchlists-sort-indicator">
                            {sortDirection.toLowerCase() === 'desc' ? '↓' : '↑'}
                          </span>
                        ) : null}
                      </button>
                      <span
                        className="watchlists-th-resizer"
                        role="separator"
                        aria-label={`Resize ${columnLabel} column`}
                        aria-orientation="vertical"
                        aria-valuemin={90}
                        aria-valuemax={420}
                        aria-valuenow={Math.round(width || WATCHLIST_DEFAULT_COLUMN_WIDTH)}
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
                            return
                          }
                          event.preventDefault()
                          const delta = event.key === 'ArrowLeft' ? -10 : 10
                          setColumnWidths((current) => {
                            const currentWidth =
                              current[column] || displayColumnWidths[column] || WATCHLIST_DEFAULT_COLUMN_WIDTH
                            return {
                              ...current,
                              [column]: clampColumnWidth(currentWidth + delta, 90, 420),
                            }
                          })
                        }}
                        onMouseDown={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          const currentWidth = displayColumnWidths[column] || WATCHLIST_DEFAULT_COLUMN_WIDTH
                          resizeState.current = {
                            column,
                            startX: event.clientX,
                            startWidth: currentWidth,
                          }
                          setColumnResizing(true)
                          document.body.style.cursor = 'col-resize'
                        }}
                      />
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {searchedRows.length ? (
                renderedGroupedRows.map((group, groupIndex) => {
                  const collapsed = collapsedGroupKeys.has(group.key)
                  return (
                    <React.Fragment key={group.key || `group-${groupIndex}`}>
                      {showGroupHeaders ? (
                        <tr
                          className={
                            group.isInstrumentTypeSection
                              ? 'watchlists-group-row watchlists-instrument-type-group-row'
                              : 'watchlists-group-row'
                          }
                        >
                          <td className="watchlists-select-col watchlists-group-spacer" aria-hidden="true" />
                          {visibleColumns.map((column, columnIndex) => {
                            if (columnIndex === 0) {
                              return (
                                <td key={column} className="watchlists-group-name-cell">
                                  <button
                                    type="button"
                                    className="watchlists-group-header"
                                    style={{ paddingLeft: `${group.depth * TAXONOMY_GROUP_DEPTH_INDENT_PX}px` }}
                                    aria-expanded={!collapsed}
                                    onClick={() =>
                                      setCollapsedGroupKeys((current) => {
                                        const next = new Set(current)
                                        if (next.has(group.key)) {
                                          next.delete(group.key)
                                        } else {
                                          next.add(group.key)
                                        }
                                        return next
                                      })
                                    }
                                  >
                                    <span
                                      className={
                                        collapsed
                                          ? 'watchlists-group-caret watchlists-group-caret-collapsed'
                                          : 'watchlists-group-caret'
                                      }
                                    >
                                      ▾
                                    </span>
                                    <span className="watchlists-group-title">
                                      {group.label || 'Unspecified'}
                                    </span>
                                    <span className="watchlists-group-count">{group.rowCount}</span>
                                  </button>
                                </td>
                              )
                            }
                            const field = fieldByKey.get(column)
                            const average = buildGroupAverageCell(column, field, group.summaryRows)
                            return (
                              <td
                                key={column}
                                className={[
                                  'watchlists-group-summary-cell',
                                  isChartFieldKey(column) ? 'chart-cell' : '',
                                ]
                                  .filter(Boolean)
                                  .join(' ')}
                              >
                                {average && average.value != null ? (
                                  <span
                                    className="watchlists-group-summary-value"
                                    title={[
                                      `Equal-weight average of ${average.count}/${average.total} rows`,
                                      average.asOfDate ? `metric as of ${average.asOfDate}` : '',
                                    ].filter(Boolean).join('; ')}
                                  >
                                    <span>{formatGroupAverageCell(column, field, average.value)}</span>
                                    {average.count !== average.total ? (
                                      <small>{average.count}/{average.total}</small>
                                    ) : null}
                                  </span>
                                ) : (
                                  <span
                                    className="watchlists-group-summary-empty"
                                    title={average?.unavailableReason || undefined}
                                  >
                                    —
                                  </span>
                                )}
                              </td>
                            )
                          })}
                        </tr>
                      ) : null}
                      {group.rows.map((row, index) => {
                        const instrumentId = String(row.instrument_id || `row-${index}`)
                        const checked = selectedRows.includes(instrumentId)
                        return (
                          <tr key={instrumentId}>
                            <td className="watchlists-select-col">
                              <input
                                type="checkbox"
                                aria-label={`Select ${String(row[primaryDisplayColumn] || instrumentId)}`}
                                checked={checked}
                                onChange={(event) =>
                                  setSelectedRows((current) =>
                                    event.target.checked
                                      ? [...current, instrumentId]
                                      : current.filter((item) => item !== instrumentId),
                                  )
                                }
                              />
                            </td>
                            {visibleColumns.map((column) => {
                              const isGroupedInstrumentName =
                                activeGroupBy === TAXONOMY_GROUP_BY_CODE && column === primaryDisplayColumn
                              return (
                                <td
                                  key={column}
                                  className={isChartFieldKey(column) ? 'chart-cell' : undefined}
                                  style={
                                    isGroupedInstrumentName
                                      ? {
                                          paddingLeft: `${
                                            12 +
                                            TAXONOMY_GROUP_LABEL_OFFSET_PX +
                                            group.depth * TAXONOMY_GROUP_DEPTH_INDENT_PX
                                          }px`,
                                        }
                                      : undefined
                                  }
                                >
                                  {renderCell(
                                    column,
                                    row[column],
                                    instrumentId,
                                    watchlistId,
                                    sparklineMap[instrumentId]?.[column],
                                    fieldByKey.get(column),
                                    row,
                                    openRisk,
                                    zh,
                                  )}
                                </td>
                              )
                            })}
                          </tr>
                        )
                      })}
                    </React.Fragment>
                  )
                })
              ) : screenerLoading || !screenerResult ? (
                <tr>
                  <td colSpan={Math.max(visibleColumns.length + 1, 1)} className="watchlists-table-loading">
                    <span className="watchlists-table-loading-bar" />
                    <span>Loading</span>
                  </td>
                </tr>
              ) : (
                <tr>
                  <td colSpan={Math.max(visibleColumns.length + 1, 1)} className="empty-state">
                    {watchlistSearchQuery
                      ? 'No rows matched the current watchlist search.'
                      : 'No rows matched the current watchlist view.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </HorizontalTableScroll>
        {screenerResult ? (
          <div className="watchlists-pagination">
            <div className="watchlists-pagination-summary">
              <span>
                {screenerResult.total_rows
                  ? watchlistSearchQuery
                    ? `Showing ${renderedInstrumentCount} of ${searchedRows.length} matched rows`
                    : `Showing ${renderedInstrumentCount} of ${screenerResult.total_rows} rows`
                  : 'No rows in this watchlist view'}
              </span>
              <span title="Return and risk windows are anchored independently for each instrument.">
                {metricAsOfRangeLabel}
              </span>
            </div>
            {renderedInstrumentCount < searchedRows.length ? (
              <div className="watchlists-pagination-actions">
                <button
                  type="button"
                  className="watchlists-pagination-button"
                  onClick={() =>
                    startTransition(() =>
                      setRenderRowLimit((current) =>
                        Math.min(current + WATCHLIST_INITIAL_RENDER_ROWS, searchedRows.length),
                      ),
                    )
                  }
                >
                  Show {Math.min(WATCHLIST_INITIAL_RENDER_ROWS, searchedRows.length - renderedInstrumentCount)} more
                </button>
                <button
                  type="button"
                  className="watchlists-pagination-button"
                  onClick={() => startTransition(() => setRenderRowLimit(searchedRows.length))}
                >
                  Show all
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      {modalKind === 'columns' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose columns"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Columns</div>
                <div className="section-heading">Manage Columns</div>
                <div className="watchlists-columns-help">
                  {zh
                    ? '所有列表共用这些字段，勾选只影响当前视图。标的名称固定显示；详细分析可打开标的查看。'
                    : 'All watchlists share these fields; selections apply to this view. Name is always shown. Open an instrument for detailed analysis.'}
                </div>
              </div>
              <button type="button" onClick={closeActiveModal}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-search">
              <input
                className="form-input"
                placeholder="Search columns"
                value={fieldSearch}
                onChange={(event) => setFieldSearch(event.target.value)}
              />
            </div>

            <div className="watchlists-column-sections">
              {columnSections.map((section) => {
                const fields = filteredFieldRegistry.filter((field) => field.section === section.key)
                if (!fields.length) return null
                return (
                  <section key={section.key} aria-labelledby={`columns-${section.key}`}>
                    <h3 id={`columns-${section.key}`} className="watchlists-column-section-title">{section.label}</h3>
                    <div className="watchlists-column-options">
                      {fields.map((field) => (
                        <div key={field.field_key} className="watchlists-field-item">
                          <label>
                            <input
                              type="checkbox"
                              checked={columnDraft.includes(field.field_key)}
                              onChange={(event) =>
                                setColumnDraft((current) =>
                                  event.target.checked
                                    ? [...current, field.field_key]
                                    : current.filter((item) => item !== field.field_key),
                                )
                              }
                            />
                            <span className="watchlists-field-label">{field.label}</span>
                          </label>
                          {field.description && (
                            <InfoHint label={field.label} detail={field.description} />
                          )}
                        </div>
                      ))}
                    </div>
                  </section>
                )
              })}
              {!filteredFieldRegistry.length && <p className="watchlists-columns-help">
                {zh ? '没有匹配的字段。' : 'No matching fields.'}
              </p>}
            </div>

            <div className="watchlists-modal-actions watchlists-modal-actions-sticky">
              <button
                type="button"
                onClick={() => {
                  setColumnDraft(ensureRequiredColumns(visibleColumns))
                  setModalKind(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  const nextColumns = ensureRequiredColumns(
                    columnDraft.length ? columnDraft : [primaryDisplayColumn],
                  )
                  setWorkingColumns(nextColumns)
                  setModalKind(null)
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'copy-items' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Copy selected instruments"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Copy Instruments</div>
                <div className="section-heading">Copy Selected Instruments To Another Watchlist</div>
              </div>
              <button
                type="button"
                disabled={isCopyingItems}
                onClick={() => {
                  resetCopyItemsForm()
                  setModalKind(null)
                }}
              >
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {modalError ? <div className="panel error-state" role="alert">{modalError}</div> : null}
              <div className="watchlists-move-summary">
                {selectedRows.length} selected from {activeWatchlist?.name || 'current watchlist'}.
              </div>
              <label className="form-field">
                <span>Target Watchlist</span>
                <select
                  className="form-input"
                  value={copyTargetWatchlist?.watchlist_id || ''}
                  onChange={(event) => setCopyTargetWatchlistId(event.target.value)}
                >
                  {moveTargetOptions.map((watchlist) => (
                    <option key={watchlist.watchlist_id} value={watchlist.watchlist_id}>
                      {watchlist.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="watchlists-move-note">
                Instruments already present in the target will not be duplicated. They will remain in the current
                watchlist after the copy.
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <button
                type="button"
                disabled={isCopyingItems}
                onClick={() => {
                  resetCopyItemsForm()
                  setModalKind(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isCopyingItems || !rowsAreCurrent || !watchlistId || !selectedRows.length || !copyTargetWatchlist}
                onClick={async () => {
                  const sourceWatchlistId = rowsAreCurrent ? screenerResultOwnerId : ''
                  if (!sourceWatchlistId || !selectedRows.length || !copyTargetWatchlist) {
                    return
                  }
                  setIsCopyingItems(true)
                  setModalError(null)
                  setNotice(null)
                  try {
                    const result = await copyWatchlistItems(
                      sourceWatchlistId,
                      selectedRows,
                      copyTargetWatchlist.watchlist_id,
                    )
                    const nextWatchlists = await getWatchlists()
                    setWatchlists(nextWatchlists)
                    setSelectedRows([])
                    resetCopyItemsForm()
                    setModalKind(null)
                    setNotice(
                      result.already_present_count > 0
                        ? `Copied ${result.copied_count} instruments to "${copyTargetWatchlist.name}". ${result.already_present_count} already existed there.`
                        : `Copied ${result.copied_count} instruments to "${copyTargetWatchlist.name}".`,
                    )
                  } catch (copyError) {
                    setModalError(copyError instanceof Error ? copyError.message : 'Failed to copy instruments.')
                  } finally {
                    setIsCopyingItems(false)
                  }
                }}
              >
                {isCopyingItems ? 'Copying...' : 'Copy Items'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'move-items' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Move selected instruments"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Move Instruments</div>
                <div className="section-heading">Move Selected Instruments To Another Watchlist</div>
              </div>
              <button
                type="button"
                disabled={isMovingItems}
                onClick={() => {
                  resetMoveItemsForm()
                  setModalKind(null)
                }}
              >
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {modalError ? <div className="panel error-state" role="alert">{modalError}</div> : null}
              <div className="watchlists-move-summary">
                {selectedRows.length} selected from {activeWatchlist?.name || 'current watchlist'}.
              </div>
              <label className="form-field">
                <span>Target Watchlist</span>
                <select
                  className="form-input"
                  value={moveTargetWatchlist?.watchlist_id || ''}
                  onChange={(event) => setMoveTargetWatchlistId(event.target.value)}
                >
                  {moveTargetOptions.map((watchlist) => (
                    <option key={watchlist.watchlist_id} value={watchlist.watchlist_id}>
                      {watchlist.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="watchlists-move-note">
                Instruments already present in the target will not be duplicated. They will still be removed from the
                current watchlist.
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <button
                type="button"
                disabled={isMovingItems}
                onClick={() => {
                  resetMoveItemsForm()
                  setModalKind(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isMovingItems || !rowsAreCurrent || !watchlistId || !selectedRows.length || !moveTargetWatchlist}
                onClick={async () => {
                  const sourceWatchlistId = rowsAreCurrent ? screenerResultOwnerId : ''
                  if (!sourceWatchlistId || !selectedRows.length || !moveTargetWatchlist) {
                    return
                  }
                  setIsMovingItems(true)
                  setModalError(null)
                  setNotice(null)
                  try {
                    const result = await moveWatchlistItems(
                      sourceWatchlistId,
                      selectedRows,
                      moveTargetWatchlist.watchlist_id,
                    )
                    const nextWatchlists = await getWatchlists()
                    setWatchlists(nextWatchlists)
                    setSelectedRows([])
                    await refreshWatchlistDetail(undefined, sourceWatchlistId)
                    setReloadToken(Date.now())
                    resetMoveItemsForm()
                    setModalKind(null)
                    setNotice(
                      result.already_present_count > 0
                        ? `Moved ${result.moved_count} instruments to "${moveTargetWatchlist.name}". ${result.already_present_count} already existed there.`
                        : `Moved ${result.moved_count} instruments to "${moveTargetWatchlist.name}".`,
                    )
                  } catch (moveError) {
                    setModalError(moveError instanceof Error ? moveError.message : 'Failed to move instruments.')
                  } finally {
                    setIsMovingItems(false)
                  }
                }}
              >
                {isMovingItems ? 'Moving...' : 'Move Items'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'create-watchlist' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Create watchlist"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div className="panel-title">Create Watchlist</div>
              <button
                type="button"
                disabled={isCreatingWatchlist}
                onClick={() => {
                  resetCreateWatchlistForm()
                  setModalKind(null)
                }}
              >
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {modalError ? <div className="panel error-state" role="alert">{modalError}</div> : null}
              <label className="form-field">
                <span>Name</span>
                <input
                  className="form-input"
                  value={createWatchlistName}
                  maxLength={200}
                  onChange={(event) => setCreateWatchlistName(event.target.value)}
                  placeholder="Coverage"
                />
              </label>
              <label className="form-field">
                <span>Description</span>
                <textarea
                  className="form-textarea"
                  value={createWatchlistDescription}
                  onChange={(event) => setCreateWatchlistDescription(event.target.value)}
                  placeholder="Optional description for this watchlist."
                />
              </label>
            </div>

            <div className="watchlists-modal-actions watchlists-modal-actions-sticky">
              <button
                type="button"
                disabled={isCreatingWatchlist}
                onClick={() => {
                  resetCreateWatchlistForm()
                  setModalKind(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isCreatingWatchlist || !createWatchlistName.trim()}
                onClick={async () => {
                  const name = createWatchlistName.trim()
                  if (!name) {
                    return
                  }
                  setIsCreatingWatchlist(true)
                  setModalError(null)
                  try {
                    const created = await createWatchlist({
                      name,
                      description: createWatchlistDescription.trim() || null,
                    })
                    const nextWatchlists = await getWatchlists()
                    setWatchlists(nextWatchlists)
                    resetCreateWatchlistForm()
                    setModalKind(null)
                    startTransition(() => {
                      navigate(buildWatchlistPath(created.watchlist_id))
                    })
                    setNotice(`Watchlist "${created.name}" created.`)
                  } catch (createError) {
                    setModalError(
                      createError instanceof Error
                        ? createError.message
                        : 'Failed to create watchlist.',
                    )
                    setIsCreatingWatchlist(false)
                  }
                }}
              >
                {isCreatingWatchlist ? 'Creating...' : 'Create Watchlist'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'save-view' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Create watchlist view"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div className="panel-title">Create View</div>
              <button type="button" disabled={isSavingView} onClick={closeActiveModal}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {modalError ? <div className="panel error-state" role="alert">{modalError}</div> : null}
              <label className="form-field">
                <span>View Name</span>
                <input
                  className="form-input"
                  value={saveViewName}
                  onChange={(event) => setSaveViewName(event.target.value)}
                  placeholder="Custom View"
                />
              </label>
              <label className="form-field">
                <span>Description</span>
                <textarea
                  className="form-textarea"
                  value={saveViewDescription}
                  onChange={(event) => setSaveViewDescription(event.target.value)}
                  placeholder="Optional notes about this view."
                />
              </label>
            </div>

            <div className="watchlists-modal-actions watchlists-modal-actions-sticky">
              <button type="button" disabled={isSavingView} onClick={closeActiveModal}>
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isSavingView || !detailIsCurrent || !saveViewName.trim()}
                onClick={async () => {
                  const sourceWatchlistId = detailIsCurrent ? watchlistDetailOwnerId : ''
                  if (!sourceWatchlistId || !saveViewName.trim()) {
                    return
                  }
                  setIsSavingView(true)
                  setModalError(null)
                  try {
                    const payload = buildViewPayload(
                      saveViewName.trim(),
                      saveViewDescription.trim() || null,
                    )
                    const created = await createWatchlistView(sourceWatchlistId, payload)
                    await refreshWatchlistDetail(created.view_id, sourceWatchlistId)
                    setModalKind(null)
                    setViewToast({ id: Date.now(), message: `View saved as "${created.name}".`, tone: 'success' })
                  } catch (saveError) {
                    setModalError(saveError instanceof Error ? saveError.message : 'Failed to save view.')
                  } finally {
                    setIsSavingView(false)
                  }
                }}
              >
                {isSavingView ? 'Creating...' : 'Create View'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'add' ? (
        <div className="watchlists-modal-backdrop" onClick={closeActiveModal}>
          <div
            ref={modalDialogRef}
            className="watchlists-modal watchlists-add-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Add instruments"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Add</div>
                <div className="section-heading">Registered Assets</div>
              </div>
              <button type="button" disabled={isAdding || isBatchAdding} onClick={closeActiveModal}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {modalError ? <div className="panel error-state" role="alert">{modalError}</div> : null}
              <label className="form-field">
                <span>Search Instruments</span>
                <input
                  className="form-input"
                  value={instrumentSearch}
                  onChange={(event) => setInstrumentSearch(event.target.value)}
                  placeholder="Ticker, ISIN, or instrument name"
                />
              </label>
              <p className="watchlists-registry-note">
                Select an existing asset to add to this watchlist. New assets and market-data sources are maintained
                through the backend CLI. Classification and research remain in Watchlist.
              </p>
              {selectedSharedInstrument ? (
                <div className="watchlists-registry-selected">
                  <span className="ticker-pill">{primarySharedIdentifier(selectedSharedInstrument)}</span>
                  <span className="watchlists-registry-name" translate="no">{selectedSharedInstrument.instrument_name}</span>
                  <span className="watchlists-registry-secondary">
                    {selectedSharedInstrument.currency} · {formatLabel(selectedSharedInstrument.instrument_type)}
                  </span>
                </div>
              ) : null}
              <div className="watchlists-registry-list">
                {isSearchingInstruments ? (
                  <div className="loading-state">Searching registered assets…</div>
                ) : null}
                {!isSearchingInstruments
                  ? sharedInstrumentResults.map((instrument) => (
                      <button
                        type="button"
                        key={instrument.instrument_id}
                        className={`watchlists-registry-row ${
                          selectedInstrumentId === instrument.instrument_id ? 'watchlists-registry-row-active' : ''
                        }`}
                        onClick={() => setSelectedInstrumentId(instrument.instrument_id)}
                      >
                        <div className="watchlists-registry-row-main">
                          <span>{primarySharedIdentifier(instrument)}</span>
                          <span className="watchlists-registry-secondary" translate="no">{instrument.instrument_name}</span>
                        </div>
                        <div className="watchlists-registry-meta">
                          <span>{formatLabel(instrument.instrument_type)}</span>
                          <span>{instrument.coverage_state || 'registered'}</span>
                        </div>
                      </button>
                    ))
                  : null}
                {!isSearchingInstruments && !sharedInstrumentResults.length ? (
                  <div className="empty-state">
                    {instrumentSearch.trim()
                      ? `No registered asset matched "${instrumentSearch.trim()}".`
                      : 'No registered assets are available. Add assets through backend maintenance.'}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <input
                ref={batchFileInputRef}
                type="file"
                accept=".csv,.tsv,.txt,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="watchlists-hidden-file-input"
                onChange={(event) => void handleBatchAddFileChange(event)}
              />
              <button
                type="button"
                disabled={isBatchAdding}
                title="Add identifiers from CSV, TSV, text, or Excel"
                onClick={() => batchFileInputRef.current?.click()}
              >
                {isBatchAdding ? 'Processing File...' : 'Add From File'}
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isAdding || !detailIsCurrent || !selectedInstrumentId}
                onClick={async () => {
                  const sourceWatchlistId = detailIsCurrent ? watchlistDetailOwnerId : ''
                  if (!sourceWatchlistId || !selectedSharedInstrument) {
                    return
                  }
                  setIsAdding(true)
                  setModalError(null)
                  try {
                    const registryInstrument = selectedSharedInstrument
                    const instrumentId = registryInstrument.instrument_id
                    const addResult = await addWatchlistItems(sourceWatchlistId, [instrumentId])
                    await refreshWatchlistDetail(undefined, sourceWatchlistId)
                    setInstrumentSearch('')
                    setSharedInstrumentResults([])
                    setSelectedInstrumentId('')
                    setModalKind(null)
                    if (addResult.accepted_count === 0) {
                      setNotice(`${primarySharedIdentifier(registryInstrument)} is already in this watchlist.`)
                    } else {
                      setNotice(`Added ${primarySharedIdentifier(registryInstrument)} to this watchlist.`)
                    }
                    setReloadToken(Date.now())
                  } catch (addError) {
                    setModalError(addError instanceof Error ? addError.message : 'Failed to add instrument.')
                  } finally {
                    setIsAdding(false)
                  }
                }}
              >
                {isAdding ? 'Adding...' : 'Add To Watchlist'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      </div>
      <ConfirmDialog
        open={Boolean(pendingDeleteItems)}
        title="Remove From List"
        description={
          pendingDeleteItems
            ? zh
              ? `从“${pendingDeleteItems.watchlistName}”移除 ${pendingDeleteItems.instrumentIds.length} 个标的？投资状态、设置和研究记录会保留，仍可从“全部标的”找到。`
              : `Remove ${pendingDeleteItems.instrumentIds.length} instruments from "${pendingDeleteItems.watchlistName}"? Investment status, settings, and research remain with each instrument, accessible in All Instruments.`
            : ''
        }
        confirmLabel="Remove From List"
        busy={deletingItems}
        busyLabel="Deleting…"
        error={confirmError}
        onCancel={() => {
          setConfirmError(null)
          setPendingDeleteItems(null)
        }}
        onConfirm={handleDeleteSelectedItems}
      />
      {renamingWatchlist && <RenameWatchlistDialog key={renamingWatchlist.watchlist_id}
        watchlist={renamingWatchlist} onCancel={() => setRenamingWatchlist(null)}
        onSaved={(saved) => {
          setWatchlists((current) => current.map((item) => item.watchlist_id === saved.watchlist_id ? { ...item, name: saved.name } : item))
          setWatchlistDetail((current) => current?.watchlist_id === saved.watchlist_id ? { ...current, name: saved.name } : current)
          setRenamingWatchlist(null)
          setNotice(zh ? '关注列表已重命名。' : 'Watchlist renamed.')
        }} />}
      <ConfirmDialog
        open={Boolean(pendingDeleteWatchlist)}
        title="Delete Watchlist"
        description="Delete this list and its saved views? Each instrument keeps its investment status, settings, and research, and remains accessible in All Instruments."
        confirmLabel="Delete Watchlist"
        confirmationText={pendingDeleteWatchlist?.name}
        busy={deletingWatchlist}
        error={confirmError}
        onCancel={() => {
          setConfirmError(null)
          setPendingDeleteWatchlist(null)
        }}
        onConfirm={handleDeleteWatchlist}
      />
    </>
  )
}
