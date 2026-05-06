import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import {
  getHoldingsWorkspace,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioHoldingRow,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type SparklinePoint,
} from '../lib/api'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'

type HoldingsColumnKey =
  | 'asset'
  | 'ticker'
  | 'asset_type'
  | 'taxonomy_top'
  | 'taxonomy_leaf'
  | 'currency'
  | 'holding_date'
  | 'quantity'
  | 'cost_method'
  | 'avg_cost_book'
  | 'last_price'
  | 'quote_date'
  | 'quote_basis'
  | 'quote_provider'
  | 'quote_status'
  | 'market_value'
  | 'market_value_base'
  | 'cost_basis'
  | 'cost_basis_base'
  | 'weight'
  | 'accounts'
  | 'open_lots'
  | 'day_change_value'
  | 'day_change_pct'
  | 'unrealized_value'
  | 'unrealized_pct'
  | 'chart_return'
  | 'chart_volatility'
  | 'chart_max_drawdown'
  | 'price_chart'
  | 'coverage'

type HoldingsGroupByKey = 'none' | 'taxonomy_top' | 'taxonomy_leaf' | 'asset_type' | 'currency' | 'coverage'
type HoldingsSortDirection = 'asc' | 'desc'
type SortableValue = number | string | null | undefined

type HoldingTaxonomyLabels = {
  topLevelId: string
  topLevelLabel: string
  leafId: string
  leafLabel: string
}

type HoldingsColumnContext = {
  workspace: HoldingsWorkspaceResponse
  taxonomyByAssetId: Map<string, HoldingTaxonomyLabels>
}

type HoldingsColumnDefinition = {
  key: HoldingsColumnKey
  label: string
  align?: 'right'
  render: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => ReactNode
  sortValue: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => SortableValue
  className?: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => string
  total?: (rows: PortfolioHoldingRow[], context: HoldingsColumnContext) => ReactNode
  totalClassName?: (rows: PortfolioHoldingRow[], context: HoldingsColumnContext) => string
}

type HoldingsGroup = {
  key: string
  label: string
  rows: PortfolioHoldingRow[]
  marketValueBase: number
  weight: number
  openLots: number
}

type HoldingsViewState = {
  columns: HoldingsColumnKey[]
  columnWidths: Partial<Record<HoldingsColumnKey, number>>
  groupBy: HoldingsGroupByKey
  sortField: HoldingsColumnKey | null
  sortDirection: HoldingsSortDirection
}

type HoldingsTableView = PortfolioTableViewOption & {
  state: HoldingsViewState
  readonly?: boolean
  createdAt?: string
  updatedAt?: string
}

type HoldingsViewStore = {
  activeViewId: string
  customViews: HoldingsTableView[]
}

const LOCKED_HOLDINGS_COLUMN: HoldingsColumnKey = 'asset'
const HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY = 'yungu.portfolio.holdings.columnWidths.v2'
const HOLDINGS_VIEWS_STORAGE_KEY = 'yungu.portfolio.holdings.views.v2'
const HOLDINGS_COLUMN_MIN_WIDTH = 84
const HOLDINGS_COLUMN_MAX_WIDTH = 520

const HOLDINGS_COLUMN_GROUPS: Array<{ label: string; columns: HoldingsColumnKey[] }> = [
  {
    label: 'Identity',
    columns: ['asset', 'ticker', 'asset_type', 'taxonomy_top', 'taxonomy_leaf', 'currency', 'coverage'],
  },
  {
    label: 'Quote',
    columns: ['last_price', 'quote_date', 'quote_basis', 'quote_provider', 'quote_status', 'price_chart'],
  },
  {
    label: 'Position',
    columns: [
      'holding_date',
      'quantity',
      'market_value',
      'market_value_base',
      'weight',
      'accounts',
      'open_lots',
    ],
  },
  {
    label: 'Cost',
    columns: [
      'cost_method',
      'avg_cost_book',
      'cost_basis',
      'cost_basis_base',
    ],
  },
  {
    label: 'P&L',
    columns: ['day_change_pct', 'day_change_value', 'unrealized_value', 'unrealized_pct', 'chart_return'],
  },
  {
    label: 'Risk',
    columns: ['chart_volatility', 'chart_max_drawdown'],
  },
]

const ALL_HOLDINGS_COLUMN_KEYS = HOLDINGS_COLUMN_GROUPS.flatMap((group) => group.columns)

const DEFAULT_HOLDINGS_COLUMNS: HoldingsColumnKey[] = [
  'asset',
  'last_price',
  'quote_date',
  'quantity',
  'avg_cost_book',
  'cost_basis',
  'market_value',
  'weight',
  'unrealized_value',
  'unrealized_pct',
]

const DEFAULT_HOLDINGS_COLUMN_WIDTHS: Record<HoldingsColumnKey, number> = {
  asset: 320,
  ticker: 120,
  asset_type: 116,
  taxonomy_top: 152,
  taxonomy_leaf: 176,
  currency: 92,
  holding_date: 126,
  quantity: 132,
  cost_method: 118,
  avg_cost_book: 148,
  last_price: 120,
  quote_date: 126,
  quote_basis: 126,
  quote_provider: 132,
  quote_status: 120,
  market_value: 148,
  market_value_base: 164,
  cost_basis: 148,
  cost_basis_base: 164,
  weight: 104,
  accounts: 104,
  open_lots: 112,
  day_change_value: 140,
  day_change_pct: 120,
  unrealized_value: 148,
  unrealized_pct: 148,
  chart_return: 128,
  chart_volatility: 112,
  chart_max_drawdown: 132,
  price_chart: 128,
  coverage: 132,
}

const DEFAULT_HOLDINGS_VIEW_STATE: HoldingsViewState = {
  columns: DEFAULT_HOLDINGS_COLUMNS,
  columnWidths: {},
  groupBy: 'none',
  sortField: null,
  sortDirection: 'asc',
}

const SYSTEM_HOLDINGS_VIEWS: HoldingsTableView[] = [
  {
    id: 'default',
    name: 'Default',
    description: 'Book cost, valuation, and unrealized P&L.',
    readonly: true,
    state: DEFAULT_HOLDINGS_VIEW_STATE,
  },
  {
    id: 'taxonomy',
    name: 'Taxonomy',
    description: 'Grouped by the default planning taxonomy.',
    readonly: true,
    state: {
      columns: ['asset', 'taxonomy_top', 'taxonomy_leaf', 'market_value_base', 'weight', 'day_change_pct', 'unrealized_pct', 'open_lots', 'coverage'],
      columnWidths: {},
      groupBy: 'taxonomy_top',
      sortField: 'weight',
      sortDirection: 'desc',
    },
  },
  {
    id: 'return-risk',
    name: 'Return & Risk',
    description: 'Return and risk metrics for broad scanning.',
    readonly: true,
    state: {
      columns: [
        'asset',
        'asset_type',
        'market_value_base',
        'weight',
        'day_change_pct',
        'day_change_value',
        'unrealized_value',
        'unrealized_pct',
        'chart_return',
        'chart_volatility',
        'chart_max_drawdown',
      ],
      columnWidths: {},
      groupBy: 'none',
      sortField: 'unrealized_pct',
      sortDirection: 'desc',
    },
  },
  {
    id: 'open-lots',
    name: 'Open Lots',
    description: 'Position dates, cost, lots, and accounts.',
    readonly: true,
    state: {
      columns: [
        'asset',
        'asset_type',
        'holding_date',
        'quantity',
        'cost_method',
        'avg_cost_book',
        'last_price',
        'market_value',
        'cost_basis',
        'weight',
        'accounts',
        'open_lots',
      ],
      columnWidths: {},
      groupBy: 'asset_type',
      sortField: 'open_lots',
      sortDirection: 'desc',
    },
  },
  {
    id: 'accounting',
    name: 'Accounting',
    description: 'Cost basis, currency, and base-currency values.',
    readonly: true,
    state: {
      columns: [
        'asset',
        'ticker',
        'currency',
        'quantity',
        'cost_method',
        'avg_cost_book',
        'market_value',
        'market_value_base',
        'cost_basis',
        'cost_basis_base',
        'unrealized_value',
        'accounts',
      ],
      columnWidths: {},
      groupBy: 'currency',
      sortField: 'market_value_base',
      sortDirection: 'desc',
    },
  },
]

const TEXT_HOLDINGS_SORT_FIELDS = new Set<HoldingsColumnKey>([
  'asset',
  'ticker',
  'asset_type',
  'taxonomy_top',
  'taxonomy_leaf',
  'currency',
  'holding_date',
  'cost_method',
  'quote_date',
  'quote_basis',
  'quote_provider',
  'quote_status',
  'coverage',
])

const HOLDINGS_GROUP_BY_OPTIONS: Array<{
  value: HoldingsGroupByKey
  label: string
  description: string
}> = [
  { value: 'none', label: 'None', description: 'Flat position table.' },
  { value: 'taxonomy_top', label: 'Taxonomy', description: 'Top-level default planning taxonomy.' },
  { value: 'taxonomy_leaf', label: 'Taxonomy Leaf', description: 'Assigned terminal taxonomy node.' },
  { value: 'asset_type', label: 'Asset Type', description: 'Fund, equity, bond, cash, FX, or other.' },
  { value: 'currency', label: 'Currency', description: 'Holding currency.' },
  { value: 'coverage', label: 'Coverage', description: 'Market data coverage state.' },
]

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.asset_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.asset_core.identifiers[0]?.identifier_value ??
    row.asset_core.asset_id
  )
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

type NormalizedCostMethod = 'fifo' | 'moving_average' | 'mixed'

function normalizeCostMethod(value: string | null | undefined): NormalizedCostMethod {
  const normalized = String(value || 'fifo').trim().toLowerCase()
  if (normalized === 'moving_average') {
    return 'moving_average'
  }
  if (normalized === 'mixed') {
    return 'mixed'
  }
  return 'fifo'
}

function costMethodLabel(value: string | null | undefined) {
  const normalized = normalizeCostMethod(value)
  if (normalized === 'moving_average') {
    return 'Moving Avg'
  }
  if (normalized === 'mixed') {
    return 'Mixed'
  }
  return 'FIFO'
}

function signedPercent(value: number | null | undefined, digits = 2) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return '—'
  }
  if (finiteValue > 0) {
    return `+${formatPercent(finiteValue, digits)}`
  }
  if (finiteValue < 0) {
    return `-${formatPercent(Math.abs(finiteValue), digits)}`
  }
  return formatPercent(finiteValue, digits)
}

function signedCurrency(value: number | null | undefined, currency: string) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return '—'
  }
  const formatted = formatCurrency(Math.abs(finiteValue), currency)
  if (finiteValue > 0) {
    return `+${formatted}`
  }
  if (finiteValue < 0) {
    return `-${formatted}`
  }
  return formatted
}

function sumNumbers(rows: PortfolioHoldingRow[], accessor: (row: PortfolioHoldingRow) => number | null | undefined) {
  let hasValue = false
  const total = rows.reduce((sum, row) => {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return sum
    }
    hasValue = true
    return sum + value
  }, 0)
  return hasValue ? total : null
}

function bookAvgCost(row: PortfolioHoldingRow) {
  const quantity = finiteNumber(row.quantity)
  const costBasis = finiteNumber(row.cost_basis)
  if (quantity == null || Math.abs(quantity) <= 1e-12 || costBasis == null) {
    return null
  }
  return costBasis / quantity
}

function quoteDate(row: PortfolioHoldingRow) {
  if (row.quote_as_of_date) {
    return row.quote_as_of_date
  }
  return row.price_chart[row.price_chart.length - 1]?.date ?? null
}

function unrealizedValue(row: PortfolioHoldingRow) {
  const marketValue = finiteNumber(row.market_value)
  const costBasis = finiteNumber(row.cost_basis)
  return marketValue == null || costBasis == null ? null : marketValue - costBasis
}

function unrealizedBaseValue(row: PortfolioHoldingRow) {
  const marketValue = finiteNumber(row.market_value_base ?? row.market_value)
  const costBasis = finiteNumber(row.cost_basis_base ?? row.cost_basis)
  return marketValue == null || costBasis == null ? null : marketValue - costBasis
}

function unrealizedPct(row: PortfolioHoldingRow) {
  const costBasis = finiteNumber(row.cost_basis)
  const unrealized = unrealizedValue(row)
  if (costBasis == null || Math.abs(costBasis) <= 1e-12 || unrealized == null) {
    return null
  }
  return unrealized / Math.abs(costBasis)
}

function totalUnrealizedBase(rows: PortfolioHoldingRow[]) {
  const marketValue = sumNumbers(rows, (row) => row.market_value_base ?? row.market_value)
  const costBasis = sumNumbers(rows, (row) => row.cost_basis_base ?? row.cost_basis)
  return marketValue == null || costBasis == null ? null : marketValue - costBasis
}

function totalUnrealizedPct(rows: PortfolioHoldingRow[]) {
  const costBasis = sumNumbers(rows, (row) => row.cost_basis_base ?? row.cost_basis)
  const unrealized = totalUnrealizedBase(rows)
  if (costBasis == null || Math.abs(costBasis) <= 1e-12 || unrealized == null) {
    return null
  }
  return unrealized / Math.abs(costBasis)
}

function chartReturn(row: PortfolioHoldingRow) {
  const points = row.price_chart.filter((point) => Number.isFinite(point.value))
  const first = points[0]?.value
  const last = points[points.length - 1]?.value
  if (first == null || last == null || Math.abs(first) <= 1e-12) {
    return null
  }
  return last / first - 1
}

function chartReturns(row: PortfolioHoldingRow) {
  const points = row.price_chart.filter((point) => Number.isFinite(point.value))
  const returns: number[] = []
  points.slice(1).forEach((point, index) => {
    const previous = points[index]
    if (previous.value > 1e-12) {
      returns.push(point.value / previous.value - 1)
    }
  })
  return returns
}

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + (value - mean) * (value - mean), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function chartVolatility(row: PortfolioHoldingRow) {
  const stddev = sampleStddev(chartReturns(row))
  return stddev == null ? null : stddev * Math.sqrt(252)
}

function chartMaxDrawdown(row: PortfolioHoldingRow) {
  const points = row.price_chart.filter((point) => Number.isFinite(point.value))
  if (points.length < 2) {
    return null
  }
  let peak = points[0].value
  let maxDrawdown = 0
  points.forEach((point) => {
    peak = Math.max(peak, point.value)
    if (peak > 1e-12) {
      maxDrawdown = Math.min(maxDrawdown, point.value / peak - 1)
    }
  })
  return maxDrawdown
}

function totalMarketValueBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumNumbers(rows, (row) => row.market_value_base ?? row.market_value)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.market_value ?? rowTotal : rowTotal
}

function totalCostBasisBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumNumbers(rows, (row) => row.cost_basis_base ?? row.cost_basis)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.cost_basis ?? rowTotal : rowTotal
}

function totalAllocation(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumNumbers(rows, (row) => row.allocation)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.allocation ?? rowTotal : rowTotal
}

function totalDayChangeBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumNumbers(rows, (row) => row.day_change_value)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.day_change_value ?? rowTotal : rowTotal
}

function totalDayChangePct(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const explicit = rowsCoverWorkspace(rows, workspace) ? finiteNumber(workspace.totals.day_change_pct) : null
  if (explicit != null) {
    return explicit
  }
  const dayChange = totalDayChangeBase(rows, workspace)
  const marketValue = totalMarketValueBase(rows, workspace)
  return dayChange == null || marketValue == null || Math.abs(marketValue) <= 1e-12 ? null : dayChange / marketValue
}

function rowsCoverWorkspace(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  if (rows.length !== workspace.rows.length) {
    return false
  }
  const rowIds = new Set(rows.map((row) => row.line_id))
  return workspace.rows.every((row) => rowIds.has(row.line_id))
}

function isRecordActive(effectiveFrom: string | null | undefined, effectiveTo: string | null | undefined, referenceDate: string) {
  if (effectiveFrom && effectiveFrom > referenceDate) {
    return false
  }
  if (effectiveTo && effectiveTo < referenceDate) {
    return false
  }
  return true
}

function resolveNodePath(taxonomyNodeId: string, nodesById: Map<string, PortfolioTaxonomyNodeRecord>) {
  const path: PortfolioTaxonomyNodeRecord[] = []
  let cursor: PortfolioTaxonomyNodeRecord | undefined = nodesById.get(taxonomyNodeId)
  const seen = new Set<string>()
  while (cursor && !seen.has(cursor.taxonomy_node_id)) {
    path.unshift(cursor)
    seen.add(cursor.taxonomy_node_id)
    cursor = cursor.parent_taxonomy_node_id ? nodesById.get(cursor.parent_taxonomy_node_id) : undefined
  }
  return path
}

function resolveGroupingTaxonomy(catalog: PortfolioTaxonomyCatalogResponse | null) {
  const taxonomies = (catalog?.taxonomies ?? []).filter(
    (taxonomy) => taxonomy.status === 'active' && taxonomy.primary_assignment_scope === 'instrument',
  )
  return (
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === catalog?.default_planning_taxonomy_id) ??
    taxonomies.find((taxonomy) => taxonomy.planning_enabled) ??
    taxonomies[0] ??
    null
  )
}

function buildTaxonomyLabelsByAssetId(
  catalog: PortfolioTaxonomyCatalogResponse | null,
  referenceDate: string | null | undefined,
) {
  const taxonomy = resolveGroupingTaxonomy(catalog)
  if (!catalog || !taxonomy || !referenceDate) {
    return new Map<string, HoldingTaxonomyLabels>()
  }

  const nodesById = new Map<string, PortfolioTaxonomyNodeRecord>(
    catalog.taxonomy_nodes
      .filter((node) => node.taxonomy_id === taxonomy.taxonomy_id && node.status === 'active')
      .map((node) => [node.taxonomy_node_id, node]),
  )

  const assignmentByAssetId = new Map<string, HoldingTaxonomyLabels>()
  ;[...catalog.taxonomy_assignments]
    .filter(
      (assignment) =>
        assignment.taxonomy_id === taxonomy.taxonomy_id &&
        assignment.target_scope === 'instrument' &&
        assignment.status === 'active' &&
        isRecordActive(assignment.effective_from, assignment.effective_to, referenceDate),
    )
    .sort(
      (left, right) =>
        (right.effective_from ?? '').localeCompare(left.effective_from ?? '') ||
        (right.effective_to ?? '').localeCompare(left.effective_to ?? '') ||
        right.assignment_id.localeCompare(left.assignment_id),
    )
    .forEach((assignment) => {
      if (assignmentByAssetId.has(assignment.target_entity_id)) {
        return
      }
      const leafNode = nodesById.get(assignment.taxonomy_node_id) ?? null
      const path = leafNode ? resolveNodePath(leafNode.taxonomy_node_id, nodesById) : []
      const topLevelNode = path[0] ?? leafNode
      assignmentByAssetId.set(assignment.target_entity_id, {
        topLevelId: topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`,
        topLevelLabel: topLevelNode?.node_name ?? 'Unassigned',
        leafId: leafNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`,
        leafLabel: leafNode?.node_name ?? 'Unassigned',
      })
    })

  return assignmentByAssetId
}

function normalizeHoldingsColumns(columns: HoldingsColumnKey[]) {
  const seen = new Set<HoldingsColumnKey>()
  const normalized: HoldingsColumnKey[] = [LOCKED_HOLDINGS_COLUMN]
  columns.forEach((column) => {
    if (column === LOCKED_HOLDINGS_COLUMN || seen.has(column) || !ALL_HOLDINGS_COLUMN_KEYS.includes(column)) {
      return
    }
    seen.add(column)
    normalized.push(column)
  })
  return normalized
}

function clampHoldingsColumnWidth(value: number) {
  return Math.min(Math.max(value, HOLDINGS_COLUMN_MIN_WIDTH), HOLDINGS_COLUMN_MAX_WIDTH)
}

function normalizeHoldingsColumnWidths(value: unknown) {
  const widths: Partial<Record<HoldingsColumnKey, number>> = {}
  if (!value || typeof value !== 'object') {
    return widths
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, rawWidth]) => {
    if (!ALL_HOLDINGS_COLUMN_KEYS.includes(key as HoldingsColumnKey) || typeof rawWidth !== 'number' || !Number.isFinite(rawWidth)) {
      return
    }
    widths[key as HoldingsColumnKey] = clampHoldingsColumnWidth(rawWidth)
  })
  return widths
}

function loadStoredHoldingsColumnWidths() {
  if (typeof window === 'undefined') {
    return {}
  }
  try {
    const rawValue = window.localStorage.getItem(HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY)
    return normalizeHoldingsColumnWidths(rawValue ? JSON.parse(rawValue) : null)
  } catch {
    return {}
  }
}

function normalizeHoldingsViewState(value: unknown): HoldingsViewState {
  if (!value || typeof value !== 'object') {
    return DEFAULT_HOLDINGS_VIEW_STATE
  }
  const record = value as Partial<HoldingsViewState>
  const sortField = parseHoldingsSortField(typeof record.sortField === 'string' ? record.sortField : null)
  return {
    columns: normalizeHoldingsColumns(Array.isArray(record.columns) ? (record.columns as HoldingsColumnKey[]) : DEFAULT_HOLDINGS_COLUMNS),
    columnWidths: normalizeHoldingsColumnWidths(record.columnWidths),
    groupBy: parseHoldingsGroupBy(typeof record.groupBy === 'string' ? record.groupBy : null),
    sortField,
    sortDirection: parseHoldingsSortDirection(typeof record.sortDirection === 'string' ? record.sortDirection : null, sortField),
  }
}

function serializeHoldingsViewState(value: HoldingsViewState) {
  const normalized = normalizeHoldingsViewState(value)
  const normalizedWidths = normalizeHoldingsColumnWidths(normalized.columnWidths)
  const columnWidths = ALL_HOLDINGS_COLUMN_KEYS.reduce<Partial<Record<HoldingsColumnKey, number>>>((result, column) => {
    const width = normalizedWidths[column]
    if (width != null) {
      result[column] = width
    }
    return result
  }, {})
  return JSON.stringify({
    columns: normalized.columns,
    columnWidths,
    groupBy: normalized.groupBy,
    sortField: normalized.sortField,
    sortDirection: normalized.sortDirection,
  })
}

function holdingsViewStatesEqual(left: HoldingsViewState, right: HoldingsViewState) {
  return serializeHoldingsViewState(left) === serializeHoldingsViewState(right)
}

function normalizeHoldingsViewStore(value: unknown): HoldingsViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<HoldingsViewStore>) : {}
  const customViews = Array.isArray(record.customViews)
    ? record.customViews
        .filter((view): view is HoldingsTableView => Boolean(view && typeof view === 'object' && typeof view.id === 'string'))
        .map((view) => ({
          id: view.id,
          name: typeof view.name === 'string' && view.name.trim() ? view.name.trim() : 'Custom View',
          description: typeof view.description === 'string' ? view.description : null,
          readonly: false,
          createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
          updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
          state: normalizeHoldingsViewState(view.state),
        }))
    : []
  const customViewIds = new Set(customViews.map((view) => view.id))
  const knownViewIds = new Set([...SYSTEM_HOLDINGS_VIEWS.map((view) => view.id), ...customViewIds])
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_HOLDINGS_VIEWS[0].id
  return { activeViewId, customViews }
}

function loadHoldingsViewStore(): HoldingsViewStore {
  if (typeof window === 'undefined') {
    return normalizeHoldingsViewStore(null)
  }
  try {
    const rawValue = window.localStorage.getItem(HOLDINGS_VIEWS_STORAGE_KEY)
    if (rawValue) {
      return normalizeHoldingsViewStore(JSON.parse(rawValue))
    }
  } catch {
    return normalizeHoldingsViewStore(null)
  }
  return normalizeHoldingsViewStore(null)
}

function saveHoldingsViewStore(store: HoldingsViewStore) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(HOLDINGS_VIEWS_STORAGE_KEY, JSON.stringify(store))
  } catch {
    return
  }
}

function getHoldingsViews(store: HoldingsViewStore) {
  return [...SYSTEM_HOLDINGS_VIEWS, ...store.customViews]
}

function getHoldingsViewById(store: HoldingsViewStore, viewId: string) {
  return getHoldingsViews(store).find((view) => view.id === viewId) ?? SYSTEM_HOLDINGS_VIEWS[0]
}

function resolveHoldingsViewState(store: HoldingsViewStore, viewId: string) {
  return getHoldingsViewById(store, viewId).state
}

function createHoldingsViewId() {
  return `custom:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`
}

function parseHoldingsSortField(value: string | null): HoldingsColumnKey | null {
  return value && ALL_HOLDINGS_COLUMN_KEYS.includes(value as HoldingsColumnKey)
    ? (value as HoldingsColumnKey)
    : null
}

function parseHoldingsSortDirection(value: string | null, field: HoldingsColumnKey | null): HoldingsSortDirection {
  if (value === 'asc' || value === 'desc') {
    return value
  }
  return 'asc'
}

function parseHoldingsGroupBy(value: string | null): HoldingsGroupByKey {
  return HOLDINGS_GROUP_BY_OPTIONS.some((option) => option.value === value) ? (value as HoldingsGroupByKey) : 'none'
}

function compareSortableValue(left: SortableValue, right: SortableValue, direction: HoldingsSortDirection) {
  const leftMissing = left == null || left === ''
  const rightMissing = right == null || right === ''
  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) {
      return 0
    }
    return leftMissing ? 1 : -1
  }

  if (typeof left === 'number' || typeof right === 'number') {
    const result = Number(left) - Number(right)
    return direction === 'asc' ? result : -result
  }

  const result = String(left).localeCompare(String(right), 'zh-Hans-CN')
  return direction === 'asc' ? result : -result
}

function csvEscape(value: string | number | null | undefined) {
  if (value == null) {
    return ''
  }
  const text = String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function holdingColumnExportValue(
  column: HoldingsColumnKey,
  row: PortfolioHoldingRow,
  context: HoldingsColumnContext,
): string | number | null {
  switch (column) {
    case 'asset':
      return row.asset_core.asset_name
    case 'ticker':
      return primaryIdentifier(row)
    case 'asset_type':
      return formatLabel(row.asset_core.asset_type)
    case 'taxonomy_top':
      return context.taxonomyByAssetId.get(row.asset_core.asset_id)?.topLevelLabel ?? 'Unassigned'
    case 'taxonomy_leaf':
      return context.taxonomyByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? 'Unassigned'
    case 'currency':
      return row.asset_core.currency
    case 'holding_date':
      return context.workspace.as_of_date
    case 'quantity':
      return row.quantity
    case 'cost_method':
      return costMethodLabel(row.cost_basis_method)
    case 'avg_cost_book':
      return bookAvgCost(row)
    case 'last_price':
      return row.last_price
    case 'quote_date':
      return quoteDate(row)
    case 'quote_basis':
      return row.quote_basis ?? null
    case 'quote_provider':
      return row.quote_provider ?? null
    case 'quote_status':
      return row.quote_status ?? null
    case 'market_value':
      return row.market_value
    case 'market_value_base':
      return row.market_value_base ?? row.market_value
    case 'cost_basis':
      return row.cost_basis
    case 'cost_basis_base':
      return row.cost_basis_base ?? row.cost_basis
    case 'weight':
      return row.allocation
    case 'accounts':
      return row.account_count ?? 0
    case 'open_lots':
      return row.open_position_lot_count ?? 0
    case 'day_change_value':
      return row.day_change_value
    case 'day_change_pct':
      return row.day_change_pct
    case 'unrealized_value':
      return unrealizedValue(row)
    case 'unrealized_pct':
      return unrealizedPct(row)
    case 'chart_return':
      return chartReturn(row)
    case 'chart_volatility':
      return chartVolatility(row)
    case 'chart_max_drawdown':
      return chartMaxDrawdown(row)
    case 'price_chart':
      return row.price_chart.map((point) => `${point.date}:${point.value}`).join(' | ')
    case 'coverage':
      return formatLabel(row.coverage_status)
    default:
      return null
  }
}

function holdingColumnTotalExportValue(
  column: HoldingsColumnKey,
  rows: PortfolioHoldingRow[],
  context: HoldingsColumnContext,
): string | number | null {
  switch (column) {
    case 'asset':
      return `Portfolio Total (${context.workspace.base_currency})`
    case 'holding_date':
      return context.workspace.as_of_date
    case 'market_value':
    case 'market_value_base':
      return totalMarketValueBase(rows, context.workspace)
    case 'cost_basis':
    case 'cost_basis_base':
      return totalCostBasisBase(rows, context.workspace)
    case 'weight':
      return totalAllocation(rows, context.workspace)
    case 'open_lots':
      return sumNumbers(rows, (row) => row.open_position_lot_count)
    case 'day_change_value':
      return totalDayChangeBase(rows, context.workspace)
    case 'day_change_pct':
      return totalDayChangePct(rows, context.workspace)
    case 'unrealized_value':
      return totalUnrealizedBase(rows)
    case 'unrealized_pct':
      return totalUnrealizedPct(rows)
    default:
      return null
  }
}

function MiniSparkline({ values }: { values: SparklinePoint[] }) {
  if (values.length < 2) {
    return <span className="sparkline-empty">—</span>
  }

  const width = 88
  const height = 24
  const min = Math.min(...values.map((point) => point.value))
  const max = Math.max(...values.map((point) => point.value))
  const span = max - min || 1
  const line = values
    .map((point, index) => {
      const x = (index / (values.length - 1)) * (width - 1)
      const y = height - ((point.value - min) / span) * (height - 6) - 2
      return `${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' L ')

  const area = `${line} L ${width - 1} ${height} L 0 ${height} Z`

  return (
    <svg className="mini-sparkline" viewBox="0 0 88 24" aria-hidden="true">
      <path d={`M ${line}`} fill="none" stroke="#ef4444" strokeWidth="1.8" />
      <path d={`M ${area}`} fill="rgba(239, 68, 68, 0.14)" />
    </svg>
  )
}

function TableStatusRow({
  colSpan,
  label,
  tone = 'neutral',
}: {
  colSpan: number
  label: string
  tone?: 'neutral' | 'error'
}) {
  return (
    <tr className="table-status-row">
      <td colSpan={colSpan} className={`empty-state-cell ${tone === 'error' ? 'table-status-cell-error' : ''}`}>
        {label}
      </td>
    </tr>
  )
}

const HOLDINGS_COLUMN_DEFINITIONS: Record<HoldingsColumnKey, HoldingsColumnDefinition> = {
  asset: {
    key: 'asset',
    label: 'Asset',
    render: (row) => (
      <div className="holding-name-stack">
        <span>{row.asset_core.asset_name}</span>
      </div>
    ),
    sortValue: (row) => row.asset_core.asset_name,
  },
  ticker: {
    key: 'ticker',
    label: 'Ticker',
    render: (row) => <span className="ticker-pill">{primaryIdentifier(row)}</span>,
    sortValue: (row) => primaryIdentifier(row),
  },
  asset_type: {
    key: 'asset_type',
    label: 'Asset Type',
    render: (row) => formatLabel(row.asset_core.asset_type),
    sortValue: (row) => row.asset_core.asset_type,
  },
  taxonomy_top: {
    key: 'taxonomy_top',
    label: 'Taxonomy',
    render: (row, context) => context.taxonomyByAssetId.get(row.asset_core.asset_id)?.topLevelLabel ?? 'Unassigned',
    sortValue: (row, context) => context.taxonomyByAssetId.get(row.asset_core.asset_id)?.topLevelLabel ?? 'Unassigned',
  },
  taxonomy_leaf: {
    key: 'taxonomy_leaf',
    label: 'Taxonomy Leaf',
    render: (row, context) => context.taxonomyByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? 'Unassigned',
    sortValue: (row, context) => context.taxonomyByAssetId.get(row.asset_core.asset_id)?.leafLabel ?? 'Unassigned',
  },
  currency: {
    key: 'currency',
    label: 'Currency',
    render: (row) => row.asset_core.currency,
    sortValue: (row) => row.asset_core.currency,
  },
  holding_date: {
    key: 'holding_date',
    label: 'Holding Date',
    render: (_row, context) => context.workspace.as_of_date,
    sortValue: (_row, context) => context.workspace.as_of_date,
    total: (_rows, context) => context.workspace.as_of_date,
  },
  quantity: {
    key: 'quantity',
    label: 'Quantity',
    align: 'right',
    render: (row) => formatQuantity(row.quantity),
    sortValue: (row) => row.quantity,
  },
  cost_method: {
    key: 'cost_method',
    label: 'Cost Method',
    render: (row) => costMethodLabel(row.cost_basis_method),
    sortValue: (row) => costMethodLabel(row.cost_basis_method),
  },
  avg_cost_book: {
    key: 'avg_cost_book',
    label: 'Avg Cost',
    align: 'right',
    render: (row) => formatUnitPrice(bookAvgCost(row), row.asset_core.currency),
    sortValue: (row) => bookAvgCost(row),
  },
  last_price: {
    key: 'last_price',
    label: 'Quote',
    align: 'right',
    render: (row) => formatUnitPrice(row.last_price, row.asset_core.currency),
    sortValue: (row) => row.last_price,
  },
  quote_date: {
    key: 'quote_date',
    label: 'Quote Date',
    render: (row) => quoteDate(row) ?? '—',
    sortValue: (row) => quoteDate(row),
  },
  quote_basis: {
    key: 'quote_basis',
    label: 'Quote Basis',
    render: (row) => (row.quote_basis ? formatLabel(row.quote_basis) : '—'),
    sortValue: (row) => row.quote_basis,
  },
  quote_provider: {
    key: 'quote_provider',
    label: 'Provider',
    render: (row) => row.quote_provider ?? '—',
    sortValue: (row) => row.quote_provider,
  },
  quote_status: {
    key: 'quote_status',
    label: 'Quote Status',
    render: (row) => (row.quote_status ? formatLabel(row.quote_status) : '—'),
    sortValue: (row) => row.quote_status,
  },
  market_value: {
    key: 'market_value',
    label: 'Market Value',
    align: 'right',
    render: (row) => formatCurrency(row.market_value, row.asset_core.currency),
    sortValue: (row) => row.market_value_base ?? row.market_value,
    total: (rows, context) => formatCurrency(totalMarketValueBase(rows, context.workspace), context.workspace.base_currency),
  },
  market_value_base: {
    key: 'market_value_base',
    label: 'Market Value Base',
    align: 'right',
    render: (row, context) => formatCurrency(row.market_value_base ?? row.market_value, context.workspace.base_currency),
    sortValue: (row) => row.market_value_base ?? row.market_value,
    total: (rows, context) => formatCurrency(totalMarketValueBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis: {
    key: 'cost_basis',
    label: 'Cost Basis',
    align: 'right',
    render: (row) => formatCurrency(row.cost_basis, row.asset_core.currency),
    sortValue: (row) => row.cost_basis_base ?? row.cost_basis,
    total: (rows, context) => formatCurrency(totalCostBasisBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis_base: {
    key: 'cost_basis_base',
    label: 'Cost Basis Base',
    align: 'right',
    render: (row, context) => formatCurrency(row.cost_basis_base ?? row.cost_basis, context.workspace.base_currency),
    sortValue: (row) => row.cost_basis_base ?? row.cost_basis,
    total: (rows, context) => formatCurrency(totalCostBasisBase(rows, context.workspace), context.workspace.base_currency),
  },
  weight: {
    key: 'weight',
    label: 'Weight',
    align: 'right',
    render: (row) => formatPercent(row.allocation),
    sortValue: (row) => row.allocation,
    total: (rows, context) => formatPercent(totalAllocation(rows, context.workspace)),
  },
  accounts: {
    key: 'accounts',
    label: 'Accounts',
    align: 'right',
    render: (row) => formatNumber(row.account_count ?? 0, 0),
    sortValue: (row) => row.account_count,
  },
  open_lots: {
    key: 'open_lots',
    label: 'Open Lots',
    align: 'right',
    render: (row) => formatNumber(row.open_position_lot_count ?? 0, 0),
    sortValue: (row) => row.open_position_lot_count,
    total: (rows) => formatNumber(sumNumbers(rows, (row) => row.open_position_lot_count), 0),
  },
  day_change_value: {
    key: 'day_change_value',
    label: 'Day Change',
    align: 'right',
    render: (row) => signedCurrency(row.day_change_value, row.asset_core.currency),
    sortValue: (row) => row.day_change_value,
    className: (row) => signedValueClass(row.day_change_value),
    total: (rows, context) => signedCurrency(totalDayChangeBase(rows, context.workspace), context.workspace.base_currency),
    totalClassName: (rows, context) => signedValueClass(totalDayChangeBase(rows, context.workspace)),
  },
  day_change_pct: {
    key: 'day_change_pct',
    label: 'Day Return',
    align: 'right',
    render: (row) => signedPercent(row.day_change_pct),
    sortValue: (row) => row.day_change_pct,
    className: (row) => signedValueClass(row.day_change_pct),
    total: (rows, context) => signedPercent(totalDayChangePct(rows, context.workspace)),
    totalClassName: (rows, context) => signedValueClass(totalDayChangePct(rows, context.workspace)),
  },
  unrealized_value: {
    key: 'unrealized_value',
    label: 'Unrealized P&L',
    align: 'right',
    render: (row) => signedCurrency(unrealizedValue(row), row.asset_core.currency),
    sortValue: (row) => unrealizedBaseValue(row),
    className: (row) => signedValueClass(unrealizedValue(row)),
    total: (rows, context) => signedCurrency(totalUnrealizedBase(rows), context.workspace.base_currency),
    totalClassName: (rows) => signedValueClass(totalUnrealizedBase(rows)),
  },
  unrealized_pct: {
    key: 'unrealized_pct',
    label: 'Unrealized Return',
    align: 'right',
    render: (row) => signedPercent(unrealizedPct(row)),
    sortValue: (row) => unrealizedPct(row),
    className: (row) => signedValueClass(unrealizedPct(row)),
    total: (rows) => signedPercent(totalUnrealizedPct(rows)),
    totalClassName: (rows) => signedValueClass(totalUnrealizedPct(rows)),
  },
  chart_return: {
    key: 'chart_return',
    label: 'Chart Return',
    align: 'right',
    render: (row) => signedPercent(chartReturn(row)),
    sortValue: (row) => chartReturn(row),
    className: (row) => signedValueClass(chartReturn(row)),
  },
  chart_volatility: {
    key: 'chart_volatility',
    label: 'Chart Vol',
    align: 'right',
    render: (row) => formatPercent(chartVolatility(row)),
    sortValue: (row) => chartVolatility(row),
  },
  chart_max_drawdown: {
    key: 'chart_max_drawdown',
    label: 'Chart Max DD',
    align: 'right',
    render: (row) => signedPercent(chartMaxDrawdown(row)),
    sortValue: (row) => chartMaxDrawdown(row),
    className: (row) => signedValueClass(chartMaxDrawdown(row)),
  },
  price_chart: {
    key: 'price_chart',
    label: 'Price Chart',
    render: (row) =>
      row.price_chart.length ? <MiniSparkline values={row.price_chart} /> : <span className="sparkline-empty">—</span>,
    sortValue: (row) => chartReturn(row),
  },
  coverage: {
    key: 'coverage',
    label: 'Coverage',
    render: (row) => (
      <span
        className={`coverage-pill ${
          row.coverage_status === 'price-nav-fx' ? 'coverage-pill-live' : 'coverage-pill-warning'
        }`}
      >
        {formatLabel(row.coverage_status)}
      </span>
    ),
    sortValue: (row) => row.coverage_status,
  },
}

function resolveGroupForRow(
  row: PortfolioHoldingRow,
  groupBy: HoldingsGroupByKey,
  taxonomyByAssetId: Map<string, HoldingTaxonomyLabels>,
) {
  if (groupBy === 'taxonomy_top') {
    const taxonomy = taxonomyByAssetId.get(row.asset_core.asset_id)
    return {
      key: taxonomy?.topLevelId ?? '__unassigned_taxonomy__',
      label: taxonomy?.topLevelLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'taxonomy_leaf') {
    const taxonomy = taxonomyByAssetId.get(row.asset_core.asset_id)
    return {
      key: taxonomy?.leafId ?? '__unassigned_taxonomy_leaf__',
      label: taxonomy?.leafLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'asset_type') {
    return { key: row.asset_core.asset_type, label: formatLabel(row.asset_core.asset_type) }
  }
  if (groupBy === 'currency') {
    return { key: row.asset_core.currency, label: row.asset_core.currency }
  }
  if (groupBy === 'coverage') {
    return { key: row.coverage_status, label: formatLabel(row.coverage_status) }
  }
  return { key: '__all__', label: 'All Holdings' }
}

function buildGroupedRows(
  rows: PortfolioHoldingRow[],
  groupBy: HoldingsGroupByKey,
  taxonomyByAssetId: Map<string, HoldingTaxonomyLabels>,
) {
  const groups = new Map<string, HoldingsGroup>()
  rows.forEach((row) => {
    const groupRef = resolveGroupForRow(row, groupBy, taxonomyByAssetId)
    const current = groups.get(groupRef.key) ?? {
      key: groupRef.key,
      label: groupRef.label,
      rows: [],
      marketValueBase: 0,
      weight: 0,
      openLots: 0,
    }
    current.rows.push(row)
    current.marketValueBase += finiteNumber(row.market_value_base ?? row.market_value) ?? 0
    current.weight += finiteNumber(row.allocation) ?? 0
    current.openLots += finiteNumber(row.open_position_lot_count) ?? 0
    groups.set(groupRef.key, current)
  })

  return [...groups.values()].sort(
    (left, right) =>
      right.marketValueBase - left.marketValueBase ||
      right.weight - left.weight ||
      left.label.localeCompare(right.label, 'zh-Hans-CN'),
  )
}

export default function PortfolioHomePage() {
  const navigate = useNavigate()
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [taxonomyError, setTaxonomyError] = useState<string | null>(null)
  const initialHoldingsViewStore = useMemo(() => loadHoldingsViewStore(), [])
  const initialHoldingsViewState = useMemo(
    () => resolveHoldingsViewState(initialHoldingsViewStore, initialHoldingsViewStore.activeViewId),
    [initialHoldingsViewStore],
  )
  const initialHoldingsUrlSortField = useMemo(
    () =>
      parseHoldingsSortField(
        searchParams.get('holdings_sort_field') ?? searchParams.get('holdings_sort')?.replace(/_(asc|desc)$/, '') ?? null,
      ),
    [searchParams],
  )
  const [holdingsViewStore, setHoldingsViewStore] = useState<HoldingsViewStore>(() => initialHoldingsViewStore)
  const [activeHoldingsViewId, setActiveHoldingsViewId] = useState(initialHoldingsViewStore.activeViewId)
  const [holdingsColumns, setHoldingsColumns] = useState<HoldingsColumnKey[]>(() => initialHoldingsViewState.columns)
  const [holdingsColumnWidths, setHoldingsColumnWidths] = useState<Partial<Record<HoldingsColumnKey, number>>>(() =>
    Object.keys(initialHoldingsViewState.columnWidths).length
      ? initialHoldingsViewState.columnWidths
      : loadStoredHoldingsColumnWidths(),
  )
  const [holdingsColumnDraft, setHoldingsColumnDraft] = useState<HoldingsColumnKey[]>(() => initialHoldingsViewState.columns)
  const [holdingsColumnsOpen, setHoldingsColumnsOpen] = useState(false)
  const [holdingsGroupByOpen, setHoldingsGroupByOpen] = useState(false)
  const [holdingsColumnCategory, setHoldingsColumnCategory] = useState(HOLDINGS_COLUMN_GROUPS[0]?.label ?? 'Core')
  const [holdingsColumnSearch, setHoldingsColumnSearch] = useState('')
  const [holdingsColumnDropTarget, setHoldingsColumnDropTarget] = useState<HoldingsColumnKey | null>(null)
  const [holdingsSortField, setHoldingsSortField] = useState<HoldingsColumnKey | null>(
    () => initialHoldingsUrlSortField ?? initialHoldingsViewState.sortField,
  )
  const [holdingsSortDirection, setHoldingsSortDirection] = useState<HoldingsSortDirection>(() =>
    initialHoldingsUrlSortField
      ? parseHoldingsSortDirection(
          searchParams.get('holdings_sort_direction') ?? searchParams.get('holdings_sort')?.match(/_(asc|desc)$/)?.[1] ?? null,
          initialHoldingsUrlSortField,
        )
      : initialHoldingsViewState.sortDirection,
  )
  const [holdingsGroupBy, setHoldingsGroupBy] = useState<HoldingsGroupByKey>(() => {
    const rawGroupBy = searchParams.get('holdings_group_by')
    return rawGroupBy ? parseHoldingsGroupBy(rawGroupBy) : initialHoldingsViewState.groupBy
  })
  const holdingsColumnResizeState = useRef<{
    column: HoldingsColumnKey
    startX: number
    startWidth: number
  } | null>(null)
  const holdingsColumnResizeFrame = useRef<number | null>(null)
  const pendingHoldingsColumnResize = useRef<{ column: HoldingsColumnKey; width: number } | null>(null)
  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedAssetId = searchParams.get('asset_id')
  const holdingsViews = useMemo(() => getHoldingsViews(holdingsViewStore), [holdingsViewStore])
  const activeHoldingsView = useMemo(
    () => getHoldingsViewById(holdingsViewStore, activeHoldingsViewId),
    [activeHoldingsViewId, holdingsViewStore],
  )
  const currentHoldingsViewState = useMemo<HoldingsViewState>(
    () => ({
      columns: normalizeHoldingsColumns(holdingsColumns),
      columnWidths: normalizeHoldingsColumnWidths(holdingsColumnWidths),
      groupBy: holdingsGroupBy,
      sortField: holdingsSortField,
      sortDirection: holdingsSortDirection,
    }),
    [holdingsColumnWidths, holdingsColumns, holdingsGroupBy, holdingsSortDirection, holdingsSortField],
  )
  const holdingsViewEdited = !holdingsViewStatesEqual(currentHoldingsViewState, activeHoldingsView.state)
  const selectedGroupByOption =
    HOLDINGS_GROUP_BY_OPTIONS.find((option) => option.value === holdingsGroupBy) ?? HOLDINGS_GROUP_BY_OPTIONS[0]
  const columnsEdited = JSON.stringify(normalizeHoldingsColumns(holdingsColumns)) !== JSON.stringify(activeHoldingsView.state.columns)

  const taxonomyByAssetId = useMemo(
    () => buildTaxonomyLabelsByAssetId(taxonomyCatalog, workspace?.as_of_date),
    [taxonomyCatalog, workspace?.as_of_date],
  )
  const columnContext = useMemo(
    () =>
      workspace
        ? {
            workspace,
            taxonomyByAssetId,
          }
        : null,
    [taxonomyByAssetId, workspace],
  )
  const visibleColumns = useMemo(
    () => normalizeHoldingsColumns(holdingsColumns).map((column) => HOLDINGS_COLUMN_DEFINITIONS[column]),
    [holdingsColumns],
  )
  const holdingsTableMinWidth = useMemo(
    () =>
      visibleColumns.reduce(
        (sum, column) => sum + (holdingsColumnWidths[column.key] ?? DEFAULT_HOLDINGS_COLUMN_WIDTHS[column.key]),
        0,
      ),
    [holdingsColumnWidths, visibleColumns],
  )
  const filteredHoldingsColumns = useMemo(() => {
    const searchQuery = holdingsColumnSearch.trim().toLocaleLowerCase()
    const selectedGroup =
      HOLDINGS_COLUMN_GROUPS.find((group) => group.label === holdingsColumnCategory) ??
      HOLDINGS_COLUMN_GROUPS[0] ?? { label: 'Core', columns: [] }
    const sourceGroups = searchQuery ? HOLDINGS_COLUMN_GROUPS : [selectedGroup]
    return sourceGroups.flatMap((group) =>
      group.columns
        .filter((column) => {
          if (!searchQuery) {
            return true
          }
          const definition = HOLDINGS_COLUMN_DEFINITIONS[column]
          return `${definition.label} ${column} ${group.label}`.toLocaleLowerCase().includes(searchQuery)
        })
        .map((column) => ({ column, groupLabel: group.label })),
    )
  }, [holdingsColumnCategory, holdingsColumnSearch])
  const sortedHoldingRows = useMemo(() => {
    const rows = workspace?.rows ?? []
    if (!columnContext || !holdingsSortField) {
      return rows.slice()
    }
    const sortColumn = HOLDINGS_COLUMN_DEFINITIONS[holdingsSortField]
    return rows.slice().sort((left, right) => {
      const primary = compareSortableValue(
        sortColumn.sortValue(left, columnContext),
        sortColumn.sortValue(right, columnContext),
        holdingsSortDirection,
      )
      return primary || primaryIdentifier(left).localeCompare(primaryIdentifier(right), 'zh-Hans-CN')
    })
  }, [columnContext, holdingsSortDirection, holdingsSortField, workspace?.rows])
  const groupedHoldingRows = useMemo(
    () => buildGroupedRows(sortedHoldingRows, holdingsGroupBy, taxonomyByAssetId),
    [holdingsGroupBy, sortedHoldingRows, taxonomyByAssetId],
  )

  function updateSearchParam(key: string, value: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedValue = value && value.trim() ? value : null
      const currentValue = current.get(key)
      if (normalizedValue === currentValue || (!normalizedValue && !currentValue)) {
        return current
      }
      if (normalizedValue) {
        next.set(key, normalizedValue)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  function applyHoldingsViewState(state: HoldingsViewState) {
    const normalized = normalizeHoldingsViewState(state)
    setHoldingsColumns(normalized.columns)
    setHoldingsColumnDraft(normalized.columns)
    setHoldingsColumnWidths(normalized.columnWidths)
    setHoldingsGroupBy(normalized.groupBy)
    setHoldingsSortField(normalized.sortField)
    setHoldingsSortDirection(normalized.sortDirection)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('holdings_sort')
      if (normalized.groupBy === 'none') {
        next.delete('holdings_group_by')
      } else {
        next.set('holdings_group_by', normalized.groupBy)
      }
      if (normalized.sortField) {
        next.set('holdings_sort_field', normalized.sortField)
        next.set('holdings_sort_direction', normalized.sortDirection)
      } else {
        next.delete('holdings_sort_field')
        next.delete('holdings_sort_direction')
      }
      return next
    })
  }

  function handleSelectHoldingsView(viewId: string) {
    const nextView = getHoldingsViewById(holdingsViewStore, viewId)
    setActiveHoldingsViewId(nextView.id)
    setHoldingsViewStore((current) => ({ ...current, activeViewId: nextView.id }))
    applyHoldingsViewState(resolveHoldingsViewState(holdingsViewStore, nextView.id))
  }

  function handleSaveHoldingsView() {
    if (activeHoldingsView.readonly) {
      return
    }
    const timestamp = new Date().toISOString()
    setHoldingsViewStore((current) => {
      return {
        ...current,
        activeViewId: activeHoldingsViewId,
        customViews: current.customViews.map((view) =>
          view.id === activeHoldingsViewId
            ? {
                ...view,
                state: currentHoldingsViewState,
                updatedAt: timestamp,
              }
            : view,
        ),
      }
    })
  }

  function handleSaveHoldingsViewAs(name: string, description: string | null) {
    const timestamp = new Date().toISOString()
    const viewId = createHoldingsViewId()
    const nextView: HoldingsTableView = {
      id: viewId,
      name,
      description,
      readonly: false,
      createdAt: timestamp,
      updatedAt: timestamp,
      state: currentHoldingsViewState,
    }
    setHoldingsViewStore((current) => {
      return {
        ...current,
        activeViewId: viewId,
        customViews: [...current.customViews, nextView],
      }
    })
    setActiveHoldingsViewId(viewId)
  }

  function handleSelectAsset(assetId: string | null) {
    const normalizedAssetId = assetId?.trim() || null
    if (!normalizedAssetId || !portfolioId) {
      updateSearchParam('asset_id', null)
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('asset_id')
    next.delete('position_lot_id')
    const query = next.toString()
    navigate(`${buildPortfolioHoldingDetailPath(portfolioId, normalizedAssetId)}${query ? `?${query}` : ''}`)
  }

  function handleHoldingsSort(field: HoldingsColumnKey) {
    let nextSortField: HoldingsColumnKey | null = field
    let nextSortDirection: HoldingsSortDirection = 'asc'
    if (holdingsSortField === field && holdingsSortDirection === 'asc') {
      nextSortDirection = 'desc'
    } else if (holdingsSortField === field && holdingsSortDirection === 'desc') {
      nextSortField = null
      nextSortDirection = 'asc'
    }
    setHoldingsSortField(nextSortField)
    setHoldingsSortDirection(nextSortDirection)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('holdings_sort')
      if (nextSortField) {
        next.set('holdings_sort_field', nextSortField)
        next.set('holdings_sort_direction', nextSortDirection)
      } else {
        next.delete('holdings_sort_field')
        next.delete('holdings_sort_direction')
      }
      return next
    })
  }

  function handleGroupByChange(value: HoldingsGroupByKey) {
    setHoldingsGroupBy(value)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (value === 'none') {
        next.delete('holdings_group_by')
      } else {
        next.set('holdings_group_by', value)
      }
      return next
    })
    setHoldingsGroupByOpen(false)
  }

  function handleHoldingsColumnDraftToggle(column: HoldingsColumnKey, checked: boolean) {
    setHoldingsColumnDraft((current) =>
      normalizeHoldingsColumns(checked ? [...current, column] : current.filter((item) => item !== column)),
    )
  }

  function moveHoldingsColumn(sourceColumn: HoldingsColumnKey, targetColumn: HoldingsColumnKey) {
    if (sourceColumn === LOCKED_HOLDINGS_COLUMN || sourceColumn === targetColumn) {
      return
    }
    setHoldingsColumns((current) => {
      const normalized = normalizeHoldingsColumns(current)
      const sourceIndex = normalized.indexOf(sourceColumn)
      const targetIndex = normalized.indexOf(targetColumn)
      if (sourceIndex < 0 || targetIndex < 0) {
        return normalized
      }
      const next = [...normalized]
      const [moved] = next.splice(sourceIndex, 1)
      let insertIndex = targetIndex === 0 ? 1 : targetIndex
      if (sourceIndex < targetIndex) {
        insertIndex = targetIndex
      }
      next.splice(insertIndex, 0, moved)
      return normalizeHoldingsColumns(next)
    })
  }

  function handleHoldingsColumnDragStart(event: DragEvent<HTMLTableCellElement>, column: HoldingsColumnKey) {
    if (column === LOCKED_HOLDINGS_COLUMN) {
      event.preventDefault()
      return
    }
    event.dataTransfer.setData('text/plain', column)
    event.dataTransfer.effectAllowed = 'move'
  }

  function handleHoldingsColumnDragOver(event: DragEvent<HTMLTableCellElement>, column: HoldingsColumnKey) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setHoldingsColumnDropTarget(column)
  }

  function handleHoldingsColumnDrop(event: DragEvent<HTMLTableCellElement>, targetColumn: HoldingsColumnKey) {
    event.preventDefault()
    const sourceColumn = event.dataTransfer.getData('text/plain') as HoldingsColumnKey
    if (ALL_HOLDINGS_COLUMN_KEYS.includes(sourceColumn)) {
      moveHoldingsColumn(sourceColumn, targetColumn)
    }
    setHoldingsColumnDropTarget(null)
  }

  function handleHoldingsColumnResizeStart(
    event: ReactMouseEvent<HTMLSpanElement>,
    column: HoldingsColumnKey,
    currentWidth: number,
  ) {
    event.preventDefault()
    event.stopPropagation()
    holdingsColumnResizeState.current = {
      column,
      startX: event.clientX,
      startWidth: currentWidth,
    }
    document.body.style.cursor = 'col-resize'
  }

  function handleDownloadCsv() {
    if (!workspace || !columnContext || !sortedHoldingRows.length) {
      return
    }

    const header = [
      ...(holdingsGroupBy !== 'none' ? ['Group'] : []),
      ...visibleColumns.map((column) => column.label),
    ]
    const rows: Array<Array<string | number | null>> = [header]

    groupedHoldingRows.forEach((group) => {
      group.rows.forEach((row) => {
        rows.push([
          ...(holdingsGroupBy !== 'none' ? [group.label] : []),
          ...visibleColumns.map((column) => holdingColumnExportValue(column.key, row, columnContext)),
        ])
      })
      if (holdingsGroupBy !== 'none') {
        rows.push([
          group.label,
          ...visibleColumns.map((column, index) =>
            index === 0 ? `Subtotal (${workspace.base_currency})` : holdingColumnTotalExportValue(column.key, group.rows, columnContext),
          ),
        ])
      }
    })

    rows.push([
      ...(holdingsGroupBy !== 'none' ? ['Portfolio Total'] : []),
      ...visibleColumns.map((column) => holdingColumnTotalExportValue(column.key, sortedHoldingRows, columnContext)),
    ])

    const csv = rows.map((row) => row.map(csvEscape).join(',')).join('\r\n')
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `holdings-${workspace.portfolio_id}-${workspace.as_of_date}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  }

  function renderHoldingsSortHeader(column: HoldingsColumnDefinition) {
    const active = holdingsSortField === column.key
    const nextSortLabel = !active ? 'ascending' : holdingsSortDirection === 'asc' ? 'descending' : 'no sorting'
    return (
      <button
        type="button"
        draggable={false}
        className={`holdings-th-label holdings-th-sortable ${active ? 'holdings-th-sortable-active' : ''}`}
        onClick={() => handleHoldingsSort(column.key)}
        title={`Sort ${column.label}: ${nextSortLabel}`}
        aria-label={`Sort ${column.label}: ${nextSortLabel}`}
      >
        <span>{column.label}</span>
        {active ? (
          <span className="holdings-sort-indicator">{holdingsSortDirection === 'asc' ? '↑' : '↓'}</span>
        ) : null}
      </button>
    )
  }

  function renderHoldingsTotalRow(rows: PortfolioHoldingRow[], label: string, className: string) {
    if (!columnContext) {
      return null
    }
    return (
      <tr className={className}>
        {visibleColumns.map((column, index) => {
          const classNames = [
            column.align === 'right' ? 'numeric-cell' : '',
            column.totalClassName?.(rows, columnContext) ?? '',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <td key={column.key} className={classNames || undefined}>
              {index === 0 ? <strong>{label}</strong> : column.total?.(rows, columnContext) ?? ''}
            </td>
          )
        })}
      </tr>
    )
  }

  function renderHoldingsGroupRow(group: HoldingsGroup) {
    if (!columnContext) {
      return null
    }
    return (
      <tr className="holdings-group-row">
        {visibleColumns.map((column, index) => {
          const classNames = [
            column.align === 'right' ? 'numeric-cell' : '',
            column.totalClassName?.(group.rows, columnContext) ?? '',
            index === 0 ? 'holdings-group-name-cell' : '',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <td key={column.key} className={classNames || undefined}>
              {index === 0 ? (
                <div className="holdings-group-header">
                  <span className="holdings-group-title">{group.label || 'Unassigned'}</span>
                  <span className="holdings-group-count">{formatNumber(group.rows.length, 0)}</span>
                </div>
              ) : (
                column.total?.(group.rows, columnContext) ?? ''
              )}
            </td>
          )
        })}
      </tr>
    )
  }

  useEffect(() => {
    saveHoldingsViewStore(holdingsViewStore)
  }, [holdingsViewStore])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    try {
      window.localStorage.setItem(HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY, JSON.stringify(holdingsColumnWidths))
    } catch {
      return
    }
  }, [holdingsColumnWidths])

  useEffect(() => {
    function handleResizeMove(event: MouseEvent) {
      if (!holdingsColumnResizeState.current) {
        return
      }
      const { column, startX, startWidth } = holdingsColumnResizeState.current
      const nextWidth = clampHoldingsColumnWidth(startWidth + event.clientX - startX)
      pendingHoldingsColumnResize.current = { column, width: nextWidth }
      if (holdingsColumnResizeFrame.current == null) {
        holdingsColumnResizeFrame.current = window.requestAnimationFrame(() => {
          if (pendingHoldingsColumnResize.current) {
            const { column: pendingColumn, width } = pendingHoldingsColumnResize.current
            setHoldingsColumnWidths((current) => ({ ...current, [pendingColumn]: width }))
          }
          pendingHoldingsColumnResize.current = null
          holdingsColumnResizeFrame.current = null
        })
      }
    }

    function handleResizeEnd() {
      holdingsColumnResizeState.current = null
      if (holdingsColumnResizeFrame.current != null) {
        window.cancelAnimationFrame(holdingsColumnResizeFrame.current)
        holdingsColumnResizeFrame.current = null
      }
      pendingHoldingsColumnResize.current = null
      document.body.style.cursor = ''
    }

    window.addEventListener('mousemove', handleResizeMove)
    window.addEventListener('mouseup', handleResizeEnd)
    return () => {
      window.removeEventListener('mousemove', handleResizeMove)
      window.removeEventListener('mouseup', handleResizeEnd)
    }
  }, [])

  useEffect(() => {
    if (!portfolioId) {
      setWorkspace(null)
      setTaxonomyCatalog(null)
      setError(null)
      setTaxonomyError(null)
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)

    Promise.allSettled([
      getHoldingsWorkspace(portfolioId || undefined, { as_of_date: requestedAsOfDate || undefined }),
      getPortfolioTaxonomyCatalog(portfolioId),
    ])
      .then(([holdingsResult, taxonomyResult]) => {
        if (cancelled) {
          return
        }

        if (holdingsResult.status === 'fulfilled') {
          setWorkspace(holdingsResult.value)
          setError(null)
        } else {
          setError(
            holdingsResult.reason instanceof Error
              ? holdingsResult.reason.message
              : 'Failed to load holdings workspace.',
          )
          setWorkspace(null)
        }

        if (taxonomyResult.status === 'fulfilled') {
          setTaxonomyCatalog(taxonomyResult.value)
          setTaxonomyError(null)
        } else {
          setTaxonomyCatalog(null)
          setTaxonomyError(
            taxonomyResult.reason instanceof Error
              ? taxonomyResult.reason.message
              : 'Failed to load taxonomy catalog.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, requestedAsOfDate])

  useEffect(() => {
    if (!workspace || !selectedAssetId) {
      return
    }

    if (workspace.rows.some((row) => row.asset_core.asset_id === selectedAssetId)) {
      return
    }

    setSearchParams((current) => {
      if (current.get('asset_id') !== selectedAssetId) {
        return current
      }
      const next = new URLSearchParams(current)
      next.delete('asset_id')
      return next
    })
  }, [workspace, selectedAssetId, setSearchParams])

  return (
    <PortfolioWorkspaceLayout activeSection="Holdings" toolbarLabel={workspace?.view_label ?? 'View: Holdings'}>
      <section className="portfolio-detail-surface holdings-surface">
        <div className="transaction-filter-bar holdings-filter-bar">
          <div className="transaction-filter-group holdings-filter-group">
            <label>
              <input
                className="transaction-filter-input"
                type="date"
                aria-label="As Of Date"
                value={requestedAsOfDate || workspace?.as_of_date || ''}
                onChange={(event) => updateSearchParam('as_of_date', event.target.value || null)}
              />
            </label>
          </div>
          <div className="transaction-filter-actions holdings-filter-actions">
            <PortfolioTableViewControls
              views={holdingsViews}
              activeViewId={activeHoldingsViewId}
              edited={holdingsViewEdited}
              canSave={!activeHoldingsView.readonly}
              onSelect={handleSelectHoldingsView}
              onSave={handleSaveHoldingsView}
              onSaveAs={handleSaveHoldingsViewAs}
            />
            <button
              type="button"
              className={`holdings-toolbar-button ${columnsEdited ? 'holdings-toolbar-button-active' : ''}`}
              onClick={() => {
                setHoldingsColumnDraft(holdingsColumns)
                setHoldingsColumnsOpen(true)
              }}
            >
              Data &amp; Columns
            </button>
            <button
              type="button"
              className="holdings-toolbar-button"
              onClick={() => setHoldingsGroupByOpen(true)}
            >
              Group By{'\u00A0: '}
              {selectedGroupByOption.label}
            </button>
            <button
              type="button"
              className="holdings-toolbar-button"
              onClick={handleDownloadCsv}
              disabled={!workspace || !sortedHoldingRows.length}
            >
              Download
            </button>
          </div>
        </div>
        {loading ? <CalculationStatus label={workspace ? 'Recalculating…' : 'Loading…'} /> : null}
        {error ? <div className="error-state">{error}</div> : null}
        {taxonomyError && holdingsGroupBy.startsWith('taxonomy') ? (
          <div className="inline-notice inline-notice-warning">{taxonomyError}</div>
        ) : null}
        {!loading && !error && workspace && columnContext ? (
          <div className="table-shell holdings-table-shell">
            <table className="holdings-table holdings-main-table" style={{ minWidth: `${holdingsTableMinWidth}px` }}>
              <colgroup>
                {visibleColumns.map((column) => (
                  <col
                    key={column.key}
                    style={{
                      width: `${holdingsColumnWidths[column.key] ?? DEFAULT_HOLDINGS_COLUMN_WIDTHS[column.key]}px`,
                    }}
                  />
                ))}
              </colgroup>
              <thead>
                <tr>
                  {visibleColumns.map((column) => {
                    const width = holdingsColumnWidths[column.key] ?? DEFAULT_HOLDINGS_COLUMN_WIDTHS[column.key]
                    return (
                      <th
                        key={column.key}
                        className={[
                          column.align === 'right' ? 'numeric-cell' : '',
                          column.key !== LOCKED_HOLDINGS_COLUMN ? 'holdings-column-draggable' : '',
                          holdingsColumnDropTarget === column.key ? 'holdings-column-drop-target' : '',
                        ]
                          .filter(Boolean)
                          .join(' ') || undefined}
                        scope="col"
                        aria-sort={
                          holdingsSortField === column.key
                            ? holdingsSortDirection === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                        }
                        draggable={column.key !== LOCKED_HOLDINGS_COLUMN}
                        style={{ width: `${width}px` }}
                        onDragStart={(event) => handleHoldingsColumnDragStart(event, column.key)}
                        onDragOver={(event) => handleHoldingsColumnDragOver(event, column.key)}
                        onDragLeave={() => setHoldingsColumnDropTarget(null)}
                        onDrop={(event) => handleHoldingsColumnDrop(event, column.key)}
                        onDragEnd={() => setHoldingsColumnDropTarget(null)}
                      >
                        {renderHoldingsSortHeader(column)}
                        <span
                          className="holdings-th-resizer"
                          onMouseDown={(event) => handleHoldingsColumnResizeStart(event, column.key, width)}
                        />
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {sortedHoldingRows.length ? (
                  groupedHoldingRows.map((group) => (
                    <Fragment key={group.key}>
                      {holdingsGroupBy !== 'none' ? renderHoldingsGroupRow(group) : null}
                      {group.rows.map((row) => {
                        const isActive = selectedAssetId === row.asset_core.asset_id
                        return (
                          <tr
                            key={row.line_id}
                            className={isActive ? 'holdings-row-active' : undefined}
                            tabIndex={0}
                            onClick={() => handleSelectAsset(row.asset_core.asset_id)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                handleSelectAsset(row.asset_core.asset_id)
                              }
                            }}
                          >
                            {visibleColumns.map((column) => {
                              const className = [
                                column.align === 'right' ? 'numeric-cell' : '',
                                column.className?.(row, columnContext) ?? '',
                                column.key === 'asset' ? 'holding-name-cell' : '',
                              ]
                                .filter(Boolean)
                                .join(' ')
                              return (
                                <td key={column.key} className={className || undefined}>
                                  {column.render(row, columnContext)}
                                </td>
                              )
                            })}
                          </tr>
                        )
                      })}
                    </Fragment>
                  ))
                ) : (
                  <TableStatusRow colSpan={visibleColumns.length} label="No holdings are available for this portfolio." />
                )}
                {sortedHoldingRows.length
                  ? renderHoldingsTotalRow(
                      sortedHoldingRows,
                      `Portfolio Total (${workspace.base_currency})`,
                      'total-row holdings-total-row',
                    )
                  : null}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {holdingsColumnsOpen ? (
        <div className="holdings-modal-backdrop" onClick={() => setHoldingsColumnsOpen(false)}>
          <div className="holdings-modal holdings-columns-modal" onClick={(event) => event.stopPropagation()}>
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Data &amp; Columns</div>
                <div className="section-heading">Manage Data And Columns</div>
              </div>
              <button type="button" onClick={() => setHoldingsColumnsOpen(false)}>
                Close
              </button>
            </div>

            <div className="holdings-modal-search">
              <input
                className="holdings-modal-search-input"
                placeholder="Search by field name or code"
                value={holdingsColumnSearch}
                onChange={(event) => setHoldingsColumnSearch(event.target.value)}
              />
            </div>

            <div className="holdings-modal-grid">
              <div className="holdings-modal-categories">
                {HOLDINGS_COLUMN_GROUPS.map((group) => (
                  <button
                    type="button"
                    className={
                      group.label === holdingsColumnCategory
                        ? 'holdings-category-item holdings-category-item-active'
                        : 'holdings-category-item'
                    }
                    key={group.label}
                    onClick={() => setHoldingsColumnCategory(group.label)}
                  >
                    {group.label}
                  </button>
                ))}
              </div>

              <div className="holdings-modal-fields">
                {filteredHoldingsColumns.length ? (
                  filteredHoldingsColumns.map(({ column, groupLabel }) => {
                    const locked = column === LOCKED_HOLDINGS_COLUMN
                    return (
                      <label className="holdings-field-item" key={`${groupLabel}:${column}`}>
                        <input
                          type="checkbox"
                          checked={holdingsColumnDraft.includes(column)}
                          disabled={locked}
                          onChange={(event) => handleHoldingsColumnDraftToggle(column, event.target.checked)}
                        />
                        <div>
                          <div className="holdings-field-label">{HOLDINGS_COLUMN_DEFINITIONS[column].label}</div>
                          <div className="holdings-field-meta">
                            {column}
                            {holdingsColumnSearch.trim() ? ` · ${groupLabel}` : ''}
                            {locked ? ' · required' : ''}
                          </div>
                        </div>
                      </label>
                    )
                  })
                ) : (
                  <div className="holdings-field-empty">No fields matched the current search.</div>
                )}
              </div>

            </div>

            <div className="holdings-modal-actions holdings-modal-actions-sticky">
              <button
                type="button"
                onClick={() => {
                  setHoldingsColumnDraft(holdingsColumns)
                  setHoldingsColumnsOpen(false)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  setHoldingsColumns(normalizeHoldingsColumns(holdingsColumnDraft))
                  setHoldingsColumnsOpen(false)
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {holdingsGroupByOpen ? (
        <div className="holdings-modal-backdrop" onClick={() => setHoldingsGroupByOpen(false)}>
          <div className="holdings-modal holdings-compact-modal" onClick={(event) => event.stopPropagation()}>
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Group By</div>
                <div className="section-heading">Choose Grouping Dimension</div>
              </div>
              <button type="button" onClick={() => setHoldingsGroupByOpen(false)}>
                Close
              </button>
            </div>
            <div className="holdings-modal-body holdings-groupby-list">
              {HOLDINGS_GROUP_BY_OPTIONS.map((option) => (
                <button
                  type="button"
                  className={`holdings-groupby-option ${option.value === holdingsGroupBy ? 'holdings-groupby-option-active' : ''}`}
                  key={option.value}
                  onClick={() => handleGroupByChange(option.value)}
                >
                  <span>{option.label}</span>
                  <small>{option.description}</small>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

    </PortfolioWorkspaceLayout>
  )
}
