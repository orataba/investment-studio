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
  label: string | null
  rows: Array<Record<string, unknown>>
  rowCount: number
  depth: number
}
type ActiveFilterEntry = {
  fieldKey: string
  value: unknown
  label: string
  valueLabel: string
  isTaxonomy: boolean
}
const WATCHLIST_PAGE_SIZE = 50
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
  asset_scope_json: ['fund'],
  product_scope_json: [],
  availability_rule_json: {},
  source_domain: 'taxonomy',
  source_metric_code: 'fund_taxonomy.tree',
  default_width: null,
  default_visible: false,
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
  const firstPage = await runScreenerQuery({
    ...payload,
    pagination: { page: 1, page_size: pageSize },
  })
  if (firstPage.total_rows <= firstPage.rows.length) {
    return firstPage.rows
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
  return rows.slice(0, firstPage.total_rows)
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
    fieldKey === 'ytd' ||
    fieldKey === 'oneYear' ||
    fieldKey === 'threeYear' ||
    fieldKey === 'fiveYear' ||
    fieldKey.endsWith('_return') ||
    fieldKey.endsWith('_return_pct') ||
    fieldKey.endsWith('_change_pct')
  )
}

function renderCell(
  fieldKey: string,
  value: unknown,
  assetId: string,
  watchlistId: string,
  sparklinePoints: FundChartPoint[] | undefined,
) {
  if (fieldKey === 'asset_name') {
    return (
      <Link to={buildInstrumentDetailPath(assetId, watchlistId)} className="table-link watchlists-asset-link">
        {typeof value === 'string' && value ? value : assetId.toUpperCase()}
      </Link>
    )
  }

  if (fieldKey === 'ticker_or_isin') {
    return <span className="ticker-pill">{String(value || '—')}</span>
  }

  if (fieldKey === 'overall_rating') {
    return value == null ? '—' : <span className="rating-pill">{String(value)}</span>
  }

  if (fieldKey === 'data_freshness_status') {
    return <span className={statusClass(value)}>{formatLabel(String(value || 'Unknown'))}</span>
  }

  if (fieldKey.includes('price_chart') || fieldKey.includes('sparkline')) {
    if (!sparklinePoints || sparklinePoints.length < 2) {
      return <span className="sparkline-empty">—</span>
    }
    const rangeSize = fieldKey.endsWith('1y')
      ? 120
      : fieldKey.endsWith('1m')
      ? 40
      : fieldKey.endsWith('1w')
      ? 20
      : 10
    const sliced =
      sparklinePoints.length > rangeSize
        ? sparklinePoints.slice(-rangeSize)
        : sparklinePoints
    const min = Math.min(...sliced.map((point) => point.value))
    const max = Math.max(...sliced.map((point) => point.value))
    const span = max - min || 1
    const width = 88
    const height = 24
    const areaBottom = height
    const points = sliced
      .map((point, index) => {
        const x = (index / (sliced.length - 1)) * (width - 1)
        const y = height - ((point.value - min) / span) * (height - 6) - 2
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
    const areaPoints = `0,${areaBottom} ${points} ${width},${areaBottom}`
    const firstValue = sliced[0]?.value ?? 0
    const lastValue = sliced[sliced.length - 1]?.value ?? firstValue
    const isPositive = lastValue >= firstValue
    const stroke = isPositive ? '#0f766e' : '#b42318'
    const fill = isPositive ? 'rgba(15, 118, 110, 0.12)' : 'rgba(180, 35, 24, 0.12)'

    return (
      <span className="sparkline-cell">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="sparkline"
          >
          <polygon points={areaPoints} fill={fill} />
          <polyline points={points} className="sparkline-path" style={{ stroke }} />
        </svg>
      </span>
    )
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
    instrument.asset_id
  )
}

export default function WatchlistsPage() {
  const primaryDisplayColumn = 'asset_name'
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
  const [workingGroupBy, setWorkingGroupBy] = useState('none')
  const [workingFilters, setWorkingFilters] = useState<FilterState>({})
  const [selectedFieldCategory, setSelectedFieldCategory] = useState('')
  const [fieldSearch, setFieldSearch] = useState('')
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [modalKind, setModalKind] = useState<ModalKind>(null)
  const [filterMenuOpen, setFilterMenuOpen] = useState(false)
  const [groupMenuOpen, setGroupMenuOpen] = useState(false)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)
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
  const [currentPage, setCurrentPage] = useState(1)
  const [sortRules, setSortRules] = useState<Array<{ field: string; direction: string }>>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sparklineMap, setSparklineMap] = useState<Record<string, SparklineCacheEntry>>({})
  const [reloadToken, setReloadToken] = useState(0)
  const filterMenuRef = useRef<HTMLDivElement | null>(null)
  const groupMenuRef = useRef<HTMLDivElement | null>(null)
  const moreMenuRef = useRef<HTMLDivElement | null>(null)
  const selectorMenuRef = useRef<HTMLDivElement | null>(null)
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
          const baseColumns = nextView?.columns?.length ? nextView.columns : [primaryDisplayColumn]
          const enforced = ensureRequiredColumns(baseColumns)
          setWorkingColumns(enforced)
          setColumnDraft(enforced)
          setWorkingGroupBy(nextView?.default_group_by || 'none')
          setWorkingFilters(normalizeFilterState(nextView?.default_filters))
          setSortRules(nextView?.default_sort?.length ? nextView.default_sort : [])
          const nextWidths: Record<string, number> = {}
          nextView?.column_meta?.forEach((item) => {
            if (item.width) {
              nextWidths[item.field_key] = item.width
            }
          })
          setColumnWidths(nextWidths)
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
      if (moreMenuOpen && moreMenuRef.current && target && !moreMenuRef.current.contains(target)) {
        setMoreMenuOpen(false)
      }
      if (selectorMenuOpen && selectorMenuRef.current && target && !selectorMenuRef.current.contains(target)) {
        setSelectorMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [filterMenuOpen, groupMenuOpen, moreMenuOpen, selectorMenuOpen])

  useEffect(() => {
    if (modalKind !== 'add') {
      return
    }

    let cancelled = false
    setIsSearchingInstruments(true)

    getSharedInstruments({
      search: instrumentSearch,
      asset_type: 'fund',
      limit: 12,
    })
      .then((results) => {
        if (cancelled) {
          return
        }

        setSharedInstrumentResults(results)
        setSelectedInstrumentId((current) => {
          if (current && results.some((item) => item.asset_id === current)) {
            return current
          }
          return results[0]?.asset_id || ''
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
    const watchlistId = watchlistDetail.watchlist_id

    async function loadRows() {
      try {
        const result = await runScreenerQuery({
          ...baseScreenerPayload,
          pagination: { page: currentPage, page_size: WATCHLIST_PAGE_SIZE },
        })

        if (!cancelled) {
          setScreenerResult(result)
          setSelectedRows((current) =>
            current.filter((assetId) => result.rows.some((row) => String(row.asset_id) === assetId)),
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
  }, [baseScreenerPayload, currentPage, reloadToken, watchlistDetail])

  useEffect(() => {
    setCurrentPage(1)
  }, [screenerCriteriaKey])

  const activeView =
    watchlistDetail?.views.find((item) => item.view_id === activeViewId) || watchlistDetail?.views[0] || null
  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((screenerResult?.total_rows || 0) / WATCHLIST_PAGE_SIZE)),
    [screenerResult?.total_rows],
  )
  const pageStart = screenerResult?.total_rows ? (currentPage - 1) * WATCHLIST_PAGE_SIZE + 1 : 0
  const pageEnd = screenerResult?.total_rows
    ? Math.min(currentPage * WATCHLIST_PAGE_SIZE, screenerResult.total_rows)
    : 0
  const visibleSparklineColumns = useMemo(
    () =>
      workingColumns.filter(
        (column) => column.startsWith('price_chart_') || column.includes('sparkline'),
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
          `${String(row.asset_id || '').trim()}:${String(row.detail_subject_id || '').trim()}`,
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
        assetId: String(row.asset_id || '').trim(),
        assetType: String(row.asset_type || '').trim().toLowerCase(),
        detailSubjectId: String(row.detail_subject_id || row.asset_id || '').trim(),
      }))
      .filter((row) => row.assetId && row.assetType === 'fund' && row.detailSubjectId)
    const missingTargets = chartTargets.filter(
      (row) => sparklineMap[row.assetId]?.requestKey !== sparklineRequestKey,
    )
    if (!missingTargets.length) {
      return
    }

    const rangeSize = 260
    async function loadSparklines() {
      const entries = await Promise.all(
        missingTargets.map(async ({ assetId, detailSubjectId }) => {
          try {
            const chart = await getInstrumentChart(detailSubjectId)
            const points: FundChartPoint[] = chart.series[0]?.points?.slice(-rangeSize) ?? []
            return [assetId, { requestKey: sparklineRequestKey, points }] as const
          } catch (chartError) {
            return [assetId, { requestKey: sparklineRequestKey, points: [] as FundChartPoint[] }] as const
          }
        }),
      )

      if (cancelled) {
        return
      }

      setSparklineMap((current) => {
        const next = { ...current }
        entries.forEach(([assetId, entry]) => {
          next[assetId] = entry
        })
        return next
      })
    }

    void loadSparklines()
    return () => {
      cancelled = true
    }
  }, [screenerResult, sparklineMap, sparklineRequestKey, visibleSparklineColumns])

  useEffect(() => {
    if (!screenerResult) {
      return
    }
    if (currentPage > totalPages) {
      setCurrentPage(totalPages)
    }
  }, [currentPage, screenerResult, totalPages])

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
      const resolvedAssetIds = new Set<string>()
      await Promise.all(
        rows.map(async (row) => {
          try {
            const resolved = await resolveSharedInstrument(row.identifier)
            if (resolved.asset_type !== 'fund') {
              throw new Error(`${row.identifier} resolves to ${resolved.asset_type}, but watchlist currently supports funds only.`)
            }
            resolvedAssetIds.add(resolved.asset_id)
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

      const addResult = await addWatchlistItems(watchlistId, [...resolvedAssetIds])
      await refreshWatchlistDetail()
      setReloadToken(Date.now())
      setModalKind(null)
      const duplicateCount = Math.max(resolvedAssetIds.size - addResult.accepted_count, 0)
      setNotice(
        duplicateCount > 0
          ? `Processed ${rows.length} rows. Added ${addResult.accepted_count}, ${duplicateCount} already existed in this watchlist.`
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
    sharedInstrumentResults.find((item) => item.asset_id === selectedInstrumentId) || null
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
        label: 'Price Chart',
        description: '1 day',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        asset_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1w',
        label: 'Price Chart',
        description: '1 week',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        asset_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1m',
        label: 'Price Chart',
        description: '1 month',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        asset_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
      {
        field_key: 'price_chart_1y',
        label: 'Price Chart',
        description: '1 year',
        category_code: 'market_data',
        data_type: 'sparkline',
        formatter_code: 'sparkline',
        sort_mode: 'none',
        filter_mode: 'none',
        group_mode: 'none',
        asset_scope_json: ['fund'],
        product_scope_json: [],
        availability_rule_json: {},
        source_domain: 'derived',
        source_metric_code: 'nav_chart',
        default_width: null,
        default_visible: false,
      },
    ]
  }, [fieldRegistry])
  const activeAssetTypes = useMemo(() => {
    const types = new Set<string>()
    ;(screenerResult?.rows || []).forEach((row) => {
      const assetType = String(row.asset_type || '').trim().toLowerCase()
      if (assetType) {
        types.add(assetType)
      }
    })
    return [...types]
  }, [screenerResult])
  const supportsAnyAssetScope = (field: FieldRegistryRecord) => {
    if (!field.asset_scope_json.length) {
      return true
    }
    if (!activeAssetTypes.length) {
      return false
    }
    return field.asset_scope_json.some((assetType) =>
      activeAssetTypes.includes(String(assetType).trim().toLowerCase()),
    )
  }
  const supportsAllAssetScope = (field: FieldRegistryRecord) => {
    if (!field.asset_scope_json.length) {
      return true
    }
    if (!activeAssetTypes.length) {
      return false
    }
    const normalizedScope = field.asset_scope_json.map((assetType) =>
      String(assetType).trim().toLowerCase(),
    )
    return activeAssetTypes.every((assetType) => normalizedScope.includes(assetType))
  }
  const scopedFieldRegistry = useMemo(
    () => mergedFieldRegistry.filter((field) => supportsAnyAssetScope(field)),
    [mergedFieldRegistry, activeAssetTypes],
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
  const fieldLabelByKey = useMemo(
    () => new Map([...mergedFieldRegistry.map((field) => [field.field_key, field.label] as const), [TAXONOMY_GROUP_BY_CODE, 'Taxonomy']]),
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
  const viewEdited =
    JSON.stringify(visibleColumns) !== JSON.stringify(baseColumns) ||
    workingGroupBy !== baseGroupBy ||
    serializeFilterState(workingFilters) !== serializeFilterState(baseFilters) ||
    JSON.stringify(sortRules) !== JSON.stringify(baseSort)
  const activeGroupBy = workingGroupBy && workingGroupBy !== 'none' ? workingGroupBy : null
  const sortField = sortRules[0]?.field || null
  const sortDirection = sortRules[0]?.direction || 'asc'

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
      return supportsAllAssetScope(field)
    })
  }, [watchlistDetail?.available_group_bys, mergedFieldRegistry, activeAssetTypes])

  const filterableFields = useMemo(
    () => {
      const fields = mergedFieldRegistry
        .filter((field) => field.filter_mode === 'multi_select' && !isTaxonomyFieldKey(field.field_key))
        .sort((left, right) => left.label.localeCompare(right.label, 'zh-Hans-CN'))
      return supportsAnyAssetScope(TAXONOMY_FILTER_FIELD) ? [TAXONOMY_FILTER_FIELD, ...fields] : fields
    },
    [mergedFieldRegistry, activeAssetTypes],
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
      return [{ label: null, rows, rowCount: rows.length, depth: 0 }]
    }
    if (activeGroupBy === TAXONOMY_GROUP_BY_CODE) {
      type TreeNode = {
        key: string
        label: string
        depth: number
        rowCount: number
        rows: Array<Record<string, unknown>>
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
          return
        }
        let currentMap = root
        let currentNode: TreeNode | null = null
        for (const [index, label] of path.entries()) {
          currentNode = getOrCreate(currentMap, path.slice(0, index + 1), label, index)
          currentNode.rowCount += 1
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
            label: node.label,
            rows: node.children.size ? [] : node.rows,
            rowCount: node.rowCount,
            depth: node.depth,
          })
          visit([...node.children.values()])
          if (node.children.size && node.rows.length) {
            flattened.push({
              label: `${node.label} · Direct`,
              rows: node.rows,
              rowCount: node.rows.length,
              depth: node.depth + 1,
            })
          }
        })
      }
      visit([...root.values()])
      return flattened.length ? flattened : [{ label: null, rows, rowCount: rows.length, depth: 0 }]
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
        label: key,
        rows: bucketMap.get(key) || [],
        rowCount: bucketMap.get(key)?.length || 0,
        depth: 0,
      }))
    bucketMap.forEach((value, key) => {
      if (!seen.has(key)) {
        groups.push({ label: key, rows: value, rowCount: value.length, depth: 0 })
      }
    })
    return groups
  }, [activeGroupBy, screenerResult, taxonomyDisplayOrderByPath])

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
  const allVisibleRowIds = screenerResult?.rows.map((row) => String(row.asset_id)) || []
  const allRowsSelected =
    allVisibleRowIds.length > 0 && allVisibleRowIds.every((assetId) => selectedRows.includes(assetId))

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

  async function handleDownloadCurrentView() {
    if (!baseScreenerPayload || !screenerResult?.total_rows) {
      setNotice('No visible rows to export.')
      return
    }

    setIsExporting(true)
    setMoreMenuOpen(false)
    setError(null)
    setNotice(null)
    try {
      const exportRows = await loadAllScreenerRows(baseScreenerPayload)
      downloadCsv(['asset_id', ...visibleColumns], exportRows)
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
                className="button-primary watchlists-toolbar-button"
                onClick={() => {
                  setInstrumentSearch('')
                  setSharedInstrumentResults([])
                  setSelectedInstrumentId('')
                  setModalKind('add')
                  setFilterMenuOpen(false)
                  setMoreMenuOpen(false)
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
                  const baseColumns = nextView?.columns?.length ? nextView.columns : [primaryDisplayColumn]
                  const enforced = ensureRequiredColumns(baseColumns)
                  setWorkingColumns(enforced)
                  setColumnDraft(enforced)
                  setWorkingGroupBy(nextView?.default_group_by || 'none')
                  setWorkingFilters(normalizeFilterState(nextView?.default_filters))
                  setSortRules(nextView?.default_sort?.length ? nextView.default_sort : [])
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
                onClick={() => {
                  if (viewEdited && watchlistId && activeView) {
                    setIsSavingView(true)
                    updateWatchlistView(
                      watchlistId,
                      activeView.view_id,
                      buildViewPayload(activeView.name, activeView.description),
                    )
                      .then((updated) => refreshWatchlistDetail(updated.view_id))
                      .then(() => setNotice('View updated.'))
                      .catch((saveError) =>
                        setError(saveError instanceof Error ? saveError.message : 'Failed to update view.'),
                      )
                      .finally(() => setIsSavingView(false))
                  } else {
                    const defaultName = activeView?.name ? `${activeView.name} Copy` : 'Custom View'
                    setSaveViewName(defaultName)
                    setSaveViewDescription('')
                    setModalKind('save-view')
                    setFilterMenuOpen(false)
                    setMoreMenuOpen(false)
                    setGroupMenuOpen(false)
                    setNotice(null)
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
                  '+'
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
                setMoreMenuOpen(false)
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
                  setMoreMenuOpen(false)
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
                  setMoreMenuOpen(false)
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

            <div className="watchlists-dropdown" ref={moreMenuRef}>
              <button
                type="button"
                className="watchlists-toolbar-button"
                onClick={() => {
                  setMoreMenuOpen((current) => !current)
                  setFilterMenuOpen(false)
                  setGroupMenuOpen(false)
                  setModalKind(null)
                }}
              >
                More
              </button>
              {moreMenuOpen ? (
                <div className="watchlists-menu">
                  <button
                    type="button"
                    className="watchlists-menu-item"
                    disabled={isExporting}
                    onClick={() => void handleDownloadCurrentView()}
                  >
                    {isExporting ? 'Exporting...' : 'Download'}
                  </button>
                </div>
              ) : null}
            </div>
            {selectedRows.length ? (
              <button
                type="button"
                className="watchlists-toolbar-button"
                disabled={!moveTargetOptions.length}
                onClick={() => {
                  resetCopyItemsForm()
                  setModalKind('copy-items')
                  setFilterMenuOpen(false)
                  setMoreMenuOpen(false)
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
                  setMoreMenuOpen(false)
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

        <div className="table-shell">
          <table className="terminal-table watchlists-table">
            <colgroup>
              <col style={{ width: '44px' }} />
              {visibleColumns.map((column) => {
                const width = columnWidths[column] ?? defaultWidthByKey.get(column)
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
                      setSelectedRows(event.target.checked ? allVisibleRowIds : [])
                    }
                  />
                </th>
                {visibleColumns.map((column) => {
                  const width = columnWidths[column] ?? defaultWidthByKey.get(column)
                  const sortMode = sortabilityByKey.get(column) || 'none'
                  return (
                    <th key={column} style={width ? { width: `${width}px` } : undefined}>
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
                        {column.startsWith('price_chart_') ? (
                          <span className="watchlists-th-sub">
                            {column.endsWith('1d')
                              ? '1 Day'
                              : column.endsWith('1w')
                              ? '1 Week'
                              : column.endsWith('1m')
                              ? '1 Month'
                              : '1 Year'}
                          </span>
                        ) : null}
                        {sortField === column ? (
                          <span className="watchlists-sort-indicator">
                            {sortDirection.toLowerCase() === 'desc' ? '↓' : '↑'}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className="watchlists-th-resizer"
                        onMouseDown={(event) => {
                          const currentWidth = columnWidths[column] || 140
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
                groupedRows.map((group, groupIndex) => (
                  <React.Fragment key={group.label || `group-${groupIndex}`}>
                    {activeGroupBy ? (
                      <tr className="watchlists-group-row">
                        <td colSpan={Math.max(visibleColumns.length + 1, 1)}>
                          <div
                            className="watchlists-group-header"
                            style={{ paddingLeft: `${group.depth * 18}px` }}
                          >
                            <span className="watchlists-group-caret">▾</span>
                            <span className="watchlists-group-title">
                              {group.label || 'Unspecified'}
                            </span>
                            <span className="watchlists-group-count">{group.rowCount}</span>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                    {group.rows.map((row, index) => {
                      const assetId = String(row.asset_id || `row-${index}`)
                      const checked = selectedRows.includes(assetId)
                      return (
                        <tr key={assetId}>
                          <td className="watchlists-select-col">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) =>
                                setSelectedRows((current) =>
                                  event.target.checked
                                    ? [...current, assetId]
                                    : current.filter((item) => item !== assetId),
                                )
                              }
                            />
                          </td>
                          {visibleColumns.map((column) => (
                            <td key={column}>
                              {renderCell(
                                column,
                                row[column],
                                assetId,
                                watchlistId,
                                sparklineMap[assetId]?.points,
                              )}
                            </td>
                          ))}
                        </tr>
                      )
                    })}
                  </React.Fragment>
                ))
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
                ? `Showing ${pageStart}-${pageEnd} of ${screenerResult.total_rows} rows`
                : 'No rows in this watchlist view'}
            </div>
            <div className="watchlists-pagination-actions">
              <button
                type="button"
                className="watchlists-pagination-button"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage(1)}
              >
                First
              </button>
              <button
                type="button"
                className="watchlists-pagination-button"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((page) => Math.max(page - 1, 1))}
              >
                Previous
              </button>
              <span className="watchlists-pagination-status">
                {`Page ${currentPage} of ${totalPages}`}
              </span>
              <button
                type="button"
                className="watchlists-pagination-button"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((page) => Math.min(page + 1, totalPages))}
              >
                Next
              </button>
              <button
                type="button"
                className="watchlists-pagination-button"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage(totalPages)}
              >
                Last
              </button>
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

              <div className="watchlists-modal-arrange">
                <div className="watchlists-arrange-head">
                  <span>Arrange</span>
                  <button type="button" onClick={() => setColumnDraft([])}>
                    Remove All
                  </button>
                </div>

              <div className="watchlists-arrange-list">
                {columnDraft.map((column, index) => (
                  <div
                    key={column}
                    className="watchlists-arrange-item"
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData('text/plain', String(index))
                      event.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => {
                      event.preventDefault()
                      const fromIndex = Number(event.dataTransfer.getData('text/plain'))
                      if (Number.isNaN(fromIndex) || fromIndex === index) {
                        return
                      }
                      setColumnDraft((current) => {
                        const next = [...current]
                        const [moved] = next.splice(fromIndex, 1)
                        next.splice(index, 0, moved)
                        return next
                      })
                    }}
                  >
                    <span className="watchlists-arrange-handle">⋮⋮</span>
                    <span>{fieldLabelByKey.get(column) || formatLabel(column)}</span>
                    <div className="watchlists-arrange-actions">
                      {requiredColumns.includes(column) ? (
                        <span className="watchlists-arrange-lock">Required</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setColumnDraft((current) => current.filter((item) => item !== column))}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
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
                  setNotice('Columns updated. Use + to save as a new view.')
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
                <div className="section-heading">Copy Selected Assets To Another Watchlist</div>
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
                Assets already present in the target will not be duplicated. They will remain in the current
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
                <div className="section-heading">Move Selected Assets To Another Watchlist</div>
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
                Assets already present in the target will not be duplicated. They will still be removed from the
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
                  try {
                    const advancedFilters =
                      activeView?.default_advanced_filters &&
                      typeof activeView.default_advanced_filters === 'object'
                        ? activeView.default_advanced_filters
                        : null
                    const payload = buildViewPayload(
                      saveViewName.trim(),
                      saveViewDescription.trim() || null,
                    )
                    const created = await createWatchlistView(watchlistId, payload)
                    await refreshWatchlistDetail(created.view_id)
                    setModalKind(null)
                    setNotice(`View saved as "${created.name}".`)
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
                Watchlist only references existing assets from{' '}
                <a href={`${PLATFORM_HOME_URL}/database-dashboard`}>Database Dashboard</a>. This release only accepts
                `fund` assets. If the fund is not listed here, it does not exist in the shared registry yet.
              </p>
              {selectedSharedInstrument ? (
                <div className="watchlists-registry-selected">
                  <span className="ticker-pill">{primarySharedIdentifier(selectedSharedInstrument)}</span>
                  <span className="watchlists-registry-name">{selectedSharedInstrument.asset_name}</span>
                  <span className="watchlists-registry-secondary">
                    {selectedSharedInstrument.currency} · {formatLabel(selectedSharedInstrument.asset_type)}
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
                        key={instrument.asset_id}
                        className={`watchlists-registry-row ${
                          selectedInstrumentId === instrument.asset_id ? 'watchlists-registry-row-active' : ''
                        }`}
                        onClick={() => setSelectedInstrumentId(instrument.asset_id)}
                      >
                        <div className="watchlists-registry-row-main">
                          <span>{primarySharedIdentifier(instrument)}</span>
                          <span className="watchlists-registry-secondary">{instrument.asset_name}</span>
                        </div>
                        <div className="watchlists-registry-meta">
                          <span>{formatLabel(instrument.asset_type)}</span>
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
                    const addResult = await addWatchlistItems(watchlistId, [selectedSharedInstrument.asset_id])
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
  )
}
