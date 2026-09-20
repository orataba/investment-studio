import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import InfoHint from '../components/InfoHint'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import { downloadTable, type TableCell, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { SerialTaskQueue } from '../../../../../packages/ui/src/serialTaskQueue'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import {
  getPortfolioInstrumentPriceChart,
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioTableViewStore,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  savePortfolioTableViewStore,
  type PortfolioInstrumentPriceChartPoint,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioContributionAxis,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCalculationGroupsResponse,
  type PortfolioPerformanceSummary,
  type PortfolioWorkspaceSummary,
  type SharedInstrumentRecord,
  type PortfolioTaxonomyCatalogResponse,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import { assessBenchmarkComparisonGuard } from '../lib/benchmarkComparisonGuard'
import { buildPerformanceHistoryReliability } from '../lib/performanceHistoryReliability'
import usePerformanceResource from '../hooks/usePerformanceResource'
import {
  buildPerformanceWindowFilters,
  localDateIso,
  normalizePerformanceWindowSelection,
  PERFORMANCE_PERIOD_PRESETS,
  performancePresetStartDate,
  performanceWindowError,
  resolvePerformanceWindow,
  shiftIsoDate,
  validIsoDate,
  type PerformancePeriodPreset,
  type PerformanceWindowMode,
  type PerformanceWindowSelection,
} from '../lib/performanceWindow'
import {
  realizedRiskContributionResidual,
  realizedRiskEstimateIsLowSample,
  realizedRiskMetricsAvailable,
} from '../lib/performanceRiskReliability'

const DEFAULT_PERFORMANCE_LOOKBACK_DAYS = 30
const DAYS_PER_YEAR = 365.25

type CalculationGroupRow = PortfolioPerformanceCalculationGroupsResponse['groups'][number]
type CalculationGroupChildRow = CalculationGroupRow['children'][number]
type CalculationDisplayRow = CalculationGroupRow | CalculationGroupChildRow

type PerformanceMetricRow = {
  section: 'Return' | 'Risk' | 'Relative'
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

type DatedReturn = {
  date: string
  value: number
}

type BenchmarkPeriodMetrics = {
  periodReturn: number | null
  annualizedReturn: number | null
  annualizedVolatility: number | null
  annualizedDownsideVolatility: number | null
  sharpe: number | null
  sortino: number | null
  currentDrawdown: number | null
  maxDrawdown: number | null
  dailyReturns: DatedReturn[]
}

type RelativePerformanceMetrics = {
  informationRatio: number | null
  trackingError: number | null
  beta: number | null
  upsideCapture: number | null
  downsideCapture: number | null
  captureRatio: number | null
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
  | 'pending_settlement_fx'
  | 'period_return'
  | 'return_contribution'
  | 'arithmetic_return_contribution'
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

type CalculationSyntheticRowKind =
  | 'initial'
  | 'final'
  | 'deposits'
  | 'withdrawals'
  | 'portfolio_total'
  | 'contribution_residual'
  | 'twr_linking_difference'
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
const PERFORMANCE_WINDOW_STORAGE_KEY = 'investment_studio.portfolio.performance.window.v1'

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
  'pending_settlement_fx',
  'period_return',
  'return_contribution',
]

const CALCULATION_COLUMN_GROUPS: Array<{ label: string; columns: CalculationColumnKey[] }> = [
  { label: 'Core', columns: ['line', 'begin_weight', 'avg_weight', 'end_weight', 'period_return', 'return_contribution', 'arithmetic_return_contribution'] },
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
      'pending_settlement_fx',
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
  'pending_settlement_fx',
  'period_return',
  'return_contribution',
  'arithmetic_return_contribution',
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
  fx_pnl: 'Position & Cash FX',
  pending_settlement_fx: 'Pending Settlement FX',
  period_return: 'Period Return',
  return_contribution: 'Linked Return Contribution',
  arithmetic_return_contribution: 'Arithmetic Return Contribution',
  own_vol: 'Vol',
  own_sharpe: 'Sharpe',
  own_corr: 'Corr to Portfolio',
  beta: 'Beta to Portfolio',
  risk_contribution: 'Realized RC',
  observations: 'Obs',
}

const CALCULATION_COLUMN_DESCRIPTIONS: Partial<Record<CalculationColumnKey, string>> = {
  return_contribution:
    'Each daily contribution is multiplied by the portfolio growth before that day. Top-level contributions sum to period TWR; child contributions sum to their parent.',
  arithmetic_return_contribution:
    'Sum of daily return contributions. The TWR linking difference reconciles this arithmetic sum to the geometrically linked period TWR.',
  fx_pnl:
    'Daily path attribution from currency translation on positions and settled cash; this is not an accounting realized/unrealized classification.',
  pending_settlement_fx:
    'Daily path attribution from currency translation while a receivable, payable, or position-recognition bridge is outstanding.',
  own_corr:
    'Correlation between the group return and portfolio Market Risk Return on aligned risk periods; the portfolio includes the group.',
  risk_contribution:
    'Covariance share of the linked group return contribution and portfolio Market Risk Return. Top-level rows sum to 100%; a diversifier can be negative.',
  observations: 'Aligned group-return observations used for Vol, Sharpe, Corr and Beta.',
}

const DEFAULT_CALCULATION_TABLE_VIEW_STATE: CalculationTableViewState = {
  columns: RISK_ATTRIBUTION_COLUMNS,
  mode: 'risk_attribution',
  groupBy: 'none',
  sortField: null,
  sortDirection: 'asc',
}
const CALCULATION_VIEW_AUTOSAVE_DELAY_MS = 250

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
        'pending_settlement_fx',
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

function savePerformanceWindowSelection(
  portfolioId: string,
  startDate: string | null,
  endDate: string | null,
  mode: PerformanceWindowMode = 'dates',
) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    const store = loadPerformanceWindowStore()
    const selection = normalizePerformanceWindowSelection({ startDate, endDate, mode })
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
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  const absolute = formatPercent(Math.abs(value), digits)
  if (Number((value * 100).toFixed(digits)) === 0) {
    return absolute
  }
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
    case 'pending_settlement_fx':
      return finiteNumber(source.pending_settlement_currency_gains)
    case 'period_return':
      return finiteNumber(source.period_return)
    case 'return_contribution':
      return finiteNumber(source.linked_period_contribution)
    case 'arithmetic_return_contribution':
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
    const leftContribution = Math.abs(finiteNumber(left.linked_period_contribution) ?? 0)
    const rightContribution = Math.abs(finiteNumber(right.linked_period_contribution) ?? 0)
    return (
      rightRisk - leftRisk ||
      rightContribution - leftContribution ||
      left.group_label.localeCompare(right.group_label, 'zh-Hans-CN')
    )
  }

  const leftMagnitude = Math.abs(finiteNumber(left.linked_period_contribution) ?? finiteNumber(left.total_pnl) ?? 0)
  const rightMagnitude = Math.abs(finiteNumber(right.linked_period_contribution) ?? finiteNumber(right.total_pnl) ?? 0)
  return rightMagnitude - leftMagnitude || left.group_label.localeCompare(right.group_label, 'zh-Hans-CN')
}

function defaultCalculationChildCompare(left: CalculationGroupChildRow, right: CalculationGroupChildRow) {
  const leftKindOrder = left.item_kind === 'instrument' ? 0 : 1
  const rightKindOrder = right.item_kind === 'instrument' ? 0 : 1
  const leftMagnitude = Math.abs(finiteNumber(left.linked_period_contribution) ?? finiteNumber(left.total_pnl) ?? 0)
  const rightMagnitude = Math.abs(finiteNumber(right.linked_period_contribution) ?? finiteNumber(right.total_pnl) ?? 0)
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
  const customViews = (storedViews || [])
    .filter((view) => !SYSTEM_CALCULATION_TABLE_VIEW_IDS.has(view.id))
    .map((view) => ({ ...view, readonly: false }))
  const views = [...systemViews, ...customViews]
  const knownViewIds = new Set(views.map((view) => view.id))
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_CALCULATION_TABLE_VIEWS[0].id
  return { activeViewId, views }
}

function getCalculationTableViews(store: CalculationTableViewStore) {
  return store.views
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

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((total, value) => total + value, 0) / values.length
  const variance =
    values.reduce((total, value) => total + (value - mean) * (value - mean), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00Z`)
  const rightTime = Date.parse(`${right}T00:00:00Z`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function annualizationPeriodsPerYear(observationCount: number, startDate: string, endDate: string) {
  const elapsedDays = dayDiff(startDate, endDate)
  return observationCount > 0 && elapsedDays != null && elapsedDays > 0
    ? (observationCount / elapsedDays) * DAYS_PER_YEAR
    : null
}

function annualizedVolatility(values: number[], startDate: string, endDate: string) {
  const stddev = sampleStddev(values)
  const periodsPerYear = annualizationPeriodsPerYear(values.length, startDate, endDate)
  return stddev == null || periodsPerYear == null ? null : stddev * Math.sqrt(periodsPerYear)
}

function annualizedDownsideVolatility(values: number[], startDate: string, endDate: string) {
  const periodsPerYear = annualizationPeriodsPerYear(values.length, startDate, endDate)
  if (!values.length || periodsPerYear == null) {
    return null
  }
  const downsideSquares = values.map((value) => Math.min(0, value) ** 2)
  if (!downsideSquares.some((value) => value > 0)) {
    return null
  }
  return Math.sqrt(downsideSquares.reduce((total, value) => total + value, 0) / values.length) * Math.sqrt(periodsPerYear)
}

function annualizedMeanReturn(values: number[], startDate: string, endDate: string) {
  const periodsPerYear = annualizationPeriodsPerYear(values.length, startDate, endDate)
  if (!values.length || periodsPerYear == null) {
    return null
  }
  return (values.reduce((total, value) => total + value, 0) / values.length) * periodsPerYear
}

function compoundReturn(values: number[]) {
  if (!values.length) {
    return null
  }
  return values.reduce((growthIndex, value) => growthIndex * (1 + value), 1) - 1
}

function ratioToDrawdown(returnValue: number | null | undefined, maxDrawdown: number | null | undefined) {
  const finiteReturn = finiteNumber(returnValue)
  const finiteDrawdown = finiteNumber(maxDrawdown)
  return finiteReturn != null && finiteDrawdown != null && finiteDrawdown < 0
    ? finiteReturn / Math.abs(finiteDrawdown)
    : null
}

function actualYearFraction(startDate: string, endDate: string) {
  if (!validIsoDate(startDate) || !validIsoDate(endDate) || endDate < startDate) {
    return null
  }
  const [year, month, day] = startDate.split('-').map(Number)
  const anniversary = (years: number) => {
    const targetYear = year + years
    const anniversaryDay = Math.min(day, new Date(Date.UTC(targetYear, month, 0)).getUTCDate())
    return `${targetYear}-${String(month).padStart(2, '0')}-${String(anniversaryDay).padStart(2, '0')}`
  }
  let wholeYears = Number(endDate.slice(0, 4)) - year
  if (anniversary(wholeYears) > endDate) {
    wholeYears -= 1
  }
  const currentAnniversary = anniversary(wholeYears)
  // Match the ledger's anniversary Actual/Actual convention, including
  // February 29 anniversaries and the actual length of a remaining stub.
  return wholeYears + dayDiff(currentAnniversary, endDate)! / dayDiff(currentAnniversary, anniversary(wholeYears + 1))!
}

function performanceStartBoundary(summary: PortfolioPerformanceSummary) {
  const startDate = summary.effective_start_date ?? summary.start_date ?? ''
  return summary.include_start_date_return ? shiftIsoDate(startDate, -1) : startDate
}

function latestBenchmarkPointOnOrBefore(
  points: PortfolioInstrumentPriceChartPoint[],
  targetDate: string,
): PortfolioInstrumentPriceChartPoint | null {
  let selected: PortfolioInstrumentPriceChartPoint | null = null
  for (const point of points) {
    if (point.date <= targetDate) {
      selected = point
    }
  }
  return selected
}

function benchmarkPointByDate(points: PortfolioInstrumentPriceChartPoint[]) {
  return new Map(points.map((point) => [point.date, point]))
}

export function eligiblePortfolioReturnDates(
  portfolioDailySeries: PortfolioDailyPerformancePoint[],
  startDate: string,
  endDate: string,
) {
  return portfolioDailySeries
    .filter((point) => {
      const portfolioReturn = finiteNumber(point.market_risk_daily_return)
      return (
        point.as_of_date >= startDate &&
        point.as_of_date <= endDate &&
        portfolioReturn != null &&
        point.market_risk_return_observation_eligible
      )
    })
    .map((point) => point.as_of_date)
    .sort()
}

function benchmarkAlignedDailyReturns(
  points: PortfolioInstrumentPriceChartPoint[],
  portfolioDailySeries: PortfolioDailyPerformancePoint[],
  startDate: string,
  endDate: string,
  marketSessionDates: string[] | null,
) {
  if (!portfolioDailySeries.length) {
    const returns: Array<{ date: string; value: number }> = []
    for (let index = 1; index < points.length; index += 1) {
      const previous = points[index - 1]
      const point = points[index]
      if (previous.value === 0) {
        return null
      }
      returns.push({ date: point.date, value: point.value / previous.value - 1 })
    }
    return returns
  }

  const eligiblePortfolioDates = eligiblePortfolioReturnDates(portfolioDailySeries, startDate, endDate)
  const benchmarkByDate = benchmarkPointByDate(points)
  const marketSessions = marketSessionDates == null ? null : new Set(marketSessionDates)
  if (eligiblePortfolioDates.some((dateKey) =>
    !benchmarkByDate.has(dateKey) && (marketSessions == null || marketSessions.has(dateKey)))) {
    return null
  }

  const dailyReturns: Array<{ date: string; value: number }> = []
  for (const dateKey of eligiblePortfolioDates) {
    const currentBenchmarkPoint = benchmarkByDate.get(dateKey) ?? latestBenchmarkPointOnOrBefore(points, dateKey)
    // A portfolio daily return measures the preceding calendar close to this
    // close, even after an excluded observation. Do not bridge a missing day.
    const previousBenchmarkPoint = latestBenchmarkPointOnOrBefore(points, shiftIsoDate(dateKey, -1))
    if (!currentBenchmarkPoint || !previousBenchmarkPoint || previousBenchmarkPoint.value === 0) {
      return null
    }
    dailyReturns.push({
      date: dateKey,
      value: currentBenchmarkPoint.value / previousBenchmarkPoint.value - 1,
    })
  }

  return dailyReturns
}

export function buildBenchmarkPeriodMetrics(
  points: PortfolioInstrumentPriceChartPoint[],
  startDate: string,
  endDate: string,
  portfolioDailySeries: PortfolioDailyPerformancePoint[] = [],
  includesStartDateReturn = false,
  marketSessionDates: string[] | null = null,
): BenchmarkPeriodMetrics | null {
  const startBoundaryDate = includesStartDateReturn
    ? shiftIsoDate(startDate, -1)
    : startDate
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value) && point.date <= endDate)
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const startAnchorPoint = latestBenchmarkPointOnOrBefore(sortedPoints, startBoundaryDate)
  const endAnchorPoint = latestBenchmarkPointOnOrBefore(sortedPoints, endDate)
  if (!startAnchorPoint || !endAnchorPoint || startAnchorPoint.value === 0) {
    return null
  }
  const periodPoints = sortedPoints.filter((point) => point.date >= startAnchorPoint.date && point.date <= endDate)

  const dailyReturns = benchmarkAlignedDailyReturns(
    periodPoints,
    portfolioDailySeries,
    startDate,
    endDate,
    marketSessionDates,
  )
  if (dailyReturns == null) {
    return null
  }

  // Whole-period comparison includes cash-only dates after the last market
  // risk observation. Risk sampling below remains a separate calculation.
  const periodReturn = endAnchorPoint.value / startAnchorPoint.value - 1
  const elapsedYears = actualYearFraction(startBoundaryDate, endDate)
  const annualizedReturn =
    elapsedYears != null && elapsedYears >= 1
      ? (1 + periodReturn) ** (1 / elapsedYears) - 1
      : null
  const dailyReturnValues = dailyReturns.map((point) => point.value)
  const annualizedVol = annualizedVolatility(dailyReturnValues, startBoundaryDate, endDate)
  const annualizedDownsideVol = annualizedDownsideVolatility(dailyReturnValues, startBoundaryDate, endDate)
  const annualizedMean = annualizedMeanReturn(dailyReturnValues, startBoundaryDate, endDate)

  let highWater = 1
  let benchmarkGrowth = 1
  let currentDrawdown: number | null = null
  let maxDrawdown: number | null = null
  dailyReturns.forEach((point) => {
    benchmarkGrowth *= 1 + point.value
    highWater = Math.max(highWater, benchmarkGrowth)
    const drawdown = highWater > 0 ? benchmarkGrowth / highWater - 1 : null
    if (drawdown != null) {
      currentDrawdown = drawdown
      maxDrawdown = maxDrawdown == null ? drawdown : Math.min(maxDrawdown, drawdown)
    }
  })

  return {
    periodReturn,
    annualizedReturn,
    annualizedVolatility: annualizedVol,
    annualizedDownsideVolatility: annualizedDownsideVol,
    sharpe:
      annualizedMean != null && annualizedVol != null && annualizedVol !== 0
        ? annualizedMean / annualizedVol
        : null,
    sortino:
      annualizedMean != null && annualizedDownsideVol != null && annualizedDownsideVol !== 0
        ? annualizedMean / annualizedDownsideVol
        : null,
    currentDrawdown,
    maxDrawdown,
    dailyReturns,
  }
}

export function buildRelativePerformanceMetrics(
  portfolioDailySeries: PortfolioDailyPerformancePoint[],
  benchmarkMetrics: BenchmarkPeriodMetrics | null,
  startBoundaryDate: string,
  endDate: string,
): RelativePerformanceMetrics | null {
  if (!benchmarkMetrics?.dailyReturns.length) {
    return null
  }
  const benchmarkByDate = new Map(benchmarkMetrics.dailyReturns.map((point) => [point.date, point.value]))
  const pairs = portfolioDailySeries
    .map((point) => {
      const portfolioReturn = finiteNumber(point.market_risk_daily_return)
      const benchmarkReturn = benchmarkByDate.get(point.as_of_date)
      return portfolioReturn != null && benchmarkReturn != null && point.market_risk_return_observation_eligible
        ? { date: point.as_of_date, portfolioReturn, benchmarkReturn }
        : null
    })
    .filter((value): value is { date: string; portfolioReturn: number; benchmarkReturn: number } => value != null)

  if (pairs.length < 2) {
    return null
  }

  const portfolioReturns = pairs.map((point) => point.portfolioReturn)
  const benchmarkReturns = pairs.map((point) => point.benchmarkReturn)
  const activeReturns = pairs.map((point) => point.portfolioReturn - point.benchmarkReturn)
  const trackingError = annualizedVolatility(activeReturns, startBoundaryDate, endDate)
  const activeAnnualizedMean = annualizedMeanReturn(activeReturns, startBoundaryDate, endDate)

  const benchmarkMean = benchmarkReturns.reduce((total, value) => total + value, 0) / benchmarkReturns.length
  const portfolioMean = portfolioReturns.reduce((total, value) => total + value, 0) / portfolioReturns.length
  const benchmarkVariance =
    benchmarkReturns.reduce((total, value) => total + (value - benchmarkMean) ** 2, 0) / (benchmarkReturns.length - 1)
  const covariance =
    pairs.reduce(
      (total, point) => total + (point.portfolioReturn - portfolioMean) * (point.benchmarkReturn - benchmarkMean),
      0,
    ) /
    (pairs.length - 1)

  const upPairs = pairs.filter((point) => point.benchmarkReturn > 0)
  const downPairs = pairs.filter((point) => point.benchmarkReturn < 0)
  const upsideBenchmarkReturn = compoundReturn(upPairs.map((point) => point.benchmarkReturn))
  const upsidePortfolioReturn = compoundReturn(upPairs.map((point) => point.portfolioReturn))
  const downsideBenchmarkReturn = compoundReturn(downPairs.map((point) => point.benchmarkReturn))
  const downsidePortfolioReturn = compoundReturn(downPairs.map((point) => point.portfolioReturn))
  const upsideCapture =
    upsidePortfolioReturn != null && upsideBenchmarkReturn != null && upsideBenchmarkReturn !== 0
      ? (upsidePortfolioReturn / upsideBenchmarkReturn) * 100
      : null
  const downsideCapture =
    downsidePortfolioReturn != null && downsideBenchmarkReturn != null && downsideBenchmarkReturn !== 0
      ? (downsidePortfolioReturn / downsideBenchmarkReturn) * 100
      : null

  return {
    informationRatio:
      activeAnnualizedMean != null && trackingError != null && trackingError !== 0
        ? activeAnnualizedMean / trackingError
        : null,
    trackingError,
    beta: benchmarkVariance > 0 ? covariance / benchmarkVariance : null,
    upsideCapture,
    downsideCapture,
    captureRatio:
      upsideCapture != null && downsideCapture != null && downsideCapture !== 0
        ? upsideCapture / downsideCapture
        : null,
  }
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

function performanceUnavailableReason(reason: string | null | undefined) {
  switch (reason) {
    case 'multiple_roots_or_non_unique':
      return 'Multiple or non-unique XIRR roots'
    case 'invalid_cash_flows':
      return 'Invalid cash-flow pattern'
    case 'no_root':
      return 'No valid XIRR root'
    case 'return_coverage_incomplete':
      return 'Return coverage is incomplete'
    case 'required_market_data_missing':
      return 'Required market data is missing; supply prices or FX to continue'
    case 'cash_flow_window_unavailable':
      return 'Cash-flow window is unavailable'
    case 'risk_observation_frequency_unavailable':
      return 'Risk observation frequency is unavailable'
    case 'risk_metric_calculation_unavailable':
      return 'Risk calculation is unavailable'
    case 'insufficient_return_samples':
      return 'Insufficient return samples'
    case 'no_modeled_market_assets':
      return 'No modeled market assets'
    default:
      return reason ? reason.replace(/_/g, ' ') : null
  }
}

export function buildPerformanceMetricRows(
  summary: PortfolioPerformanceSummary,
  dailySeries: PortfolioDailyPerformancePoint[],
  baseCurrency: string,
  selectedBenchmarkInstrument: SharedInstrumentRecord | null,
  benchmarkLoading: boolean,
  benchmarkMetrics: BenchmarkPeriodMetrics | null,
  relativeComparisonEligible: boolean,
) {
  const historyReliability = buildPerformanceHistoryReliability(summary)
  const annualizedReturnEligible = historyReliability.annualizedReturnEligible
  const comparableBenchmarkMetrics = relativeComparisonEligible ? benchmarkMetrics : null
  const riskAvailable = summary.risk_result_status === 'available'
  const riskValue = (value: number | null) => riskAvailable ? value : null
  const startBoundaryDate = performanceStartBoundary(summary)
  const endDate = summary.effective_end_date ?? summary.end_date ?? ''
  const relativeMetrics = riskAvailable
    ? buildRelativePerformanceMetrics(dailySeries, comparableBenchmarkMetrics, startBoundaryDate, endDate)
    : null
  const showRiskComparison =
    riskAvailable &&
    relativeComparisonEligible &&
    (selectedBenchmarkInstrument != null || benchmarkLoading)
  const operationalReturn = summary.performance_basis === 'operational_carrying_basis'
  const showReturnComparison = relativeComparisonEligible && !operationalReturn && summary.return_coverage_state === 'complete'
  const irr = finiteNumber(summary.irr) ?? finiteNumber(summary.mwror)
  const fxPnlComponents = [
    summary.cash_currency_gains,
    summary.instrument_currency_gains,
    summary.pending_settlement_currency_gains,
  ]
  const totalFxPnl = fxPnlComponents.every((value) => finiteNumber(value) != null)
    ? sumNullable(...fxPnlComponents)
    : null
  const annualizationNote = summary.annualization_unavailable_reason === 'measurement_period_shorter_than_one_year'
    ? 'Requires ≥ 1 year'
    : summary.annualization_unavailable_reason === 'operational_carrying_basis_not_annualized'
      ? 'Requires complete fair-value valuations'
      : historyReliability.annualizationMessage ?? undefined
  const irrReliabilityNote = !annualizedReturnEligible
    ? annualizationNote
    : irr == null
      ? performanceUnavailableReason(summary.irr_unavailable_reason) ?? 'IRR / MWRR unavailable'
      : undefined
  const riskReliabilityNote =
    summary.risk_result_status === 'available'
      ? undefined
      : summary.risk_unavailable_reason === 'insufficient_return_samples'
        ? `Requires ≥ ${summary.risk_minimum_sample_count} ${summary.risk_calculation_frequency} return samples (${summary.risk_sample_count} available)`
        : performanceUnavailableReason(summary.risk_unavailable_reason) ?? 'Risk metrics unavailable'
  const elapsedYears = actualYearFraction(startBoundaryDate, endDate)
  const calmarAnnualizationEligible = elapsedYears != null && elapsedYears >= 1
  const annualizedMarketRiskReturn = riskAvailable && calmarAnnualizationEligible &&
    summary.market_risk_cumulative_return != null
    ? (1 + summary.market_risk_cumulative_return) ** (1 / elapsedYears) - 1
    : null
  const calmarRatio = ratioToDrawdown(annualizedMarketRiskReturn, summary.max_drawdown)
  const returnDifference =
    summary.cumulative_twr != null && comparableBenchmarkMetrics?.periodReturn != null
      ? summary.cumulative_twr - comparableBenchmarkMetrics.periodReturn
      : null
  const annualizedReturnDifference =
    annualizedReturnEligible && summary.annualized_twr != null && comparableBenchmarkMetrics?.annualizedReturn != null
      ? summary.annualized_twr - comparableBenchmarkMetrics.annualizedReturn
      : null
  const volatilityDifference =
    summary.annualized_volatility != null && comparableBenchmarkMetrics?.annualizedVolatility != null
      ? summary.annualized_volatility - comparableBenchmarkMetrics.annualizedVolatility
      : null
  const downsideVolatilityDifference =
    summary.annualized_downside_volatility != null && comparableBenchmarkMetrics?.annualizedDownsideVolatility != null
      ? summary.annualized_downside_volatility - comparableBenchmarkMetrics.annualizedDownsideVolatility
      : null
  const sharpeDifference =
    summary.sharpe_ratio != null && comparableBenchmarkMetrics?.sharpe != null
      ? summary.sharpe_ratio - comparableBenchmarkMetrics.sharpe
      : null
  const sortinoDifference =
    summary.sortino_ratio != null && comparableBenchmarkMetrics?.sortino != null
      ? summary.sortino_ratio - comparableBenchmarkMetrics.sortino
      : null
  const currentDrawdownDifference =
    summary.current_drawdown != null && comparableBenchmarkMetrics?.currentDrawdown != null
      ? summary.current_drawdown - comparableBenchmarkMetrics.currentDrawdown
      : null
  const maxDrawdownDifference =
    summary.max_drawdown != null && comparableBenchmarkMetrics?.maxDrawdown != null
      ? summary.max_drawdown - comparableBenchmarkMetrics.maxDrawdown
      : null

  const rows: PerformanceMetricRow[] = [
    {
      section: 'Return',
      metric: summary.performance_label,
      value: signedPercent(summary.cumulative_twr),
      valueClassName: signedValueClass(summary.cumulative_twr),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.periodReturn),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        comparableBenchmarkMetrics?.periodReturn,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      showComparison: showReturnComparison,
    },
    {
      section: 'Return',
      metric: 'Annualized TWR',
      value: annualizedReturnEligible ? signedPercent(summary.annualized_twr) : 'N/A',
      valueClassName: annualizedReturnEligible ? signedValueClass(summary.annualized_twr) : 'performance-cell-muted',
      reliabilityNote: annualizedReturnEligible ? undefined : annualizationNote,
      benchmark: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.annualizedReturn)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      benchmarkClassName: annualizedReturnEligible
        ? benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.annualizedReturn)
        : 'performance-cell-muted',
      difference: annualizedReturnEligible
        ? benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, annualizedReturnDifference)
        : selectedBenchmarkInstrument
          ? 'N/A'
          : '—',
      differenceClassName: annualizedReturnEligible
        ? benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, annualizedReturnDifference)
        : 'performance-cell-muted',
      showComparison: showReturnComparison,
    },
    {
      section: 'Return',
      metric: 'IRR / MWRR',
      value: annualizedReturnEligible && irr != null ? signedPercent(irr) : 'N/A',
      valueClassName:
        annualizedReturnEligible && irr != null ? signedValueClass(irr) : 'performance-cell-muted',
      reliabilityNote: irrReliabilityNote,
    },
    {
      section: 'Return',
      metric: 'Total P&L',
      value: formatSignedCurrency(summary.total_pnl, baseCurrency),
      valueClassName: signedValueClass(summary.total_pnl),
    },
    {
      section: 'Return',
      metric: 'Total FX Attribution',
      value: formatSignedCurrency(totalFxPnl, baseCurrency),
      valueClassName: signedValueClass(totalFxPnl),
    },
    {
      section: 'Return',
      metric: 'Market Risk P&L',
      value: formatSignedCurrency(summary.market_risk_pnl, baseCurrency),
      valueClassName: signedValueClass(summary.market_risk_pnl),
    },
    {
      section: 'Return',
      metric: 'P&L Excluded from Market Risk',
      value: formatSignedCurrency(summary.risk_scope_excluded_pnl, baseCurrency),
      valueClassName: signedValueClass(summary.risk_scope_excluded_pnl),
    },
    {
      section: 'Return',
      metric: 'Derivative Lifecycle Realized P&L',
      value: formatSignedCurrency(summary.derivative_lifecycle_realized_pnl, baseCurrency),
      valueClassName: signedValueClass(summary.derivative_lifecycle_realized_pnl),
    },
    {
      section: 'Risk',
      metric: 'Market Risk Return',
      value: signedPercent(riskValue(summary.market_risk_cumulative_return)),
      valueClassName: signedValueClass(riskValue(summary.market_risk_cumulative_return)),
    },
    {
      section: 'Risk',
      metric: 'Market Risk Mean Daily Return',
      value: signedPercent(riskValue(summary.mean_daily_return), 3),
      valueClassName: signedValueClass(riskValue(summary.mean_daily_return)),
    },
    {
      section: 'Risk',
      metric: 'Market Risk Calmar Ratio',
      value: calmarRatio == null ? 'N/A' : formatRatio(calmarRatio),
      reliabilityNote: !calmarAnnualizationEligible ? 'Requires ≥ 1 year' : riskReliabilityNote,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Volatility',
      value: formatPercent(riskValue(summary.annualized_volatility)),
      reliabilityNote: riskReliabilityNote,
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        comparableBenchmarkMetrics?.annualizedVolatility,
        formatPercent,
      ),
      benchmarkClassName: undefined,
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      showComparison: showRiskComparison,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Downside Volatility',
      value: formatPercent(riskValue(summary.annualized_downside_volatility)),
      reliabilityNote: riskReliabilityNote,
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        comparableBenchmarkMetrics?.annualizedDownsideVolatility,
        formatPercent,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, downsideVolatilityDifference),
      differenceClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        downsideVolatilityDifference,
      ),
      showComparison: showRiskComparison,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Sharpe Ratio',
      value: formatRatio(riskValue(summary.sharpe_ratio)),
      reliabilityNote: riskReliabilityNote,
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.sharpe, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference),
      showComparison: showRiskComparison,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Sortino Ratio',
      value: formatRatio(riskValue(summary.sortino_ratio)),
      reliabilityNote: riskReliabilityNote,
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.sortino, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference),
      showComparison: showRiskComparison,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Current DD',
      value: signedPercent(riskValue(summary.current_drawdown)),
      valueClassName: signedValueClass(riskValue(summary.current_drawdown)),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.currentDrawdown),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        comparableBenchmarkMetrics?.currentDrawdown,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      showComparison: showRiskComparison,
    },
    {
      section: 'Risk',
      metric: 'Market Risk Max DD',
      value: signedPercent(riskValue(summary.max_drawdown)),
      valueClassName: signedValueClass(riskValue(summary.max_drawdown)),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.maxDrawdown),
      benchmarkClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, comparableBenchmarkMetrics?.maxDrawdown),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      showComparison: showRiskComparison,
    },
    {
      section: 'Relative',
      metric: 'Tracking Error',
      reliabilityNote: riskReliabilityNote,
      value: formatPercent(relativeMetrics?.trackingError),
    },
    {
      section: 'Relative',
      metric: 'Information Ratio',
      reliabilityNote: riskReliabilityNote,
      value: formatRatio(relativeMetrics?.informationRatio),
    },
    {
      section: 'Relative',
      metric: 'Beta',
      value: formatRatio(relativeMetrics?.beta),
    },
    {
      section: 'Relative',
      metric: 'Upside Capture',
      value: formatPercent(relativeMetrics?.upsideCapture == null ? null : relativeMetrics.upsideCapture / 100),
    },
    {
      section: 'Relative',
      metric: 'Downside Capture',
      value: formatPercent(relativeMetrics?.downsideCapture == null ? null : relativeMetrics.downsideCapture / 100),
    },
    {
      section: 'Relative',
      metric: 'CAP Ratio',
      value: formatRatio(relativeMetrics?.captureRatio),
    },
  ]
  return rows.filter((row) => row.section !== 'Relative' || selectedBenchmarkInstrument != null)
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
  const columnLabels: PerformanceMetricRow['section'][] = ['Return', 'Risk', 'Relative']
  const sections = columnLabels
    .map((label) => ({ label, rows: rows.filter((row) => row.section === label),
      showComparison: rows.some((row) => row.section === label && row.showComparison) }))
    .filter((section) => section.rows.length > 0)

  return (
    <div className="performance-metric-table-grid">
      {sections.map((section) => (
        <HorizontalTableScroll className="performance-metric-table-shell" key={section.label}>
          <div className="performance-metric-column-title">{section.label}</div>
          <table
            className={`performance-metric-table ${section.label === 'Return' ? 'performance-metric-table-return' : ''} ${
              section.showComparison ? 'performance-metric-table-has-comparison' : ''
            }`}
          >
            {section.label === 'Return' && section.showComparison ? (
              <colgroup><col style={{ width: '40%' }} /><col style={{ width: '30%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /></colgroup>
            ) : null}
            <thead>
              <tr>
                <th>Metric</th>
                <th>Portfolio</th>
                {section.showComparison ? <th>BM</th> : null}
                {section.showComparison ? <th>Diff</th> : null}
              </tr>
            </thead>
            <tbody>
              {section.rows.map((row) => (
                <tr key={row.metric}>
                  <th scope="row">
                    <span className="performance-metric-table-name">
                      <span>{row.metric}</span>
                      {row.reliabilityNote ? (
                        <InfoHint
                          label={`${row.metric} availability`}
                          detail={row.reliabilityNote}
                          tone="warning"
                        />
                      ) : null}
                    </span>
                  </th>
                  <td className={row.valueClassName ?? ''}>{row.value}</td>
                  {section.showComparison ? (
                    <td className={row.benchmarkClassName ?? ''}>{row.showComparison ? row.benchmark ?? '—' : '—'}</td>
                  ) : null}
                  {section.showComparison ? (
                    <td className={row.differenceClassName ?? ''}>{row.showComparison ? row.difference ?? '—' : '—'}</td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </HorizontalTableScroll>
      ))}
    </div>
  )
}

function PerformancePage() {
  const zh = useLanguage().language === 'zh-Hans'
  const { portfolioId } = useParams<{ portfolioId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()

  const todayDate = useMemo(() => localDateIso(), [])
  const queryStartDate = validIsoDate(searchParams.get('start_date'))
  const queryEndDate = validIsoDate(searchParams.get('end_date'))
  const querySinceInception = searchParams.get('period') === 'si'
  const hasDateWindowParams = searchParams.has('start_date') || searchParams.has('end_date') || querySinceInception
  const storedPerformanceWindow = useMemo(
    () => (!hasDateWindowParams && portfolioId ? loadPerformanceWindowSelection(portfolioId) : null),
    [hasDateWindowParams, portfolioId],
  )
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [portfolioSummaryReadyPortfolioId, setPortfolioSummaryReadyPortfolioId] = useState<string | null>(null)
  const portfolioSummarySettled = Boolean(portfolioId && portfolioSummaryReadyPortfolioId === portfolioId)
  const portfolioAsOfDate =
    portfolioSummary && portfolioSummary.portfolio_id === portfolioId ? validIsoDate(portfolioSummary.as_of_date) : ''
  const {
    appliedSinceInception,
    appliedEndDate,
    waitingForDefaultEndDate: unresolvedDefaultEndDate,
    effectiveEndDate,
    effectiveStartDate,
    windowError,
  } = useMemo(
    () =>
      resolvePerformanceWindow({
        queryStartDate,
        queryEndDate,
        querySinceInception,
        storedSelection: storedPerformanceWindow,
        portfolioAsOfDate,
        todayDate,
        portfolioSummarySettled,
        defaultLookbackDays: DEFAULT_PERFORMANCE_LOOKBACK_DAYS,
      }),
    [
      portfolioAsOfDate,
      portfolioSummarySettled,
      queryEndDate,
      querySinceInception,
      queryStartDate,
      storedPerformanceWindow,
      todayDate,
    ],
  )
  const waitingForDefaultEndDate = Boolean(portfolioId && unresolvedDefaultEndDate)
  const [draftStartDate, setDraftStartDate] = useState(effectiveStartDate)
  const [draftEndDate, setDraftEndDate] = useState(effectiveEndDate)
  useEffect(() => {
    setDraftStartDate(effectiveStartDate)
    setDraftEndDate(effectiveEndDate)
  }, [effectiveStartDate, effectiveEndDate, portfolioId])
  const windowDraftChanged = draftStartDate !== effectiveStartDate || draftEndDate !== effectiveEndDate
  const draftWindowError = performanceWindowError(draftStartDate, draftEndDate)
  const performanceWindowFilters = useMemo(
    () => buildPerformanceWindowFilters(effectiveStartDate, effectiveEndDate),
    [effectiveEndDate, effectiveStartDate],
  )
  const performanceWindowResourceKey = [
    portfolioId ?? '',
    performanceWindowFilters.start_date ?? '',
    performanceWindowFilters.end_date ?? '',
  ].join(':')
  const loadPerformanceWorkspace = useCallback(
    (signal: AbortSignal) => getPortfolioPerformance(portfolioId ?? '', performanceWindowFilters, signal),
    [performanceWindowFilters, portfolioId],
  )
  const {
    data: workspace,
    loading,
    error,
  } = usePerformanceResource({
    enabled: Boolean(portfolioId && !waitingForDefaultEndDate && !windowError),
    resourceKey: performanceWindowResourceKey,
    load: loadPerformanceWorkspace,
    fallbackError: 'Failed to load performance workspace.',
  })
  const calculationWindowFilters = useMemo(
    () =>
      buildPerformanceWindowFilters(
        validIsoDate(workspace?.summary.effective_start_date) || effectiveStartDate,
        validIsoDate(workspace?.summary.effective_end_date) || effectiveEndDate,
      ),
    [
      effectiveEndDate,
      effectiveStartDate,
      workspace?.summary.effective_end_date,
      workspace?.summary.effective_start_date,
    ],
  )
  const calculationWindowResourceKey = [
    portfolioId ?? '',
    calculationWindowFilters.start_date ?? '',
    calculationWindowFilters.end_date ?? '',
  ].join(':')
  const loadCalculationWorkspace = useCallback(
    (signal: AbortSignal) => getPortfolioPerformanceCalculation(portfolioId ?? '', calculationWindowFilters, signal),
    [calculationWindowFilters, portfolioId],
  )
  const {
    data: calculationWorkspace,
    loading: calculationLoading,
    error: calculationError,
  } = usePerformanceResource({
    enabled: Boolean(portfolioId && workspace && !waitingForDefaultEndDate),
    resourceKey: calculationWindowResourceKey,
    load: loadCalculationWorkspace,
    fallbackError: 'Failed to load period calculation.',
  })

  const initialCalculationTableViewStore = useMemo(
    () => normalizeCalculationTableViewStore(null),
    [],
  )
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
  const persistedCalculationTableViewStoreRef = useRef<{
    portfolioId: string
    serializedStore: string
  } | null>(null)
  const calculationViewSaveQueueRef = useRef(new SerialTaskQueue())
  const [calculationTableViewStoreReadyPortfolioId, setCalculationTableViewStoreReadyPortfolioId] =
    useState<string | null>(null)
  const [calculationTableViewStoreSettledPortfolioId, setCalculationTableViewStoreSettledPortfolioId] =
    useState<string | null>(null)
  const [calculationTableViewStoreError, setCalculationTableViewStoreError] = useState<string | null>(null)
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
  const [taxonomyCatalogReadyPortfolioId, setTaxonomyCatalogReadyPortfolioId] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [groupingTaxonomyId, setGroupingTaxonomyId] = useState('')
  const groupingTaxonomies = taxonomyCatalog?.taxonomies.filter((taxonomy) => taxonomy.status === 'active') ?? []
  const selectedGroupingTaxonomy = useMemo(
    () =>
      groupingTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === groupingTaxonomyId) ??
      groupingTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === taxonomyCatalog?.default_planning_taxonomy_id) ??
      groupingTaxonomies[0] ?? null,
    [taxonomyCatalog, groupingTaxonomyId],
  )
  const taxonomyCatalogReady = taxonomyCatalogReadyPortfolioId === portfolioId
  const calculationTableViewStoreSettled =
    calculationTableViewStoreSettledPortfolioId === portfolioId
  const effectiveCalculationGroupBy: CalculationGroupByKey =
    calculationGroupBy === 'taxonomy' && taxonomyCatalogReady && !selectedGroupingTaxonomy
      ? 'none'
      : calculationGroupBy
  const resolvedCalculationGroupBy: PortfolioContributionAxis =
    effectiveCalculationGroupBy === 'none' ? 'instrument' : effectiveCalculationGroupBy
  const calculationGroupsReady =
    calculationTableViewStoreSettled &&
    (resolvedCalculationGroupBy !== 'taxonomy' || taxonomyCatalogReady)
  const calculationGroupsFilters = useMemo(
    () => ({
      ...calculationWindowFilters,
      axis: resolvedCalculationGroupBy,
      taxonomy_id:
        resolvedCalculationGroupBy === 'taxonomy'
          ? selectedGroupingTaxonomy?.taxonomy_id ?? undefined
          : undefined,
    }),
    [
      selectedGroupingTaxonomy?.taxonomy_id,
      calculationWindowFilters,
      resolvedCalculationGroupBy,
    ],
  )
  const calculationGroupsResourceKey = [
    calculationWindowResourceKey,
    calculationGroupsFilters.axis,
    calculationGroupsFilters.taxonomy_id ?? '',
  ].join(':')
  const loadCalculationGroupsWorkspace = useCallback(
    (signal: AbortSignal) =>
      getPortfolioPerformanceCalculationGroups(
        portfolioId ?? '',
        calculationGroupsFilters,
        signal,
      ),
    [calculationGroupsFilters, portfolioId],
  )
  const {
    data: calculationGroupsWorkspace,
    loading: calculationGroupsLoading,
    error: calculationGroupsError,
  } = usePerformanceResource({
    enabled: Boolean(
      portfolioId &&
        workspace &&
        !waitingForDefaultEndDate &&
        calculationGroupsReady,
    ),
    resourceKey: calculationGroupsResourceKey,
    load: loadCalculationGroupsWorkspace,
    fallbackError: 'Failed to load calculation groups.',
  })
  const calculationGroupsPending =
    calculationGroupsLoading || Boolean(workspace && !calculationGroupsReady)
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
        disabled: !selectedGroupingTaxonomy,
      },
    ],
    [selectedGroupingTaxonomy],
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

  useEffect(() => {
    if (
      calculationTableViewStoreReadyPortfolioId !== portfolioId ||
      !calculationTableViewEdited
    ) {
      return undefined
    }
    const timeoutId = window.setTimeout(
      () => updateCalculationTableViewState(currentCalculationTableViewState),
      CALCULATION_VIEW_AUTOSAVE_DELAY_MS,
    )
    return () => window.clearTimeout(timeoutId)
  }, [
    activeCalculationTableViewId,
    calculationTableViewEdited,
    calculationTableViewStoreReadyPortfolioId,
    currentCalculationTableViewState,
    portfolioId,
  ])
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
    const controller = new AbortController()
    setPortfolioSummaryReadyPortfolioId(null)
    getWorkspaceSummaryForPortfolio(portfolioId, controller.signal)
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
      controller.abort()
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

  function updateCalculationTableViewState(state: CalculationTableViewState) {
    const timestamp = new Date().toISOString()
    const normalizedState = normalizeCalculationTableViewState(state)
    setCalculationTableViewStore((current) => ({
      ...current,
      activeViewId: activeCalculationTableViewId,
      views: current.views.map((view) =>
        view.id === activeCalculationTableViewId
          ? {
              ...view,
              state: normalizedState,
              updatedAt: timestamp,
            }
          : view,
      ),
    }))
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
      persistedCalculationTableViewStoreRef.current = null
      setCalculationTableViewStoreReadyPortfolioId(null)
      setCalculationTableViewStoreError(null)
      return
    }

    let cancelled = false
    const controller = new AbortController()
    const defaultStore = normalizeCalculationTableViewStore(null)
    persistedCalculationTableViewStoreRef.current = null
    setCalculationTableViewStoreReadyPortfolioId(null)
    setCalculationTableViewStoreSettledPortfolioId(null)
    setCalculationTableViewStoreError(null)
    setCalculationTableViewStore(defaultStore)
    setActiveCalculationTableViewId(defaultStore.activeViewId)
    applyCalculationTableViewState(
      resolveCalculationTableViewState(defaultStore, defaultStore.activeViewId),
    )
    getPortfolioTableViewStore<CalculationTableViewStore>(portfolioId, 'performance_calculation', controller.signal)
      .then((response) => {
        if (cancelled) {
          return
        }
        const nextStore = response.store
          ? normalizeCalculationTableViewStore(response.store)
          : defaultStore
        persistedCalculationTableViewStoreRef.current = {
          portfolioId,
          serializedStore: JSON.stringify(nextStore),
        }
        setCalculationTableViewStore(nextStore)
        setActiveCalculationTableViewId(nextStore.activeViewId)
        applyCalculationTableViewState(resolveCalculationTableViewState(nextStore, nextStore.activeViewId))
        setCalculationTableViewStoreReadyPortfolioId(portfolioId)
        setCalculationTableViewStoreSettledPortfolioId(portfolioId)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setCalculationTableViewStoreError(
            `Calculation table views unavailable: ${
              requestError instanceof Error ? requestError.message : 'backend read failed.'
            }`,
          )
          setCalculationTableViewStoreSettledPortfolioId(portfolioId)
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId || calculationTableViewStoreReadyPortfolioId !== portfolioId) {
      return
    }
    const serializedStore = JSON.stringify(calculationTableViewStore)
    if (
      persistedCalculationTableViewStoreRef.current?.portfolioId === portfolioId
      && persistedCalculationTableViewStoreRef.current.serializedStore === serializedStore
    ) {
      return
    }
    persistedCalculationTableViewStoreRef.current = { portfolioId, serializedStore }
    calculationViewSaveQueueRef.current
      .enqueue(() =>
        savePortfolioTableViewStore(
          portfolioId,
          'performance_calculation',
          calculationTableViewStore,
        ),
      )
      .then(
        () => {
          if (persistedCalculationTableViewStoreRef.current?.portfolioId === portfolioId) {
            setCalculationTableViewStoreError(null)
          }
        },
        (requestError: unknown) => {
          if (persistedCalculationTableViewStoreRef.current?.portfolioId === portfolioId) {
            setCalculationTableViewStoreError(
              `Failed to save calculation table views: ${
                requestError instanceof Error ? requestError.message : 'backend write failed.'
              }`,
            )
          }
        },
      )
  }, [calculationTableViewStore, calculationTableViewStoreReadyPortfolioId, portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      setTaxonomyCatalog(null)
      setTaxonomyCatalogReadyPortfolioId(null)
      return
    }

    let cancelled = false
    const controller = new AbortController()
    setTaxonomyCatalogReadyPortfolioId(null)
    getPortfolioInstruments(portfolioId, controller.signal)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkInstruments(response.instruments)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBenchmarkInstruments([])
        }
      })
    getPortfolioTaxonomyCatalog(portfolioId, {}, controller.signal)
      .then((response) => {
        if (!cancelled) {
          setTaxonomyCatalog(response)
          setTaxonomyCatalogReadyPortfolioId(portfolioId)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTaxonomyCatalog(null)
          setTaxonomyCatalogReadyPortfolioId(portfolioId)
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [portfolioId])

  const benchmarkAsOfDate =
    validIsoDate(workspace?.summary.effective_end_date) || effectiveEndDate

  useEffect(() => {
    if (!portfolioId || !benchmarkInstrumentId || waitingForDefaultEndDate) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    const controller = new AbortController()
    setBenchmarkLoading(true)
    setBenchmarkError(null)
    setBenchmarkChart(null)

    getPortfolioInstrumentPriceChart(portfolioId, benchmarkInstrumentId, {
      as_of_date: benchmarkAsOfDate,
      range: 'all',
    }, controller.signal)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkChart(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setBenchmarkChart(null)
          setBenchmarkError(requestError instanceof Error ? requestError.message : 'Failed to load benchmark history.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkLoading(false)
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [benchmarkAsOfDate, benchmarkInstrumentId, portfolioId, waitingForDefaultEndDate])

  function updateWindowParams(
    nextStartDate: string | null,
    nextEndDate: string | null,
    mode: PerformanceWindowMode = 'dates',
  ) {
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
    if (mode === 'since_inception') {
      nextParams.set('period', 'si')
    } else {
      nextParams.delete('period')
    }
    if (portfolioId) {
      savePerformanceWindowSelection(portfolioId, normalizedStartDate, normalizedEndDate, mode)
    }
    const nextWindow = resolvePerformanceWindow({
      queryStartDate: normalizedStartDate,
      queryEndDate: normalizedEndDate,
      querySinceInception: mode === 'since_inception',
      portfolioAsOfDate,
      todayDate,
      portfolioSummarySettled,
      defaultLookbackDays: DEFAULT_PERFORMANCE_LOOKBACK_DAYS,
    })
    setDraftStartDate(nextWindow.effectiveStartDate)
    setDraftEndDate(nextWindow.effectiveEndDate)
    setSearchParams(nextParams)
  }

  function handlePerformancePeriodPreset(preset: PerformancePeriodPreset) {
    if (preset === 'si') {
      updateWindowParams(null, appliedEndDate || null, 'since_inception')
      return
    }
    updateWindowParams(performancePresetStartDate(preset, effectiveEndDate), appliedEndDate || null)
  }

  const selectedPerformancePeriodPreset = useMemo<PerformancePeriodPreset | null>(() => {
    if (appliedSinceInception) {
      return 'si'
    }
    return (
      PERFORMANCE_PERIOD_PRESETS.find(
        (preset) =>
          preset.key !== 'si' && performancePresetStartDate(preset.key, effectiveEndDate) === effectiveStartDate,
      )?.key ?? null
    )
  }, [appliedSinceInception, effectiveEndDate, effectiveStartDate])

  const summary = workspace?.summary ?? null
  const performanceIsOperational = summary?.performance_basis === 'operational_carrying_basis'
  const operationalPerformanceDetail =
    'Event-valued assets and obligations use carrying-basis measurements. This is an operational ledger return, not a complete fair-value or GIPS-informed TWR. Market-risk metrics use a separate return chain that models derivatives and base-currency cash at zero return.'
  const baseCurrency = workspace?.base_currency ?? calculationWorkspace?.base_currency ?? calculationGroupsWorkspace?.base_currency ?? 'USD'
  const reportStartDate =
    validIsoDate(summary?.effective_start_date) || validIsoDate(summary?.start_date) || effectiveStartDate
  const reportEndDate =
    validIsoDate(summary?.effective_end_date) || validIsoDate(summary?.end_date) || effectiveEndDate
  const periodLabel = `${reportStartDate} to ${reportEndDate}`
  const performanceHistoryReliability = useMemo(
    () => (summary ? buildPerformanceHistoryReliability(summary) : null),
    [summary],
  )
  const performanceMetricsMeta = performanceHistoryReliability?.sampleLabel ?? periodLabel

  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId) ?? null
  const benchmarkEligibleDates = useMemo(
    () => eligiblePortfolioReturnDates(workspace?.daily_series ?? [], reportStartDate, reportEndDate),
    [reportEndDate, reportStartDate, workspace?.daily_series],
  )
  const benchmarkStartBoundaryDate = summary ? performanceStartBoundary(summary) : reportStartDate
  const benchmarkGuard = useMemo(
    () =>
      selectedBenchmarkInstrument && benchmarkChart
        ? assessBenchmarkComparisonGuard({
            chartBasis: benchmarkChart.chart_basis,
            returnSemantics: benchmarkChart.return_semantics,
            benchmarkCurrency: benchmarkChart.currency,
            portfolioCurrency: baseCurrency,
            points: benchmarkChart.points,
            startBoundaryDate: benchmarkStartBoundaryDate,
            eligiblePortfolioDates: benchmarkEligibleDates,
            endBoundaryDate: reportEndDate,
            marketSessionDates: benchmarkChart.market_session_dates,
          })
        : null,
    [
      baseCurrency,
      benchmarkChart,
      benchmarkEligibleDates,
      benchmarkStartBoundaryDate,
      selectedBenchmarkInstrument,
      reportEndDate,
    ],
  )
  const benchmarkComparisonDetail = benchmarkGuard
    ? `${
        benchmarkGuard.mode === 'canonical'
          ? 'Canonical comparator'
          : benchmarkGuard.reason === 'benchmark_price_return_comparable'
            ? 'Price-return comparator'
            : benchmarkGuard.mode === 'exploratory'
              ? 'Exploratory comparator'
              : 'Comparator unavailable'
      }. ${benchmarkGuard.basisAssessment.label} (${benchmarkGuard.basisAssessment.basis ?? 'basis unavailable'}).${
        benchmarkGuard.warning ? ` ${benchmarkGuard.warning}` : ''
      }`
    : null
  const benchmarkMetrics = useMemo(
    () =>
      benchmarkGuard == null || benchmarkGuard.mode === 'unavailable'
        ? null
        : buildBenchmarkPeriodMetrics(
            benchmarkChart?.points ?? [],
            reportStartDate,
            reportEndDate,
            workspace?.daily_series ?? [],
            summary?.include_start_date_return ?? false,
            benchmarkChart?.market_session_dates ?? null,
          ),
    [
      benchmarkChart,
      benchmarkGuard?.mode,
      reportEndDate,
      reportStartDate,
      summary?.include_start_date_return,
      workspace?.daily_series,
    ],
  )
  const metricRows = useMemo(
    () =>
      summary
        ? buildPerformanceMetricRows(
            summary,
            workspace?.daily_series ?? [],
            baseCurrency,
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkMetrics,
            benchmarkGuard?.relativeComparisonEligible ?? false,
          )
        : [],
    [
      summary,
      workspace?.daily_series,
      baseCurrency,
      selectedBenchmarkInstrument,
      benchmarkLoading,
      benchmarkMetrics,
      benchmarkGuard?.relativeComparisonEligible,
      performanceIsOperational,
    ],
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
  const portfolioRealizedGain = calculationSummary?.realized_capital_gains ?? null
  const portfolioUnrealizedGain = calculationSummary?.unrealized_capital_gains ?? null
  const portfolioIncome = calculationSummary?.earnings ?? summary?.income_cash_amount ?? null
  const portfolioFxPnl = sumNullable(calculationSummary?.cash_currency_gains, calculationSummary?.instrument_currency_gains)
  const portfolioPendingSettlementFx = calculationSummary?.pending_settlement_currency_gains ?? null
  const pendingSettlementFxUnavailable = Boolean(
    calculationSummary &&
      calculationSummary.coverage_state !== 'complete' &&
      calculationSummary.pending_settlement_currency_gains == null,
  )
  const portfolioFees = expenseImpact(calculationSummary?.fees)
  const portfolioTaxes = expenseImpact(calculationSummary?.taxes)
  const portfolioPeriodPnl = calculationSummary?.delta ?? summary?.delta ?? summary?.total_pnl ?? null
  const portfolioContribution = calculationGroupsSummary?.total_linked_period_contribution ?? null
  const portfolioArithmeticContribution = calculationGroupsSummary?.total_period_contribution ?? null
  const contributionResidual = calculationGroupsSummary?.contribution_residual ?? null
  const twrLinkingDifference = summary?.cumulative_twr != null &&
    calculationGroupsSummary?.total_period_contribution != null
    ? summary.cumulative_twr - calculationGroupsSummary.total_period_contribution
    : null
  const showTwrLinkingDifference = visibleCalculationColumns.includes('arithmetic_return_contribution') && twrLinkingDifference != null
  const showContributionResidual = visibleCalculationColumns.includes('arithmetic_return_contribution') && contributionResidual != null && Math.abs(contributionResidual) > 0.0000005
  const calculationChildRowCount = calculationRows.reduce((total, row) => total + (row.children?.length ?? 0), 0)
  const calculationRiskStatusLabel = calculationGroupsSummary?.risk_frequency_status_label ?? null
  const calculationMeta =
    !calculationGroupsWorkspace && calculationGroupsLoading
      ? `${periodLabel} · Loading`
      : `${periodLabel} · ${formatNumber(calculationRows.length, 0)} ${calculationAxisCountLabel(
          resolvedCalculationGroupBy,
        )}${calculationChildRowCount ? ` · ${formatNumber(calculationChildRowCount, 0)} instruments/cash` : ''}`
  const riskMetricsAvailable = summary?.risk_result_status === 'available' && realizedRiskMetricsAvailable(
    calculationGroupsSummary?.risk_return_observation_count,
    calculationGroupsSummary?.annualized_volatility,
  )
  const realizedRiskEstimateLowSample = realizedRiskEstimateIsLowSample(
    calculationGroupsSummary?.risk_return_observation_count,
  )
  const showsRealizedRiskEstimate =
    visibleCalculationColumns.includes('own_corr') ||
    visibleCalculationColumns.includes('risk_contribution')
  const realizedRiskEstimateDetail = `Corr to Portfolio and Realized RC use only ${formatNumber(
    calculationGroupsSummary?.risk_return_observation_count ?? 0,
    0,
  )} aligned ${calculationGroupsSummary?.risk_calculation_frequency} observations. Treat the ranking and sign as preliminary; a negative Realized RC means diversification, not a loss.`
  const performancePanelDetail = [
    performanceMetricsMeta,
    summary ? `Basis: ${formatLabel(summary.performance_basis)}` : null,
    `Currency: ${baseCurrency}; Recorded fees and taxes deducted`,
    performanceIsOperational
      ? operationalPerformanceDetail
      : 'Market-value time-weighted return',
    'Risk: daily observations, annualized by actual period length; risk-free rate and minimum acceptable return = 0',
    summary ? `${summary.risk_return_observation_count} risk observations` : null,
    performanceIsOperational ? null : 'Derivatives and base-currency cash modeled at zero return',
  ]
    .filter(Boolean)
    .join('. ')
  const calculationPanelDetail = [
    calculationMeta,
    calculationRiskStatusLabel,
    realizedRiskEstimateLowSample && showsRealizedRiskEstimate
      ? realizedRiskEstimateDetail
      : null,
    visibleCalculationColumns.includes('return_contribution')
      ? 'Linked contributions sum to period TWR; child contributions sum to their parent.'
      : null,
    visibleCalculationColumns.includes('arithmetic_return_contribution')
      ? 'Arithmetic contributions + TWR linking difference = period TWR.'
      : null,
  ]
    .filter(Boolean)
    .join('. ')
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
      if (showTwrLinkingDifference) {
        rows.push({ kind: 'synthetic', key: 'twr-linking-difference',
          className: 'performance-calculation-residual-row',
          label: 'TWR Linking Difference', syntheticKind: 'twr_linking_difference' })
      }
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
    if (showTwrLinkingDifference) {
      rows.push({ kind: 'synthetic', key: 'twr-linking-difference',
        className: 'performance-calculation-residual-row',
        label: 'TWR Linking Difference', syntheticKind: 'twr_linking_difference' })
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
    showTwrLinkingDifference,
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
          return column === 'arithmetic_return_contribution' ? finiteNumber(contributionResidual) : null
        case 'twr_linking_difference':
          return column === 'arithmetic_return_contribution' ? finiteNumber(twrLinkingDifference) : null
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
            case 'pending_settlement_fx':
              return finiteNumber(portfolioPendingSettlementFx)
            case 'period_return':
              return finiteNumber(summary?.cumulative_twr)
            case 'return_contribution':
              return finiteNumber(portfolioContribution)
            case 'arithmetic_return_contribution':
              return finiteNumber(portfolioArithmeticContribution)
            case 'own_vol':
              return summary?.risk_result_status === 'available'
                ? finiteNumber(calculationGroupsSummary?.annualized_volatility ?? summary?.annualized_volatility)
                : null
            case 'own_sharpe':
              return summary?.risk_result_status === 'available'
                ? finiteNumber(calculationGroupsSummary?.sharpe_ratio ?? summary?.sharpe_ratio)
                : null
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
      case 'pending_settlement_fx':
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
      case 'arithmetic_return_contribution':
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
    const description = CALCULATION_COLUMN_DESCRIPTIONS[column]
    return (
      <button
        type="button"
        className={`portfolio-table-th-label portfolio-table-th-sortable ${active ? 'portfolio-table-th-sortable-active' : ''}`}
        onClick={() => handleCalculationSort(column)}
        title={`${description ? `${description} ` : ''}Sort ${CALCULATION_COLUMN_LABELS[column]}: ${nextSortLabel}`}
        aria-label={`Sort ${CALCULATION_COLUMN_LABELS[column]}: ${nextSortLabel}`}
      >
        <span>{CALCULATION_COLUMN_LABELS[column]}</span>
        {active ? (
          <span className="portfolio-table-sort-indicator">{calculationSortDirection === 'asc' ? '↑' : '↓'}</span>
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
      ['Portfolio', portfolioId],
      ['Period', periodLabel],
      ['Currency', baseCurrency],
      ['Return basis', summary?.performance_label ?? ''],
      ['Start boundary', summary?.start_boundary_kind ?? ''],
      ['Valuation timezone', workspace?.valuation_timezone ?? ''],
      ['Valuation cutoff', workspace?.valuation_cutoff_policy ?? ''],
      ['Fees and taxes', 'Recorded fees and taxes deducted'],
      ['Risk method', 'Daily observations; annualized by actual period length; risk-free rate and minimum acceptable return = 0'],
      ['Return units', 'Decimal fractions: 0.01 = 1%'],
      ['Contribution method', 'Linked contribution = sum of daily contributions multiplied by preceding portfolio TWR growth; top-level rows sum to period TWR, children sum to their parent. Arithmetic contribution is the unlinked daily sum.'],
      [],
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
      `performance-calculation-${viewSlug || 'view'}-${portfolioId}-${reportStartDate}-${reportEndDate}-${effectiveCalculationGroupBy}`,
      rows,
      format,
      'Calculation',
    )
  }

  return (
    <>
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <PortfolioWorkspaceLayout
        activeSection="Performance"
        busy={loading || waitingForDefaultEndDate}
      >
      <section className="portfolio-detail-surface performance-surface">
        <div className="performance-window-bar">
          <form
            className="performance-window-group"
            onSubmit={(event) => {
              event.preventDefault()
              if (draftWindowError || waitingForDefaultEndDate) return
              updateWindowParams(
                draftStartDate || null,
                draftEndDate || null,
                appliedSinceInception && !draftStartDate ? 'since_inception' : 'dates',
              )
            }}
          >
            <label>
              <span>Start Date</span>
              <input
                className="performance-window-input"
                type="date"
                title="Normally an end-of-day boundary; a funded-segment start includes that day's BOD-to-EOD return."
                value={draftStartDate}
                onChange={(event) => setDraftStartDate(event.target.value)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="performance-window-input"
                type="date"
                title="The interval ends at this date's end-of-day valuation."
                value={draftEndDate}
                onChange={(event) => setDraftEndDate(event.target.value)}
              />
            </label>
            <button
              type="submit"
              className="performance-window-action"
              disabled={!windowDraftChanged || Boolean(draftWindowError) || waitingForDefaultEndDate}
            >
              Apply period
            </button>
          </form>
          <div className="performance-window-actions" aria-label="Performance period controls">
            <button
              type="button"
              className={`performance-window-action${!appliedEndDate ? ' performance-window-action-active' : ''}`}
              aria-pressed={!appliedEndDate}
              disabled={waitingForDefaultEndDate}
              onClick={() =>
                updateWindowParams(
                  appliedSinceInception ? null : effectiveStartDate || null,
                  null,
                  appliedSinceInception ? 'since_inception' : 'dates',
                )
              }
            >
              Latest
            </button>
            <button
              type="button"
              className="performance-window-action"
              onClick={() => updateWindowParams(null, null)}
            >
              Reset
            </button>
            {PERFORMANCE_PERIOD_PRESETS.map((preset) => (
              <button
                key={preset.key}
                type="button"
                className={`performance-window-action${
                  selectedPerformancePeriodPreset === preset.key ? ' performance-window-action-active' : ''
                }`}
                aria-pressed={selectedPerformancePeriodPreset === preset.key}
                disabled={waitingForDefaultEndDate}
                onClick={() => handlePerformancePeriodPreset(preset.key)}
              >
                {preset.label}
              </button>
            ))}
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
              setBenchmarkChart(null)
              setBenchmarkError(null)
            }}
            onClear={() => {
              setBenchmarkInstrumentId('')
              setBenchmarkSearch('')
              setBenchmarkChart(null)
              setBenchmarkError(null)
            }}
            placeholder="Compare benchmark..."
          />
          {benchmarkComparisonDetail ? (
            <InfoHint
              label="Benchmark comparison"
              detail={benchmarkComparisonDetail}
              tone={benchmarkGuard?.mode === 'canonical' ? 'info' : 'warning'}
            />
          ) : null}
          {summary?.as_of_clamp_reason ? (
            <InfoHint
              label="Performance data cutoff"
              detail={`Performance requested through ${summary.requested_end_date ?? effectiveEndDate}; reliable results end on ${
                summary.effective_end_date ?? summary.end_date ?? '—'
              } (${performanceUnavailableReason(summary.as_of_clamp_reason)}).`}
              tone="warning"
            />
          ) : null}
          <QualityWarningsNotice warnings={summary?.quality_warnings} />
        </div>
        {windowError || (windowDraftChanged && draftWindowError) ? (
          <div className="inline-notice inline-notice-error" role="alert">
            {windowDraftChanged && draftWindowError ? draftWindowError : windowError}
          </div>
        ) : null}

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        {calculationTableViewStoreError ? (
          <div className="inline-notice inline-notice-error" role="alert">
            {calculationTableViewStoreError}
          </div>
        ) : null}
        {benchmarkError ? <div className="inline-notice inline-notice-error">{benchmarkError}</div> : null}
        {(loading || waitingForDefaultEndDate) && !workspace ? <CalculationStatus /> : null}
        {loading && workspace ? <CalculationStatus /> : null}
        {!waitingForDefaultEndDate && !loading && !workspace && !error ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {workspace && summary ? (
          <div className="performance-section-stack">
            <section className="portfolio-section-block">
              <div className="portfolio-detail-toolbar portfolio-section-toolbar performance-section-toolbar">
                <div className="panel-title portfolio-title-with-hint">
                  <span>{summary.performance_label}</span>
                  <InfoHint
                    label="Performance details"
                    detail={performancePanelDetail}
                    tone={performanceIsOperational ? 'warning' : 'info'}
                  />
                </div>
              </div>
              {summary.risk_result_status !== 'available' ? (
                <div className="inline-notice inline-notice-warning" role="status">
                  <span>Risk metrics unavailable</span>: {performanceUnavailableReason(summary.risk_unavailable_reason) ?? 'Risk calculation is unavailable'}
                </div>
              ) : null}
              <MetricGrid rows={metricRows} />
            </section>

            <section className="portfolio-section-block">
              <div className="portfolio-detail-toolbar portfolio-section-toolbar performance-section-toolbar performance-calculation-toolbar">
                <div className="performance-calculation-toolbar-main">
                  <div className="panel-title portfolio-title-with-hint">
                    <span>Calculation</span>
                    <InfoHint
                      label="Calculation details"
                      detail={calculationPanelDetail}
                      tone={realizedRiskEstimateLowSample && showsRealizedRiskEstimate ? 'warning' : 'info'}
                    />
                  </div>
                  <div className="performance-calculation-actions">
                    {calculationTableViewStoreReadyPortfolioId === portfolioId ? (
                      <PortfolioTableViewControls
                        views={calculationTableViews}
                        activeViewId={activeCalculationTableViewId}
                        canDelete
                        onSelect={handleSelectCalculationTableView}
                        onSaveAs={handleSaveCalculationTableViewAs}
                        onDelete={handleDeleteCalculationTableView}
                      />
                    ) : (
                      <span className="portfolio-detail-meta">
                        {calculationTableViewStoreError ? 'Table views unavailable' : 'Loading table views'}
                      </span>
                    )}
                    <button
                      type="button"
                      className="portfolio-table-toolbar-button"
                      onClick={() => {
                        setCalculationColumnDraft(calculationColumns)
                        setCalculationModeDraft(calculationTableMode)
                        setCalculationColumnsOpen(true)
                      }}
                    >
                      Columns
                    </button>
                    <button
                      type="button"
                      className="portfolio-table-toolbar-button"
                      onClick={() => setCalculationGroupByOpen(true)}
                    >
                      Group By{'\u00A0: '}
                      {selectedCalculationGroupByOption.label}{effectiveCalculationGroupBy === 'taxonomy' && selectedGroupingTaxonomy ? ` · ${selectedGroupingTaxonomy.name}` : ''}
                    </button>
                    <DownloadFormatMenu
                      wrapperClassName="portfolio-download-menu"
                      buttonClassName="portfolio-table-toolbar-button"
                      menuClassName="portfolio-download-menu-list"
                      itemClassName="portfolio-download-menu-item"
                      disabled={!calculationGroupsWorkspace || calculationGroupsPending}
                      onSelect={handleDownloadCalculation}
                    />
                  </div>
                </div>
              </div>
              {calculationError ? <div className="inline-notice inline-notice-error">{calculationError}</div> : null}
              {calculationGroupsError ? (
                <div className="inline-notice inline-notice-error">{calculationGroupsError}</div>
              ) : null}
              {pendingSettlementFxUnavailable ? (
                <div className="inline-notice inline-notice-warning" role="status">
                  Pending settlement monetary FX is unavailable because a reliable FX boundary or complete attribution
                  coverage is missing ({calculationSummary?.coverage_state}).
                </div>
              ) : calculationSummary?.stale_fx_flag ? (
                <div className="inline-notice inline-notice-warning" role="status">
                  FX attribution is incomplete for this period. Results that require fresh exchange-rate observations
                  remain unavailable until those observations are refreshed.
                </div>
              ) : null}
              {calculationLoading || calculationGroupsPending ? <CalculationStatus /> : null}
              <HorizontalTableScroll className="table-shell">
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
                    ) : calculationGroupsPending ? (
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
              </HorizontalTableScroll>
            </section>
          </div>
        ) : null}
      </section>
      {calculationColumnsOpen ? (
        <div className="portfolio-table-config-backdrop" onClick={() => setCalculationColumnsOpen(false)}>
          <div
            ref={calculationColumnsDialogRef}
            className="portfolio-table-config-modal portfolio-table-config-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose calculation columns"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header">
              <div className="panel-title">Columns</div>
              <button type="button" onClick={() => setCalculationColumnsOpen(false)}>
                Close
              </button>
            </div>

            <div className="performance-calculation-mode-panel">
              <button
                type="button"
                className={`portfolio-table-config-groupby-option ${
                  calculationModeDraft === 'risk_attribution' ? 'portfolio-table-config-groupby-option-active' : ''
                }`}
                onClick={() => setCalculationModeDraft('risk_attribution')}
              >
                <span>Group Attribution Rows</span>
              </button>
              <button
                type="button"
                className={`portfolio-table-config-groupby-option ${
                  calculationModeDraft === 'calculation' ? 'portfolio-table-config-groupby-option-active' : ''
                }`}
                onClick={() => setCalculationModeDraft('calculation')}
              >
                <span>Calculation Ledger Rows</span>
              </button>
            </div>

            <div className="portfolio-table-config-search">
              <input
                className="portfolio-table-config-search-input"
                placeholder="Search columns"
                value={calculationColumnSearch}
                onChange={(event) => setCalculationColumnSearch(event.target.value)}
              />
            </div>

            <div className="portfolio-table-config-grid">
              <div className="portfolio-table-config-categories">
                {CALCULATION_COLUMN_GROUPS.map((group) => (
                  <button
                    type="button"
                    className={
                      group.label === calculationColumnCategory
                        ? 'portfolio-table-config-category-item portfolio-table-config-category-item-active'
                        : 'portfolio-table-config-category-item'
                    }
                    key={group.label}
                    onClick={() => setCalculationColumnCategory(group.label)}
                  >
                    {group.label}
                  </button>
                ))}
              </div>

              <div className="portfolio-table-config-fields">
                {filteredCalculationColumns.length ? (
                  filteredCalculationColumns.map(({ column, groupLabel }) => {
                    const locked = column === LOCKED_CALCULATION_COLUMN
                    return (
                      <label className="portfolio-table-config-field-item" key={`${groupLabel}:${column}`}>
                        <input
                          type="checkbox"
                          checked={calculationColumnDraft.includes(column)}
                          disabled={locked}
                          onChange={(event) => handleCalculationColumnDraftToggle(column, event.target.checked)}
                        />
                        <div>
                          <div className="portfolio-table-config-field-label">{CALCULATION_COLUMN_LABELS[column]}</div>
                          {locked ? <div className="portfolio-table-config-field-meta">required</div> : null}
                        </div>
                      </label>
                    )
                  })
                ) : (
                  <div className="portfolio-table-config-field-empty">No columns.</div>
                )}
              </div>
            </div>

            <div className="portfolio-table-config-actions portfolio-table-config-actions-sticky">
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
                  const nextColumns = normalizeCalculationColumns(calculationColumnDraft)
                  setCalculationColumns(nextColumns)
                  setCalculationTableMode(calculationModeDraft)
                  updateCalculationTableViewState({
                    ...currentCalculationTableViewState,
                    columns: nextColumns,
                    mode: calculationModeDraft,
                  })
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
        <div className="portfolio-table-config-backdrop" onClick={() => setCalculationGroupByOpen(false)}>
          <div
            ref={calculationGroupByDialogRef}
            className="portfolio-table-config-modal portfolio-table-config-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Group performance calculations"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header">
              <div>
                <div className="panel-title">Group By</div>
                <div className="section-heading">Grouping</div>
              </div>
              <button type="button" onClick={() => setCalculationGroupByOpen(false)}>
                Close
              </button>
            </div>
            <div className="portfolio-table-config-body portfolio-table-config-groupby-list">
              <label>{zh ? '分类' : 'Taxonomy'} <select aria-label={zh ? '绩效分组分类' : 'Performance grouping taxonomy'} value={selectedGroupingTaxonomy?.taxonomy_id ?? ''}
                onChange={(event) => { setGroupingTaxonomyId(event.target.value); setCalculationGroupBy('taxonomy') }}>
                {!groupingTaxonomies.length && <option value="">{zh ? '暂无分类' : 'No taxonomy'}</option>}
                {groupingTaxonomies.map((taxonomy) => <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id}>{taxonomy.name}</option>)}
              </select></label>
              {calculationGroupByOptions.map((option) => (
                <button
                  type="button"
                  className={`portfolio-table-config-groupby-option ${
                    option.value === effectiveCalculationGroupBy ? 'portfolio-table-config-groupby-option-active' : ''
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
