import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import { downloadTable, type TableCell, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceComparison,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioTableViewStore,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  savePortfolioTableViewStore,
  type PortfolioContributionAxis,
  type PortfolioPerformanceCalculationGroupsResponse,
  type PortfolioPerformanceCalculationResponse,
  type PortfolioPerformanceComparisonResponse,
  type PortfolioPerformanceResponse,
  type PortfolioPerformanceSummary,
  type PortfolioWorkspaceSummary,
  type SharedInstrumentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import {
  realizedRiskContributionResidual,
  realizedRiskMetricsAvailable,
} from '../lib/performanceRiskReliability'
import {
  annualizedReturnDisplayEligible,
  selectPerformanceHistoryReliability,
} from '../lib/performanceHistoryPresentation'

const DEFAULT_PERFORMANCE_LOOKBACK_DAYS = 30

type CalculationGroupRow = PortfolioPerformanceCalculationGroupsResponse['groups'][number]
type CalculationGroupChildRow = CalculationGroupRow['children'][number]
type CalculationDisplayRow = CalculationGroupRow | CalculationGroupChildRow

type PerformanceMetricRow = {
  metric: string
  value: string
  reliabilityNote?: string
  valueClassName?: string
  benchmark?: string
  benchmarkClassName?: string
  difference?: string
  differenceClassName?: string
  showComparison?: boolean
}

type CalculationGroupByOption = {
  value: CalculationGroupByKey
  label: string
  disabled?: boolean
}

type CalculationGroupByKey = 'none' | 'account' | 'instrument_type' | 'currency' | 'taxonomy'

type CalculationTableMode = 'risk_attribution' | 'calculation'
type CalculationSortDirection = 'asc' | 'desc'
type CalculationSortableValue = number | string | null | undefined

type CalculationColumnKey =
  | 'line'
  | 'pnl_flow'
  | 'start_value'
  | 'end_value'
  | 'begin_weight'
  | 'avg_weight'
  | 'end_weight'
  | 'realized_gain'
  | 'unrealized_gain'
  | 'income'
  | 'fees'
  | 'taxes'
  | 'fx_pnl'
  | 'period_return'
  | 'return_contribution'
  | 'own_vol'
  | 'own_sharpe'
  | 'own_corr'
  | 'beta'
  | 'risk_contribution'
  | 'observations'

type CalculationTableViewState = {
  columns: CalculationColumnKey[]
  mode: CalculationTableMode
  groupBy: CalculationGroupByKey
  sortField: CalculationColumnKey | null
  sortDirection: CalculationSortDirection
}

type CalculationTableView = PortfolioTableViewOption & {
  state: CalculationTableViewState
  readonly?: boolean
  createdAt?: string
  updatedAt?: string
}

type CalculationTableViewStore = {
  activeViewId: string
  views: CalculationTableView[]
}

type PerformanceWindowSelection = {
  startDate: string
  endDate: string
}

type CalculationSyntheticRowKind =
  | 'initial'
  | 'final'
  | 'deposits'
  | 'withdrawals'
  | 'portfolio_total'
  | 'contribution_residual'
  | 'risk_contribution_residual'

type CalculationTableRow =
  | {
      kind: 'group'
      key: string
      className?: string
      row: CalculationGroupRow
    }
  | {
      kind: 'child'
      key: string
      className?: string
      row: CalculationGroupChildRow
    }
  | {
      kind: 'synthetic'
      key: string
      className?: string
      label: string
      syntheticKind: CalculationSyntheticRowKind
    }

const LOCKED_CALCULATION_COLUMN: CalculationColumnKey = 'line'
const PERFORMANCE_WINDOW_STORAGE_KEY = 'portfolio_ops.portfolio.performance.window.v1'
const CALCULATION_TABLE_VIEWS_STORAGE_KEY = 'portfolio_ops.portfolio.performance.calculation.views.v1'

const RISK_ATTRIBUTION_COLUMNS: CalculationColumnKey[] = [
  'line',
  'begin_weight',
  'avg_weight',
  'end_weight',
  'period_return',
  'return_contribution',
  'own_vol',
  'own_sharpe',
  'own_corr',
  'risk_contribution',
  'observations',
]

const FULL_CALCULATION_COLUMNS: CalculationColumnKey[] = [
  'line',
  'pnl_flow',
  'start_value',
  'end_value',
  'begin_weight',
  'avg_weight',
  'end_weight',
  'realized_gain',
  'unrealized_gain',
  'income',
  'fees',
  'taxes',
  'fx_pnl',
  'period_return',
  'return_contribution',
]

const CALCULATION_COLUMN_GROUPS: Array<{ label: string; columns: CalculationColumnKey[] }> = [
  { label: 'Core', columns: ['line', 'begin_weight', 'avg_weight', 'end_weight', 'period_return', 'return_contribution'] },
  {
    label: 'P&L',
    columns: [
      'pnl_flow',
      'start_value',
      'end_value',
      'realized_gain',
      'unrealized_gain',
      'income',
      'fees',
      'taxes',
      'fx_pnl',
    ],
  },
  {
    label: 'Risk Attribution',
    columns: ['own_vol', 'own_sharpe', 'own_corr', 'beta', 'risk_contribution', 'observations'],
  },
]

const ALL_CALCULATION_COLUMN_KEYS = CALCULATION_COLUMN_GROUPS.flatMap((group) => group.columns)
const SIGNED_CALCULATION_COLUMN_KEYS = new Set<CalculationColumnKey>([
  'pnl_flow',
  'realized_gain',
  'unrealized_gain',
  'income',
  'fees',
  'taxes',
  'fx_pnl',
  'period_return',
  'return_contribution',
  'own_corr',
  'beta',
  'risk_contribution',
])

const CALCULATION_COLUMN_LABELS: Record<CalculationColumnKey, string> = {
  line: 'Line',
  pnl_flow: 'P&L / Flow',
  start_value: 'Start Value',
  end_value: 'End Value',
  begin_weight: 'Begin Weight',
  avg_weight: 'Avg Weight',
  end_weight: 'End Weight',
  realized_gain: 'Realized Gain',
  unrealized_gain: 'Unrealized Gain',
  income: 'Income',
  fees: 'Fees',
  taxes: 'Taxes',
  fx_pnl: 'FX P&L',
  period_return: 'Period Return',
  return_contribution: 'Return Contribution',
  own_vol: 'Vol',
  own_sharpe: 'Sharpe',
  own_corr: 'Corr to Portfolio',
  beta: 'Beta to Portfolio',
  risk_contribution: 'Realized RC',
  observations: 'Obs',
}

const DEFAULT_CALCULATION_TABLE_VIEW_STATE: CalculationTableViewState = {
  columns: RISK_ATTRIBUTION_COLUMNS,
  mode: 'risk_attribution',
  groupBy: 'none',
  sortField: null,
  sortDirection: 'asc',
}

const SYSTEM_CALCULATION_TABLE_VIEWS: CalculationTableView[] = [
  {
    id: 'risk-attribution',
    name: 'Default',
    readonly: true,
    state: DEFAULT_CALCULATION_TABLE_VIEW_STATE,
  },
  {
    id: 'full-calculation',
    name: 'Full Calculation',
    readonly: true,
    state: {
      columns: FULL_CALCULATION_COLUMNS,
      mode: 'calculation',
      groupBy: 'none',
      sortField: null,
      sortDirection: 'asc',
    },
  },
  {
    id: 'pnl-breakdown',
    name: 'P&L Breakdown',
    readonly: true,
    state: {
      columns: [
        'line',
        'pnl_flow',
        'start_value',
        'end_value',
        'realized_gain',
        'unrealized_gain',
        'income',
        'fees',
        'taxes',
        'fx_pnl',
        'return_contribution',
      ],
      mode: 'calculation',
      groupBy: 'none',
      sortField: null,
      sortDirection: 'asc',
    },
  },
]
const SYSTEM_CALCULATION_TABLE_VIEW_IDS = new Set(SYSTEM_CALCULATION_TABLE_VIEWS.map((view) => view.id))

function localDateIso(input = new Date()) {
  const year = input.getFullYear()
  const month = `${input.getMonth() + 1}`.padStart(2, '0')
  const day = `${input.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const nextDate = new Date(year, (month || 1) - 1, day || 1)
  nextDate.setDate(nextDate.getDate() + days)
  return localDateIso(nextDate)
}

function validIsoDate(value: string | null | undefined) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ''
}

function normalizePerformanceWindowSelection(value: unknown): PerformanceWindowSelection | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const record = value as { startDate?: unknown; endDate?: unknown }
  const startDate = typeof record.startDate === 'string' ? validIsoDate(record.startDate) : ''
  const endDate = typeof record.endDate === 'string' ? validIsoDate(record.endDate) : ''
  return startDate || endDate ? { startDate, endDate } : null
}

function loadPerformanceWindowStore() {
  const store: Record<string, PerformanceWindowSelection> = {}
  if (typeof window === 'undefined') {
    return store
  }
  try {
    const rawValue = window.localStorage.getItem(PERFORMANCE_WINDOW_STORAGE_KEY)
    const parsedValue = rawValue ? JSON.parse(rawValue) : null
    if (!parsedValue || typeof parsedValue !== 'object') {
      return store
    }
    Object.entries(parsedValue as Record<string, unknown>).forEach(([portfolioId, rawSelection]) => {
      if (!portfolioId) {
        return
      }
      const selection = normalizePerformanceWindowSelection(rawSelection)
      if (selection) {
        store[portfolioId] = selection
      }
    })
  } catch {
    return store
  }
  return store
}

function loadPerformanceWindowSelection(portfolioId: string) {
  return loadPerformanceWindowStore()[portfolioId] ?? null
}

function savePerformanceWindowSelection(portfolioId: string, startDate: string | null, endDate: string | null) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    const store = loadPerformanceWindowStore()
    const selection = normalizePerformanceWindowSelection({ startDate, endDate })
    if (selection) {
      store[portfolioId] = selection
    } else {
      delete store[portfolioId]
    }
    window.localStorage.setItem(PERFORMANCE_WINDOW_STORAGE_KEY, JSON.stringify(store))
  } catch {
    return
  }
}

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) {
    return '—'
  }
  const absolute = formatPercent(Math.abs(value), digits)
  if (value > 0) {
    return `+${absolute}`
  }
  if (value < 0) {
    return `-${absolute}`
  }
  return absolute
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function csvNumber(value: number | null | undefined) {
  return finiteNumber(value)
}

function sumNullable(...values: Array<number | null | undefined>) {
  let hasValue = false
  let total = 0
  values.forEach((value) => {
    const finiteValue = finiteNumber(value)
    if (finiteValue == null) {
      return
    }
    hasValue = true
    total += finiteValue
  })
  return hasValue ? total : null
}

function expenseImpact(value: number | null | undefined) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return null
  }
  return finiteValue === 0 ? 0 : -Math.abs(finiteValue)
}

function fxPnlAmount(row: Pick<CalculationGroupRow, 'cash_currency_gains' | 'instrument_currency_gains'>) {
  return sumNullable(row.cash_currency_gains, row.instrument_currency_gains)
}

function calculationDisplayMetricValue(source: CalculationDisplayRow, column: CalculationColumnKey): number | null {
  switch (column) {
    case 'pnl_flow':
      return finiteNumber(source.total_pnl)
    case 'start_value':
      return finiteNumber(source.initial_value)
    case 'end_value':
      return finiteNumber(source.final_value)
    case 'begin_weight':
      return finiteNumber(source.beginning_weight)
    case 'avg_weight':
      return finiteNumber(source.average_weight)
    case 'end_weight':
      return finiteNumber(source.ending_weight)
    case 'realized_gain':
      return finiteNumber(source.realized_capital_gains)
    case 'unrealized_gain':
      return finiteNumber(source.unrealized_pnl_change)
    case 'income':
      return finiteNumber(source.earnings)
    case 'fees':
      return finiteNumber(expenseImpact(source.fees))
    case 'taxes':
      return finiteNumber(expenseImpact(source.taxes))
    case 'fx_pnl':
      return finiteNumber(fxPnlAmount(source))
    case 'period_return':
      return finiteNumber(source.period_return)
    case 'return_contribution':
      return finiteNumber(source.period_contribution)
    case 'own_vol':
      return finiteNumber(source.annualized_volatility)
    case 'own_sharpe':
      return finiteNumber(source.sharpe_ratio)
    case 'own_corr':
      return finiteNumber(source.correlation_to_portfolio)
    case 'beta':
      return finiteNumber(source.beta_to_portfolio)
    case 'risk_contribution':
      return finiteNumber(source.realized_risk_contribution)
    case 'observations':
      return finiteNumber(source.risk_return_observation_count)
    default:
      return null
  }
}

function isCalculationChildRow(row: CalculationDisplayRow): row is CalculationGroupChildRow {
  return 'item_key' in row
}

function calculationDisplayLabel(row: CalculationDisplayRow) {
  return isCalculationChildRow(row) ? row.item_label : row.group_label
}

function calculationAxisLabel(axis: PortfolioContributionAxis) {
  if (axis === 'account') {
    return 'Account'
  }
  if (axis === 'instrument_type') {
    return 'Instrument Type'
  }
  if (axis === 'currency') {
    return 'Currency'
  }
  if (axis === 'taxonomy') {
    return 'Taxonomy'
  }
  return 'Instrument'
}

function calculationAxisCountLabel(axis: PortfolioContributionAxis) {
  if (axis === 'account') {
    return 'accounts'
  }
  if (axis === 'instrument_type') {
    return 'instrument types'
  }
  if (axis === 'currency') {
    return 'currencies'
  }
  if (axis === 'taxonomy') {
    return 'groups'
  }
  return 'instruments'
}

function parseCalculationSortField(value: string | null | undefined): CalculationColumnKey | null {
  return value && ALL_CALCULATION_COLUMN_KEYS.includes(value as CalculationColumnKey)
    ? (value as CalculationColumnKey)
    : null
}

function parseCalculationSortDirection(value: string | null | undefined): CalculationSortDirection {
  return value === 'asc' || value === 'desc' ? value : 'asc'
}

function compareCalculationSortableValue(
  left: CalculationSortableValue,
  right: CalculationSortableValue,
  direction: CalculationSortDirection,
) {
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

function calculationSortValue(row: CalculationDisplayRow, column: CalculationColumnKey): CalculationSortableValue {
  return column === 'line' ? calculationDisplayLabel(row) : calculationDisplayMetricValue(row, column)
}

function defaultCalculationGroupCompare(
  left: CalculationGroupRow,
  right: CalculationGroupRow,
  mode: CalculationTableMode,
) {
  if (mode === 'risk_attribution') {
    const leftRisk = Math.abs(finiteNumber(left.realized_risk_contribution) ?? 0)
    const rightRisk = Math.abs(finiteNumber(right.realized_risk_contribution) ?? 0)
    const leftContribution = Math.abs(finiteNumber(left.period_contribution) ?? 0)
    const rightContribution = Math.abs(finiteNumber(right.period_contribution) ?? 0)
    return (
      rightRisk - leftRisk ||
      rightContribution - leftContribution ||
      left.group_label.localeCompare(right.group_label, 'zh-Hans-CN')
    )
  }

  const leftMagnitude = Math.abs(finiteNumber(left.period_contribution) ?? finiteNumber(left.total_pnl) ?? 0)
  const rightMagnitude = Math.abs(finiteNumber(right.period_contribution) ?? finiteNumber(right.total_pnl) ?? 0)
  return rightMagnitude - leftMagnitude || left.group_label.localeCompare(right.group_label, 'zh-Hans-CN')
}

function defaultCalculationChildCompare(left: CalculationGroupChildRow, right: CalculationGroupChildRow) {
  const leftKindOrder = left.item_kind === 'instrument' ? 0 : 1
  const rightKindOrder = right.item_kind === 'instrument' ? 0 : 1
  const leftMagnitude = Math.abs(finiteNumber(left.period_contribution) ?? finiteNumber(left.total_pnl) ?? 0)
  const rightMagnitude = Math.abs(finiteNumber(right.period_contribution) ?? finiteNumber(right.total_pnl) ?? 0)
  return (
    leftKindOrder - rightKindOrder ||
    rightMagnitude - leftMagnitude ||
    left.item_label.localeCompare(right.item_label, 'zh-Hans-CN')
  )
}

function compareCalculationRowsByColumn(
  left: CalculationDisplayRow,
  right: CalculationDisplayRow,
  sortField: CalculationColumnKey,
  sortDirection: CalculationSortDirection,
) {
  return (
    compareCalculationSortableValue(
      calculationSortValue(left, sortField),
      calculationSortValue(right, sortField),
      sortDirection,
    ) || calculationDisplayLabel(left).localeCompare(calculationDisplayLabel(right), 'zh-Hans-CN')
  )
}

function normalizeCalculationColumns(columns: CalculationColumnKey[]) {
  const seen = new Set<CalculationColumnKey>()
  const normalized: CalculationColumnKey[] = [LOCKED_CALCULATION_COLUMN]
  columns.forEach((column) => {
    if (
      column === LOCKED_CALCULATION_COLUMN ||
      seen.has(column) ||
      !ALL_CALCULATION_COLUMN_KEYS.includes(column)
    ) {
      return
    }
    seen.add(column)
    normalized.push(column)
  })
  return normalized
}

function parseCalculationTableMode(value: string | null | undefined): CalculationTableMode {
  return value === 'calculation' || value === 'risk_attribution' ? value : 'risk_attribution'
}

function parseCalculationGroupBy(value: string | null | undefined): CalculationGroupByKey {
  return value === 'none' ||
    value === 'account' ||
    value === 'instrument_type' ||
    value === 'currency' ||
    value === 'taxonomy'
    ? value
    : 'none'
}

function normalizeCalculationTableViewState(value: unknown): CalculationTableViewState {
  if (!value || typeof value !== 'object') {
    return DEFAULT_CALCULATION_TABLE_VIEW_STATE
  }
  const record = value as Partial<CalculationTableViewState>
  const sortField = parseCalculationSortField(typeof record.sortField === 'string' ? record.sortField : null)
  return {
    columns: normalizeCalculationColumns(
      Array.isArray(record.columns) ? (record.columns as CalculationColumnKey[]) : RISK_ATTRIBUTION_COLUMNS,
    ),
    mode: parseCalculationTableMode(typeof record.mode === 'string' ? record.mode : null),
    groupBy: parseCalculationGroupBy(typeof record.groupBy === 'string' ? record.groupBy : null),
    sortField,
    sortDirection: parseCalculationSortDirection(typeof record.sortDirection === 'string' ? record.sortDirection : null),
  }
}

function serializeCalculationTableViewState(value: CalculationTableViewState) {
  const normalized = normalizeCalculationTableViewState(value)
  return JSON.stringify({
    columns: normalized.columns,
    mode: normalized.mode,
    groupBy: normalized.groupBy,
    sortField: normalized.sortField,
    sortDirection: normalized.sortDirection,
  })
}

function calculationTableViewStatesEqual(left: CalculationTableViewState, right: CalculationTableViewState) {
  return serializeCalculationTableViewState(left) === serializeCalculationTableViewState(right)
}

function normalizeCalculationTableView(value: unknown, readonly: boolean): CalculationTableView | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const view = value as Partial<CalculationTableView>
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
    state: normalizeCalculationTableViewState(view.state),
  }
}

function normalizeCalculationTableViewStore(value: unknown): CalculationTableViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<CalculationTableViewStore>) : {}
  const storedViews = Array.isArray(record.views)
    ? record.views
        .map((view) =>
          normalizeCalculationTableView(
            view,
            SYSTEM_CALCULATION_TABLE_VIEW_IDS.has((view as Partial<CalculationTableView>)?.id || ''),
          ),
        )
        .filter((view): view is CalculationTableView => Boolean(view))
    : null
  const storedViewById = new Map((storedViews || []).map((view) => [view.id, view]))
  const systemViews = SYSTEM_CALCULATION_TABLE_VIEWS.map((defaultView) => {
    const storedView = storedViewById.get(defaultView.id)
    return storedView ? { ...storedView, readonly: true } : defaultView
  })
  const customViews = storedViews
    ? storedViews.filter((view) => !SYSTEM_CALCULATION_TABLE_VIEW_IDS.has(view.id)).map((view) => ({ ...view, readonly: false }))
    : Array.isArray((record as { customViews?: unknown }).customViews)
      ? ((record as { customViews: unknown[] }).customViews)
          .map((view) => normalizeCalculationTableView(view, false))
          .filter((view): view is CalculationTableView => Boolean(view))
      : []
  const views = [...systemViews, ...customViews]
  const knownViewIds = new Set(views.map((view) => view.id))
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_CALCULATION_TABLE_VIEWS[0].id
  return { activeViewId, views }
}

function loadCalculationTableViewStore(): CalculationTableViewStore {
  if (typeof window === 'undefined') {
    return normalizeCalculationTableViewStore(null)
  }
  try {
    const rawValue = window.localStorage.getItem(CALCULATION_TABLE_VIEWS_STORAGE_KEY)
    if (rawValue) {
      return normalizeCalculationTableViewStore(JSON.parse(rawValue))
    }
  } catch {
    return normalizeCalculationTableViewStore(null)
  }
  return normalizeCalculationTableViewStore(null)
}

function saveCalculationTableViewStore(store: CalculationTableViewStore) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(CALCULATION_TABLE_VIEWS_STORAGE_KEY, JSON.stringify(store))
  } catch {
    return
  }
}

function getCalculationTableViews(store: CalculationTableViewStore) {
  return store.views.length ? store.views : SYSTEM_CALCULATION_TABLE_VIEWS
}

function getCalculationTableViewById(store: CalculationTableViewStore, viewId: string) {
  return getCalculationTableViews(store).find((view) => view.id === viewId) ?? SYSTEM_CALCULATION_TABLE_VIEWS[0]
}

function resolveCalculationTableViewState(store: CalculationTableViewStore, viewId: string) {
  return getCalculationTableViewById(store, viewId).state
}

function createCalculationTableViewId() {
  return `custom:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`
}

function formatRatio(value: number | null | undefined, digits = 2) {
  const finiteValue = finiteNumber(value)
  return finiteValue == null ? '—' : formatNumber(finiteValue, digits)
}

function signedRatio(value: number | null | undefined, digits = 2) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return '—'
  }
  if (finiteValue > 0) {
    return `+${formatNumber(finiteValue, digits)}`
  }
  return formatNumber(finiteValue, digits)
}

function benchmarkMetricText(
  selected: SharedInstrumentRecord | null,
  loading: boolean,
  value: number | null | undefined,
  formatter: (input: number | null | undefined) => string = signedPercent,
) {
  if (!selected) {
    return '—'
  }
  if (loading) {
    return 'Loading'
  }
  return formatter(value)
}

function benchmarkMetricClassName(
  selected: SharedInstrumentRecord | null,
  loading: boolean,
  value: number | null | undefined,
) {
  return selected && !loading ? signedValueClass(value) : undefined
}

function buildPerformanceMetricRows(
  summary: PortfolioPerformanceSummary,
  baseCurrency: string | null,
  selectedBenchmarkInstrument: SharedInstrumentRecord | null,
  benchmarkLoading: boolean,
  comparison: PortfolioPerformanceComparisonResponse | null,
) {
  const historyReliability = selectPerformanceHistoryReliability(
    summary.history_reliability,
    comparison?.history_reliability,
  )
  const annualizedReturnEligible = annualizedReturnDisplayEligible(historyReliability)
  const comparisonReady = comparison?.status === 'ready'
  const portfolioComparisonMetrics = comparisonReady ? comparison.portfolio_metrics : null
  const benchmarkComparisonMetrics = comparisonReady ? comparison.benchmark_metrics : null
  const relativeComparisonMetrics = comparisonReady ? comparison.relative_metrics : null
  const comparisonDifferences = comparisonReady ? comparison.differences : null
  const showComparison = selectedBenchmarkInstrument != null || benchmarkLoading
  const irr = finiteNumber(summary.irr) ?? finiteNumber(summary.mwror)
  const portfolioPeriodReturn = comparisonReady ? portfolioComparisonMetrics?.period_return ?? null : summary.cumulative_twr
  const portfolioAnnualizedReturn = comparisonReady
    ? portfolioComparisonMetrics?.annualized_return ?? null
    : summary.annualized_twr
  const portfolioVolatility = comparisonReady
    ? portfolioComparisonMetrics?.annualized_volatility ?? null
    : summary.annualized_volatility
  const portfolioDownsideVolatility = comparisonReady
    ? portfolioComparisonMetrics?.annualized_downside_volatility ?? null
    : summary.annualized_downside_volatility
  const portfolioSharpe = comparisonReady ? portfolioComparisonMetrics?.sharpe_ratio ?? null : summary.sharpe_ratio
  const portfolioSortino = comparisonReady ? portfolioComparisonMetrics?.sortino_ratio ?? null : summary.sortino_ratio
  const portfolioCurrentDrawdown = comparisonReady
    ? portfolioComparisonMetrics?.current_drawdown ?? null
    : summary.current_drawdown
  const portfolioMaxDrawdown = comparisonReady
    ? portfolioComparisonMetrics?.max_drawdown ?? null
    : summary.max_drawdown
  const calmarRatio = comparisonReady ? portfolioComparisonMetrics?.calmar_ratio ?? null : summary.calmar_ratio
  const returnDifference = comparisonDifferences?.period_return ?? null
  const annualizedReturnDifference = comparisonDifferences?.annualized_return ?? null
  const volatilityDifference = comparisonDifferences?.annualized_volatility ?? null
  const downsideVolatilityDifference = comparisonDifferences?.annualized_downside_volatility ?? null
  const sharpeDifference = comparisonDifferences?.sharpe_ratio ?? null
  const sortinoDifference = comparisonDifferences?.sortino_ratio ?? null
  const calmarDifference = comparisonDifferences?.calmar_ratio ?? null
  const currentDrawdownDifference = comparisonDifferences?.current_drawdown ?? null
  const maxDrawdownDifference = comparisonDifferences?.max_drawdown ?? null

  return [
    {
      metric: 'Period TWR',
      value: signedPercent(portfolioPeriodReturn),
      valueClassName: signedValueClass(portfolioPeriodReturn),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.period_return),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkComparisonMetrics?.period_return,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      showComparison,
    },
    {
      metric: 'Annualized TWR',
      value: annualizedReturnEligible ? signedPercent(portfolioAnnualizedReturn) : 'N/A',
      valueClassName: annualizedReturnEligible ? signedValueClass(portfolioAnnualizedReturn) : 'performance-cell-muted',
      reliabilityNote: annualizedReturnEligible ? undefined : 'Requires ≥ 1 year',
      benchmark: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.annualized_return)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      benchmarkClassName: annualizedReturnEligible
        ? benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.annualized_return)
        : 'performance-cell-muted',
      difference: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, annualizedReturnDifference)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      differenceClassName: annualizedReturnEligible
        ? benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, annualizedReturnDifference)
        : 'performance-cell-muted',
      showComparison,
    },
    {
      metric: 'IRR / MWRR',
      value: annualizedReturnEligible ? signedPercent(irr) : 'N/A',
      valueClassName: annualizedReturnEligible ? signedValueClass(irr) : 'performance-cell-muted',
      reliabilityNote: annualizedReturnEligible ? undefined : 'Requires ≥ 1 year',
    },
    {
      metric: 'Total P&L',
      value: formatSignedCurrency(summary.total_pnl, baseCurrency),
      valueClassName: signedValueClass(summary.total_pnl),
    },
    {
      metric: 'Mean Daily Return',
      value: signedPercent(summary.mean_daily_return, 3),
      valueClassName: signedValueClass(summary.mean_daily_return),
    },
    {
      metric: 'Calmar Ratio',
      value: annualizedReturnEligible ? formatRatio(calmarRatio) : 'N/A',
      valueClassName: annualizedReturnEligible ? undefined : 'performance-cell-muted',
      reliabilityNote: annualizedReturnEligible ? undefined : 'Annualized-return dependent',
      benchmark: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.calmar_ratio, formatRatio)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      difference: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, calmarDifference, signedRatio)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      differenceClassName: annualizedReturnEligible
        ? benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, calmarDifference)
        : 'performance-cell-muted',
      showComparison,
    },
    {
      metric: 'Volatility',
      value: formatPercent(portfolioVolatility),
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkComparisonMetrics?.annualized_volatility,
        formatPercent,
      ),
      benchmarkClassName: undefined,
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      showComparison,
    },
    {
      metric: 'Downside Volatility',
      value: formatPercent(portfolioDownsideVolatility),
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkComparisonMetrics?.annualized_downside_volatility,
        formatPercent,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, downsideVolatilityDifference),
      differenceClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        downsideVolatilityDifference,
      ),
      showComparison,
    },
    {
      metric: 'Sharpe Ratio',
      value: formatRatio(portfolioSharpe),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.sharpe_ratio, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference),
      showComparison,
    },
    {
      metric: 'Sortino Ratio',
      value: formatRatio(portfolioSortino),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.sortino_ratio, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference),
      showComparison,
    },
    {
      metric: 'Current DD',
      value: signedPercent(portfolioCurrentDrawdown),
      valueClassName: signedValueClass(portfolioCurrentDrawdown),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.current_drawdown),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkComparisonMetrics?.current_drawdown,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      showComparison,
    },
    {
      metric: 'Max DD',
      value: signedPercent(portfolioMaxDrawdown),
      valueClassName: signedValueClass(portfolioMaxDrawdown),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.max_drawdown),
      benchmarkClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparisonMetrics?.max_drawdown),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      showComparison,
    },
    {
      metric: 'Tracking Error',
      value: formatPercent(relativeComparisonMetrics?.tracking_error),
    },
    {
      metric: 'Information Ratio',
      value: formatRatio(relativeComparisonMetrics?.information_ratio),
    },
    {
      metric: 'Beta',
      value: formatRatio(relativeComparisonMetrics?.beta),
    },
    {
      metric: 'Correlation',
      value: formatRatio(relativeComparisonMetrics?.correlation),
    },
    {
      metric: 'Upside Capture',
      value: formatPercent(relativeComparisonMetrics?.upside_capture),
    },
    {
      metric: 'Downside Capture',
      value: formatPercent(relativeComparisonMetrics?.downside_capture),
    },
    {
      metric: 'CAP Ratio',
      value: formatRatio(relativeComparisonMetrics?.capture_ratio),
    },
  ] satisfies PerformanceMetricRow[]
}

function TableStatusRow({ colSpan, label, tone = 'muted' }: { colSpan: number; label: string; tone?: 'muted' | 'error' }) {
  return (
    <tr>
      <td colSpan={colSpan} className={`table-empty-cell ${tone === 'error' ? 'table-empty-cell-error' : ''}`}>
        {label}
      </td>
    </tr>
  )
}

function MetricGrid({ rows }: { rows: PerformanceMetricRow[] }) {
  const columnLabels = ['Return', 'Risk', 'Relative']
  const columnCount = columnLabels.length
  const showComparisonColumns = rows.some((row) => row.showComparison)
  const rowsPerColumn = Math.ceil(rows.length / columnCount)
  const columnRows = Array.from({ length: columnCount }, (_, index) =>
    rows.slice(index * rowsPerColumn, (index + 1) * rowsPerColumn),
  ).filter((items) => items.length > 0)

  return (
    <div className="performance-metric-table-grid">
      {columnRows.map((items, columnIndex) => (
        <div className="performance-metric-table-shell" key={`metric-column-${columnIndex}`}>
          <div className="performance-metric-column-title">{columnLabels[columnIndex]}</div>
          <table
            className={`performance-metric-table ${
              showComparisonColumns ? 'performance-metric-table-has-comparison' : ''
            }`}
          >
            <thead>
              <tr>
                <th>Metric</th>
                <th>Portfolio</th>
                {showComparisonColumns ? <th>BM</th> : null}
                {showComparisonColumns ? <th>Diff</th> : null}
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.metric}>
                  <th scope="row">
                    <span className="performance-metric-table-name">
                      <span>
                        {row.metric}
                        {row.reliabilityNote ? (
                          <small className="performance-metric-reliability-note">{row.reliabilityNote}</small>
                        ) : null}
                      </span>
                    </span>
                  </th>
                  <td className={row.valueClassName ?? ''}>{row.value}</td>
                  {showComparisonColumns ? (
                    <td className={row.benchmarkClassName ?? ''}>{row.showComparison ? row.benchmark ?? '—' : '—'}</td>
                  ) : null}
                  {showComparisonColumns ? (
                    <td className={row.differenceClassName ?? ''}>{row.showComparison ? row.difference ?? '—' : '—'}</td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function PerformancePage() {
  const { portfolioId } = useParams<{ portfolioId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()

  const todayDate = useMemo(() => localDateIso(), [])
  const queryStartDate = validIsoDate(searchParams.get('start_date'))
  const queryEndDate = validIsoDate(searchParams.get('end_date'))
  const hasDateWindowParams = searchParams.has('start_date') || searchParams.has('end_date')
  const storedPerformanceWindow = useMemo(
    () => (!hasDateWindowParams && portfolioId ? loadPerformanceWindowSelection(portfolioId) : null),
    [hasDateWindowParams, portfolioId],
  )
  const appliedStartDate = queryStartDate || storedPerformanceWindow?.startDate || ''
  const appliedEndDate = queryEndDate || storedPerformanceWindow?.endDate || ''
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [portfolioSummaryReadyPortfolioId, setPortfolioSummaryReadyPortfolioId] = useState<string | null>(null)
  const portfolioSummarySettled = Boolean(portfolioId && portfolioSummaryReadyPortfolioId === portfolioId)
  const portfolioAsOfDate =
    portfolioSummary && portfolioSummary.portfolio_id === portfolioId ? validIsoDate(portfolioSummary.as_of_date) : ''
  const waitingForDefaultEndDate = Boolean(portfolioId && !appliedEndDate && !portfolioSummarySettled)
  const effectiveEndDate = appliedEndDate || portfolioAsOfDate || todayDate
  const defaultStartDate = useMemo(
    () => shiftIsoDate(effectiveEndDate, -(DEFAULT_PERFORMANCE_LOOKBACK_DAYS - 1)),
    [effectiveEndDate],
  )
  const effectiveStartDate = appliedStartDate || defaultStartDate

  const [workspace, setWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [calculationWorkspace, setCalculationWorkspace] = useState<PortfolioPerformanceCalculationResponse | null>(null)
  const [calculationLoading, setCalculationLoading] = useState(false)
  const [calculationError, setCalculationError] = useState<string | null>(null)
  const [calculationGroupsWorkspace, setCalculationGroupsWorkspace] =
    useState<PortfolioPerformanceCalculationGroupsResponse | null>(null)
  const [calculationGroupsLoading, setCalculationGroupsLoading] = useState(false)
  const [calculationGroupsError, setCalculationGroupsError] = useState<string | null>(null)
  const initialCalculationTableViewStore = useMemo(() => loadCalculationTableViewStore(), [])
  const initialCalculationTableViewState = useMemo(
    () => resolveCalculationTableViewState(initialCalculationTableViewStore, initialCalculationTableViewStore.activeViewId),
    [initialCalculationTableViewStore],
  )
  const [calculationGroupBy, setCalculationGroupBy] = useState<CalculationGroupByKey>(
    () => initialCalculationTableViewState.groupBy,
  )
  const [calculationGroupByOpen, setCalculationGroupByOpen] = useState(false)
  const [calculationTableViewStore, setCalculationTableViewStore] = useState<CalculationTableViewStore>(
    () => initialCalculationTableViewStore,
  )
  const [calculationTableViewStoreRemoteReady, setCalculationTableViewStoreRemoteReady] = useState(false)
  const [activeCalculationTableViewId, setActiveCalculationTableViewId] = useState(
    initialCalculationTableViewStore.activeViewId,
  )
  const [calculationColumns, setCalculationColumns] = useState<CalculationColumnKey[]>(
    () => initialCalculationTableViewState.columns,
  )
  const [calculationColumnDraft, setCalculationColumnDraft] = useState<CalculationColumnKey[]>(
    () => initialCalculationTableViewState.columns,
  )
  const [calculationTableMode, setCalculationTableMode] = useState<CalculationTableMode>(
    () => initialCalculationTableViewState.mode,
  )
  const [calculationSortField, setCalculationSortField] = useState<CalculationColumnKey | null>(
    () => initialCalculationTableViewState.sortField,
  )
  const [calculationSortDirection, setCalculationSortDirection] = useState<CalculationSortDirection>(
    () => initialCalculationTableViewState.sortDirection,
  )
  const [calculationModeDraft, setCalculationModeDraft] = useState<CalculationTableMode>(
    () => initialCalculationTableViewState.mode,
  )
  const [calculationColumnsOpen, setCalculationColumnsOpen] = useState(false)
  const calculationColumnsDialogRef = useModalDialog(
    calculationColumnsOpen,
    () => setCalculationColumnsOpen(false),
  )
  const calculationGroupByDialogRef = useModalDialog(
    calculationGroupByOpen,
    () => setCalculationGroupByOpen(false),
  )
  const [calculationColumnCategory, setCalculationColumnCategory] = useState(
    CALCULATION_COLUMN_GROUPS[0]?.label ?? 'Core',
  )
  const [calculationColumnSearch, setCalculationColumnSearch] = useState('')
  const [viewToast, setViewToast] = useState<NoticeToastMessage | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkComparison, setBenchmarkComparison] =
    useState<PortfolioPerformanceComparisonResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const defaultPlanningTaxonomy = useMemo(
    () =>
      taxonomyCatalog?.taxonomies.find(
        (taxonomy) =>
          taxonomy.taxonomy_id === taxonomyCatalog.default_planning_taxonomy_id &&
          taxonomy.planning_enabled &&
          taxonomy.status === 'active',
      ) ?? null,
    [taxonomyCatalog],
  )
  const effectiveCalculationGroupBy: CalculationGroupByKey =
    calculationGroupBy === 'taxonomy' && !defaultPlanningTaxonomy ? 'none' : calculationGroupBy
  const resolvedCalculationGroupBy: PortfolioContributionAxis =
    effectiveCalculationGroupBy === 'none' ? 'instrument' : effectiveCalculationGroupBy
  const calculationGroupByOptions = useMemo<CalculationGroupByOption[]>(
    () => [
      {
        value: 'none',
        label: 'None',
      },
      {
        value: 'instrument_type',
        label: 'Instrument Type',
      },
      {
        value: 'currency',
        label: 'Currency',
      },
      {
        value: 'account',
        label: 'Account',
      },
      {
        value: 'taxonomy',
        label: 'Taxonomy',
        disabled: !defaultPlanningTaxonomy,
      },
    ],
    [defaultPlanningTaxonomy],
  )
  const selectedCalculationGroupByOption =
    calculationGroupByOptions.find((option) => option.value === effectiveCalculationGroupBy) ??
    calculationGroupByOptions[0]
  const calculationTableViews = useMemo(
    () => getCalculationTableViews(calculationTableViewStore),
    [calculationTableViewStore],
  )
  const activeCalculationTableView = useMemo(
    () => getCalculationTableViewById(calculationTableViewStore, activeCalculationTableViewId),
    [activeCalculationTableViewId, calculationTableViewStore],
  )
  const currentCalculationTableViewState = useMemo<CalculationTableViewState>(
    () => ({
      columns: normalizeCalculationColumns(calculationColumns),
      mode: calculationTableMode,
      groupBy: calculationGroupBy,
      sortField: calculationSortField,
      sortDirection: calculationSortDirection,
    }),
    [calculationColumns, calculationGroupBy, calculationSortDirection, calculationSortField, calculationTableMode],
  )
  const calculationTableViewEdited = !calculationTableViewStatesEqual(
    currentCalculationTableViewState,
    activeCalculationTableView.state,
  )
  const visibleCalculationColumns = useMemo(
    () => normalizeCalculationColumns(calculationColumns),
    [calculationColumns],
  )
  const filteredCalculationColumns = useMemo(() => {
    const search = calculationColumnSearch.trim().toLowerCase()
    const groups = search
      ? CALCULATION_COLUMN_GROUPS
      : CALCULATION_COLUMN_GROUPS.filter((group) => group.label === calculationColumnCategory)
    return groups.flatMap((group) =>
      group.columns
        .filter((column) => {
          if (!search) {
            return true
          }
          return (
            column.toLowerCase().includes(search) ||
            CALCULATION_COLUMN_LABELS[column].toLowerCase().includes(search) ||
            group.label.toLowerCase().includes(search)
          )
        })
        .map((column) => ({ column, groupLabel: group.label })),
    )
  }, [calculationColumnCategory, calculationColumnSearch])

  function handleCalculationGroupByChange(value: CalculationGroupByKey) {
    setCalculationGroupBy(value)
    setCalculationGroupByOpen(false)
  }

  useEffect(() => {
    if (!portfolioId) {
      setPortfolioSummary(null)
      setPortfolioSummaryReadyPortfolioId(null)
      return
    }

    let cancelled = false
    setPortfolioSummaryReadyPortfolioId(null)
    getWorkspaceSummaryForPortfolio(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setPortfolioSummary(response)
          setPortfolioSummaryReadyPortfolioId(portfolioId)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPortfolioSummary(null)
          setPortfolioSummaryReadyPortfolioId(portfolioId)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  function handleCalculationSort(column: CalculationColumnKey) {
    let nextSortField: CalculationColumnKey | null = column
    let nextSortDirection: CalculationSortDirection = 'asc'
    if (calculationSortField === column && calculationSortDirection === 'asc') {
      nextSortDirection = 'desc'
    } else if (calculationSortField === column && calculationSortDirection === 'desc') {
      nextSortField = null
      nextSortDirection = 'asc'
    }
    setCalculationSortField(nextSortField)
    setCalculationSortDirection(nextSortDirection)
  }

  function applyCalculationTableViewState(state: CalculationTableViewState) {
    const normalized = normalizeCalculationTableViewState(state)
    setCalculationColumns(normalized.columns)
    setCalculationColumnDraft(normalized.columns)
    setCalculationTableMode(normalized.mode)
    setCalculationModeDraft(normalized.mode)
    setCalculationGroupBy(normalized.groupBy)
    setCalculationSortField(normalized.sortField)
    setCalculationSortDirection(normalized.sortDirection)
  }

  function handleSelectCalculationTableView(viewId: string) {
    const nextView = getCalculationTableViewById(calculationTableViewStore, viewId)
    setActiveCalculationTableViewId(nextView.id)
    setCalculationTableViewStore((current) => ({ ...current, activeViewId: nextView.id }))
    applyCalculationTableViewState(resolveCalculationTableViewState(calculationTableViewStore, nextView.id))
  }

  function handleSaveCalculationTableView() {
    const timestamp = new Date().toISOString()
    setCalculationTableViewStore((current) => ({
      ...current,
      activeViewId: activeCalculationTableViewId,
      views: current.views.map((view) =>
        view.id === activeCalculationTableViewId
          ? {
              ...view,
              state: currentCalculationTableViewState,
              updatedAt: timestamp,
            }
          : view,
      ),
    }))
    setViewToast({ id: Date.now(), message: 'View updated.', tone: 'success' })
  }

  function handleSaveCalculationTableViewAs(name: string, description: string | null) {
    const timestamp = new Date().toISOString()
    const viewId = createCalculationTableViewId()
    const nextView: CalculationTableView = {
      id: viewId,
      name,
      description,
      readonly: false,
      createdAt: timestamp,
      updatedAt: timestamp,
      state: currentCalculationTableViewState,
    }
    setCalculationTableViewStore((current) => ({
      ...current,
      activeViewId: viewId,
      views: [...current.views, nextView],
    }))
    setActiveCalculationTableViewId(viewId)
    setViewToast({ id: Date.now(), message: `View saved as "${name}".`, tone: 'success' })
  }

  function handleDeleteCalculationTableView(viewId: string) {
    const targetView = getCalculationTableViewById(calculationTableViewStore, viewId)
    if (targetView.readonly) {
      return
    }
    const fallbackView = SYSTEM_CALCULATION_TABLE_VIEWS[0]
    const deletingActiveView = targetView.id === activeCalculationTableViewId
    setCalculationTableViewStore((current) => ({
      ...current,
      activeViewId: deletingActiveView ? fallbackView.id : current.activeViewId,
      views: current.views.filter((view) => view.id !== targetView.id),
    }))
    if (deletingActiveView) {
      setActiveCalculationTableViewId(fallbackView.id)
      applyCalculationTableViewState(fallbackView.state)
    }
  }

  function handleCalculationColumnDraftToggle(column: CalculationColumnKey, checked: boolean) {
    setCalculationColumnDraft((current) =>
      normalizeCalculationColumns(checked ? [...current, column] : current.filter((item) => item !== column)),
    )
  }

  useEffect(() => {
    if (!portfolioId) {
      setCalculationTableViewStoreRemoteReady(false)
      return
    }

    let cancelled = false
    setCalculationTableViewStoreRemoteReady(false)
    getPortfolioTableViewStore<CalculationTableViewStore>(portfolioId, 'performance_calculation')
      .then((response) => {
        if (cancelled) {
          return
        }
        const nextStore = response.store
          ? normalizeCalculationTableViewStore(response.store)
          : normalizeCalculationTableViewStore(loadCalculationTableViewStore())
        setCalculationTableViewStore(nextStore)
        setActiveCalculationTableViewId(nextStore.activeViewId)
        applyCalculationTableViewState(resolveCalculationTableViewState(nextStore, nextStore.activeViewId))
        setCalculationTableViewStoreRemoteReady(true)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          console.warn('Failed to load persisted calculation views.', requestError)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    saveCalculationTableViewStore(calculationTableViewStore)
    if (!portfolioId || !calculationTableViewStoreRemoteReady) {
      return
    }
    savePortfolioTableViewStore(portfolioId, 'performance_calculation', calculationTableViewStore).catch(
      (requestError: unknown) => {
        console.warn('Failed to persist calculation views.', requestError)
      },
    )
  }, [calculationTableViewStore, calculationTableViewStoreRemoteReady, portfolioId])

  useEffect(() => {
    if (!portfolioId || waitingForDefaultEndDate) {
      setWorkspace(null)
      setLoading(false)
      setError(null)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)
    getPortfolioPerformance(portfolioId, { start_date: effectiveStartDate, end_date: effectiveEndDate })
      .then((response) => {
        if (!cancelled) {
          setWorkspace(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setWorkspace(null)
          setError(requestError instanceof Error ? requestError.message : 'Failed to load performance workspace.')
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
  }, [portfolioId, effectiveStartDate, effectiveEndDate, waitingForDefaultEndDate])

  useEffect(() => {
    if (!portfolioId || waitingForDefaultEndDate) {
      setCalculationWorkspace(null)
      setCalculationLoading(false)
      setCalculationError(null)
      return
    }

    let cancelled = false
    setCalculationLoading(true)
    setCalculationError(null)
    getPortfolioPerformanceCalculation(portfolioId, { start_date: effectiveStartDate, end_date: effectiveEndDate })
      .then((response) => {
        if (!cancelled) {
          setCalculationWorkspace(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setCalculationWorkspace(null)
          setCalculationError(requestError instanceof Error ? requestError.message : 'Failed to load period calculation.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setCalculationLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, effectiveStartDate, effectiveEndDate, waitingForDefaultEndDate])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      setTaxonomyCatalog(null)
      return
    }

    let cancelled = false
    Promise.allSettled([getPortfolioInstruments(portfolioId), getPortfolioTaxonomyCatalog(portfolioId)])
      .then(([instrumentResult, taxonomyResult]) => {
        if (cancelled) {
          return
        }
        if (instrumentResult.status === 'fulfilled') {
          setBenchmarkInstruments(instrumentResult.value.instruments)
        } else {
          setBenchmarkInstruments([])
        }
        if (taxonomyResult.status === 'fulfilled') {
          setTaxonomyCatalog(taxonomyResult.value)
        } else {
          setTaxonomyCatalog(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId || waitingForDefaultEndDate) {
      setCalculationGroupsWorkspace(null)
      setCalculationGroupsLoading(false)
      setCalculationGroupsError(null)
      return
    }

    let cancelled = false
    setCalculationGroupsLoading(true)
    setCalculationGroupsError(null)
    getPortfolioPerformanceCalculationGroups(portfolioId, {
      start_date: effectiveStartDate,
      end_date: effectiveEndDate,
      axis: resolvedCalculationGroupBy,
      taxonomy_id:
        resolvedCalculationGroupBy === 'taxonomy' ? defaultPlanningTaxonomy?.taxonomy_id ?? undefined : undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setCalculationGroupsWorkspace(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setCalculationGroupsWorkspace(null)
          setCalculationGroupsError(
            requestError instanceof Error ? requestError.message : 'Failed to load calculation groups.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setCalculationGroupsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    portfolioId,
    effectiveStartDate,
    effectiveEndDate,
    resolvedCalculationGroupBy,
    defaultPlanningTaxonomy?.taxonomy_id,
    waitingForDefaultEndDate,
  ])

  useEffect(() => {
    if (!portfolioId || !benchmarkInstrumentId || waitingForDefaultEndDate) {
      setBenchmarkComparison(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)
    setBenchmarkComparison(null)

    getPortfolioPerformanceComparison(portfolioId, {
      benchmark_instrument_id: benchmarkInstrumentId,
      start_date: effectiveStartDate,
      end_date: effectiveEndDate,
      as_of_date: effectiveEndDate,
    })
      .then((response) => {
        if (!cancelled) {
          setBenchmarkComparison(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setBenchmarkComparison(null)
          setBenchmarkError(requestError instanceof Error ? requestError.message : 'Failed to load benchmark comparison.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [benchmarkInstrumentId, effectiveEndDate, effectiveStartDate, portfolioId, waitingForDefaultEndDate])

  function updateWindowParams(nextStartDate: string | null, nextEndDate: string | null) {
    const normalizedStartDate = validIsoDate(nextStartDate)
    const normalizedEndDate = validIsoDate(nextEndDate)
    const nextParams = new URLSearchParams(searchParams)
    if (normalizedStartDate) {
      nextParams.set('start_date', normalizedStartDate)
    } else {
      nextParams.delete('start_date')
    }
    if (normalizedEndDate) {
      nextParams.set('end_date', normalizedEndDate)
    } else {
      nextParams.delete('end_date')
    }
    if (portfolioId) {
      savePerformanceWindowSelection(portfolioId, normalizedStartDate, normalizedEndDate)
    }
    setSearchParams(nextParams)
  }

  const summary = workspace?.summary ?? null
  const baseCurrency =
    workspace?.base_currency ?? calculationWorkspace?.base_currency ?? calculationGroupsWorkspace?.base_currency ?? null
  const periodLabel =
    summary?.start_date && summary.end_date
      ? `${summary.start_date} to ${summary.end_date}`
      : `${effectiveStartDate} to ${effectiveEndDate}`
  const performanceHistoryReliability = selectPerformanceHistoryReliability(
    summary?.history_reliability,
    benchmarkComparison?.history_reliability,
  )
  const performanceMetricsMeta = performanceHistoryReliability?.sample_label ?? periodLabel

  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId) ?? null
  const metricRows = useMemo(
    () =>
      summary
        ? buildPerformanceMetricRows(
            summary,
            baseCurrency,
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkComparison,
          )
        : [],
    [summary, baseCurrency, selectedBenchmarkInstrument, benchmarkLoading, benchmarkComparison],
  )
  const calculationRows = useMemo(() => {
    if (!calculationGroupsWorkspace) {
      return []
    }
    const rows: CalculationGroupRow[] = calculationGroupsWorkspace.groups.map((group) => {
      const children = (group.children ?? []).slice().sort((left, right) => {
        if (calculationSortField) {
          return (
            compareCalculationRowsByColumn(left, right, calculationSortField, calculationSortDirection) ||
            defaultCalculationChildCompare(left, right)
          )
        }
        return defaultCalculationChildCompare(left, right)
      })
      return { ...group, children }
    })
    return rows.sort((left, right) => {
      if (calculationSortField) {
        return (
          compareCalculationRowsByColumn(left, right, calculationSortField, calculationSortDirection) ||
          defaultCalculationGroupCompare(left, right, calculationTableMode)
        )
      }
      return defaultCalculationGroupCompare(left, right, calculationTableMode)
    })
  }, [calculationGroupsWorkspace, calculationSortDirection, calculationSortField, calculationTableMode])

  const calculationSummary = calculationWorkspace?.summary ?? null
  const calculationGroupsSummary = calculationGroupsWorkspace?.summary ?? null
  const initialValue = calculationSummary?.initial_value ?? summary?.start_nav ?? null
  const finalValue = calculationSummary?.final_value ?? summary?.end_nav ?? null
  const portfolioRealizedGain = calculationSummary?.realized_capital_gains ?? summary?.realized_pnl ?? null
  const portfolioUnrealizedGain = calculationSummary?.unrealized_capital_gains ?? null
  const portfolioIncome = calculationSummary?.earnings ?? summary?.income_cash_amount ?? null
  const portfolioFxPnl = sumNullable(calculationSummary?.cash_currency_gains, calculationSummary?.instrument_currency_gains)
  const portfolioFees = expenseImpact(calculationSummary?.fees)
  const portfolioTaxes = expenseImpact(calculationSummary?.taxes)
  const portfolioPeriodPnl = calculationSummary?.delta ?? summary?.delta ?? summary?.total_pnl ?? null
  const portfolioContribution = calculationGroupsSummary?.total_period_contribution ?? summary?.cumulative_twr ?? null
  const contributionResidual = calculationGroupsSummary?.contribution_residual ?? null
  const showContributionResidual = contributionResidual != null && Math.abs(contributionResidual) > 0.0000005
  const calculationGroupLabel = calculationAxisLabel(resolvedCalculationGroupBy)
  const calculationChildRowCount = calculationRows.reduce((total, row) => total + (row.children?.length ?? 0), 0)
  const calculationRiskStatusLabel = calculationGroupsSummary?.risk_frequency_status_label ?? null
  const calculationMeta =
    !calculationGroupsWorkspace && calculationGroupsLoading
      ? `${periodLabel} · Loading`
      : `${periodLabel} · ${formatNumber(calculationRows.length, 0)} ${calculationAxisCountLabel(
          resolvedCalculationGroupBy,
        )}${calculationChildRowCount ? ` · ${formatNumber(calculationChildRowCount, 0)} instruments/cash` : ''}${
          calculationRiskStatusLabel ? ` · ${calculationRiskStatusLabel}` : ''
        }`
  const riskMetricsAvailable = realizedRiskMetricsAvailable(
    calculationGroupsSummary?.risk_return_observation_count,
    calculationGroupsSummary?.annualized_volatility,
  )
  const riskContributionResidual = realizedRiskContributionResidual(
    calculationRows.map((row) => finiteNumber(row.realized_risk_contribution)),
    riskMetricsAvailable,
  )
  const showRiskContributionResidual =
    calculationTableMode === 'risk_attribution' &&
    visibleCalculationColumns.includes('risk_contribution') &&
    riskContributionResidual != null &&
    Math.abs(riskContributionResidual) > 0.0005
  const calculationTableRows = useMemo<CalculationTableRow[]>(() => {
    if (!calculationGroupsWorkspace) {
      return []
    }

    if (calculationTableMode === 'risk_attribution') {
      const rows: CalculationTableRow[] = []
      calculationRows.forEach((row) => {
        rows.push({
          kind: 'group',
          key: `risk:${row.group_key}`,
          className: row.children?.length ? 'performance-calculation-group-row' : undefined,
          row,
        })
        ;(row.children ?? []).forEach((child) => {
          rows.push({
            kind: 'child',
            key: `risk-child:${child.parent_group_key}:${child.item_kind}:${child.item_key}`,
            className: 'performance-calculation-child-row',
            row: child,
          })
        })
      })
      rows.push({
        kind: 'synthetic',
        key: 'portfolio-total',
        className: 'performance-calculation-total-row',
        label: 'Portfolio Total',
        syntheticKind: 'portfolio_total',
      })
      if (showRiskContributionResidual) {
        rows.push({
          kind: 'synthetic',
          key: 'risk-contribution-residual',
          className: 'performance-calculation-residual-row',
          label: 'Risk Contribution Residual',
          syntheticKind: 'risk_contribution_residual',
        })
      }
      return rows
    }

    const rows: CalculationTableRow[] = [
      {
        kind: 'synthetic',
        key: 'initial-value',
        className: 'performance-calculation-boundary-row',
        label: 'Initial Value',
        syntheticKind: 'initial',
      },
    ]
    calculationRows.forEach((row) => {
      rows.push({
        kind: 'group',
        key: `group:${row.group_key}`,
        className: row.children?.length ? 'performance-calculation-group-row' : undefined,
        row,
      })
      ;(row.children ?? []).forEach((child) => {
        rows.push({
          kind: 'child',
          key: `child:${child.parent_group_key}:${child.item_kind}:${child.item_key}`,
          className: 'performance-calculation-child-row',
          row: child,
        })
      })
    })
    rows.push(
      {
        kind: 'synthetic',
        key: 'deposits',
        className: 'performance-calculation-external-row',
        label: 'Deposits',
        syntheticKind: 'deposits',
      },
      {
        kind: 'synthetic',
        key: 'withdrawals',
        className: 'performance-calculation-external-row',
        label: 'Withdrawals',
        syntheticKind: 'withdrawals',
      },
      {
        kind: 'synthetic',
        key: 'portfolio-total',
        className: 'performance-calculation-total-row',
        label: 'Portfolio Total',
        syntheticKind: 'portfolio_total',
      },
    )
    if (showContributionResidual) {
      rows.push({
        kind: 'synthetic',
        key: 'contribution-residual',
        className: 'performance-calculation-residual-row',
        label: 'Contribution Residual',
        syntheticKind: 'contribution_residual',
      })
    }
    rows.push({
      kind: 'synthetic',
      key: 'final-value',
      className: 'performance-calculation-boundary-row performance-calculation-final-row',
      label: 'Final Value',
      syntheticKind: 'final',
    })
    return rows
  }, [
    calculationGroupsWorkspace,
    calculationRows,
    calculationTableMode,
    showContributionResidual,
    showRiskContributionResidual,
  ])

  function calculationTableRowLabel(row: CalculationTableRow) {
    if (row.kind === 'synthetic') {
      return row.label
    }
    return calculationDisplayLabel(row.row)
  }

  function calculationTableMetricValue(row: CalculationTableRow, column: CalculationColumnKey): number | null {
    if (column === 'line') {
      return null
    }

    if (row.kind === 'synthetic') {
      switch (row.syntheticKind) {
        case 'initial':
          return column === 'start_value' ? finiteNumber(initialValue) : null
        case 'final':
          return column === 'end_value' ? finiteNumber(finalValue) : null
        case 'deposits':
          return column === 'pnl_flow' ? finiteNumber(calculationSummary?.deposits) : null
        case 'withdrawals':
          return column === 'pnl_flow' ? finiteNumber(expenseImpact(calculationSummary?.withdrawals)) : null
        case 'contribution_residual':
          return column === 'return_contribution' ? finiteNumber(contributionResidual) : null
        case 'risk_contribution_residual':
          return column === 'risk_contribution' ? finiteNumber(riskContributionResidual) : null
        case 'portfolio_total':
          switch (column) {
            case 'pnl_flow':
              return finiteNumber(portfolioPeriodPnl)
            case 'start_value':
              return finiteNumber(initialValue)
            case 'end_value':
              return finiteNumber(finalValue)
            case 'begin_weight':
            case 'avg_weight':
            case 'end_weight':
              return 1
            case 'realized_gain':
              return finiteNumber(portfolioRealizedGain)
            case 'unrealized_gain':
              return finiteNumber(portfolioUnrealizedGain)
            case 'income':
              return finiteNumber(portfolioIncome)
            case 'fees':
              return finiteNumber(portfolioFees)
            case 'taxes':
              return finiteNumber(portfolioTaxes)
            case 'fx_pnl':
              return finiteNumber(portfolioFxPnl)
            case 'period_return':
              return finiteNumber(summary?.cumulative_twr)
            case 'return_contribution':
              return finiteNumber(portfolioContribution)
            case 'own_vol':
              return finiteNumber(calculationGroupsSummary?.annualized_volatility ?? summary?.annualized_volatility)
            case 'own_sharpe':
              return finiteNumber(calculationGroupsSummary?.sharpe_ratio ?? summary?.sharpe_ratio)
            case 'own_corr':
            case 'beta':
            case 'risk_contribution':
              return riskMetricsAvailable ? 1 : null
            case 'observations':
              return finiteNumber(
                calculationGroupsSummary?.risk_return_observation_count ?? summary?.risk_return_observation_count,
              )
            default:
              return null
          }
        default:
          return null
      }
    }

    return calculationDisplayMetricValue(row.row, column)
  }

  function calculationTableCellClassName(row: CalculationTableRow, column: CalculationColumnKey) {
    if (column === 'line') {
      return row.kind === 'child' ? 'performance-line-label performance-line-label-child' : 'performance-line-label'
    }
    const value = calculationTableMetricValue(row, column)
    return `performance-cell-number${SIGNED_CALCULATION_COLUMN_KEYS.has(column) ? ` ${signedValueClass(value)}` : ''}`
  }

  function renderCalculationTableCellValue(row: CalculationTableRow, column: CalculationColumnKey): ReactNode {
    if (column === 'line') {
      return calculationTableRowLabel(row)
    }
    const value = calculationTableMetricValue(row, column)
    switch (column) {
      case 'pnl_flow':
      case 'realized_gain':
      case 'unrealized_gain':
      case 'income':
      case 'fees':
      case 'taxes':
      case 'fx_pnl':
        return formatSignedCurrency(value, baseCurrency)
      case 'start_value':
      case 'end_value':
        return formatCurrency(value, baseCurrency)
      case 'begin_weight':
      case 'avg_weight':
      case 'end_weight':
      case 'own_vol':
        return formatPercent(value)
      case 'period_return':
      case 'return_contribution':
      case 'risk_contribution':
        return signedPercent(value)
      case 'own_sharpe':
        return formatRatio(value)
      case 'own_corr':
      case 'beta':
        return signedRatio(value)
      case 'observations':
        return value == null ? '—' : formatNumber(value, 0)
      default:
        return '—'
    }
  }

  function renderCalculationSortHeader(column: CalculationColumnKey) {
    const active = calculationSortField === column
    const nextSortLabel = !active ? 'ascending' : calculationSortDirection === 'asc' ? 'descending' : 'no sorting'
    return (
      <button
        type="button"
        className={`holdings-th-label holdings-th-sortable ${active ? 'holdings-th-sortable-active' : ''}`}
        onClick={() => handleCalculationSort(column)}
        title={`Sort ${CALCULATION_COLUMN_LABELS[column]}: ${nextSortLabel}`}
        aria-label={`Sort ${CALCULATION_COLUMN_LABELS[column]}: ${nextSortLabel}`}
      >
        <span>{CALCULATION_COLUMN_LABELS[column]}</span>
        {active ? (
          <span className="holdings-sort-indicator">{calculationSortDirection === 'asc' ? '↑' : '↓'}</span>
        ) : null}
      </button>
    )
  }

  function renderCalculationTableRow(row: CalculationTableRow) {
    return (
      <tr key={row.key} className={row.className}>
        {visibleCalculationColumns.map((column) => {
          const className = calculationTableCellClassName(row, column)
          return column === 'line' ? (
            <th key={column} scope="row" className={className}>
              {renderCalculationTableCellValue(row, column)}
            </th>
          ) : (
            <td key={column} className={className}>
              {renderCalculationTableCellValue(row, column)}
            </td>
          )
        })}
      </tr>
    )
  }

  function handleDownloadCalculation(format: TableExportFormat) {
    if (!portfolioId || !calculationGroupsWorkspace) {
      return
    }

    const header = visibleCalculationColumns.map((column) => CALCULATION_COLUMN_LABELS[column])
    const rows: TableCell[][] = [
      header,
      ...calculationTableRows.map((row) =>
        visibleCalculationColumns.map((column) =>
          column === 'line' ? calculationTableRowLabel(row) : csvNumber(calculationTableMetricValue(row, column)),
        ),
      ),
    ]
    const viewSlug = activeCalculationTableView.name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
    downloadTable(
      `performance-calculation-${viewSlug || 'view'}-${portfolioId}-${effectiveStartDate}-${effectiveEndDate}-${effectiveCalculationGroupBy}`,
      rows,
      format,
      'Calculation',
    )
  }

  return (
    <>
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <PortfolioWorkspaceLayout activeSection="Performance" toolbarLabel="View: Performance">
      <section className="portfolio-detail-surface performance-surface">
        <div className="transaction-filter-bar performance-window-bar">
          <div className="performance-filter-group performance-window-group">
            <label>
              <span>Start Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={effectiveStartDate}
                onChange={(event) => updateWindowParams(event.target.value || null, effectiveEndDate || null)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={effectiveEndDate}
                onChange={(event) => updateWindowParams(effectiveStartDate || null, event.target.value || null)}
              />
            </label>
          </div>
          <BenchmarkSearchBox
            className="performance-benchmark-search"
            instruments={benchmarkInstruments}
            selectedInstrumentId={benchmarkInstrumentId}
            searchValue={benchmarkSearch}
            onSearchChange={setBenchmarkSearch}
            onSelectInstrument={(instrument) => {
              setBenchmarkInstrumentId(instrument.instrument_id)
              setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
              setBenchmarkComparison(null)
              setBenchmarkError(null)
            }}
            onClear={() => {
              setBenchmarkInstrumentId('')
              setBenchmarkSearch('')
              setBenchmarkComparison(null)
              setBenchmarkError(null)
            }}
            placeholder="Compare benchmark..."
          />
        </div>

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        {benchmarkError ? <div className="inline-notice inline-notice-error">{benchmarkError}</div> : null}
        {benchmarkComparison ? (
          <div
            className={`performance-benchmark-basis-status ${
              benchmarkComparison.status === 'ready'
                ? 'performance-benchmark-basis-status-comparable'
                : 'performance-benchmark-basis-status-fallback'
            }`}
          >
            <span>Benchmark basis</span>
            <code>{benchmarkComparison.coverage.benchmark_quote_basis ?? 'unavailable'}</code>
            <span>
              {benchmarkComparison.status === 'ready'
                ? `Canonical total return · ${benchmarkComparison.coverage.aligned_observation_count}/${benchmarkComparison.coverage.required_observation_count} aligned`
                : 'Comparison unavailable'}
            </span>
          </div>
        ) : null}
        {!benchmarkError && benchmarkComparison?.status === 'unavailable' ? (
          <div className="inline-notice inline-notice-warning performance-benchmark-basis-warning" role="status">
            Relative metrics withheld: {benchmarkComparison.unavailable_reasons.join(', ')}.
          </div>
        ) : null}
        <QualityWarningsNotice warnings={summary?.quality_warnings} />
        {(loading || waitingForDefaultEndDate) && !workspace ? <CalculationStatus /> : null}
        {loading && workspace ? <CalculationStatus /> : null}
        {!waitingForDefaultEndDate && !loading && !workspace && !error ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {workspace && summary ? (
          <div className="performance-section-stack">
            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Return &amp; Risk Metrics</div>
                  <div className="portfolio-detail-meta">{performanceMetricsMeta}</div>
                </div>
              </div>
              {performanceHistoryReliability?.annualization_message ? (
                <div className="performance-history-reliability-warning" role="status">
                  <strong>Annualization eligibility.</strong>
                  <span>{performanceHistoryReliability.annualization_message}</span>
                </div>
              ) : null}
              <MetricGrid rows={metricRows} />
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar performance-calculation-toolbar">
                <div className="performance-calculation-toolbar-main">
                  <div>
                    <div className="panel-title">Calculation</div>
                    <div className="portfolio-detail-meta">{calculationMeta}</div>
                  </div>
                  <div className="transaction-filter-actions holdings-filter-actions performance-calculation-actions">
                    <PortfolioTableViewControls
                      views={calculationTableViews}
                      activeViewId={activeCalculationTableViewId}
                      edited={calculationTableViewEdited}
                      canSave
                      canDelete
                      onSelect={handleSelectCalculationTableView}
                      onSave={handleSaveCalculationTableView}
                      onSaveAs={handleSaveCalculationTableViewAs}
                      onDelete={handleDeleteCalculationTableView}
                    />
                    <button
                      type="button"
                      className={`holdings-toolbar-button ${calculationTableViewEdited ? 'holdings-toolbar-button-active' : ''}`}
                      onClick={() => {
                        setCalculationColumnDraft(calculationColumns)
                        setCalculationModeDraft(calculationTableMode)
                        setCalculationColumnsOpen(true)
                      }}
                    >
                      Data &amp; Columns
                    </button>
                    <button
                      type="button"
                      className="holdings-toolbar-button"
                      onClick={() => setCalculationGroupByOpen(true)}
                    >
                      Group By{'\u00A0: '}
                      {selectedCalculationGroupByOption.label}
                    </button>
                    <DownloadFormatMenu
                      wrapperClassName="portfolio-download-menu"
                      buttonClassName="holdings-toolbar-button"
                      menuClassName="portfolio-download-menu-list"
                      itemClassName="portfolio-download-menu-item"
                      disabled={!calculationGroupsWorkspace || calculationGroupsLoading}
                      onSelect={handleDownloadCalculation}
                    />
                  </div>
                </div>
              </div>
              {calculationError ? <div className="inline-notice inline-notice-error">{calculationError}</div> : null}
              {calculationGroupsError ? (
                <div className="inline-notice inline-notice-error">{calculationGroupsError}</div>
              ) : null}
              {calculationLoading || calculationGroupsLoading ? <CalculationStatus /> : null}
              <div className="table-shell">
                <table className="transactions-table performance-calculation-table">
                  <thead>
                    <tr>
                      {visibleCalculationColumns.map((column) => (
                        <th
                          key={column}
                          className={column === 'line' ? undefined : 'performance-cell-number'}
                          aria-sort={
                            calculationSortField === column
                              ? calculationSortDirection === 'asc'
                                ? 'ascending'
                                : 'descending'
                              : 'none'
                          }
                        >
                          {renderCalculationSortHeader(column)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {calculationTableRows.length ? (
                      calculationTableRows.map((row) => renderCalculationTableRow(row))
                    ) : calculationGroupsLoading ? (
                      <TableStatusRow
                        colSpan={visibleCalculationColumns.length}
                        label="Loading"
                      />
                    ) : calculationGroupsError ? (
                      <TableStatusRow colSpan={visibleCalculationColumns.length} label={calculationGroupsError} tone="error" />
                    ) : (
                      <TableStatusRow
                        colSpan={visibleCalculationColumns.length}
                        label="No rows."
                      />
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}
      </section>
      {calculationColumnsOpen ? (
        <div className="holdings-modal-backdrop" onClick={() => setCalculationColumnsOpen(false)}>
          <div
            ref={calculationColumnsDialogRef}
            className="holdings-modal holdings-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose calculation columns"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Data &amp; Columns</div>
                <div className="section-heading">Manage Calculation Table View</div>
              </div>
              <button type="button" onClick={() => setCalculationColumnsOpen(false)}>
                Close
              </button>
            </div>

            <div className="performance-calculation-mode-panel">
              <button
                type="button"
                className={`holdings-groupby-option ${
                  calculationModeDraft === 'risk_attribution' ? 'holdings-groupby-option-active' : ''
                }`}
                onClick={() => setCalculationModeDraft('risk_attribution')}
              >
                <span>Group Attribution Rows</span>
              </button>
              <button
                type="button"
                className={`holdings-groupby-option ${
                  calculationModeDraft === 'calculation' ? 'holdings-groupby-option-active' : ''
                }`}
                onClick={() => setCalculationModeDraft('calculation')}
              >
                <span>Calculation Ledger Rows</span>
              </button>
            </div>

            <div className="holdings-modal-search">
              <input
                className="holdings-modal-search-input"
                placeholder="Search fields"
                value={calculationColumnSearch}
                onChange={(event) => setCalculationColumnSearch(event.target.value)}
              />
            </div>

            <div className="holdings-modal-grid">
              <div className="holdings-modal-categories">
                {CALCULATION_COLUMN_GROUPS.map((group) => (
                  <button
                    type="button"
                    className={
                      group.label === calculationColumnCategory
                        ? 'holdings-category-item holdings-category-item-active'
                        : 'holdings-category-item'
                    }
                    key={group.label}
                    onClick={() => setCalculationColumnCategory(group.label)}
                  >
                    {group.label}
                  </button>
                ))}
              </div>

              <div className="holdings-modal-fields">
                {filteredCalculationColumns.length ? (
                  filteredCalculationColumns.map(({ column, groupLabel }) => {
                    const locked = column === LOCKED_CALCULATION_COLUMN
                    return (
                      <label className="holdings-field-item" key={`${groupLabel}:${column}`}>
                        <input
                          type="checkbox"
                          checked={calculationColumnDraft.includes(column)}
                          disabled={locked}
                          onChange={(event) => handleCalculationColumnDraftToggle(column, event.target.checked)}
                        />
                        <div>
                          <div className="holdings-field-label">{CALCULATION_COLUMN_LABELS[column]}</div>
                          <div className="holdings-field-meta">
                            {column}
                            {calculationColumnSearch.trim() ? ` · ${groupLabel}` : ''}
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
                  setCalculationColumnDraft(calculationColumns)
                  setCalculationModeDraft(calculationTableMode)
                  setCalculationColumnsOpen(false)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  setCalculationColumns(normalizeCalculationColumns(calculationColumnDraft))
                  setCalculationTableMode(calculationModeDraft)
                  setCalculationColumnsOpen(false)
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {calculationGroupByOpen ? (
        <div className="holdings-modal-backdrop" onClick={() => setCalculationGroupByOpen(false)}>
          <div
            ref={calculationGroupByDialogRef}
            className="holdings-modal holdings-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Group performance calculations"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Group By</div>
                <div className="section-heading">Grouping</div>
              </div>
              <button type="button" onClick={() => setCalculationGroupByOpen(false)}>
                Close
              </button>
            </div>
            <div className="holdings-modal-body holdings-groupby-list">
              {calculationGroupByOptions.map((option) => (
                <button
                  type="button"
                  className={`holdings-groupby-option ${
                    option.value === effectiveCalculationGroupBy ? 'holdings-groupby-option-active' : ''
                  }`}
                  key={option.value}
                  onClick={() => handleCalculationGroupByChange(option.value)}
                  disabled={option.disabled}
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

export default PerformancePage
