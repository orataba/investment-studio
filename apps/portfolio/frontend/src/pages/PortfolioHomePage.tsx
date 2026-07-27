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
import { useNavigate, useParams, useSearchParams } from 'react-router'

import CalculationStatus from '../components/CalculationStatus'
import HoldingsTotalRow, { isPortfolioReturnColumn } from '../components/HoldingsTotalRow'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
import { downloadTable, type TableCell, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
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
  getPortfolioTableViewStore,
  getPortfolioTaxonomyCatalog,
  savePortfolioTableViewStore,
  type HoldingReturnSeries,
  type HoldingsWorkspaceResponse,
  type PortfolioCalculationFrequency,
  type PortfolioHoldingRow,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
} from '../lib/api'
import { baseAmountForRow, normalizedCurrency } from '../lib/holdingAmounts'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'

type HoldingsColumnKey =
  | 'instrument'
  | 'ticker'
  | 'instrument_type'
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
  | 'instrument_return_1w'
  | 'instrument_return_mtd'
  | 'instrument_return_ytd'
  | 'instrument_return_1y'
  | 'instrument_current_drawdown'
  | 'instrument_max_drawdown'
  | 'instrument_holding_max_drawdown'
  | 'instrument_volatility_1m'
  | 'instrument_volatility_3m'
  | 'instrument_volatility_6m'
  | 'instrument_volatility_1y'
  | 'forward_risk_share'
  | 'price_chart_1m'
  | 'price_chart_3m'
  | 'price_chart_6m'
  | 'price_chart_1y'
  | 'coverage'

type HoldingsGroupByKey = 'none' | 'taxonomy_top' | 'taxonomy_leaf' | 'instrument_type' | 'currency' | 'coverage'
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
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>
}

type HoldingsColumnDefinition = {
  key: HoldingsColumnKey
  label: string
  align?: 'right' | 'center'
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

type HoldingsDisplayItem =
  | { kind: 'holding'; row: PortfolioHoldingRow }
  | { kind: 'noncash-total' }

type GroupVolatilityRangeKey = '1m' | '3m' | '6m' | '1y'
type GroupVolatilitySeries = {
  dates: string[]
  returns: number[]
  firstReturnStartDate: string | null
}

const DAYS_PER_YEAR = 365.25
const GROUP_METRIC_MIN_VALUE_COVERAGE = 0.8
const GROUP_VOL_WINDOW_DAYS: Record<GroupVolatilityRangeKey, number> = {
  '1m': 31,
  '3m': 92,
  '6m': 183,
  '1y': 366,
}
const GROUP_VOL_MIN_WINDOW_COVERAGE_RATIO = 0.8
const GROUP_VOL_MIN_RETURN_OBSERVATIONS: Record<PortfolioCalculationFrequency, Record<GroupVolatilityRangeKey, number>> = {
  daily: {
    '1m': 10,
    '3m': 30,
    '6m': 60,
    '1y': 120,
  },
  weekly: {
    '1m': 3,
    '3m': 6,
    '6m': 12,
    '1y': 24,
  },
  monthly: {
    '1m': 2,
    '3m': 2,
    '6m': 4,
    '1y': 6,
  },
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
  views: HoldingsTableView[]
}

const LOCKED_HOLDINGS_COLUMN: HoldingsColumnKey = 'instrument'
const HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY = 'portfolio_ops.portfolio.holdings.columnWidths.v4'
const HOLDINGS_COLUMN_MIN_WIDTH = 84
const HOLDINGS_COLUMN_MAX_WIDTH = 520

const HOLDINGS_COLUMN_GROUPS: Array<{ label: string; columns: HoldingsColumnKey[] }> = [
  {
    label: 'Identity',
    columns: ['instrument', 'ticker', 'instrument_type', 'taxonomy_top', 'taxonomy_leaf', 'currency', 'coverage'],
  },
  {
    label: 'Quote',
    columns: ['last_price', 'quote_date', 'quote_basis', 'quote_provider', 'quote_status'],
  },
  {
    label: 'Instrument Trend',
    columns: [
      'price_chart_1m',
      'price_chart_3m',
      'price_chart_6m',
      'price_chart_1y',
      'instrument_return_1w',
      'instrument_return_mtd',
      'instrument_return_ytd',
      'instrument_return_1y',
    ],
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
    columns: ['day_change_pct', 'day_change_value', 'unrealized_value', 'unrealized_pct'],
  },
  {
    label: 'Risk',
    columns: [
      'instrument_volatility_1m',
      'instrument_volatility_3m',
      'instrument_volatility_6m',
      'instrument_volatility_1y',
      'forward_risk_share',
      'instrument_current_drawdown',
      'instrument_max_drawdown',
      'instrument_holding_max_drawdown',
    ],
  },
]

const ALL_HOLDINGS_COLUMN_KEYS = HOLDINGS_COLUMN_GROUPS.flatMap((group) => group.columns)

const DEFAULT_HOLDINGS_COLUMNS: HoldingsColumnKey[] = [
  'instrument',
  'last_price',
  'quote_date',
  'price_chart_6m',
  'quantity',
  'avg_cost_book',
  'cost_basis',
  'market_value',
  'weight',
  'forward_risk_share',
  'unrealized_value',
  'unrealized_pct',
]

const DEFAULT_HOLDINGS_COLUMN_WIDTHS: Record<HoldingsColumnKey, number> = {
  instrument: 320,
  ticker: 120,
  instrument_type: 116,
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
  instrument_return_1w: 128,
  instrument_return_mtd: 112,
  instrument_return_ytd: 112,
  instrument_return_1y: 112,
  instrument_current_drawdown: 120,
  instrument_max_drawdown: 124,
  instrument_holding_max_drawdown: 136,
  instrument_volatility_1m: 112,
  instrument_volatility_3m: 112,
  instrument_volatility_6m: 112,
  instrument_volatility_1y: 112,
  forward_risk_share: 128,
  price_chart_1m: 132,
  price_chart_3m: 132,
  price_chart_6m: 132,
  price_chart_1y: 132,
  coverage: 132,
}

const COMPACT_HOLDINGS_COLUMN_MIN_WIDTHS: Partial<Record<HoldingsColumnKey, number>> = {
  instrument: 180,
  ticker: 92,
  instrument_type: 96,
  taxonomy_top: 112,
  taxonomy_leaf: 124,
  currency: 76,
  holding_date: 104,
  quantity: 96,
  cost_method: 104,
  avg_cost_book: 104,
  last_price: 92,
  quote_date: 104,
  quote_basis: 104,
  quote_provider: 104,
  quote_status: 104,
  market_value: 108,
  market_value_base: 116,
  cost_basis: 108,
  cost_basis_base: 116,
  weight: 84,
  accounts: 84,
  open_lots: 88,
  day_change_value: 104,
  day_change_pct: 92,
  unrealized_value: 108,
  unrealized_pct: 104,
  instrument_return_1w: 92,
  instrument_return_mtd: 92,
  instrument_return_ytd: 92,
  instrument_return_1y: 92,
  instrument_current_drawdown: 100,
  instrument_max_drawdown: 100,
  instrument_holding_max_drawdown: 112,
  instrument_volatility_1m: 92,
  instrument_volatility_3m: 92,
  instrument_volatility_6m: 92,
  instrument_volatility_1y: 92,
  forward_risk_share: 104,
  price_chart_1m: 104,
  price_chart_3m: 104,
  price_chart_6m: 104,
  price_chart_1y: 104,
  coverage: 104,
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
    readonly: true,
    state: DEFAULT_HOLDINGS_VIEW_STATE,
  },
  {
    id: 'return-risk',
    name: 'Return & Risk',
    readonly: true,
    state: {
      columns: [
        'instrument',
        'instrument_type',
        'market_value_base',
        'weight',
        'price_chart_6m',
        'instrument_return_1w',
        'instrument_return_mtd',
        'instrument_return_ytd',
        'day_change_pct',
        'day_change_value',
        'unrealized_value',
        'unrealized_pct',
        'instrument_volatility_1m',
        'instrument_volatility_3m',
        'instrument_volatility_6m',
        'instrument_volatility_1y',
        'forward_risk_share',
        'instrument_current_drawdown',
        'instrument_max_drawdown',
        'instrument_holding_max_drawdown',
      ],
      columnWidths: {},
      groupBy: 'none',
      sortField: 'unrealized_pct',
      sortDirection: 'desc',
    },
  },
]
const SYSTEM_HOLDINGS_VIEW_IDS = new Set(SYSTEM_HOLDINGS_VIEWS.map((view) => view.id))
const RETIRED_SYSTEM_HOLDINGS_VIEW_IDS = new Set(['taxonomy', 'open-lots', 'accounting', 'instrument-trend'])

function isActiveStoredHoldingsView(view: HoldingsTableView | null): view is HoldingsTableView {
  return view !== null && !RETIRED_SYSTEM_HOLDINGS_VIEW_IDS.has(view.id)
}

const TEXT_HOLDINGS_SORT_FIELDS = new Set<HoldingsColumnKey>([
  'instrument',
  'ticker',
  'instrument_type',
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
}> = [
  { value: 'none', label: 'None' },
  { value: 'taxonomy_top', label: 'Taxonomy' },
  { value: 'taxonomy_leaf', label: 'Taxonomy Leaf' },
  { value: 'instrument_type', label: 'Instrument Type' },
  { value: 'currency', label: 'Currency' },
  { value: 'coverage', label: 'Coverage' },
]

function primaryIdentifier(row: PortfolioHoldingRow) {
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function instrumentTrendCoverageLabel(row: PortfolioHoldingRow) {
  const basis = row.instrument_trend_basis ? formatLabel(row.instrument_trend_basis) : 'Unavailable basis'
  const coverage = row.instrument_trend_coverage
  const state = formatLabel(coverage?.state ?? 'unavailable')
  const observationCount = coverage?.observation_count ?? 0
  return `Trend: ${basis} · ${state} · ${observationCount} observation${observationCount === 1 ? '' : 's'}`
}

function instrumentTrendReasonLabel(row: PortfolioHoldingRow) {
  const basis = row.instrument_trend_basis ? formatLabel(row.instrument_trend_basis) : 'trend basis'
  switch (row.instrument_trend_reason) {
    case 'selected_more_complete_alternate_series':
      return `Using ${basis} because it provides more complete history than the preferred basis.`
    case 'selected_split_adjusted_raw_price_series':
      return `Raw ${basis} history was adjusted across confirmed split ratios.`
    case 'selected_series_has_single_observation':
      return 'Trend history has only one observation.'
    case 'raw_price_split_evidence_unconfirmed':
      return 'Trend withheld because split evidence is not confirmed.'
    case 'raw_price_split_ratio_invalid':
      return 'Trend withheld because the confirmed split ratio is invalid.'
    case 'raw_price_split_fraction_treatment_insufficient':
      return 'Trend withheld because split fraction treatment is insufficient.'
    case 'raw_price_split_effective_date_invalid':
      return 'Trend withheld because the split effective date is invalid.'
    case 'selected_policy_series':
      return 'Policy-preferred trend basis selected.'
    case 'quote_policy_unavailable':
    case 'quote_series_unavailable':
      return 'Trend unavailable because no eligible quote series was found.'
    default:
      return row.instrument_trend_reason
        ? `Trend status: ${formatLabel(row.instrument_trend_reason)}.`
        : 'Trend selection reason unavailable.'
  }
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

function sumCompleteNumbers(rows: PortfolioHoldingRow[], accessor: (row: PortfolioHoldingRow) => number | null | undefined) {
  if (!rows.length) {
    return null
  }
  let total = 0
  for (const row of rows) {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return null
    }
    total += value
  }
  return total
}

function isCashHoldingRow(row: PortfolioHoldingRow) {
  return (
    row.instrument_core.instrument_type === 'cash' ||
    row.instrument_core.instrument_id.toLowerCase().startsWith('cash:') ||
    row.line_id.toLowerCase().startsWith('cash:')
  )
}

function isBaseCashHoldingRow(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return isCashHoldingRow(row) && normalizedCurrency(row.instrument_core.currency) === normalizedCurrency(workspace.base_currency)
}

function nonCashHoldingRows(rows: PortfolioHoldingRow[]) {
  return rows.filter((row) => !isCashHoldingRow(row))
}

function compareCashHoldingRows(left: PortfolioHoldingRow, right: PortfolioHoldingRow) {
  return (
    normalizedCurrency(left.instrument_core.currency).localeCompare(
      normalizedCurrency(right.instrument_core.currency),
      'zh-Hans-CN',
    ) ||
    left.instrument_core.instrument_name.localeCompare(right.instrument_core.instrument_name, 'zh-Hans-CN') ||
    left.line_id.localeCompare(right.line_id, 'zh-Hans-CN')
  )
}

function dayChangeBaseForRow(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return baseAmountForRow(row, workspace.base_currency, row.day_change_value_base, row.day_change_value)
}

function dayChangeDisplayValue(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  if (isCashHoldingRow(row)) {
    return {
      value: dayChangeBaseForRow(row, workspace),
      currency: workspace.base_currency,
    }
  }
  return { value: row.day_change_value, currency: row.instrument_core.currency }
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
  return row.price_chart_6m[row.price_chart_6m.length - 1]?.date ?? null
}

function chartPointsForColumn(row: PortfolioHoldingRow, column: HoldingsColumnKey) {
  switch (column) {
    case 'price_chart_1m':
      return row.price_chart_1m
    case 'price_chart_3m':
      return row.price_chart_3m
    case 'price_chart_6m':
      return row.price_chart_6m
    case 'price_chart_1y':
      return row.price_chart_1y
    default:
      return []
  }
}

function isHoldingsChartColumn(column: HoldingsColumnKey) {
  return column.startsWith('price_chart_')
}

function holdingsAlignmentClass(column: HoldingsColumnDefinition) {
  if (column.align === 'right') {
    return 'numeric-cell'
  }
  if (column.align === 'center') {
    return 'center-cell'
  }
  return ''
}

function chartReturnForColumn(row: PortfolioHoldingRow, column: HoldingsColumnKey) {
  const points = chartPointsForColumn(row, column)
  if (points.length < 2) {
    return null
  }
  const firstPoint = points.find((point) => Number.isFinite(point.value) && point.value !== 0)
  const lastPoint = points[points.length - 1]
  if (!firstPoint || !lastPoint || !Number.isFinite(lastPoint.value)) {
    return null
  }
  return (lastPoint.value - firstPoint.value) / Math.abs(firstPoint.value)
}

function unrealizedValue(row: PortfolioHoldingRow) {
  const marketValue = finiteNumber(row.market_value)
  const costBasis = finiteNumber(row.cost_basis)
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

function unrealizedBaseValueForWorkspace(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  const marketValue = baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value)
  const costBasis = baseAmountForRow(row, workspace.base_currency, row.cost_basis_base, row.cost_basis)
  return marketValue == null || costBasis == null ? null : marketValue - costBasis
}

function totalUnrealizedBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const nonCashRows = nonCashHoldingRows(rows)
  const marketValue = sumCompleteNumbers(nonCashRows, (row) =>
    baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value),
  )
  const costBasis = sumCompleteNumbers(nonCashRows, (row) =>
    baseAmountForRow(row, workspace.base_currency, row.cost_basis_base, row.cost_basis),
  )
  return marketValue == null || costBasis == null ? null : marketValue - costBasis
}

function totalUnrealizedPct(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const nonCashRows = nonCashHoldingRows(rows)
  const costBasis = sumCompleteNumbers(nonCashRows, (row) =>
    baseAmountForRow(row, workspace.base_currency, row.cost_basis_base, row.cost_basis),
  )
  const unrealized = totalUnrealizedBase(rows, workspace)
  if (costBasis == null || unrealized == null) {
    return null
  }
  const cashRows = rows.filter((row) => isCashHoldingRow(row))
  const cashBasis = cashRows.length ? sumCompleteNumbers(cashRows, (row) => rowMarketValueBase(row, workspace)) : 0
  if (cashBasis == null) {
    return null
  }
  const denominator = costBasis + cashBasis
  if (Math.abs(denominator) <= 1e-12) {
    return null
  }
  return unrealized / Math.abs(denominator)
}

function totalMarketValueBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(rows, (row) =>
    baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value),
  )
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.market_value ?? rowTotal : rowTotal
}

function totalCostBasisBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(nonCashHoldingRows(rows), (row) =>
    baseAmountForRow(row, workspace.base_currency, row.cost_basis_base, row.cost_basis),
  )
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.cost_basis ?? rowTotal : rowTotal
}

function totalAllocation(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(rows, (row) => row.allocation)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.allocation ?? rowTotal : rowTotal
}

function totalDayChangeBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(rows, (row) => dayChangeBaseForRow(row, workspace))
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.day_change_value ?? rowTotal : rowTotal
}

function totalDayChangePct(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const explicit = rowsCoverWorkspace(rows, workspace) ? finiteNumber(workspace.totals.day_change_pct) : null
  if (explicit != null) {
    return explicit
  }
  const dayChange = totalDayChangeBase(rows, workspace)
  const marketValue = totalMarketValueBase(rows, workspace)
  const priorMarketValue = marketValue != null && dayChange != null ? marketValue - dayChange : null
  return dayChange == null || priorMarketValue == null || Math.abs(priorMarketValue) <= 1e-12
    ? null
    : dayChange / priorMarketValue
}

function rowMarketValueBase(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value)
}

function weightedHoldingMetric(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  accessor: (row: PortfolioHoldingRow) => number | null | undefined,
) {
  const totalAbsValue = rows.reduce((sum, row) => sum + Math.abs(rowMarketValueBase(row, workspace) ?? 0), 0)
  if (totalAbsValue <= 1e-12) {
    return null
  }
  let eligibleAbsValue = 0
  let weightedTotal = 0
  let denominator = 0
  for (const row of rows) {
    const value = rowMarketValueBase(row, workspace)
    const rawMetric = finiteNumber(accessor(row))
    const metric = rawMetric ?? (isBaseCashHoldingRow(row, workspace) ? 0 : null)
    if (value == null || metric == null) {
      continue
    }
    eligibleAbsValue += Math.abs(value)
    weightedTotal += value * metric
    denominator += value
  }
  if (eligibleAbsValue / totalAbsValue < GROUP_METRIC_MIN_VALUE_COVERAGE || Math.abs(denominator) <= 1e-12) {
    return null
  }
  return weightedTotal / denominator
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
  return Math.sqrt(Math.max(0, variance))
}

function returnSeriesForVolatilityRange(row: PortfolioHoldingRow, rangeKey: GroupVolatilityRangeKey) {
  switch (rangeKey) {
    case '1m':
      return row.instrument_return_series_1m
    case '3m':
      return row.instrument_return_series_3m
    case '6m':
      return row.instrument_return_series_6m
    case '1y':
      return row.instrument_return_series_1y
    default:
      return null
  }
}

function normalizedReturnSeries(series: HoldingReturnSeries | null | undefined) {
  const points = Array.isArray(series?.points) ? series.points : []
  return points
    .map((point) => ({
      startDate: point.start_date || null,
      date: point.date,
      value: finiteNumber(point.value),
    }))
    .filter(
      (point): point is { startDate: string | null; date: string; value: number } =>
        Boolean(point.date) && point.value != null,
    )
    .sort((left, right) => `${left.startDate || ''}|${left.date}`.localeCompare(`${right.startDate || ''}|${right.date}`))
}

function calculationFrequencyForRows(rows: PortfolioHoldingRow[]): PortfolioCalculationFrequency {
  const frequency = rows.find((row) => row.instrument_risk_frequency)?.instrument_risk_frequency
  return frequency === 'weekly' || frequency === 'monthly' ? frequency : 'daily'
}

function groupedReturnSeries(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  seriesAccessor: (row: PortfolioHoldingRow) => HoldingReturnSeries | null | undefined,
): GroupVolatilitySeries | null {
  const totalAbsValue = rows.reduce((sum, row) => sum + Math.abs(rowMarketValueBase(row, workspace) ?? 0), 0)
  if (totalAbsValue <= 1e-12) {
    return null
  }

  const valuedRows = rows
    .map((row) => ({
      row,
      value: rowMarketValueBase(row, workspace),
      points: normalizedReturnSeries(seriesAccessor(row)),
    }))
    .filter((item) => item.value != null)
  const returnRows = valuedRows.filter((item) => item.points.length >= 2)
  const zeroReturnRows = valuedRows.filter((item) => item.points.length < 2 && isBaseCashHoldingRow(item.row, workspace))
  const eligibleRows = [...returnRows, ...zeroReturnRows]

  const eligibleAbsValue = eligibleRows.reduce((sum, item) => sum + Math.abs(item.value ?? 0), 0)
  const denominator = eligibleRows.reduce((sum, item) => sum + (item.value ?? 0), 0)
  if (
    eligibleAbsValue / totalAbsValue < GROUP_METRIC_MIN_VALUE_COVERAGE ||
    Math.abs(denominator) <= 1e-12
  ) {
    return null
  }
  if (!returnRows.length) {
    return zeroReturnRows.length ? { dates: [], returns: [], firstReturnStartDate: null } : null
  }

  const pointMaps = eligibleRows.map((item) => ({
    weight: (item.value ?? 0) / denominator,
    zeroReturn: item.points.length < 2 && isBaseCashHoldingRow(item.row, workspace),
    byPeriod: new Map(item.points.map((point) => [`${point.startDate || ''}|${point.date}`, point.value])),
  }))
  const periodCounts = new Map<string, number>()
  for (const item of returnRows) {
    for (const point of item.points) {
      const periodKey = `${point.startDate || ''}|${point.date}`
      periodCounts.set(periodKey, (periodCounts.get(periodKey) ?? 0) + 1)
    }
  }
  const periodKeys = [...periodCounts.entries()]
    .filter(([, count]) => count === returnRows.length)
    .map(([periodKey]) => periodKey)
    .sort()
  const returns: number[] = []
  const returnDates: string[] = []

  for (const periodKey of periodKeys) {
    const [, dateKey] = periodKey.split('|', 2)
    if (!dateKey) {
      continue
    }
    const portfolioReturn = pointMaps.reduce((sum, item) => {
      if (item.zeroReturn) {
        return sum
      }
      const itemReturn = item.byPeriod.get(periodKey)
      if (itemReturn == null) {
        return sum
      }
      return sum + item.weight * itemReturn
    }, 0)
    if (Number.isFinite(portfolioReturn)) {
      returns.push(portfolioReturn)
      returnDates.push(dateKey)
    }
  }

  const firstReturnDate = returnDates[0]
  const firstReturnStartDate =
    periodKeys.find((periodKey) => periodKey.endsWith(`|${firstReturnDate}`))?.split('|', 2)[0] || null

  return { dates: returnDates, returns, firstReturnStartDate }
}

function allValuedRowsAreBaseCash(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const valuedRows = rows.filter((row) => {
    const value = rowMarketValueBase(row, workspace)
    return value != null && Math.abs(value) > 1e-12
  })
  return valuedRows.length > 0 && valuedRows.every((row) => isBaseCashHoldingRow(row, workspace))
}

function groupedVolatilitySeries(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  rangeKey: GroupVolatilityRangeKey,
) {
  return groupedReturnSeries(rows, workspace, (row) => returnSeriesForVolatilityRange(row, rangeKey))
}

function groupedAnnualizedVolatility(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  rangeKey: GroupVolatilityRangeKey,
) {
  const series = groupedVolatilitySeries(rows, workspace, rangeKey)
  const calculationFrequency = calculationFrequencyForRows(rows)
  if (
    !series ||
    series.returns.length < GROUP_VOL_MIN_RETURN_OBSERVATIONS[calculationFrequency][rangeKey] ||
    series.firstReturnStartDate == null
  ) {
    if (allValuedRowsAreBaseCash(rows, workspace)) {
      return 0
    }
    return null
  }
  const lastDate = series.dates[series.dates.length - 1]
  if (!lastDate) {
    return null
  }
  const elapsedDays = dayDiff(series.firstReturnStartDate, lastDate)
  if (elapsedDays == null || elapsedDays <= 0) {
    return null
  }
  if (elapsedDays < Math.floor(GROUP_VOL_WINDOW_DAYS[rangeKey] * GROUP_VOL_MIN_WINDOW_COVERAGE_RATIO)) {
    return null
  }
  const stddev = sampleStddev(series.returns)
  return stddev == null ? null : stddev * Math.sqrt((series.returns.length / elapsedDays) * DAYS_PER_YEAR)
}

function drawdownFromReturns(returns: number[]) {
  if (!returns.length) {
    return null
  }
  let value = 1
  let peak = 1
  let maxDrawdown = 0
  for (const periodReturn of returns) {
    value *= 1 + periodReturn
    peak = Math.max(peak, value)
    if (peak > 1e-12) {
      maxDrawdown = Math.min(maxDrawdown, value / peak - 1)
    }
  }
  return {
    currentDrawdown: peak > 1e-12 ? value / peak - 1 : null,
    maxDrawdown,
  }
}

function groupedDrawdownSeries(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse, scope: 'all' | 'holding') {
  return groupedReturnSeries(rows, workspace, (row) =>
    scope === 'holding' ? row.instrument_holding_return_series : row.instrument_return_series_all,
  )
}

function groupedCurrentDrawdown(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const series = groupedDrawdownSeries(rows, workspace, 'all')
  if ((!series || !series.returns.length) && allValuedRowsAreBaseCash(rows, workspace)) {
    return 0
  }
  const drawdown = series ? drawdownFromReturns(series.returns) : null
  return drawdown?.currentDrawdown ?? null
}

function groupedMaxDrawdown(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse, scope: 'all' | 'holding') {
  const series = groupedDrawdownSeries(rows, workspace, scope)
  if ((!series || !series.returns.length) && allValuedRowsAreBaseCash(rows, workspace)) {
    return 0
  }
  const drawdown = series ? drawdownFromReturns(series.returns) : null
  return drawdown?.maxDrawdown ?? null
}

function rowsCoverWorkspace(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  if (rows.length !== workspace.rows.length) {
    return false
  }
  const rowIds = new Set(rows.map((row) => row.line_id))
  return workspace.rows.every((row) => rowIds.has(row.line_id))
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

const CASH_BUCKET_TAXONOMY_FALLBACK_KEY = '__cash_bucket__'

function cashBucketTaxonomyKey(accountId: string) {
  return `cash_bucket:${accountId}`
}

function labelsForTaxonomyAssignment(
  assignment: PortfolioTaxonomyAssignmentRecord,
  nodesById: Map<string, PortfolioTaxonomyNodeRecord>,
  taxonomyId: string,
): HoldingTaxonomyLabels {
  const leafNode = nodesById.get(assignment.taxonomy_node_id) ?? null
  const path = leafNode ? resolveNodePath(leafNode.taxonomy_node_id, nodesById) : []
  const topLevelNode = path[0] ?? leafNode
  return {
    topLevelId: topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`,
    topLevelLabel: topLevelNode?.node_name ?? 'Unassigned',
    leafId: leafNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`,
    leafLabel: leafNode?.node_name ?? 'Unassigned',
  }
}

function buildTaxonomyLabelsByInstrumentId(
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

  const assignmentByInstrumentId = new Map<string, HoldingTaxonomyLabels>()
  const cashBucketLabels: HoldingTaxonomyLabels[] = []
  ;[...catalog.taxonomy_assignments]
    .filter(
      (assignment) =>
        assignment.taxonomy_id === taxonomy.taxonomy_id &&
        (assignment.target_scope === 'instrument' || assignment.target_scope === 'cash_bucket') &&
        assignment.status === 'active',
    )
    .sort(
      (left, right) =>
        right.assignment_id.localeCompare(left.assignment_id),
    )
    .forEach((assignment) => {
      const key =
        assignment.target_scope === 'cash_bucket'
          ? cashBucketTaxonomyKey(assignment.target_entity_id)
          : assignment.target_entity_id
      if (assignmentByInstrumentId.has(key)) {
        return
      }
      const labels = labelsForTaxonomyAssignment(assignment, nodesById, taxonomy.taxonomy_id)
      assignmentByInstrumentId.set(key, labels)
      if (assignment.target_scope === 'cash_bucket') {
        cashBucketLabels.push(labels)
      }
    })

  const cashTopLevelLabels = new Map(cashBucketLabels.map((labels) => [labels.topLevelId, labels]))
  const cashLeafLabels = new Map(cashBucketLabels.map((labels) => [labels.leafId, labels]))
  if (cashLeafLabels.size === 1) {
    assignmentByInstrumentId.set(CASH_BUCKET_TAXONOMY_FALLBACK_KEY, cashBucketLabels[0])
  } else if (cashTopLevelLabels.size === 1) {
    const labels = cashBucketLabels[0]
    assignmentByInstrumentId.set(CASH_BUCKET_TAXONOMY_FALLBACK_KEY, {
      topLevelId: labels.topLevelId,
      topLevelLabel: labels.topLevelLabel,
      leafId: CASH_BUCKET_TAXONOMY_FALLBACK_KEY,
      leafLabel: labels.topLevelLabel,
    })
  }

  return assignmentByInstrumentId
}

function fallbackCashTaxonomyLabels(): HoldingTaxonomyLabels {
  return {
    topLevelId: '__cash__',
    topLevelLabel: '现金',
    leafId: '__cash__',
    leafLabel: '现金',
  }
}

function taxonomyLabelsForHoldingRow(
  row: PortfolioHoldingRow,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
) {
  if (!isCashHoldingRow(row)) {
    return taxonomyByInstrumentId.get(row.instrument_core.instrument_id) ?? null
  }

  const accountLabels = (row.account_ids ?? [])
    .map((accountId) => taxonomyByInstrumentId.get(cashBucketTaxonomyKey(accountId)))
    .filter((labels): labels is HoldingTaxonomyLabels => labels != null)
  const leafLabels = new Map(accountLabels.map((labels) => [labels.leafId, labels]))
  if (leafLabels.size === 1) {
    return accountLabels[0]
  }
  const topLevelLabels = new Map(accountLabels.map((labels) => [labels.topLevelId, labels]))
  if (topLevelLabels.size === 1) {
    const labels = accountLabels[0]
    return {
      topLevelId: labels.topLevelId,
      topLevelLabel: labels.topLevelLabel,
      leafId: CASH_BUCKET_TAXONOMY_FALLBACK_KEY,
      leafLabel: labels.topLevelLabel,
    }
  }

  return taxonomyByInstrumentId.get(CASH_BUCKET_TAXONOMY_FALLBACK_KEY) ?? fallbackCashTaxonomyLabels()
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

function compactTableColumnWidths<T extends string>(
  columns: T[],
  getRequestedWidth: (column: T) => number,
  getMinimumWidth: (column: T) => number,
  availableWidth: number,
) {
  const specs = columns.map((column) => {
    const minWidth = getMinimumWidth(column)
    const requestedWidth = Math.max(getRequestedWidth(column), minWidth)
    return { column, minWidth, requestedWidth }
  })
  const requestedWidth = specs.reduce((total, spec) => total + spec.requestedWidth, 0)
  const minimumWidth = specs.reduce((total, spec) => total + spec.minWidth, 0)
  const targetWidth =
    availableWidth > 0 && requestedWidth > availableWidth
      ? Math.max(minimumWidth, availableWidth)
      : requestedWidth
  const widths = {} as Record<T, number>

  if (targetWidth >= requestedWidth || requestedWidth <= minimumWidth) {
    specs.forEach((spec) => {
      widths[spec.column] = Math.round(spec.requestedWidth)
    })
  } else {
    const flexibleWidth = requestedWidth - minimumWidth
    specs.forEach((spec) => {
      const share = (spec.requestedWidth - spec.minWidth) / flexibleWidth
      widths[spec.column] = Math.round(spec.minWidth + (targetWidth - minimumWidth) * share)
    })
  }

  return {
    widths,
    totalWidth: columns.reduce((total, column) => total + widths[column], 0),
  }
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

function normalizeHoldingsTableView(value: unknown, readonly: boolean): HoldingsTableView | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const view = value as Partial<HoldingsTableView>
  if (typeof view.id !== 'string' || !view.id.trim()) {
    return null
  }
  return {
    id: view.id,
    name: typeof view.name === 'string' && view.name.trim() ? view.name.trim() : 'Custom View',
    description: typeof view.description === 'string' ? view.description : null,
    readonly,
    createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
    updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
    state: normalizeHoldingsViewState(view.state),
  }
}

function normalizeHoldingsViewStore(value: unknown): HoldingsViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<HoldingsViewStore>) : {}
  const storedViews = Array.isArray(record.views)
    ? record.views
        .map((view) => normalizeHoldingsTableView(view, SYSTEM_HOLDINGS_VIEW_IDS.has((view as Partial<HoldingsTableView>)?.id || '')))
        .filter(isActiveStoredHoldingsView)
    : null
  const storedViewById = new Map((storedViews || []).map((view) => [view.id, view]))
  const systemViews = SYSTEM_HOLDINGS_VIEWS.map((defaultView) => {
    const storedView = storedViewById.get(defaultView.id)
    return storedView ? { ...storedView, readonly: true } : defaultView
  })
  const customViews = (storedViews || [])
    .filter((view) => !SYSTEM_HOLDINGS_VIEW_IDS.has(view.id))
    .map((view) => ({ ...view, readonly: false }))
  const views = [...systemViews, ...customViews]
  const knownViewIds = new Set(views.map((view) => view.id))
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_HOLDINGS_VIEWS[0].id
  return { activeViewId, views }
}

function getHoldingsViews(store: HoldingsViewStore) {
  return store.views
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

function holdingColumnExportValue(
  column: HoldingsColumnKey,
  row: PortfolioHoldingRow,
  context: HoldingsColumnContext,
): string | number | null {
  switch (column) {
    case 'instrument':
      return row.instrument_core.instrument_name
    case 'ticker':
      return primaryIdentifier(row)
    case 'instrument_type':
      return formatLabel(row.instrument_core.instrument_type)
    case 'taxonomy_top':
      return taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.topLevelLabel ?? 'Unassigned'
    case 'taxonomy_leaf':
      return taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.leafLabel ?? 'Unassigned'
    case 'currency':
      return row.instrument_core.currency
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
      return baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value)
    case 'cost_basis':
      return row.cost_basis
    case 'cost_basis_base':
      return baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis)
    case 'weight':
      return row.allocation
    case 'accounts':
      return row.account_count ?? 0
    case 'open_lots':
      return row.open_position_lot_count ?? 0
    case 'day_change_value':
      return isCashHoldingRow(row) ? dayChangeBaseForRow(row, context.workspace) : row.day_change_value
    case 'day_change_pct':
      return row.day_change_pct
    case 'unrealized_value':
      return unrealizedValue(row)
    case 'unrealized_pct':
      return unrealizedPct(row)
    case 'instrument_return_1w':
      return row.instrument_return_1w ?? null
    case 'instrument_return_mtd':
      return row.instrument_return_mtd ?? null
    case 'instrument_return_ytd':
      return row.instrument_return_ytd ?? null
    case 'instrument_return_1y':
      return row.instrument_return_1y ?? null
    case 'instrument_current_drawdown':
      return row.instrument_current_drawdown ?? null
    case 'instrument_max_drawdown':
      return row.instrument_max_drawdown ?? null
    case 'instrument_holding_max_drawdown':
      return row.instrument_holding_max_drawdown ?? null
    case 'instrument_volatility_1m':
      return row.instrument_volatility_1m ?? null
    case 'instrument_volatility_3m':
      return row.instrument_volatility_3m ?? null
    case 'instrument_volatility_6m':
      return row.instrument_volatility_6m ?? null
    case 'instrument_volatility_1y':
      return row.instrument_volatility_1y ?? null
    case 'forward_risk_share':
      return row.forward_risk_share ?? null
    case 'price_chart_1m':
    case 'price_chart_3m':
    case 'price_chart_6m':
    case 'price_chart_1y':
      return chartPointsForColumn(row, column).map((point) => `${point.date}:${point.value}`).join(' | ')
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
  allowBlendedInstrumentReturn = true,
): string | number | null {
  switch (column) {
    case 'instrument':
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
      return totalUnrealizedBase(rows, context.workspace)
    case 'unrealized_pct':
      return totalUnrealizedPct(rows, context.workspace)
    case 'instrument_return_1w':
      return allowBlendedInstrumentReturn
        ? weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)
        : null
    case 'instrument_return_mtd':
      return allowBlendedInstrumentReturn
        ? weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)
        : null
    case 'instrument_return_ytd':
      return allowBlendedInstrumentReturn
        ? weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)
        : null
    case 'instrument_return_1y':
      return allowBlendedInstrumentReturn
        ? weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)
        : null
    case 'instrument_current_drawdown':
      return groupedCurrentDrawdown(rows, context.workspace)
    case 'instrument_volatility_1m':
      return groupedAnnualizedVolatility(rows, context.workspace, '1m')
    case 'instrument_volatility_3m':
      return groupedAnnualizedVolatility(rows, context.workspace, '3m')
    case 'instrument_volatility_6m':
      return groupedAnnualizedVolatility(rows, context.workspace, '6m')
    case 'instrument_volatility_1y':
      return groupedAnnualizedVolatility(rows, context.workspace, '1y')
    case 'forward_risk_share':
      return sumNumbers(rows, (row) => row.forward_risk_share)
    case 'instrument_max_drawdown':
      return groupedMaxDrawdown(rows, context.workspace, 'all')
    case 'instrument_holding_max_drawdown':
      return groupedMaxDrawdown(rows, context.workspace, 'holding')
    default:
      return null
  }
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
  instrument: {
    key: 'instrument',
    label: 'Instrument',
    render: (row) => (
      <div className="holding-name-stack">
        <span>{row.instrument_core.instrument_name}</span>
        <span className="holding-secondary holding-trend-summary">{instrumentTrendCoverageLabel(row)}</span>
        <span
          className="holding-secondary holding-trend-reason"
          title={instrumentTrendReasonLabel(row)}
        >
          {instrumentTrendReasonLabel(row)}
        </span>
      </div>
    ),
    sortValue: (row) => row.instrument_core.instrument_name,
  },
  ticker: {
    key: 'ticker',
    label: 'Ticker',
    align: 'center',
    render: (row) => <span className="ticker-pill">{primaryIdentifier(row)}</span>,
    sortValue: (row) => primaryIdentifier(row),
  },
  instrument_type: {
    key: 'instrument_type',
    label: 'Instrument Type',
    align: 'center',
    render: (row) => formatLabel(row.instrument_core.instrument_type),
    sortValue: (row) => row.instrument_core.instrument_type,
  },
  taxonomy_top: {
    key: 'taxonomy_top',
    label: 'Taxonomy',
    render: (row, context) => taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.topLevelLabel ?? 'Unassigned',
    sortValue: (row, context) => taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.topLevelLabel ?? 'Unassigned',
  },
  taxonomy_leaf: {
    key: 'taxonomy_leaf',
    label: 'Taxonomy Leaf',
    render: (row, context) => taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.leafLabel ?? 'Unassigned',
    sortValue: (row, context) => taxonomyLabelsForHoldingRow(row, context.taxonomyByInstrumentId)?.leafLabel ?? 'Unassigned',
  },
  currency: {
    key: 'currency',
    label: 'Currency',
    align: 'center',
    render: (row) => row.instrument_core.currency,
    sortValue: (row) => row.instrument_core.currency,
  },
  holding_date: {
    key: 'holding_date',
    label: 'Holding Date',
    align: 'center',
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
    align: 'center',
    render: (row) => costMethodLabel(row.cost_basis_method),
    sortValue: (row) => costMethodLabel(row.cost_basis_method),
  },
  avg_cost_book: {
    key: 'avg_cost_book',
    label: 'Avg Cost',
    align: 'right',
    render: (row) => formatUnitPrice(bookAvgCost(row), row.instrument_core.currency),
    sortValue: (row) => bookAvgCost(row),
  },
  last_price: {
    key: 'last_price',
    label: 'Quote',
    align: 'right',
    render: (row) => formatUnitPrice(row.last_price, row.instrument_core.currency),
    sortValue: (row) => row.last_price,
  },
  quote_date: {
    key: 'quote_date',
    label: 'Quote Date',
    align: 'center',
    render: (row) => quoteDate(row) ?? '—',
    sortValue: (row) => quoteDate(row),
  },
  quote_basis: {
    key: 'quote_basis',
    label: 'Quote Basis',
    align: 'center',
    render: (row) => (row.quote_basis ? formatLabel(row.quote_basis) : '—'),
    sortValue: (row) => row.quote_basis,
  },
  quote_provider: {
    key: 'quote_provider',
    label: 'Provider',
    align: 'center',
    render: (row) => row.quote_provider ?? '—',
    sortValue: (row) => row.quote_provider,
  },
  quote_status: {
    key: 'quote_status',
    label: 'Quote Status',
    align: 'center',
    render: (row) => (row.quote_status ? formatLabel(row.quote_status) : '—'),
    sortValue: (row) => row.quote_status,
  },
  market_value: {
    key: 'market_value',
    label: 'Market Value',
    align: 'right',
    render: (row) => formatCurrency(row.market_value, row.instrument_core.currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value),
    total: (rows, context) => formatCurrency(totalMarketValueBase(rows, context.workspace), context.workspace.base_currency),
  },
  market_value_base: {
    key: 'market_value_base',
    label: 'Market Value Base',
    align: 'right',
    render: (row, context) =>
      formatCurrency(baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value), context.workspace.base_currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value),
    total: (rows, context) => formatCurrency(totalMarketValueBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis: {
    key: 'cost_basis',
    label: 'Cost Basis',
    align: 'right',
    render: (row) => formatCurrency(row.cost_basis, row.instrument_core.currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis),
    total: (rows, context) => formatCurrency(totalCostBasisBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis_base: {
    key: 'cost_basis_base',
    label: 'Cost Basis Base',
    align: 'right',
    render: (row, context) =>
      formatCurrency(baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis), context.workspace.base_currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis),
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
    render: (row, context) => {
      const displayValue = dayChangeDisplayValue(row, context.workspace)
      return signedCurrency(displayValue.value, displayValue.currency)
    },
    sortValue: (row, context) => dayChangeBaseForRow(row, context.workspace),
    className: (row, context) => signedValueClass(dayChangeDisplayValue(row, context.workspace).value),
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
    render: (row) => signedCurrency(unrealizedValue(row), row.instrument_core.currency),
    sortValue: (row, context) => unrealizedBaseValueForWorkspace(row, context.workspace),
    className: (row) => signedValueClass(unrealizedValue(row)),
    total: (rows, context) => signedCurrency(totalUnrealizedBase(rows, context.workspace), context.workspace.base_currency),
    totalClassName: (rows, context) => signedValueClass(totalUnrealizedBase(rows, context.workspace)),
  },
  unrealized_pct: {
    key: 'unrealized_pct',
    label: 'Unrealized Return',
    align: 'right',
    render: (row) => signedPercent(unrealizedPct(row)),
    sortValue: (row) => unrealizedPct(row),
    className: (row) => signedValueClass(unrealizedPct(row)),
    total: (rows, context) => signedPercent(totalUnrealizedPct(rows, context.workspace)),
    totalClassName: (rows, context) => signedValueClass(totalUnrealizedPct(rows, context.workspace)),
  },
  instrument_return_1w: {
    key: 'instrument_return_1w',
    label: '1W Return',
    align: 'right',
    render: (row) => signedPercent(row.instrument_return_1w),
    sortValue: (row) => row.instrument_return_1w,
    className: (row) => signedValueClass(row.instrument_return_1w),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)),
  },
  instrument_return_mtd: {
    key: 'instrument_return_mtd',
    label: 'MTD',
    align: 'right',
    render: (row) => signedPercent(row.instrument_return_mtd),
    sortValue: (row) => row.instrument_return_mtd,
    className: (row) => signedValueClass(row.instrument_return_mtd),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)),
  },
  instrument_return_ytd: {
    key: 'instrument_return_ytd',
    label: 'YTD',
    align: 'right',
    render: (row) => signedPercent(row.instrument_return_ytd),
    sortValue: (row) => row.instrument_return_ytd,
    className: (row) => signedValueClass(row.instrument_return_ytd),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)),
  },
  instrument_return_1y: {
    key: 'instrument_return_1y',
    label: '1Y',
    align: 'right',
    render: (row) => signedPercent(row.instrument_return_1y),
    sortValue: (row) => row.instrument_return_1y,
    className: (row) => signedValueClass(row.instrument_return_1y),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)),
  },
  instrument_current_drawdown: {
    key: 'instrument_current_drawdown',
    label: 'Current DD',
    align: 'right',
    render: (row) => signedPercent(row.instrument_current_drawdown),
    sortValue: (row) => row.instrument_current_drawdown,
    className: (row) => signedValueClass(row.instrument_current_drawdown),
    total: (rows, context) => signedPercent(groupedCurrentDrawdown(rows, context.workspace)),
    totalClassName: (rows, context) => signedValueClass(groupedCurrentDrawdown(rows, context.workspace)),
  },
  instrument_volatility_1m: {
    key: 'instrument_volatility_1m',
    label: '1M Vol',
    align: 'right',
    render: (row) => formatPercent(row.instrument_volatility_1m),
    sortValue: (row) => row.instrument_volatility_1m,
    total: (rows, context) => formatPercent(groupedAnnualizedVolatility(rows, context.workspace, '1m')),
  },
  instrument_volatility_3m: {
    key: 'instrument_volatility_3m',
    label: '3M Vol',
    align: 'right',
    render: (row) => formatPercent(row.instrument_volatility_3m),
    sortValue: (row) => row.instrument_volatility_3m,
    total: (rows, context) => formatPercent(groupedAnnualizedVolatility(rows, context.workspace, '3m')),
  },
  instrument_volatility_6m: {
    key: 'instrument_volatility_6m',
    label: '6M Vol',
    align: 'right',
    render: (row) => formatPercent(row.instrument_volatility_6m),
    sortValue: (row) => row.instrument_volatility_6m,
    total: (rows, context) => formatPercent(groupedAnnualizedVolatility(rows, context.workspace, '6m')),
  },
  instrument_volatility_1y: {
    key: 'instrument_volatility_1y',
    label: '1Y Vol',
    align: 'right',
    render: (row) => formatPercent(row.instrument_volatility_1y),
    sortValue: (row) => row.instrument_volatility_1y,
    total: (rows, context) => formatPercent(groupedAnnualizedVolatility(rows, context.workspace, '1y')),
  },
  forward_risk_share: {
    key: 'forward_risk_share',
    label: 'Forward RC',
    align: 'right',
    render: (row) => (row.forward_risk_status === 'ok' || row.forward_risk_status === 'cash' ? signedPercent(row.forward_risk_share) : '—'),
    sortValue: (row) => row.forward_risk_share,
    className: (row) => signedValueClass(row.forward_risk_share),
    total: (rows, context) =>
      context.workspace.forward_risk?.status === 'ok' ? signedPercent(sumNumbers(rows, (row) => row.forward_risk_share)) : '—',
    totalClassName: (rows, context) =>
      context.workspace.forward_risk?.status === 'ok' ? signedValueClass(sumNumbers(rows, (row) => row.forward_risk_share)) : '',
  },
  instrument_max_drawdown: {
    key: 'instrument_max_drawdown',
    label: 'Max DD',
    align: 'right',
    render: (row) => signedPercent(row.instrument_max_drawdown),
    sortValue: (row) => row.instrument_max_drawdown,
    className: (row) => signedValueClass(row.instrument_max_drawdown),
    total: (rows, context) => signedPercent(groupedMaxDrawdown(rows, context.workspace, 'all')),
    totalClassName: (rows, context) => signedValueClass(groupedMaxDrawdown(rows, context.workspace, 'all')),
  },
  instrument_holding_max_drawdown: {
    key: 'instrument_holding_max_drawdown',
    label: 'Held Max DD',
    align: 'right',
    render: (row) => signedPercent(row.instrument_holding_max_drawdown),
    sortValue: (row) => row.instrument_holding_max_drawdown,
    className: (row) => signedValueClass(row.instrument_holding_max_drawdown),
    total: (rows, context) => signedPercent(groupedMaxDrawdown(rows, context.workspace, 'holding')),
    totalClassName: (rows, context) => signedValueClass(groupedMaxDrawdown(rows, context.workspace, 'holding')),
  },
  price_chart_1m: {
    key: 'price_chart_1m',
    label: 'Chart 1M',
    render: (row) => <Sparkline values={row.price_chart_1m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_1m'),
  },
  price_chart_3m: {
    key: 'price_chart_3m',
    label: 'Chart 3M',
    render: (row) => <Sparkline values={row.price_chart_3m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_3m'),
  },
  price_chart_6m: {
    key: 'price_chart_6m',
    label: 'Chart 6M',
    render: (row) => <Sparkline values={row.price_chart_6m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_6m'),
  },
  price_chart_1y: {
    key: 'price_chart_1y',
    label: 'Chart 1Y',
    render: (row) => <Sparkline values={row.price_chart_1y} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_1y'),
  },
  coverage: {
    key: 'coverage',
    label: 'Coverage',
    align: 'center',
    render: (row) => (
      <span
        className={`coverage-pill ${
          row.coverage_status === 'price-nav-fx' || row.coverage_status === 'cash'
            ? 'coverage-pill-live'
            : 'coverage-pill-warning'
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
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
) {
  if (groupBy === 'taxonomy_top') {
    const taxonomy = taxonomyLabelsForHoldingRow(row, taxonomyByInstrumentId)
    return {
      key: taxonomy?.topLevelId ?? '__unassigned_taxonomy__',
      label: taxonomy?.topLevelLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'taxonomy_leaf') {
    const taxonomy = taxonomyLabelsForHoldingRow(row, taxonomyByInstrumentId)
    return {
      key: taxonomy?.leafId ?? '__unassigned_taxonomy_leaf__',
      label: taxonomy?.leafLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'instrument_type') {
    return { key: row.instrument_core.instrument_type, label: formatLabel(row.instrument_core.instrument_type) }
  }
  if (groupBy === 'currency') {
    return { key: row.instrument_core.currency, label: row.instrument_core.currency }
  }
  if (groupBy === 'coverage') {
    return { key: row.coverage_status, label: formatLabel(row.coverage_status) }
  }
  return { key: '__all__', label: 'All Holdings' }
}

function buildGroupedRows(
  rows: PortfolioHoldingRow[],
  groupBy: HoldingsGroupByKey,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
  workspace: HoldingsWorkspaceResponse | null,
) {
  const groups = new Map<string, HoldingsGroup>()
  rows.forEach((row) => {
    const groupRef = resolveGroupForRow(row, groupBy, taxonomyByInstrumentId)
    const current = groups.get(groupRef.key) ?? {
      key: groupRef.key,
      label: groupRef.label,
      rows: [],
      marketValueBase: 0,
      weight: 0,
      openLots: 0,
    }
    current.rows.push(row)
    current.marketValueBase += workspace ? (rowMarketValueBase(row, workspace) ?? 0) : 0
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
  const [workspaceResponse, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [taxonomyCatalogResponse, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const workspace = workspaceResponse?.portfolio_id === portfolioId ? workspaceResponse : null
  const taxonomyCatalog =
    taxonomyCatalogResponse?.portfolio_id === portfolioId ? taxonomyCatalogResponse : null
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [taxonomyError, setTaxonomyError] = useState<string | null>(null)
  const [riskPolicyRevision, setRiskPolicyRevision] = useState(0)
  const initialHoldingsViewStore = useMemo(() => normalizeHoldingsViewStore(null), [])
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
  const [holdingsViewStoreReadyPortfolioId, setHoldingsViewStoreReadyPortfolioId] =
    useState<string | null>(null)
  const [holdingsViewStoreError, setHoldingsViewStoreError] = useState<string | null>(null)
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
  const holdingsColumnsDialogRef = useModalDialog(
    holdingsColumnsOpen,
    () => setHoldingsColumnsOpen(false),
  )
  const holdingsGroupByDialogRef = useModalDialog(
    holdingsGroupByOpen,
    () => setHoldingsGroupByOpen(false),
  )
  const [holdingsColumnCategory, setHoldingsColumnCategory] = useState(HOLDINGS_COLUMN_GROUPS[0]?.label ?? 'Core')
  const [holdingsColumnSearch, setHoldingsColumnSearch] = useState('')
  const [holdingsColumnDropTarget, setHoldingsColumnDropTarget] = useState<HoldingsColumnKey | null>(null)
  const [viewToast, setViewToast] = useState<NoticeToastMessage | null>(null)
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
  const holdingsTableShellRef = useRef<HTMLDivElement | null>(null)
  const [holdingsTableShellWidth, setHoldingsTableShellWidth] = useState(0)
  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const selectedInstrumentId = searchParams.get('instrument_id')
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

  const taxonomyByInstrumentId = useMemo(
    () => buildTaxonomyLabelsByInstrumentId(taxonomyCatalog, workspace?.as_of_date),
    [taxonomyCatalog, workspace?.as_of_date],
  )
  const columnContext = useMemo(
    () =>
      workspace
        ? {
            workspace,
            taxonomyByInstrumentId,
          }
        : null,
    [taxonomyByInstrumentId, workspace],
  )
  const visibleColumns = useMemo(
    () => normalizeHoldingsColumns(holdingsColumns).map((column) => HOLDINGS_COLUMN_DEFINITIONS[column]),
    [holdingsColumns],
  )
  const compactHoldingsColumns = useMemo(
    () =>
      compactTableColumnWidths(
        visibleColumns.map((column) => column.key),
        (column) => holdingsColumnWidths[column] ?? DEFAULT_HOLDINGS_COLUMN_WIDTHS[column],
        (column) => COMPACT_HOLDINGS_COLUMN_MIN_WIDTHS[column] ?? HOLDINGS_COLUMN_MIN_WIDTH,
        holdingsTableShellWidth,
      ),
    [holdingsColumnWidths, holdingsTableShellWidth, visibleColumns],
  )
  const displayHoldingsColumnWidths = compactHoldingsColumns.widths
  const displayHoldingsTableMinWidth = compactHoldingsColumns.totalWidth
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

  useEffect(() => {
    const element = holdingsTableShellRef.current
    if (!element) {
      return undefined
    }

    const updateWidth = () => setHoldingsTableShellWidth(Math.floor(element.clientWidth))
    updateWidth()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateWidth)
      return () => window.removeEventListener('resize', updateWidth)
    }

    const observer = new ResizeObserver(updateWidth)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

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
  const nonCashPortfolioRows = useMemo(() => nonCashHoldingRows(sortedHoldingRows), [sortedHoldingRows])
  const cashPortfolioRows = useMemo(
    () => sortedHoldingRows.filter((row) => isCashHoldingRow(row)).sort(compareCashHoldingRows),
    [sortedHoldingRows],
  )
  const showNonCashPortfolioRow = nonCashPortfolioRows.length > 0 && cashPortfolioRows.length > 0
  const separateCashTaxonomyGroups = holdingsGroupBy === 'taxonomy_top' || holdingsGroupBy === 'taxonomy_leaf'
  const nonCashPortfolioLabel = workspace
    ? `Non-cash Portfolio (${workspace.base_currency})`
    : 'Non-cash Portfolio'
  const ungroupedHoldingDisplayItems = useMemo<HoldingsDisplayItem[]>(() => {
    const items: HoldingsDisplayItem[] = nonCashPortfolioRows.map((row) => ({ kind: 'holding', row }))
    if (showNonCashPortfolioRow) {
      items.push({ kind: 'noncash-total' })
    }
    cashPortfolioRows.forEach((row) => items.push({ kind: 'holding', row }))

    return items
  }, [cashPortfolioRows, nonCashPortfolioRows, showNonCashPortfolioRow])
  const groupedHoldingRows = useMemo(
    () =>
      buildGroupedRows(
        separateCashTaxonomyGroups ? nonCashPortfolioRows : sortedHoldingRows,
        holdingsGroupBy,
        taxonomyByInstrumentId,
        workspace,
      ),
    [holdingsGroupBy, nonCashPortfolioRows, separateCashTaxonomyGroups, sortedHoldingRows, taxonomyByInstrumentId, workspace],
  )
  const groupedCashHoldingRows = useMemo(
    () =>
      separateCashTaxonomyGroups
        ? buildGroupedRows(cashPortfolioRows, holdingsGroupBy, taxonomyByInstrumentId, workspace)
        : [],
    [cashPortfolioRows, holdingsGroupBy, separateCashTaxonomyGroups, taxonomyByInstrumentId, workspace],
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
    const timestamp = new Date().toISOString()
    setHoldingsViewStore((current) => {
      return {
        ...current,
        activeViewId: activeHoldingsViewId,
        views: current.views.map((view) =>
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
    setViewToast({ id: Date.now(), message: 'View updated.', tone: 'success' })
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
        views: [...current.views, nextView],
      }
    })
    setActiveHoldingsViewId(viewId)
    setViewToast({ id: Date.now(), message: `View saved as "${name}".`, tone: 'success' })
  }

  function handleDeleteHoldingsView(viewId: string) {
    const targetView = getHoldingsViewById(holdingsViewStore, viewId)
    if (targetView.readonly) {
      return
    }
    const fallbackView = SYSTEM_HOLDINGS_VIEWS[0]
    const deletingActiveView = targetView.id === activeHoldingsViewId
    setHoldingsViewStore((current) => ({
      ...current,
      activeViewId: deletingActiveView ? fallbackView.id : current.activeViewId,
      views: current.views.filter((view) => view.id !== targetView.id),
    }))
    if (deletingActiveView) {
      setActiveHoldingsViewId(fallbackView.id)
      applyHoldingsViewState(fallbackView.state)
    }
  }

  function handleSelectInstrument(instrumentId: string | null) {
    const normalizedInstrumentId = instrumentId?.trim() || null
    if (!normalizedInstrumentId || !portfolioId) {
      updateSearchParam('instrument_id', null)
      return
    }
    if (normalizedInstrumentId.toLowerCase().startsWith('cash:')) {
      updateSearchParam('instrument_id', null)
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('instrument_id')
    next.delete('position_lot_id')
    const query = next.toString()
    navigate(`${buildPortfolioHoldingDetailPath(portfolioId, normalizedInstrumentId)}${query ? `?${query}` : ''}`)
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

  function handleDownload(format: TableExportFormat) {
    if (!workspace || !columnContext || !sortedHoldingRows.length) {
      return
    }

    const header = [
      ...(holdingsGroupBy !== 'none' ? ['Group'] : []),
      ...visibleColumns.map((column) => column.label),
    ]
    const rows: TableCell[][] = [header]

    const pushHoldingExportRow = (row: PortfolioHoldingRow, groupLabel: string | null) => {
      rows.push([
        ...(holdingsGroupBy !== 'none' ? [groupLabel] : []),
        ...visibleColumns.map((column) => holdingColumnExportValue(column.key, row, columnContext)),
      ])
    }
    const pushNonCashPortfolioExportRow = () => {
      rows.push([
        ...(holdingsGroupBy !== 'none' ? ['Portfolio View'] : []),
        ...visibleColumns.map((column, index) =>
          index === 0
            ? nonCashPortfolioLabel
            : holdingColumnTotalExportValue(column.key, nonCashPortfolioRows, columnContext, false),
          ),
      ])
    }
    const pushGroupedExportRows = (groups: HoldingsGroup[]) => {
      groups.forEach((group) => {
        group.rows.forEach((row) => pushHoldingExportRow(row, group.label))
        rows.push([
          group.label,
          ...visibleColumns.map((column, index) =>
            index === 0 ? `Subtotal (${workspace.base_currency})` : holdingColumnTotalExportValue(column.key, group.rows, columnContext),
          ),
        ])
      })
    }

    if (holdingsGroupBy === 'none') {
      ungroupedHoldingDisplayItems.forEach((item) => {
        if (item.kind === 'noncash-total') {
          pushNonCashPortfolioExportRow()
          return
        }
        pushHoldingExportRow(item.row, null)
      })
    } else {
      pushGroupedExportRows(groupedHoldingRows)
      if (showNonCashPortfolioRow) {
        pushNonCashPortfolioExportRow()
      }
      pushGroupedExportRows(groupedCashHoldingRows)
    }

    rows.push([
      ...(holdingsGroupBy !== 'none' ? ['Portfolio Total'] : []),
      ...visibleColumns.map((column) =>
        holdingColumnTotalExportValue(column.key, sortedHoldingRows, columnContext, false),
      ),
    ])

    downloadTable(`holdings-${workspace.portfolio_id}-${workspace.as_of_date}`, rows, format, 'Holdings')
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

  function renderHoldingDataRow(row: PortfolioHoldingRow) {
    if (!columnContext) {
      return null
    }
    const isActive = selectedInstrumentId === row.instrument_core.instrument_id
    return (
      <tr
        key={row.line_id}
        className={isActive ? 'holdings-row-active' : undefined}
        tabIndex={0}
        onClick={() => handleSelectInstrument(row.instrument_core.instrument_id)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            handleSelectInstrument(row.instrument_core.instrument_id)
          }
        }}
      >
        {visibleColumns.map((column) => {
          const className = [
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
            column.className?.(row, columnContext) ?? '',
            column.key === 'instrument' ? 'holding-name-cell' : '',
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
  }

  function renderHoldingsTotalRow(rows: PortfolioHoldingRow[], label: string, className: string, key?: string) {
    if (!columnContext) {
      return null
    }
    return (
      <HoldingsTotalRow
        key={key}
        className={className}
        label={label}
        cells={visibleColumns.map((column, index) => {
          const withholdInstrumentReturn = isPortfolioReturnColumn(column.key)
          const classNames = [
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
            withholdInstrumentReturn ? '' : column.totalClassName?.(rows, columnContext) ?? '',
          ]
            .filter(Boolean)
            .join(' ')
          return {
            key: column.key,
            className: classNames || undefined,
            content:
              index === 0 || withholdInstrumentReturn
                ? undefined
                : column.total?.(rows, columnContext) ?? '',
          }
        })}
      />
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
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
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
    if (!portfolioId) {
      setHoldingsViewStoreReadyPortfolioId(null)
      setHoldingsViewStoreError(null)
      return
    }

    let cancelled = false
    const defaultStore = normalizeHoldingsViewStore(null)
    setHoldingsViewStoreReadyPortfolioId(null)
    setHoldingsViewStoreError(null)
    setHoldingsViewStore(defaultStore)
    setActiveHoldingsViewId(defaultStore.activeViewId)
    applyHoldingsViewState(resolveHoldingsViewState(defaultStore, defaultStore.activeViewId))
    getPortfolioTableViewStore<HoldingsViewStore>(portfolioId, 'holdings')
      .then((response) => {
        if (cancelled) {
          return
        }
        const nextStore = response.store
          ? normalizeHoldingsViewStore(response.store)
          : defaultStore
        setHoldingsViewStore(nextStore)
        setActiveHoldingsViewId(nextStore.activeViewId)
        applyHoldingsViewState(resolveHoldingsViewState(nextStore, nextStore.activeViewId))
        setHoldingsViewStoreReadyPortfolioId(portfolioId)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setHoldingsViewStoreError(
            `Holdings table views unavailable: ${
              requestError instanceof Error ? requestError.message : 'backend read failed.'
            }`,
          )
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId || holdingsViewStoreReadyPortfolioId !== portfolioId) {
      return
    }
    savePortfolioTableViewStore(portfolioId, 'holdings', holdingsViewStore).catch((requestError: unknown) => {
      setHoldingsViewStoreError(
        `Failed to save holdings table views: ${
          requestError instanceof Error ? requestError.message : 'backend write failed.'
        }`,
      )
    })
  }, [holdingsViewStore, holdingsViewStoreReadyPortfolioId, portfolioId])

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
    function handleRiskPolicyUpdated(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        setRiskPolicyRevision((current) => current + 1)
      }
    }

    window.addEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
    return () => window.removeEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
  }, [portfolioId])

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
      getHoldingsWorkspace(portfolioId || undefined, {
        as_of_date: requestedAsOfDate || undefined,
      }),
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
  }, [portfolioId, requestedAsOfDate, riskPolicyRevision])

  useEffect(() => {
    if (!workspace || !selectedInstrumentId) {
      return
    }

    if (workspace.rows.some((row) => row.instrument_core.instrument_id === selectedInstrumentId)) {
      return
    }

    setSearchParams((current) => {
      if (current.get('instrument_id') !== selectedInstrumentId) {
        return current
      }
      const next = new URLSearchParams(current)
      next.delete('instrument_id')
      return next
    })
  }, [workspace, selectedInstrumentId, setSearchParams])

  return (
    <>
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <PortfolioWorkspaceLayout
        activeSection="Holdings"
        toolbarLabel={workspace?.view_label ?? 'View: Holdings'}
        busy={loading}
      >
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
            {holdingsViewStoreReadyPortfolioId === portfolioId ? (
              <PortfolioTableViewControls
                views={holdingsViews}
                activeViewId={activeHoldingsViewId}
                edited={holdingsViewEdited}
                canSave
                canDelete
                onSelect={handleSelectHoldingsView}
                onSave={handleSaveHoldingsView}
                onSaveAs={handleSaveHoldingsViewAs}
                onDelete={handleDeleteHoldingsView}
              />
            ) : (
              <span className="portfolio-detail-meta">
                {holdingsViewStoreError ? 'Table views unavailable' : 'Loading table views'}
              </span>
            )}
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
            <DownloadFormatMenu
              wrapperClassName="portfolio-download-menu"
              buttonClassName="holdings-toolbar-button"
              menuClassName="portfolio-download-menu-list"
              itemClassName="portfolio-download-menu-item"
              disabled={!workspace || !sortedHoldingRows.length}
              onSelect={handleDownload}
            />
          </div>
        </div>
        {loading ? <CalculationStatus /> : null}
        {error ? <div className="error-state">{error}</div> : null}
        {holdingsViewStoreError ? (
          <div className="inline-notice inline-notice-error" role="alert">
            {holdingsViewStoreError}
          </div>
        ) : null}
        <QualityWarningsNotice warnings={workspace?.quality_warnings} />
        {taxonomyError && holdingsGroupBy.startsWith('taxonomy') ? (
          <div className="inline-notice inline-notice-warning">{taxonomyError}</div>
        ) : null}
        {!loading && !error && workspace && columnContext ? (
          <div className="table-shell holdings-table-shell" ref={holdingsTableShellRef}>
            <table className="holdings-table holdings-main-table" style={{ minWidth: `${displayHoldingsTableMinWidth}px` }}>
              <colgroup>
                {visibleColumns.map((column) => (
                  <col
                    key={column.key}
                    style={{
                      width: `${displayHoldingsColumnWidths[column.key]}px`,
                    }}
                  />
                ))}
              </colgroup>
              <thead>
                <tr>
                  {visibleColumns.map((column) => {
                    const width = displayHoldingsColumnWidths[column.key]
                    return (
                      <th
                        key={column.key}
                        className={[
                          holdingsAlignmentClass(column),
                          isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
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
                          role="separator"
                          aria-label={`Resize ${column.label} column`}
                          aria-orientation="vertical"
                          aria-valuemin={HOLDINGS_COLUMN_MIN_WIDTH}
                          aria-valuemax={HOLDINGS_COLUMN_MAX_WIDTH}
                          aria-valuenow={Math.round(width)}
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
                              return
                            }
                            event.preventDefault()
                            const delta = event.key === 'ArrowLeft' ? -10 : 10
                            setHoldingsColumnWidths((current) => ({
                              ...current,
                              [column.key]: clampHoldingsColumnWidth((current[column.key] ?? width) + delta),
                            }))
                          }}
                          onMouseDown={(event) => handleHoldingsColumnResizeStart(event, column.key, width)}
                        />
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {sortedHoldingRows.length ? (
                  holdingsGroupBy === 'none' ? (
                    ungroupedHoldingDisplayItems.map((item) =>
                      item.kind === 'noncash-total'
                        ? renderHoldingsTotalRow(
                            nonCashPortfolioRows,
                            nonCashPortfolioLabel,
                            'total-row holdings-noncash-row',
                            'noncash-portfolio',
                          )
                        : renderHoldingDataRow(item.row),
                    )
                  ) : (
                    <>
                      {groupedHoldingRows.map((group) => (
                        <Fragment key={group.key}>
                          {renderHoldingsGroupRow(group)}
                          {group.rows.map((row) => renderHoldingDataRow(row))}
                        </Fragment>
                      ))}
                      {showNonCashPortfolioRow
                        ? renderHoldingsTotalRow(
                            nonCashPortfolioRows,
                            nonCashPortfolioLabel,
                            'total-row holdings-noncash-row',
                            'noncash-portfolio',
                          )
                        : null}
                      {groupedCashHoldingRows.map((group) => (
                        <Fragment key={`cash:${group.key}`}>
                          {renderHoldingsGroupRow(group)}
                          {group.rows.map((row) => renderHoldingDataRow(row))}
                        </Fragment>
                      ))}
                    </>
                  )
                ) : (
                  <TableStatusRow colSpan={visibleColumns.length} label="No holdings." />
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
          <div
            ref={holdingsColumnsDialogRef}
            className="holdings-modal holdings-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose holdings columns"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Data &amp; Columns</div>
                <div className="section-heading">Columns</div>
              </div>
              <button type="button" onClick={() => setHoldingsColumnsOpen(false)}>
                Close
              </button>
            </div>

            <div className="holdings-modal-search">
              <input
                className="holdings-modal-search-input"
                placeholder="Search fields"
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
                  <div className="holdings-field-empty">No fields.</div>
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
          <div
            ref={holdingsGroupByDialogRef}
            className="holdings-modal holdings-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Group holdings"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Group By</div>
                <div className="section-heading">Grouping</div>
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
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      </PortfolioWorkspaceLayout>
    </>
  )
}
