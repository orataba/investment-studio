import React, { startTransition, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  copyWatchlistItems,
  copyWatchlist,
  type FieldCategory,
  type FieldRegistryRecord,
  type FundChartPoint,
  type FundTaxonomyTreeNode,
  type FundTaxonomyTreeResponse,
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
  getFundTaxonomyTree,
  getInstrumentChart,
  getSharedInstruments,
  getWatchlistDetail,
  getWatchlists,
  moveWatchlistItems,
  resolveSharedInstrument,
  updateWatchlistView,
  runScreenerQuery,
} from '../lib/api'
import {
  buildInstrumentDetailPath,
  buildWatchlistPath,
  PLATFORM_HOME_URL,
} from '../lib/navigation'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
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
type SparklineCacheEntry = { requestKey: string; points: FundChartPoint[] }
type FilterState = Record<string, unknown[]>
type FilterOption = { key: string; label: string; value: unknown }
type WatchlistRowGroup = {
  key: string
  label: string | null
  rows: Array<Record<string, unknown>>
  summaryRows: Array<Record<string, unknown>>
  rowCount: number
  depth: number
}
type GroupAverageCell = {
  value: number
  count: number
  total: number
}
type ActiveFilterEntry = {
  fieldKey: string
  value: unknown
  label: string
  valueLabel: string
  isTaxonomy: boolean
}
const SCREENER_BULK_PAGE_SIZE = 500
const ALL_COVERAGE_WATCHLIST_ID = 'all-coverage'
const TAXONOMY_FILTER_FIELD_KEY = 'taxonomy'
const TAXONOMY_GROUP_BY_CODE = 'taxonomy'
const TAXONOMY_GROUP_FIELD_KEYS = [
  'attr.fund_regime',
  'attr.fund_taxonomy_level_1',
  'attr.fund_taxonomy_level_2',
  'attr.fund_taxonomy_level_3',
  'attr.fund_taxonomy_level_4',
  'attr.fund_taxonomy_level_5',
  'attr.fund_taxonomy_level_6',
]
const TAXONOMY_GROUP_DEPTH_INDENT_PX = 18
const TAXONOMY_GROUP_LABEL_OFFSET_PX = 26
const WATCHLIST_SELECT_COLUMN_WIDTH = 44
const WATCHLIST_DEFAULT_COLUMN_WIDTH = 140
const TAXONOMY_FILTER_FIELD: FieldRegistryRecord = {
  field_key: TAXONOMY_FILTER_FIELD_KEY,
  label: 'Taxonomy',
  description: 'Choose one taxonomy node; descendants under that node remain included.',
  category_code: 'product_taxonomy',
  data_type: 'string',
  formatter_code: 'text',
  sort_mode: 'none',
  filter_mode: 'multi_select',
  group_mode: 'none',
  instrument_scope_json: ['fund'],
  product_scope_json: [],
  availability_rule_json: {},
  source_domain: 'taxonomy',
  source_metric_code: 'fund_taxonomy.tree',
  default_width: null,
  default_visible: false,
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

function isAllCoverageWatchlist(watchlist?: WatchlistRecord | WatchlistDetail | null) {
  return Boolean(
    watchlist &&
      (watchlist.watchlist_id === ALL_COVERAGE_WATCHLIST_ID ||
        (watchlist.is_default && watchlist.owner_type === 'system')),
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
  return index === 0 ? 'attr.fund_regime' : `attr.fund_taxonomy_level_${index}`
}

function taxonomyPathKey(pathLabels: string[]) {
  return pathLabels.join(' / ')
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
  pageSize: number = SCREENER_BULK_PAGE_SIZE,
) {
  return (await loadCompleteScreenerResult(payload, pageSize)).rows
}

async function loadCompleteScreenerResult(
  payload: Record<string, unknown>,
  pageSize: number = SCREENER_BULK_PAGE_SIZE,
): Promise<ScreenerResponse> {
  const firstPage = await runScreenerQuery({
    ...payload,
    pagination: { page: 1, page_size: pageSize },
  })
  if (firstPage.total_rows <= firstPage.rows.length) {
    return firstPage
  }

  const rows = [...firstPage.rows]
  const totalPages = Math.max(1, Math.ceil(firstPage.total_rows / pageSize))
  for (let page = 2; page <= totalPages; page += 1) {
    const nextPage = await runScreenerQuery({
      ...payload,
      pagination: { page, page_size: pageSize },
    })
    if (!nextPage.rows.length) {
      break
    }
    rows.push(...nextPage.rows)
  }
  const completeRows = rows.slice(0, firstPage.total_rows)
  return {
    ...firstPage,
    rows: completeRows,
  }
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
  return fieldKey.startsWith('price_chart_') || fieldKey.includes('sparkline')
}

function isAverageSummaryField(fieldKey: string, field: FieldRegistryRecord | undefined) {
  if (!field || !['number', 'integer'].includes(field.data_type)) {
    return false
  }
  if (
    fieldKey === 'aum' ||
    fieldKey === 'latest_quote' ||
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
  return {
    value: values.reduce((sum, value) => sum + value, 0) / values.length,
    count: values.length,
    total: rows.length,
  }
}

function formatGroupAverageCell(fieldKey: string, field: FieldRegistryRecord | undefined, value: number) {
  if (fieldKey.includes('_percentile')) {
    return `${formatNumber(value, 0)} pct`
  }
  if (fieldKey === 'overall_rating') {
    return formatNumber(value, 1)
  }
  if (field?.formatter_code === 'percent' || isReturnMetricField(fieldKey)) {
    return formatPercent(value)
  }
  if (field?.formatter_code === 'decimal' || field?.data_type === 'integer') {
    return formatNumber(value, field.data_type === 'integer' ? 1 : 2)
  }
  return formatNumber(value)
}

function priceChartMaxPoints(fieldKey: string) {
  if (fieldKey.endsWith('1y')) {
    return 120
  }
  if (fieldKey.endsWith('1m')) {
    return 40
  }
  if (fieldKey.endsWith('1w')) {
    return 20
  }
  if (fieldKey.endsWith('1d')) {
    return 10
  }
  return undefined
}

function renderCell(
  fieldKey: string,
  value: unknown,
  instrumentId: string,
  watchlistId: string,
  sparklinePoints: FundChartPoint[] | undefined,
) {
  if (fieldKey === 'instrument_name') {
    return (
      <Link to={buildInstrumentDetailPath(instrumentId, watchlistId)} className="table-link watchlists-instrument-link">
        {typeof value === 'string' && value ? value : instrumentId.toUpperCase()}
      </Link>
    )
  }

  if (fieldKey === 'ticker_or_isin') {
    return <span className="ticker-pill">{String(value || '—')}</span>
  }

  if (fieldKey === 'overall_rating') {
    return value == null ? '—' : <span className="rating-pill">{String(value)}</span>
  }

  if (fieldKey === 'attr.coverage_status') {
    return value == null || value === '' ? '—' : <span className="status-badge status-attribute">{String(value)}</span>
  }

  if (fieldKey === 'data_freshness_status') {
    return <span className={statusClass(value)}>{formatLabel(String(value || 'Unknown'))}</span>
  }

  if (isChartFieldKey(fieldKey)) {
    return <Sparkline values={sparklinePoints} maxPoints={priceChartMaxPoints(fieldKey)} />
  }

  if (fieldKey === 'aum') {
    return formatCompactCurrency(asNumber(value))
  }

  if (fieldKey === 'latest_quote') {
    return formatNumber(asNumber(value), 4)
  }

  if (fieldKey.includes('_percentile')) {
    const numericValue = asNumber(value)
    return numericValue == null ? '—' : `${formatNumber(numericValue, 0)} pct`
  }

  if (
    isReturnMetricField(fieldKey) ||
    fieldKey.endsWith('_ratio')
  ) {
    const numericValue = asNumber(value)
    const renderedValue = formatPercent(numericValue)
    return isReturnMetricField(fieldKey) ? (
      <span className={signedValueClass(numericValue)}>{renderedValue}</span>
    ) : (
      renderedValue
    )
  }

  if (fieldKey === 'duration' || fieldKey === 'volatility' || fieldKey === 'sharpe_ratio') {
    return formatNumber(asNumber(value))
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
    return value.length ? value.join(', ') : '—'
  }

  if (value == null || value === '') {
    return '—'
  }

  return String(value)
}

function downloadCsv(columns: string[], rows: Array<Record<string, unknown>>) {
  const escapeCell = (value: unknown) => {
    const text =
      value == null
        ? ''
        : Array.isArray(value)
          ? value.join(', ')
          : String(value)
    if (text.includes(',') || text.includes('"') || text.includes('\n')) {
      return `"${text.replace(/"/g, '""')}"`
    }
    return text
  }

  const csv = [
    columns.join(','),
    ...rows.map((row) =>
      columns.map((column) => escapeCell(row[column] ?? row[column.replace(/^attr\./, '')])).join(','),
    ),
  ].join('\n')

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'watchlist-export.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

function parseDelimitedRow(line: string, delimiter: string) {
  const cells: string[] = []
  let current = ''
  let inQuotes = false

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    const next = line[index + 1]

    if (char === '"') {
      if (inQuotes && next === '"') {
        current += '"'
        index += 1
      } else {
        inQuotes = !inQuotes
      }
      continue
    }

    if (char === delimiter && !inQuotes) {
      cells.push(current.trim())
      current = ''
      continue
    }

    current += char
  }

  cells.push(current.trim())
  return cells
}

function parseBatchInstrumentFile(text: string) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  if (!lines.length) {
    return []
  }

  const delimiter = lines.some((line) => line.includes('\t')) ? '\t' : ','
  const parsedRows = lines.map((line) => parseDelimitedRow(line, delimiter))
  const header = parsedRows[0].map((cell) => cell.trim().toLowerCase())
  const hasHeader =
    header.includes('ticker') ||
    header.includes('ticker / isin') ||
    header.includes('isin') ||
    header.includes('identifier')
  const identifierIndex = hasHeader
    ? Math.max(
        header.findIndex((cell) => cell === 'ticker'),
        header.findIndex((cell) => cell === 'ticker / isin'),
        header.findIndex((cell) => cell === 'isin'),
        header.findIndex((cell) => cell === 'identifier'),
      )
    : 0
  const dataRows = hasHeader ? parsedRows.slice(1) : parsedRows

  return dataRows
    .map((cells) => ({
      identifier: (cells[identifierIndex] || '').trim(),
    }))
    .filter((row) => row.identifier)
}

function primarySharedIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

export default function WatchlistsPage() {
  const primaryDisplayColumn = 'instrument_name'
  const requiredColumns = [primaryDisplayColumn]
  const navigate = useNavigate()
  const { watchlistId = '' } = useParams()
  const [watchlists, setWatchlists] = useState<WatchlistRecord[]>([])
  const [fieldCategories, setFieldCategories] = useState<FieldCategory[]>([])
  const [fieldRegistry, setFieldRegistry] = useState<FieldRegistryRecord[]>([])
  const [fundTaxonomy, setFundTaxonomy] = useState<FundTaxonomyTreeResponse | null>(null)
  const [activeViewId, setActiveViewId] = useState('')
  const [watchlistDetail, setWatchlistDetail] = useState<WatchlistDetail | null>(null)
  const [screenerResult, setScreenerResult] = useState<ScreenerResponse | null>(null)
  const [workingColumns, setWorkingColumns] = useState<string[]>([])
  const [columnDraft, setColumnDraft] = useState<string[]>([])
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({})
  const [columnDropTarget, setColumnDropTarget] = useState('')
  const [workingGroupBy, setWorkingGroupBy] = useState('none')
  const [workingFilters, setWorkingFilters] = useState<FilterState>({})
  const [selectedFieldCategory, setSelectedFieldCategory] = useState('')
  const [fieldSearch, setFieldSearch] = useState('')
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
  const [sortRules, setSortRules] = useState<Array<{ field: string; direction: string }>>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [viewToast, setViewToast] = useState<NoticeToastMessage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sparklineMap, setSparklineMap] = useState<Record<string, SparklineCacheEntry>>({})
  const [collapsedGroupKeys, setCollapsedGroupKeys] = useState<Set<string>>(new Set())
  const [reloadToken, setReloadToken] = useState(0)
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
  const baseScreenerPayload = useMemo(() => {
    if (!watchlistId) {
      return null
    }

    const requestedFields = workingColumns.length ? [...workingColumns] : [primaryDisplayColumn]
    if (workingGroupBy === TAXONOMY_GROUP_BY_CODE) {
      TAXONOMY_GROUP_FIELD_KEYS.forEach((fieldKey) => {
        if (!requestedFields.includes(fieldKey)) {
          requestedFields.push(fieldKey)
        }
      })
    } else if (workingGroupBy && workingGroupBy !== 'none' && !requestedFields.includes(workingGroupBy)) {
      requestedFields.push(workingGroupBy)
    }

    return {
      watchlist_id: watchlistId,
      view_id: activeViewId || null,
      selected_fields: requestedFields,
      filters: workingFilters,
      sort: sortRules,
      group_by: workingGroupBy,
    }
  }, [activeViewId, primaryDisplayColumn, sortRules, watchlistId, workingColumns, workingFilters, workingGroupBy])
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
          getFundTaxonomyTree(),
        ])

        if (cancelled) {
          return
        }

        setWatchlists(watchlistData)
        setFieldCategories(fieldRegistryData.categories)
        setFieldRegistry(fieldRegistryData.fields)
        setFundTaxonomy(taxonomyData)
        const resolvedWatchlistId = watchlistData.some((item) => item.watchlist_id === watchlistId)
          ? watchlistId
          : watchlistData[0]?.watchlist_id || ''
        if (resolvedWatchlistId && resolvedWatchlistId !== watchlistId) {
          startTransition(() => {
            navigate(buildWatchlistPath(resolvedWatchlistId), { replace: true })
          })
        }
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
  }, [navigate, watchlistId])

  useEffect(() => {
    if (!watchlistId) {
      setWatchlistDetail(null)
      return
    }

    let cancelled = false

    async function loadWatchlistDetail() {
      setError(null)

      try {
        const detail = await getWatchlistDetail(watchlistId)
        if (cancelled) {
          return
        }

        setWatchlistDetail(detail)
        startTransition(() => {
          const nextViewId = detail.default_view_id || detail.views[0]?.view_id || ''
          setActiveViewId(nextViewId)
          const nextView =
            detail.views.find((item) => item.view_id === nextViewId) || detail.views[0] || null
          applyWatchlistView(nextView)
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

    getSharedInstruments({
      search: instrumentSearch,
      instrument_type: 'fund',
      limit: 12,
    })
      .then((results) => {
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
          setError(loadError instanceof Error ? loadError.message : 'Failed to load shared registry.')
          setSharedInstrumentResults([])
          setSelectedInstrumentId('')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsSearchingInstruments(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [instrumentSearch, modalKind])

  useEffect(() => {
    if (!watchlistDetail || !baseScreenerPayload) {
      setScreenerResult(null)
      return
    }

    let cancelled = false
    const payload = baseScreenerPayload

    async function loadRows() {
      try {
        const result = await loadCompleteScreenerResult(payload)

        if (!cancelled) {
          setScreenerResult(result)
          setSelectedRows((current) =>
            current.filter((instrumentId) => result.rows.some((row) => String(row.instrument_id) === instrumentId)),
          )
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load watchlist rows.')
        }
      }
    }

    void loadRows()

    return () => {
      cancelled = true
    }
  }, [baseScreenerPayload, reloadToken, watchlistDetail])

  const activeView =
    watchlistDetail?.views.find((item) => item.view_id === activeViewId) || watchlistDetail?.views[0] || null
  const visibleSparklineColumns = useMemo(
    () =>
      workingColumns.filter(
        (column) => isChartFieldKey(column),
      ),
    [workingColumns],
  )
  const sparklineRequestKey = useMemo(() => {
    if (!screenerResult?.rows.length || !visibleSparklineColumns.length) {
      return ''
    }
    const rowKey = screenerResult.rows
      .map(
        (row) =>
          `${String(row.instrument_id || '').trim()}:${String(row.detail_subject_id || '').trim()}`,
      )
      .join('|')
    return [
      watchlistId,
      activeViewId,
      visibleSparklineColumns.join(','),
      screenerResult.snapshot_metadata?.source_cutoff_at || '',
      String(reloadToken),
      rowKey,
    ].join('::')
  }, [activeViewId, reloadToken, screenerResult, visibleSparklineColumns, watchlistId])

  useEffect(() => {
    if (!screenerResult?.rows.length || !visibleSparklineColumns.length || !sparklineRequestKey) {
      return
    }

    let cancelled = false
    const chartTargets = screenerResult.rows
      .map((row) => ({
        instrumentId: String(row.instrument_id || '').trim(),
        instrumentType: String(row.instrument_type || '').trim().toLowerCase(),
        detailSubjectId: String(row.detail_subject_id || row.instrument_id || '').trim(),
      }))
      .filter((row) => row.instrumentId && row.instrumentType === 'fund' && row.detailSubjectId)
    const missingTargets = chartTargets.filter(
      (row) => sparklineMap[row.instrumentId]?.requestKey !== sparklineRequestKey,
    )
    if (!missingTargets.length) {
      return
    }

    const rangeSize = 260
    async function loadSparklines() {
      const entries = await Promise.all(
        missingTargets.map(async ({ instrumentId, detailSubjectId }) => {
          try {
            const chart = await getInstrumentChart(detailSubjectId)
            const points: FundChartPoint[] = chart.series[0]?.points?.slice(-rangeSize) ?? []
            return [instrumentId, { requestKey: sparklineRequestKey, points }] as const
          } catch (chartError) {
            return [instrumentId, { requestKey: sparklineRequestKey, points: [] as FundChartPoint[] }] as const
          }
        }),
      )

      if (cancelled) {
        return
      }

      setSparklineMap((current) => {
        const next = { ...current }
        entries.forEach(([instrumentId, entry]) => {
          next[instrumentId] = entry
        })
        return next
      })
    }

    void loadSparklines()
    return () => {
      cancelled = true
    }
  }, [screenerResult, sparklineMap, sparklineRequestKey, visibleSparklineColumns])

  async function handleBatchAddFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !watchlistId) {
      return
    }

    setIsBatchAdding(true)
    setError(null)
    setNotice(null)

    try {
      const text = await file.text()
      const rows = parseBatchInstrumentFile(text)
      if (!rows.length) {
        throw new Error('No valid rows found. Expected an Identifier / Ticker / ISIN column.')
      }

      const missingIdentifiers: string[] = []
      const resolvedInstrumentIds = new Set<string>()
      await Promise.all(
        rows.map(async (row) => {
          try {
            const resolved = await resolveSharedInstrument(row.identifier)
            if (resolved.instrument_type !== 'fund') {
              throw new Error(`${row.identifier} resolves to ${resolved.instrument_type}, but watchlist currently supports funds only.`)
            }
            resolvedInstrumentIds.add(resolved.instrument_id)
          } catch (resolveError) {
            missingIdentifiers.push(row.identifier)
          }
        }),
      )

      if (missingIdentifiers.length) {
        throw new Error(
          `Watchlist accepts shared-registry funds only. Check these identifiers in Database Dashboard: ${missingIdentifiers.join(', ')}.`,
        )
      }

      const addResult = await addWatchlistItems(watchlistId, [...resolvedInstrumentIds])
      await refreshWatchlistDetail()
      setReloadToken(Date.now())
      setModalKind(null)
      const skippedCount = Math.max(rows.length - addResult.accepted_count, 0)
      setNotice(
        skippedCount > 0
          ? `Processed ${rows.length} rows. Added ${addResult.accepted_count}; ${skippedCount} were duplicate rows or already existed in this watchlist.`
          : `Processed ${rows.length} rows. Added ${addResult.accepted_count} from shared registry.`,
      )
    } catch (batchError) {
      setError(batchError instanceof Error ? batchError.message : 'Failed to add instruments from file.')
    } finally {
      setIsBatchAdding(false)
      event.target.value = ''
    }
  }

  const activeWatchlist = watchlists.find((item) => item.watchlist_id === watchlistId) || null
  const activeWatchlistIsAllCoverage = isAllCoverageWatchlist(activeWatchlist || watchlistDetail)
  const moveTargetOptions = useMemo(
    () => watchlists.filter((item) => item.watchlist_id !== watchlistId && !isAllCoverageWatchlist(item)),
    [watchlists, watchlistId],
  )
  const copyTargetWatchlist =
    moveTargetOptions.find((item) => item.watchlist_id === copyTargetWatchlistId) || moveTargetOptions[0] || null
  const moveTargetWatchlist =
    moveTargetOptions.find((item) => item.watchlist_id === moveTargetWatchlistId) || moveTargetOptions[0] || null
  const selectedSharedInstrument =
    sharedInstrumentResults.find((item) => item.instrument_id === selectedInstrumentId) || null
  const mergedFieldRegistry = useMemo(() => {
    const sparklineFields = ['price_chart_1d', 'price_chart_1w', 'price_chart_1m', 'price_chart_1y']
    const hasSparkline = fieldRegistry.some((field) => sparklineFields.includes(field.field_key))
    if (hasSparkline) {
      return fieldRegistry
    }
    return [
      ...fieldRegistry,
      {
        field_key: 'price_chart_1d',
        label: 'Chart 1D',
        description: '1 day',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        instrument_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1w',
        label: 'Chart 1W',
        description: '1 week',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        instrument_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1m',
        label: 'Chart 1M',
        description: '1 month',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        instrument_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1y',
        label: 'Chart 1Y',
        description: '1 year',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        instrument_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
    ]
  }, [fieldRegistry])
  const activeInstrumentTypes = useMemo(() => {
    const types = new Set<string>()
    ;(screenerResult?.rows || []).forEach((row) => {
      const instrumentType = String(row.instrument_type || '').trim().toLowerCase()
      if (instrumentType) {
        types.add(instrumentType)
      }
    })
    return types.size ? [...types] : ['fund']
  }, [screenerResult])
  const supportsAnyInstrumentScope = (field: FieldRegistryRecord) => {
    if (!field.instrument_scope_json.length) {
      return true
    }
    return field.instrument_scope_json.some((instrumentType) =>
      activeInstrumentTypes.includes(String(instrumentType).trim().toLowerCase()),
    )
  }
  const supportsAllInstrumentScope = (field: FieldRegistryRecord) => {
    if (!field.instrument_scope_json.length) {
      return true
    }
    const normalizedScope = field.instrument_scope_json.map((instrumentType) =>
      String(instrumentType).trim().toLowerCase(),
    )
    return activeInstrumentTypes.every((instrumentType) => normalizedScope.includes(instrumentType))
  }
  const scopedFieldRegistry = useMemo(
    () => mergedFieldRegistry.filter((field) => supportsAnyInstrumentScope(field)),
    [mergedFieldRegistry, activeInstrumentTypes],
  )
  const taxonomyNodesByParent = useMemo(() => {
    const map = new Map<string | null, FundTaxonomyTreeNode[]>()
    ;(fundTaxonomy?.nodes || []).forEach((node) => {
      const key = node.parent_node_id || null
      map.set(key, [...(map.get(key) || []), node])
    })
    map.forEach((nodes) => {
      nodes.sort((left, right) => left.display_order - right.display_order || left.label.localeCompare(right.label, 'zh-Hans-CN'))
    })
    return map
  }, [fundTaxonomy])
  const taxonomyNodeByPath = useMemo(() => {
    const map = new Map<string, FundTaxonomyTreeNode>()
    ;(fundTaxonomy?.nodes || []).forEach((node) => {
      map.set(taxonomyPathKey(node.path_labels), node)
    })
    return map
  }, [fundTaxonomy])
  const taxonomyDisplayOrderByPath = useMemo(() => {
    const map = new Map<string, number>()
    let index = 0
    const visit = (parentId: string | null) => {
      ;(taxonomyNodesByParent.get(parentId) || []).forEach((node) => {
        map.set(taxonomyPathKey(node.path_labels), index)
        index += 1
        visit(node.node_id)
      })
    }
    visit(null)
    return map
  }, [taxonomyNodesByParent])
  const ensureRequiredColumns = (columns: string[]) => {
    const rest = columns.filter((field) => !requiredColumns.includes(field))
    return [...requiredColumns, ...rest]
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
  const visibleColumns = workingColumns.length ? workingColumns : [primaryDisplayColumn]
  const baseColumns = activeView?.columns || []
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
  const sortField = sortRules[0]?.field || null
  const sortDirection = sortRules[0]?.direction || 'asc'

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

  const availableCategoryList = useMemo(() => {
    if (fieldCategories.length) {
      return [...fieldCategories]
        .filter((category) =>
          scopedFieldRegistry.some((field) => field.category_code === category.category_code),
        )
        .sort((left, right) => left.display_order - right.display_order)
    }
    const byCode = new Map<string, FieldCategory>()
    scopedFieldRegistry.forEach((field, index) => {
      if (!byCode.has(field.category_code)) {
        byCode.set(field.category_code, {
          category_code: field.category_code,
          label: formatLabel(field.category_code),
          display_order: index,
          parent_category_code: null,
        })
      }
    })
    return [...byCode.values()]
  }, [fieldCategories, scopedFieldRegistry])

  useEffect(() => {
    if (
      selectedFieldCategory &&
      !availableCategoryList.some((category) => category.category_code === selectedFieldCategory)
    ) {
      setSelectedFieldCategory(availableCategoryList[0]?.category_code || '')
      return
    }
    if (!selectedFieldCategory && availableCategoryList.length) {
      setSelectedFieldCategory(availableCategoryList[0].category_code)
    }
  }, [availableCategoryList, selectedFieldCategory])

  const filteredFieldRegistry = scopedFieldRegistry.filter((field) => {
    const matchesCategory = !selectedFieldCategory || field.category_code === selectedFieldCategory
    const matchesSearch =
      !fieldSearch.trim() ||
      field.label.toLowerCase().includes(fieldSearch.trim().toLowerCase()) ||
      field.field_key.toLowerCase().includes(fieldSearch.trim().toLowerCase())
    return matchesCategory && matchesSearch
  })
  const availableGroupByOptions = useMemo(() => {
    return (watchlistDetail?.available_group_bys || []).filter((option) => {
      if (option.code === 'none') {
        return true
      }
      const field = mergedFieldRegistry.find((item) => item.field_key === option.code)
      if (!field) {
        return true
      }
      return supportsAllInstrumentScope(field)
    })
  }, [watchlistDetail?.available_group_bys, mergedFieldRegistry, activeInstrumentTypes])

  const filterableFields = useMemo(
    () => {
      const fields = mergedFieldRegistry
        .filter((field) => field.filter_mode === 'multi_select' && !isTaxonomyFieldKey(field.field_key))
        .sort((left, right) => left.label.localeCompare(right.label, 'zh-Hans-CN'))
      return supportsAnyInstrumentScope(TAXONOMY_FILTER_FIELD) ? [TAXONOMY_FILTER_FIELD, ...fields] : fields
    },
    [mergedFieldRegistry, activeInstrumentTypes],
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
    if (!watchlistId || !filterableFields.length) {
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
  }, [watchlistId, filterFieldKeySignature, reloadToken, optionFilterFields])

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
  const activeTaxonomyFilterNode = activeTaxonomyFilterPath.length
    ? taxonomyNodeByPath.get(taxonomyPathKey(activeTaxonomyFilterPath)) || null
    : null
  const activeTaxonomyFilterLabel = activeTaxonomyFilterPath.length
    ? taxonomyPathKey(activeTaxonomyFilterPath)
    : null
  const activeFilterCount = useMemo(() => {
    const nonTaxonomyCount = Object.entries(workingFilters).filter(
      ([fieldKey, values]) => !isTaxonomyFieldKey(fieldKey) && values.length,
    ).length
    return nonTaxonomyCount + (activeTaxonomyFilterPath.length ? 1 : 0)
  }, [workingFilters, activeTaxonomyFilterPath])

  const activeFilterEntries = useMemo(
    () => {
      const entries: ActiveFilterEntry[] = Object.entries(workingFilters).flatMap(([fieldKey, values]) => {
        if (isTaxonomyFieldKey(fieldKey)) {
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
      path.forEach((_, index) => {
        const key = taxonomyPathKey(path.slice(0, index + 1))
        counts.set(key, (counts.get(key) || 0) + 1)
      })
    })
    return counts
  }, [filterOptionRows])
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

  const groupedRows = useMemo(() => {
    const rows = screenerResult?.rows || []
    if (!activeGroupBy) {
      return [{ key: 'all', label: null, rows, summaryRows: rows, rowCount: rows.length, depth: 0 }]
    }
    if (activeGroupBy === TAXONOMY_GROUP_BY_CODE) {
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

      rows.forEach((row) => {
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
          const leftOrder = taxonomyDisplayOrderByPath.get(left.key) ?? Number.MAX_SAFE_INTEGER
          const rightOrder = taxonomyDisplayOrderByPath.get(right.key) ?? Number.MAX_SAFE_INTEGER
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
            })
          }
        })
      }
      visit([...root.values()])
      return flattened.length
        ? flattened
        : [{ key: 'all', label: null, rows, summaryRows: rows, rowCount: rows.length, depth: 0 }]
    }
    const bucketMap = new Map<string, Array<Record<string, unknown>>>()
    rows.forEach((row) => {
      const rawValue = row[activeGroupBy]
      const key = rawValue == null || rawValue === '' ? 'Unspecified' : String(rawValue)
      const current = bucketMap.get(key) || []
      current.push(row)
      bucketMap.set(key, current)
    })
    const orderedKeys = screenerResult?.groups.length
      ? screenerResult.groups.map((group) => group.group_value || 'Unspecified')
      : Array.from(bucketMap.keys())
    const seen = new Set<string>()
    const groups = orderedKeys
      .filter((key) => {
        if (seen.has(key)) {
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
  }, [activeGroupBy, screenerResult, taxonomyDisplayOrderByPath])

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

  const sortabilityByKey = useMemo(() => {
    const map = new Map<string, string>()
    mergedFieldRegistry.forEach((field) => {
      map.set(field.field_key, field.sort_mode)
    })
    return map
  }, [mergedFieldRegistry])

  const buildViewPayload = (name: string, description: string | null) => {
    const advancedFilters =
      activeView?.default_advanced_filters &&
      typeof activeView.default_advanced_filters === 'object'
        ? activeView.default_advanced_filters
        : null
    const enforcedColumns = ensureRequiredColumns(visibleColumns)
    return {
      name,
      description,
      default_group_by: workingGroupBy,
      default_sort: sortRules,
      default_filters: workingFilters,
      default_advanced_filters: advancedFilters,
      columns: enforcedColumns.map((field_key, index) => ({
        field_key,
        display_order: index,
        width: columnWidths[field_key] ?? defaultWidthByKey.get(field_key),
        is_visible: true,
      })),
    }
  }
  const allVisibleInstrumentIds = screenerResult?.rows.map((row) => String(row.instrument_id)) || []
  const allRowsSelected =
    allVisibleInstrumentIds.length > 0 && allVisibleInstrumentIds.every((instrumentId) => selectedRows.includes(instrumentId))

  async function refreshWatchlistDetail(nextViewId?: string) {
    if (!watchlistId) {
      return
    }
    try {
      const detail = await getWatchlistDetail(watchlistId)
      setWatchlistDetail(detail)
      const nextId =
        nextViewId || detail.default_view_id || detail.views[0]?.view_id || activeViewId || ''
      setActiveViewId(nextId)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to refresh watchlist.')
    }
  }

  function openSaveViewModal() {
    const defaultName = activeView?.name ? `${activeView.name} Copy` : 'Custom View'
    setSaveViewName(defaultName)
    setSaveViewDescription('')
    setModalKind('save-view')
    setFilterMenuOpen(false)
    setGroupMenuOpen(false)
    setNotice(null)
  }

  async function handleSaveActiveWatchlistView() {
    if (!watchlistId || !activeView) {
      return
    }
    setIsSavingView(true)
    setError(null)
    try {
      const updated = await updateWatchlistView(
        watchlistId,
        activeView.view_id,
        buildViewPayload(activeView.name, activeView.description),
      )
      await refreshWatchlistDetail(updated.view_id)
      setViewToast({ id: Date.now(), message: 'View updated.', tone: 'success' })
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Failed to update view.')
    } finally {
      setIsSavingView(false)
    }
  }

  async function handleDownloadCurrentView() {
    if (!baseScreenerPayload || !screenerResult?.total_rows) {
      setNotice('No visible rows to export.')
      return
    }

    setIsExporting(true)
    setError(null)
    setNotice(null)
    try {
      const exportRows = await loadAllScreenerRows(baseScreenerPayload)
      downloadCsv(['instrument_id', ...visibleColumns], exportRows)
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
  }

  function resetCopyItemsForm() {
    setCopyTargetWatchlistId(moveTargetOptions[0]?.watchlist_id || '')
    setIsCopyingItems(false)
  }

  function resetMoveItemsForm() {
    setMoveTargetWatchlistId(moveTargetOptions[0]?.watchlist_id || '')
    setIsMovingItems(false)
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

  function setTaxonomyFilter(node: FundTaxonomyTreeNode) {
    setWorkingFilters((current) => {
      const next = removeTaxonomyFilters(current)
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

  function renderTaxonomyFilterNodes(parentNodeId: string | null = null): React.ReactNode {
    const nodes = taxonomyNodesByParent.get(parentNodeId) || []
    if (!nodes.length) {
      return null
    }
    return nodes.map((node) => {
      const pathKey = taxonomyPathKey(node.path_labels)
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
      resizeState.current = null
      if (resizeFrame.current != null) {
        window.cancelAnimationFrame(resizeFrame.current)
        resizeFrame.current = null
      }
      pendingResize.current = null
      document.body.style.cursor = ''
    }

    window.addEventListener('mousemove', handleResizeMove)
    window.addEventListener('mouseup', handleResizeEnd)
    return () => {
      window.removeEventListener('mousemove', handleResizeMove)
      window.removeEventListener('mouseup', handleResizeEnd)
    }
  }, [])


  if (loading) {
    return (
      <div className="watchlists-page">
        <div className="panel">
          <div className="loading-state">Loading watchlists...</div>
        </div>
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
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <div className="watchlists-page">
      <div className="watchlists-pagehead">
        <div className="watchlist-breadcrumbs">
          <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">
            Home
          </a>
          <span className="watchlist-breadcrumb-separator">/</span>
          <Link to="/watchlists" className="watchlist-breadcrumb-link">
            Watchlist
          </Link>
          <span className="watchlist-breadcrumb-separator">/</span>
          <span className="watchlist-breadcrumb-current">{activeWatchlist?.name || 'Watchlists'}</span>
        </div>

        <div className="watchlist-app-title">Watchlist</div>

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
              <div className="watchlist-menu-shell" key={watchlist.watchlist_id} ref={selectorMenuRef}>
                <div className="watchlist-switcher-chip watchlist-switcher-chip-active">
                  <Link
                    className="watchlist-switcher-chip-label watchlist-switcher-chip-label-active"
                    to={buildWatchlistPath(watchlist.watchlist_id)}
                  >
                    {watchlist.name}
                  </Link>
                  <button
                    type="button"
                    className="watchlist-menu-trigger watchlist-menu-trigger-active"
                    onClick={() => setSelectorMenuOpen((current) => !current)}
                    aria-label="Watchlist actions"
                  >
                    ...
                  </button>
                </div>
                {selectorMenuOpen ? (
                  <div className="watchlist-menu">
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const copied = await copyWatchlist(watchlist.watchlist_id)
                          setWatchlists((current) => [...current, copied])
                          setNotice(`Copied watchlist "${watchlist.name}".`)
                        } catch (requestError) {
                          setError(
                            requestError instanceof Error
                              ? requestError.message
                              : 'Failed to copy watchlist.',
                          )
                        } finally {
                          setSelectorMenuOpen(false)
                        }
                      }}
                    >
                      Copy Watchlist
                    </button>
                    {!isAllCoverageWatchlist(watchlist) ? (
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await deleteWatchlist(watchlist.watchlist_id)
                            setWatchlists((current) =>
                              current.filter((item) => item.watchlist_id !== watchlist.watchlist_id),
                            )
                            setNotice(`Deleted watchlist "${watchlist.name}".`)
                            navigate('/watchlists')
                          } catch (requestError) {
                            setError(
                              requestError instanceof Error
                                ? requestError.message
                                : 'Failed to delete watchlist.',
                            )
                          } finally {
                            setSelectorMenuOpen(false)
                          }
                        }}
                      >
                        Delete Watchlist
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : (
              <button
                type="button"
                key={watchlist.watchlist_id}
                className="watchlist-switcher-chip watchlist-switcher-chip-inactive"
                onClick={() => navigate(buildWatchlistPath(watchlist.watchlist_id))}
              >
                {watchlist.name}
              </button>
            ),
          )}

          <button
            type="button"
            className="watchlist-create-link"
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
            {!activeWatchlistIsAllCoverage ? (
              <button
                type="button"
                className="watchlists-toolbar-button"
                onClick={() => {
                  setInstrumentSearch('')
                  setSharedInstrumentResults([])
                  setSelectedInstrumentId('')
                  setModalKind('add')
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                  setNotice(null)
                  setError(null)
                }}
              >
                Add Funds
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
                    {`View\u00A0: ${view.name}${view.view_id === activeViewId && viewEdited ? ' (Edited)' : ''}`}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="watchlists-plus-button"
                disabled={isSavingView}
                aria-label={viewEdited ? 'Save view' : 'Create view'}
                title={viewEdited ? 'Save view' : 'Create view'}
                onClick={() => {
                  if (viewEdited && watchlistId && activeView) {
                    void handleSaveActiveWatchlistView()
                  } else {
                    openSaveViewModal()
                  }
                }}
              >
                {viewEdited ? (
                  isSavingView ? (
                    '...'
                  ) : (
                    <span className="watchlists-save-icon" aria-label="Save">
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path
                          d="M4 3h12l4 4v14H4V3zm2 2v4h10V5H6zm0 8v6h12v-6H6zm2 2h4v2H8v-2z"
                          fill="currentColor"
                        />
                      </svg>
                    </span>
                  )
                ) : (
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
                )}
              </button>
            </div>

            <button
              type="button"
              className="watchlists-toolbar-button"
              onClick={() => {
                setColumnDraft(visibleColumns)
                setModalKind('columns')
                setFilterMenuOpen(false)
                setGroupMenuOpen(false)
                setNotice(null)
              }}
            >
              Data &amp; Columns
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
                        Filter the current product pool by taxonomy, research labels, and
                        monitoring labels.
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
                                <div className="watchlists-filter-values-subtitle">
                                  {selectedFilterFieldRecord.description || selectedFilterFieldRecord.field_key}
                                </div>
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
                                {fundTaxonomy?.nodes.length ? (
                                  renderTaxonomyFilterNodes()
                                ) : (
                                  <div className="watchlists-filter-empty">No taxonomy tree is available.</div>
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

            <button
              type="button"
              className="watchlists-toolbar-button"
              disabled={isExporting}
              onClick={() => void handleDownloadCurrentView()}
            >
              {isExporting ? 'Exporting...' : 'Download'}
            </button>
            {selectedRows.length ? (
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
            {selectedRows.length && !activeWatchlistIsAllCoverage ? (
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
            {selectedRows.length && !activeWatchlistIsAllCoverage ? (
              <button
                type="button"
                className="watchlists-toolbar-button watchlists-danger"
                onClick={async () => {
                  if (!watchlistId || !selectedRows.length) {
                    return
                  }
                  try {
                    await deleteWatchlistItems(watchlistId, selectedRows)
                    setSelectedRows([])
                    await refreshWatchlistDetail()
                    setReloadToken(Date.now())
                    setNotice(`Deleted ${selectedRows.length} instruments.`)
                  } catch (deleteError) {
                    setError(deleteError instanceof Error ? deleteError.message : 'Failed to delete instruments.')
                  }
                }}
              >
                Delete
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

        {notice ? <div className="inline-notice">{notice}</div> : null}
        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}

        <div className="table-shell" ref={tableShellRef}>
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
                    checked={allRowsSelected}
                    onChange={(event) =>
                      setSelectedRows(event.target.checked ? allVisibleInstrumentIds : [])
                    }
                  />
                </th>
                {visibleColumns.map((column) => {
                  const width = displayColumnWidths[column]
                  const sortMode = sortabilityByKey.get(column) || 'none'
                  return (
                    <th
                      key={column}
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
                      <span
                        className={
                          sortMode !== 'none'
                            ? 'watchlists-th-label watchlists-th-sortable'
                            : 'watchlists-th-label'
                        }
                        onClick={() => {
                          if (sortMode === 'none') {
                            return
                          }
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
                      </span>
                      <span
                        className="watchlists-th-resizer"
                        onMouseDown={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          const currentWidth = displayColumnWidths[column] || WATCHLIST_DEFAULT_COLUMN_WIDTH
                          resizeState.current = {
                            column,
                            startX: event.clientX,
                            startWidth: currentWidth,
                          }
                          document.body.style.cursor = 'col-resize'
                        }}
                      />
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {screenerResult?.rows.length ? (
                visibleGroupedRows.map((group, groupIndex) => {
                  const collapsed = collapsedGroupKeys.has(group.key)
                  return (
                    <React.Fragment key={group.key || `group-${groupIndex}`}>
                      {activeGroupBy ? (
                        <tr className="watchlists-group-row">
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
                                {average ? (
                                  <span
                                    className="watchlists-group-summary-value"
                                    title={`Equal-weight average of ${average.count}/${average.total} rows`}
                                  >
                                    <span>{formatGroupAverageCell(column, field, average.value)}</span>
                                    {average.count !== average.total ? (
                                      <small>{average.count}/{average.total}</small>
                                    ) : null}
                                  </span>
                                ) : (
                                  <span className="watchlists-group-summary-empty">—</span>
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
                                    sparklineMap[instrumentId]?.points,
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
              ) : (
                <tr>
                  <td colSpan={Math.max(visibleColumns.length + 1, 1)} className="empty-state">
                    No rows matched the current watchlist view.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {screenerResult ? (
          <div className="watchlists-pagination">
            <div className="watchlists-pagination-summary">
              {screenerResult.total_rows
                ? `Showing all ${screenerResult.total_rows} rows`
                : 'No rows in this watchlist view'}
            </div>
          </div>
        ) : null}
      </section>

      {modalKind === 'columns' ? (
        <div className="watchlists-modal-backdrop" onClick={() => setModalKind(null)}>
          <div
            className="watchlists-modal watchlists-columns-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Data &amp; Columns</div>
                <div className="section-heading">Manage Data And Columns</div>
              </div>
              <button type="button" onClick={() => setModalKind(null)}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-search">
              <input
                className="form-input"
                placeholder="Search by field name or code"
                value={fieldSearch}
                onChange={(event) => setFieldSearch(event.target.value)}
              />
            </div>

            <div className="watchlists-modal-grid">
              <div className="watchlists-modal-categories">
                {availableCategoryList.map((category) => (
                  <button
                    key={category.category_code}
                    type="button"
                    className={
                      category.category_code === selectedFieldCategory
                        ? 'watchlists-category-item watchlists-category-item-active'
                        : 'watchlists-category-item'
                    }
                    onClick={() => setSelectedFieldCategory(category.category_code)}
                  >
                    {category.label}
                  </button>
                ))}
              </div>

              <div className="watchlists-modal-fields">
                {filteredFieldRegistry.map((field) => {
                  const checked = columnDraft.includes(field.field_key)
                  const locked = requiredColumns.includes(field.field_key)
                  return (
                    <label key={field.field_key} className="watchlists-field-item">
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={locked}
                        onChange={(event) =>
                          setColumnDraft((current) =>
                            event.target.checked
                              ? [...current, field.field_key]
                              : current.filter((item) => item !== field.field_key),
                          )
                        }
                      />
                      <div>
                        <div className="watchlists-field-label">{field.label}</div>
                        <div className="watchlists-field-meta">
                          {field.field_key}
                          {locked ? ' · required' : ''}
                        </div>
                      </div>
                    </label>
                  )
                })}
              </div>

            </div>

            <div className="watchlists-modal-actions watchlists-modal-actions-sticky">
              <button
                type="button"
                onClick={() => {
                  setColumnDraft(visibleColumns)
                  setModalKind(null)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  setWorkingColumns(columnDraft.length ? columnDraft : [primaryDisplayColumn])
                  setModalKind(null)
                  setViewToast({ id: Date.now(), message: 'Columns updated. Save the view to keep changes.', tone: 'info' })
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'copy-items' ? (
        <div className="watchlists-modal-backdrop" onClick={() => !isCopyingItems && setModalKind(null)}>
          <div
            className="watchlists-modal watchlists-compact-modal"
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
              <div className="watchlists-move-summary">
                {selectedRows.length} selected from {activeWatchlist?.name || 'current watchlist'}.
              </div>
              <div className="form-field">
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
              </div>
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
                disabled={isCopyingItems || !watchlistId || !selectedRows.length || !copyTargetWatchlist}
                onClick={async () => {
                  if (!watchlistId || !selectedRows.length || !copyTargetWatchlist) {
                    return
                  }
                  setIsCopyingItems(true)
                  setError(null)
                  setNotice(null)
                  try {
                    const result = await copyWatchlistItems(
                      watchlistId,
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
                    setError(copyError instanceof Error ? copyError.message : 'Failed to copy instruments.')
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
        <div className="watchlists-modal-backdrop" onClick={() => !isMovingItems && setModalKind(null)}>
          <div
            className="watchlists-modal watchlists-compact-modal"
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
              <div className="watchlists-move-summary">
                {selectedRows.length} selected from {activeWatchlist?.name || 'current watchlist'}.
              </div>
              <div className="form-field">
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
              </div>
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
                disabled={isMovingItems || !watchlistId || !selectedRows.length || !moveTargetWatchlist}
                onClick={async () => {
                  if (!watchlistId || !selectedRows.length || !moveTargetWatchlist) {
                    return
                  }
                  setIsMovingItems(true)
                  setError(null)
                  setNotice(null)
                  try {
                    const result = await moveWatchlistItems(
                      watchlistId,
                      selectedRows,
                      moveTargetWatchlist.watchlist_id,
                    )
                    const nextWatchlists = await getWatchlists()
                    setWatchlists(nextWatchlists)
                    setSelectedRows([])
                    await refreshWatchlistDetail()
                    setReloadToken(Date.now())
                    resetMoveItemsForm()
                    setModalKind(null)
                    setNotice(
                      result.already_present_count > 0
                        ? `Moved ${result.moved_count} instruments to "${moveTargetWatchlist.name}". ${result.already_present_count} already existed there.`
                        : `Moved ${result.moved_count} instruments to "${moveTargetWatchlist.name}".`,
                    )
                  } catch (moveError) {
                    setError(moveError instanceof Error ? moveError.message : 'Failed to move instruments.')
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
        <div className="watchlists-modal-backdrop" onClick={() => setModalKind(null)}>
          <div className="watchlists-modal watchlists-save-modal" onClick={(event) => event.stopPropagation()}>
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Create Watchlist</div>
                <div className="section-heading">Create A New List For Instruments And Views</div>
              </div>
              <button
                type="button"
                onClick={() => {
                  resetCreateWatchlistForm()
                  setModalKind(null)
                }}
              >
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              <div className="form-field">
                <span>Name</span>
                <input
                  className="form-input"
                  value={createWatchlistName}
                  onChange={(event) => setCreateWatchlistName(event.target.value)}
                  placeholder="Coverage"
                />
              </div>
              <div className="form-field">
                <span>Description</span>
                <textarea
                  className="form-textarea"
                  value={createWatchlistDescription}
                  onChange={(event) => setCreateWatchlistDescription(event.target.value)}
                  placeholder="Optional description for this watchlist."
                />
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <button
                type="button"
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
                  setError(null)
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
                    setError(
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
        <div className="watchlists-modal-backdrop" onClick={() => setModalKind(null)}>
          <div className="watchlists-modal watchlists-save-modal" onClick={(event) => event.stopPropagation()}>
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Create View</div>
                <div className="section-heading">Save Current Columns And Grouping</div>
              </div>
              <button type="button" onClick={() => setModalKind(null)}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              <div className="form-field">
                <span>View Name</span>
                <input
                  className="form-input"
                  value={saveViewName}
                  onChange={(event) => setSaveViewName(event.target.value)}
                  placeholder="Custom View"
                />
              </div>
              <div className="form-field">
                <span>Description</span>
                <textarea
                  className="form-textarea"
                  value={saveViewDescription}
                  onChange={(event) => setSaveViewDescription(event.target.value)}
                  placeholder="Optional notes about this view."
                />
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <button type="button" onClick={() => setModalKind(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isSavingView || !saveViewName.trim()}
                onClick={async () => {
                  if (!watchlistId || !saveViewName.trim()) {
                    return
                  }
                  setIsSavingView(true)
                  setError(null)
                  try {
                    const payload = buildViewPayload(
                      saveViewName.trim(),
                      saveViewDescription.trim() || null,
                    )
                    const created = await createWatchlistView(watchlistId, payload)
                    await refreshWatchlistDetail(created.view_id)
                    setModalKind(null)
                    setViewToast({ id: Date.now(), message: `View saved as "${created.name}".`, tone: 'success' })
                  } catch (saveError) {
                    setError(saveError instanceof Error ? saveError.message : 'Failed to save view.')
                  } finally {
                    setIsSavingView(false)
                  }
                }}
              >
                {isSavingView ? 'Saving...' : 'Save View'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalKind === 'add' ? (
        <div className="watchlists-modal-backdrop" onClick={() => setModalKind(null)}>
          <div className="watchlists-modal watchlists-add-modal" onClick={(event) => event.stopPropagation()}>
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title">Add Funds</div>
                <div className="section-heading">Shared Registry · Fund Only</div>
              </div>
              <button type="button" onClick={() => setModalKind(null)}>
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              <div className="form-field">
                <span>Search Shared Registry</span>
                <input
                  className="form-input"
                  value={instrumentSearch}
                  onChange={(event) => setInstrumentSearch(event.target.value)}
                  placeholder="Ticker, ISIN, or fund name"
                />
              </div>
              <p className="watchlists-registry-note">
                Watchlist only references existing instruments from{' '}
                <a href={`${PLATFORM_HOME_URL}/database-dashboard`}>Database Dashboard</a>. This release only accepts
                `fund` instruments. If the fund is not listed here, it does not exist in the shared registry yet.
              </p>
              {selectedSharedInstrument ? (
                <div className="watchlists-registry-selected">
                  <span className="ticker-pill">{primarySharedIdentifier(selectedSharedInstrument)}</span>
                  <span className="watchlists-registry-name">{selectedSharedInstrument.instrument_name}</span>
                  <span className="watchlists-registry-secondary">
                    {selectedSharedInstrument.currency} · {formatLabel(selectedSharedInstrument.instrument_type)}
                  </span>
                </div>
              ) : null}
              <div className="watchlists-registry-list">
                {isSearchingInstruments ? (
                  <div className="loading-state">Searching shared registry…</div>
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
                          <span className="watchlists-registry-secondary">{instrument.instrument_name}</span>
                        </div>
                        <div className="watchlists-registry-meta">
                          <span>{formatLabel(instrument.instrument_type)}</span>
                          <span>{instrument.coverage_state || 'registry'}</span>
                        </div>
                      </button>
                    ))
                  : null}
                {!isSearchingInstruments && !sharedInstrumentResults.length ? (
                  <div className="empty-state">
                    {instrumentSearch.trim()
                      ? `Fund "${instrumentSearch.trim()}" does not exist in the shared registry.`
                      : 'No fund instruments are available in the shared registry.'}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="watchlists-modal-actions">
              <input
                ref={batchFileInputRef}
                type="file"
                accept=".csv,.tsv,.txt"
                className="watchlists-hidden-file-input"
                onChange={(event) => void handleBatchAddFileChange(event)}
              />
              <button
                type="button"
                disabled={isBatchAdding}
                onClick={() => batchFileInputRef.current?.click()}
              >
                {isBatchAdding ? 'Processing File...' : 'Add From File'}
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isAdding || !selectedInstrumentId}
                onClick={async () => {
                  if (!watchlistId || !selectedSharedInstrument) {
                    return
                  }
                  setIsAdding(true)
                  try {
                    const addResult = await addWatchlistItems(watchlistId, [selectedSharedInstrument.instrument_id])
                    await refreshWatchlistDetail()
                    setInstrumentSearch('')
                    setSharedInstrumentResults([])
                    setSelectedInstrumentId('')
                    setModalKind(null)
                    if (addResult.accepted_count === 0) {
                      setNotice(`${primarySharedIdentifier(selectedSharedInstrument)} is already in this watchlist.`)
                    } else {
                      setNotice(`Added ${primarySharedIdentifier(selectedSharedInstrument)} from the shared registry.`)
                    }
                    setReloadToken(Date.now())
                  } catch (addError) {
                    setError(addError instanceof Error ? addError.message : 'Failed to add instrument.')
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
    </>
  )
}
