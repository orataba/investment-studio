import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import { downloadCsv } from '../lib/csv'
import {
  getPortfolioInstrumentPriceChart,
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioTaxonomyCatalog,
  type PortfolioInstrumentPriceChartPoint,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioContributionAxis,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCalculationGroupsResponse,
  type PortfolioPerformanceCalculationResponse,
  type PortfolioPerformanceResponse,
  type PortfolioPerformanceSummary,
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

const DEFAULT_PERFORMANCE_LOOKBACK_DAYS = 30
const DAYS_PER_YEAR = 365.25

type CalculationGroupRow = PortfolioPerformanceCalculationGroupsResponse['groups'][number]
type CalculationGroupChildRow = CalculationGroupRow['children'][number]
type CalculationDisplayRow = CalculationGroupRow | CalculationGroupChildRow

type PerformanceMetricRow = {
  metric: string
  value: string
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
  calmar: number | null
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
  description: string
  disabled?: boolean
}

type CalculationGroupByKey = 'none' | 'account' | 'instrument_type' | 'currency' | 'taxonomy'

type CalculationTableMode = 'risk_attribution' | 'calculation'

type CalculationColumnKey =
  | 'line'
  | 'pnl_flow'
  | 'start_value'
  | 'end_value'
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
}

type CalculationTableView = PortfolioTableViewOption & {
  state: CalculationTableViewState
  readonly?: boolean
  createdAt?: string
  updatedAt?: string
}

type CalculationTableViewStore = {
  activeViewId: string
  customViews: CalculationTableView[]
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
const CALCULATION_TABLE_VIEWS_STORAGE_KEY = 'yungu.portfolio.performance.calculation.views.v1'

const RISK_ATTRIBUTION_COLUMNS: CalculationColumnKey[] = [
  'line',
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
  { label: 'Core', columns: ['line', 'avg_weight', 'end_weight', 'period_return', 'return_contribution'] },
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

const CALCULATION_COLUMN_DESCRIPTIONS: Partial<Record<CalculationColumnKey, string>> = {
  own_vol: 'Annualized volatility of this group’s canonical risk-basis return during the selected period.',
  own_sharpe: 'Annualized mean return divided by annualized volatility on the canonical risk basis.',
  own_corr: 'Sample correlation between this group’s risk-basis return and the portfolio risk-basis return.',
  beta: 'Covariance of this group’s risk-basis return with portfolio risk-basis return divided by portfolio variance.',
  risk_contribution:
    'Realized variance contribution share: Cov(group contribution, portfolio return) divided by portfolio variance on the canonical risk basis.',
  observations: 'Number of eligible risk-basis observations used for the realized risk attribution metrics.',
}

const DEFAULT_CALCULATION_TABLE_VIEW_STATE: CalculationTableViewState = {
  columns: RISK_ATTRIBUTION_COLUMNS,
  mode: 'risk_attribution',
}

const SYSTEM_CALCULATION_TABLE_VIEWS: CalculationTableView[] = [
  {
    id: 'risk-attribution',
    name: 'Default',
    description: 'Realized group risk contribution and own-risk metrics.',
    readonly: true,
    state: DEFAULT_CALCULATION_TABLE_VIEW_STATE,
  },
  {
    id: 'full-calculation',
    name: 'Full Calculation',
    description: 'Period P&L, gain split, flows, TWR, and return contribution.',
    readonly: true,
    state: {
      columns: FULL_CALCULATION_COLUMNS,
      mode: 'calculation',
    },
  },
  {
    id: 'pnl-breakdown',
    name: 'P&L Breakdown',
    description: 'Compact realized, unrealized, income, expense, and FX attribution.',
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
    },
  },
]

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

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
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

function normalizeCalculationTableViewState(value: unknown): CalculationTableViewState {
  if (!value || typeof value !== 'object') {
    return DEFAULT_CALCULATION_TABLE_VIEW_STATE
  }
  const record = value as Partial<CalculationTableViewState>
  return {
    columns: normalizeCalculationColumns(
      Array.isArray(record.columns) ? (record.columns as CalculationColumnKey[]) : RISK_ATTRIBUTION_COLUMNS,
    ),
    mode: parseCalculationTableMode(typeof record.mode === 'string' ? record.mode : null),
  }
}

function serializeCalculationTableViewState(value: CalculationTableViewState) {
  const normalized = normalizeCalculationTableViewState(value)
  return JSON.stringify({
    columns: normalized.columns,
    mode: normalized.mode,
  })
}

function calculationTableViewStatesEqual(left: CalculationTableViewState, right: CalculationTableViewState) {
  return serializeCalculationTableViewState(left) === serializeCalculationTableViewState(right)
}

function normalizeCalculationTableViewStore(value: unknown): CalculationTableViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<CalculationTableViewStore>) : {}
  const customViews = Array.isArray(record.customViews)
    ? record.customViews
        .filter((view): view is CalculationTableView => Boolean(view && typeof view === 'object' && typeof view.id === 'string'))
        .map((view) => ({
          id: view.id,
          name: typeof view.name === 'string' && view.name.trim() ? view.name.trim() : 'Custom View',
          description: typeof view.description === 'string' ? view.description : null,
          readonly: false,
          createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
          updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
          state: normalizeCalculationTableViewState(view.state),
        }))
    : []
  const customViewIds = new Set(customViews.map((view) => view.id))
  const knownViewIds = new Set([...SYSTEM_CALCULATION_TABLE_VIEWS.map((view) => view.id), ...customViewIds])
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_CALCULATION_TABLE_VIEWS[0].id
  return { activeViewId, customViews }
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
  return [...SYSTEM_CALCULATION_TABLE_VIEWS, ...store.customViews]
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

function sampleCovariance(leftValues: number[], rightValues: number[]) {
  if (leftValues.length < 2 || leftValues.length !== rightValues.length) {
    return null
  }
  const leftMean = leftValues.reduce((total, value) => total + value, 0) / leftValues.length
  const rightMean = rightValues.reduce((total, value) => total + value, 0) / rightValues.length
  return (
    leftValues.reduce(
      (total, leftValue, index) => total + (leftValue - leftMean) * (rightValues[index] - rightMean),
      0,
    ) /
    (leftValues.length - 1)
  )
}

function sampleCorrelation(leftValues: number[], rightValues: number[]) {
  const covariance = sampleCovariance(leftValues, rightValues)
  const leftStddev = sampleStddev(leftValues)
  const rightStddev = sampleStddev(rightValues)
  return covariance != null && leftStddev != null && rightStddev != null && leftStddev > 0 && rightStddev > 0
    ? covariance / (leftStddev * rightStddev)
    : null
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function annualizationPeriodsPerYear(dateKeys: string[], observationCount = dateKeys.length, startDate?: string | null) {
  const sortedDates = [...dateKeys].sort()
  if (observationCount < 1 || sortedDates.length < 2) {
    return null
  }
  if (startDate) {
    const elapsedDays = dayDiff(startDate, sortedDates[sortedDates.length - 1])
    return elapsedDays != null && elapsedDays > 0 ? (observationCount / elapsedDays) * DAYS_PER_YEAR : null
  }
  const elapsedDays = dayDiff(sortedDates[0], sortedDates[sortedDates.length - 1])
  if (elapsedDays == null) {
    return null
  }
  const gaps = sortedDates
    .slice(1)
    .map((dateKey, index) => dayDiff(sortedDates[index], dateKey))
    .filter((value): value is number => value != null && value > 0)
    .sort((left, right) => left - right)
  const medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1
  const observationSpanDays = elapsedDays + medianGap
  return observationSpanDays > 0 ? (observationCount / observationSpanDays) * DAYS_PER_YEAR : null
}

function annualizedVolatility(values: number[], dateKeys: string[] = [], startDate?: string | null) {
  const stddev = sampleStddev(values)
  const periodsPerYear = annualizationPeriodsPerYear(dateKeys, values.length, startDate)
  return stddev == null || periodsPerYear == null ? null : stddev * Math.sqrt(periodsPerYear)
}

function annualizedDownsideVolatility(values: number[], dateKeys: string[] = [], startDate?: string | null) {
  const periodsPerYear = annualizationPeriodsPerYear(dateKeys, values.length, startDate)
  if (!values.length || periodsPerYear == null) {
    return null
  }
  const downsideSquares = values.map((value) => Math.min(0, value) ** 2)
  if (!downsideSquares.some((value) => value > 0)) {
    return null
  }
  return Math.sqrt(downsideSquares.reduce((total, value) => total + value, 0) / values.length) * Math.sqrt(periodsPerYear)
}

function annualizedMeanReturn(values: number[], dateKeys: string[], startDate?: string | null) {
  const periodsPerYear = annualizationPeriodsPerYear(dateKeys, values.length, startDate)
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

function buildBenchmarkPeriodMetrics(
  points: PortfolioInstrumentPriceChartPoint[],
  startDate: string,
  endDate: string,
): BenchmarkPeriodMetrics | null {
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value) && point.date >= startDate && point.date <= endDate)
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const firstPoint = sortedPoints[0]
  const lastPoint = sortedPoints[sortedPoints.length - 1]
  if (!firstPoint || !lastPoint || sortedPoints.length < 2) {
    return null
  }

  const dailyReturns = sortedPoints
    .slice(1)
    .map((point, index) => {
      const previous = sortedPoints[index]
      return previous.value !== 0 ? { date: point.date, value: point.value / previous.value - 1 } : null
    })
    .filter((value): value is { date: string; value: number } => value != null)
  const periodReturn = firstPoint.value !== 0 ? lastPoint.value / firstPoint.value - 1 : null
  const elapsedDays = dayDiff(firstPoint.date, lastPoint.date)
  const annualizedReturn =
    periodReturn != null && elapsedDays != null && elapsedDays > 0
      ? (1 + periodReturn) ** (DAYS_PER_YEAR / elapsedDays) - 1
      : null
  const dailyReturnValues = dailyReturns.map((point) => point.value)
  const dailyReturnDates = dailyReturns.map((point) => point.date)
  const annualizedVol = annualizedVolatility(dailyReturnValues, dailyReturnDates, firstPoint.date)
  const annualizedDownsideVol = annualizedDownsideVolatility(dailyReturnValues, dailyReturnDates, firstPoint.date)
  const annualizedMean = annualizedMeanReturn(dailyReturnValues, dailyReturnDates, firstPoint.date)

  let highWater = firstPoint.value
  let currentDrawdown: number | null = null
  let maxDrawdown: number | null = null
  sortedPoints.forEach((point) => {
    highWater = Math.max(highWater, point.value)
    const drawdown = highWater > 0 ? point.value / highWater - 1 : null
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
    calmar: ratioToDrawdown(annualizedReturn, maxDrawdown),
    currentDrawdown,
    maxDrawdown,
    dailyReturns,
  }
}

function buildRelativePerformanceMetrics(
  portfolioDailySeries: PortfolioDailyPerformancePoint[],
  benchmarkMetrics: BenchmarkPeriodMetrics | null,
): RelativePerformanceMetrics | null {
  if (!benchmarkMetrics?.dailyReturns.length) {
    return null
  }
  const benchmarkByDate = new Map(benchmarkMetrics.dailyReturns.map((point) => [point.date, point.value]))
  const pairs = portfolioDailySeries
    .map((point) => {
      const portfolioReturn = finiteNumber(point.daily_twr)
      const benchmarkReturn = benchmarkByDate.get(point.as_of_date)
      return portfolioReturn != null && benchmarkReturn != null && point.return_observation_eligible
        ? { date: point.as_of_date, portfolioReturn, benchmarkReturn }
        : null
    })
    .filter((value): value is { date: string; portfolioReturn: number; benchmarkReturn: number } => value != null)

  if (pairs.length < 2) {
    return null
  }

  const dates = pairs.map((point) => point.date)
  const portfolioReturns = pairs.map((point) => point.portfolioReturn)
  const benchmarkReturns = pairs.map((point) => point.benchmarkReturn)
  const activeReturns = pairs.map((point) => point.portfolioReturn - point.benchmarkReturn)
  const trackingError = annualizedVolatility(activeReturns, dates)
  const activeAnnualizedMean = annualizedMeanReturn(activeReturns, dates)

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

function buildPerformanceMetricRows(
  summary: PortfolioPerformanceSummary,
  dailySeries: PortfolioDailyPerformancePoint[],
  baseCurrency: string,
  selectedBenchmarkInstrument: SharedInstrumentRecord | null,
  benchmarkLoading: boolean,
  benchmarkMetrics: BenchmarkPeriodMetrics | null,
) {
  const relativeMetrics = buildRelativePerformanceMetrics(dailySeries, benchmarkMetrics)
  const showComparison = selectedBenchmarkInstrument != null || benchmarkLoading
  const irr = finiteNumber(summary.irr) ?? finiteNumber(summary.mwror)
  const calmarRatio = ratioToDrawdown(summary.annualized_twr, summary.max_drawdown)
  const returnDifference =
    summary.cumulative_twr != null && benchmarkMetrics?.periodReturn != null
      ? summary.cumulative_twr - benchmarkMetrics.periodReturn
      : null
  const annualizedReturnDifference =
    summary.annualized_twr != null && benchmarkMetrics?.annualizedReturn != null
      ? summary.annualized_twr - benchmarkMetrics.annualizedReturn
      : null
  const volatilityDifference =
    summary.annualized_volatility != null && benchmarkMetrics?.annualizedVolatility != null
      ? summary.annualized_volatility - benchmarkMetrics.annualizedVolatility
      : null
  const downsideVolatilityDifference =
    summary.annualized_downside_volatility != null && benchmarkMetrics?.annualizedDownsideVolatility != null
      ? summary.annualized_downside_volatility - benchmarkMetrics.annualizedDownsideVolatility
      : null
  const sharpeDifference =
    summary.sharpe_ratio != null && benchmarkMetrics?.sharpe != null ? summary.sharpe_ratio - benchmarkMetrics.sharpe : null
  const sortinoDifference =
    summary.sortino_ratio != null && benchmarkMetrics?.sortino != null ? summary.sortino_ratio - benchmarkMetrics.sortino : null
  const calmarDifference =
    calmarRatio != null && benchmarkMetrics?.calmar != null ? calmarRatio - benchmarkMetrics.calmar : null
  const currentDrawdownDifference =
    summary.current_drawdown != null && benchmarkMetrics?.currentDrawdown != null
      ? summary.current_drawdown - benchmarkMetrics.currentDrawdown
      : null
  const maxDrawdownDifference =
    summary.max_drawdown != null && benchmarkMetrics?.maxDrawdown != null
      ? summary.max_drawdown - benchmarkMetrics.maxDrawdown
      : null

  return [
    {
      metric: 'TWR',
      value: signedPercent(summary.cumulative_twr),
      valueClassName: signedValueClass(summary.cumulative_twr),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.periodReturn),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkMetrics?.periodReturn,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, returnDifference),
      showComparison,
    },
    {
      metric: 'Annualized TWR',
      value: signedPercent(summary.annualized_twr),
      valueClassName: signedValueClass(summary.annualized_twr),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.annualizedReturn),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkMetrics?.annualizedReturn,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, annualizedReturnDifference),
      differenceClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        annualizedReturnDifference,
      ),
      showComparison,
    },
    {
      metric: 'IRR / MWR',
      value: signedPercent(irr),
      valueClassName: signedValueClass(irr),
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
      value: formatRatio(calmarRatio),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.calmar, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, calmarDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, calmarDifference),
      showComparison,
    },
    {
      metric: 'Volatility',
      value: formatPercent(summary.annualized_volatility),
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkMetrics?.annualizedVolatility,
        formatPercent,
      ),
      benchmarkClassName: undefined,
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, volatilityDifference),
      showComparison,
    },
    {
      metric: 'Downside Volatility',
      value: formatPercent(summary.annualized_downside_volatility),
      benchmark: benchmarkMetricText(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkMetrics?.annualizedDownsideVolatility,
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
      value: formatRatio(summary.sharpe_ratio),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.sharpe, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sharpeDifference),
      showComparison,
    },
    {
      metric: 'Sortino Ratio',
      value: formatRatio(summary.sortino_ratio),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.sortino, formatRatio),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference, signedRatio),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, sortinoDifference),
      showComparison,
    },
    {
      metric: 'Current DD',
      value: signedPercent(summary.current_drawdown),
      valueClassName: signedValueClass(summary.current_drawdown),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.currentDrawdown),
      benchmarkClassName: benchmarkMetricClassName(
        selectedBenchmarkInstrument,
        benchmarkLoading,
        benchmarkMetrics?.currentDrawdown,
      ),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, currentDrawdownDifference),
      showComparison,
    },
    {
      metric: 'Max DD',
      value: signedPercent(summary.max_drawdown),
      valueClassName: signedValueClass(summary.max_drawdown),
      benchmark: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.maxDrawdown),
      benchmarkClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics?.maxDrawdown),
      difference: benchmarkMetricText(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      differenceClassName: benchmarkMetricClassName(selectedBenchmarkInstrument, benchmarkLoading, maxDrawdownDifference),
      showComparison,
    },
    {
      metric: 'Tracking Error',
      value: formatPercent(relativeMetrics?.trackingError),
    },
    {
      metric: 'Information Ratio',
      value: formatRatio(relativeMetrics?.informationRatio),
    },
    {
      metric: 'Beta',
      value: formatRatio(relativeMetrics?.beta),
    },
    {
      metric: 'Upside Capture',
      value: formatPercent(relativeMetrics?.upsideCapture == null ? null : relativeMetrics.upsideCapture / 100),
    },
    {
      metric: 'Downside Capture',
      value: formatPercent(relativeMetrics?.downsideCapture == null ? null : relativeMetrics.downsideCapture / 100),
    },
    {
      metric: 'CAP Ratio',
      value: formatRatio(relativeMetrics?.captureRatio),
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
                      <span>{row.metric}</span>
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
  const appliedStartDate = searchParams.get('start_date') ?? ''
  const appliedEndDate = searchParams.get('end_date') ?? ''
  const effectiveEndDate = appliedEndDate || todayDate
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
  const [calculationGroupBy, setCalculationGroupBy] = useState<CalculationGroupByKey>('none')
  const [calculationGroupByOpen, setCalculationGroupByOpen] = useState(false)
  const [calculationGroupsWorkspace, setCalculationGroupsWorkspace] =
    useState<PortfolioPerformanceCalculationGroupsResponse | null>(null)
  const [calculationGroupsLoading, setCalculationGroupsLoading] = useState(false)
  const [calculationGroupsError, setCalculationGroupsError] = useState<string | null>(null)
  const initialCalculationTableViewStore = useMemo(() => loadCalculationTableViewStore(), [])
  const initialCalculationTableViewState = useMemo(
    () => resolveCalculationTableViewState(initialCalculationTableViewStore, initialCalculationTableViewStore.activeViewId),
    [initialCalculationTableViewStore],
  )
  const [calculationTableViewStore, setCalculationTableViewStore] = useState<CalculationTableViewStore>(
    () => initialCalculationTableViewStore,
  )
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
  const [calculationModeDraft, setCalculationModeDraft] = useState<CalculationTableMode>(
    () => initialCalculationTableViewState.mode,
  )
  const [calculationColumnsOpen, setCalculationColumnsOpen] = useState(false)
  const [calculationColumnCategory, setCalculationColumnCategory] = useState(
    CALCULATION_COLUMN_GROUPS[0]?.label ?? 'Core',
  )
  const [calculationColumnSearch, setCalculationColumnSearch] = useState('')
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
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
        description: 'Show instrument lines directly without aggregating them into a higher-level group.',
      },
      {
        value: 'instrument_type',
        label: 'Instrument Type',
        description: 'Group period calculation rows by instrument type, with cash kept in a cash line.',
      },
      {
        value: 'currency',
        label: 'Currency',
        description: 'Group position and cash effects by local currency.',
      },
      {
        value: 'account',
        label: 'Account',
        description: 'Group period calculation rows by portfolio account or custody sleeve.',
      },
      {
        value: 'taxonomy',
        label: 'Taxonomy',
        description: defaultPlanningTaxonomy
          ? `Group rows by the default planning taxonomy: ${defaultPlanningTaxonomy.name}.`
          : 'No default planning taxonomy is configured for this portfolio.',
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
    }),
    [calculationColumns, calculationTableMode],
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

  function applyCalculationTableViewState(state: CalculationTableViewState) {
    const normalized = normalizeCalculationTableViewState(state)
    setCalculationColumns(normalized.columns)
    setCalculationColumnDraft(normalized.columns)
    setCalculationTableMode(normalized.mode)
    setCalculationModeDraft(normalized.mode)
  }

  function handleSelectCalculationTableView(viewId: string) {
    const nextView = getCalculationTableViewById(calculationTableViewStore, viewId)
    setActiveCalculationTableViewId(nextView.id)
    setCalculationTableViewStore((current) => ({ ...current, activeViewId: nextView.id }))
    applyCalculationTableViewState(resolveCalculationTableViewState(calculationTableViewStore, nextView.id))
  }

  function handleSaveCalculationTableView() {
    if (activeCalculationTableView.readonly) {
      return
    }
    const timestamp = new Date().toISOString()
    setCalculationTableViewStore((current) => ({
      ...current,
      activeViewId: activeCalculationTableViewId,
      customViews: current.customViews.map((view) =>
        view.id === activeCalculationTableViewId
          ? {
              ...view,
              state: currentCalculationTableViewState,
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
      customViews: [...current.customViews, nextView],
    }))
    setActiveCalculationTableViewId(viewId)
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
      customViews: current.customViews.filter((view) => view.id !== targetView.id),
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
    saveCalculationTableViewStore(calculationTableViewStore)
  }, [calculationTableViewStore])

  useEffect(() => {
    if (!portfolioId) {
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
  }, [portfolioId, effectiveStartDate, effectiveEndDate])

  useEffect(() => {
    if (!portfolioId) {
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
  }, [portfolioId, effectiveStartDate, effectiveEndDate])

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
    if (!portfolioId) {
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
  ])

  useEffect(() => {
    if (!portfolioId || !benchmarkInstrumentId) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioInstrumentPriceChart(portfolioId, benchmarkInstrumentId, {
      as_of_date: effectiveEndDate,
      range: 'all',
    })
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
    }
  }, [benchmarkInstrumentId, effectiveEndDate, portfolioId])

  function updateWindowParams(nextStartDate: string | null, nextEndDate: string | null) {
    const nextParams = new URLSearchParams(searchParams)
    if (nextStartDate) {
      nextParams.set('start_date', nextStartDate)
    } else {
      nextParams.delete('start_date')
    }
    if (nextEndDate) {
      nextParams.set('end_date', nextEndDate)
    } else {
      nextParams.delete('end_date')
    }
    setSearchParams(nextParams)
  }

  const summary = workspace?.summary ?? null
  const baseCurrency = workspace?.base_currency ?? calculationWorkspace?.base_currency ?? calculationGroupsWorkspace?.base_currency ?? 'USD'
  const periodLabel =
    summary?.start_date && summary.end_date
      ? `${summary.start_date} to ${summary.end_date}`
      : `${effectiveStartDate} to ${effectiveEndDate}`

  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId) ?? null
  const benchmarkMetrics = useMemo(
    () => buildBenchmarkPeriodMetrics(benchmarkChart?.points ?? [], effectiveStartDate, effectiveEndDate),
    [benchmarkChart, effectiveStartDate, effectiveEndDate],
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
          )
        : [],
    [summary, workspace?.daily_series, baseCurrency, selectedBenchmarkInstrument, benchmarkLoading, benchmarkMetrics],
  )
  const calculationRows = useMemo(() => {
    if (!calculationGroupsWorkspace) {
      return []
    }
    return calculationGroupsWorkspace.groups.slice().sort((left, right) => {
      const leftMagnitude = Math.abs(finiteNumber(left.period_contribution) ?? finiteNumber(left.total_pnl) ?? 0)
      const rightMagnitude = Math.abs(finiteNumber(right.period_contribution) ?? finiteNumber(right.total_pnl) ?? 0)
      return rightMagnitude - leftMagnitude || left.group_label.localeCompare(right.group_label)
    })
  }, [calculationGroupsWorkspace])
  const riskAttributionRows = useMemo(() => {
    return calculationRows.slice().sort((left, right) => {
      const leftRisk = Math.abs(finiteNumber(left.realized_risk_contribution) ?? 0)
      const rightRisk = Math.abs(finiteNumber(right.realized_risk_contribution) ?? 0)
      const leftContribution = Math.abs(finiteNumber(left.period_contribution) ?? 0)
      const rightContribution = Math.abs(finiteNumber(right.period_contribution) ?? 0)
      return rightRisk - leftRisk || rightContribution - leftContribution || left.group_label.localeCompare(right.group_label)
    })
  }, [calculationRows])

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
  const calculationMeta = `${periodLabel} · ${formatNumber(calculationRows.length, 0)} ${calculationAxisCountLabel(
    resolvedCalculationGroupBy,
  )}${calculationChildRowCount ? ` · ${formatNumber(calculationChildRowCount, 0)} instruments/cash` : ''}${
    calculationRiskStatusLabel ? ` · ${calculationRiskStatusLabel}` : ''
  }`
  const calculationStatusLabel =
    (calculationLoading && calculationWorkspace) ||
    (calculationGroupsLoading && calculationGroupsWorkspace)
      ? `Updating ${calculationGroupLabel.toLowerCase()} calculation…`
      : calculationTableMode === 'risk_attribution'
        ? 'Building canonical risk attribution…'
        : 'Building performance calculation…'
  const riskContributionTotal = riskAttributionRows.reduce(
    (total, row) => total + (finiteNumber(row.realized_risk_contribution) ?? 0),
    0,
  )
  const riskContributionResidual = riskAttributionRows.length ? 1 - riskContributionTotal : null
  const showRiskContributionResidual =
    calculationTableMode === 'risk_attribution' &&
    visibleCalculationColumns.includes('risk_contribution') &&
    riskContributionResidual != null &&
    Math.abs(riskContributionResidual) > 0.0005
  const calculationTableRows = useMemo<CalculationTableRow[]>(() => {
    if (calculationTableMode === 'risk_attribution') {
      const rows: CalculationTableRow[] = riskAttributionRows.map((row) => ({
        kind: 'group',
        key: `risk:${row.group_key}`,
        className: 'performance-calculation-group-row',
        row,
      }))
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
  }, [calculationRows, calculationTableMode, riskAttributionRows, showContributionResidual, showRiskContributionResidual])

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
              return finiteNumber(summary?.start_nav)
            case 'end_value':
              return finiteNumber(summary?.end_nav)
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
              return 1
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

    const source = row.row
    switch (column) {
      case 'pnl_flow':
        return finiteNumber(source.total_pnl)
      case 'start_value':
        return finiteNumber(source.initial_value)
      case 'end_value':
        return finiteNumber(source.final_value)
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

  function handleDownloadCalculationCsv() {
    if (!portfolioId || !calculationGroupsWorkspace) {
      return
    }

    const header = visibleCalculationColumns.map((column) => CALCULATION_COLUMN_LABELS[column])
    const rows: Array<Array<string | number | null>> = [
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
    downloadCsv(
      `performance-calculation-${viewSlug || 'view'}-${portfolioId}-${effectiveStartDate}-${effectiveEndDate}-${effectiveCalculationGroupBy}.csv`,
      rows,
    )
  }

  return (
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
                onChange={(event) => updateWindowParams(event.target.value || null, appliedEndDate || null)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={effectiveEndDate}
                onChange={(event) => updateWindowParams(appliedStartDate || null, event.target.value || null)}
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
        </div>

        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        {benchmarkError ? <div className="inline-notice inline-notice-error">{benchmarkError}</div> : null}
        {loading && !workspace ? <CalculationStatus label="Loading performance workspace…" /> : null}
        {loading && workspace ? <CalculationStatus label="Refreshing performance workspace…" /> : null}
        {!loading && !workspace && !error ? (
          <div className="empty-state">Select a portfolio to review performance.</div>
        ) : null}

        {workspace && summary ? (
          <div className="performance-section-stack">
            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar performance-section-toolbar">
                <div>
                  <div className="panel-title">Return &amp; Risk Metrics</div>
                  <div className="portfolio-detail-meta">{periodLabel}</div>
                </div>
              </div>
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
                      canSave={!activeCalculationTableView.readonly}
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
                    <button
                      type="button"
                      className="holdings-toolbar-button"
                      onClick={handleDownloadCalculationCsv}
                      disabled={
                        !calculationGroupsWorkspace ||
                        calculationGroupsLoading
                      }
                    >
                      Download
                    </button>
                  </div>
                </div>
              </div>
              {calculationError ? <div className="inline-notice inline-notice-error">{calculationError}</div> : null}
              {calculationGroupsError ? (
                <div className="inline-notice inline-notice-error">{calculationGroupsError}</div>
              ) : null}
              {calculationLoading || calculationGroupsLoading ? (
                <CalculationStatus label={calculationStatusLabel} />
              ) : null}
              <div className="table-shell">
                <table className="transactions-table performance-calculation-table">
                  <thead>
                    <tr>
                      {visibleCalculationColumns.map((column) => {
                        const description = CALCULATION_COLUMN_DESCRIPTIONS[column]
                        return (
                          <th key={column} title={description}>
                            {CALCULATION_COLUMN_LABELS[column]}
                          </th>
                        )
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {calculationTableRows.length ? (
                      calculationTableRows.map((row) => renderCalculationTableRow(row))
                    ) : calculationGroupsLoading ? (
                      <TableStatusRow
                        colSpan={visibleCalculationColumns.length}
                        label={`Loading ${calculationGroupLabel.toLowerCase()} calculation…`}
                      />
                    ) : calculationGroupsError ? (
                      <TableStatusRow colSpan={visibleCalculationColumns.length} label={calculationGroupsError} tone="error" />
                    ) : (
                      <TableStatusRow
                        colSpan={visibleCalculationColumns.length}
                        label={`No ${calculationGroupLabel.toLowerCase()} calculation rows available.`}
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
          <div className="holdings-modal holdings-columns-modal" onClick={(event) => event.stopPropagation()}>
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
                <small>Show top-level group lines with portfolio-relative risk attribution metrics.</small>
              </button>
              <button
                type="button"
                className={`holdings-groupby-option ${
                  calculationModeDraft === 'calculation' ? 'holdings-groupby-option-active' : ''
                }`}
                onClick={() => setCalculationModeDraft('calculation')}
              >
                <span>Calculation Ledger Rows</span>
                <small>Show initial/final value, flows, group rows, and available child rows.</small>
              </button>
            </div>

            <div className="holdings-modal-search">
              <input
                className="holdings-modal-search-input"
                placeholder="Search by field name or code"
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
                    const description = CALCULATION_COLUMN_DESCRIPTIONS[column]
                    return (
                      <label className="holdings-field-item" key={`${groupLabel}:${column}`} title={description}>
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
                  <div className="holdings-field-empty">No fields matched the current search.</div>
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
          <div className="holdings-modal holdings-compact-modal" onClick={(event) => event.stopPropagation()}>
            <div className="holdings-modal-header">
              <div>
                <div className="panel-title">Group By</div>
                <div className="section-heading">Choose Grouping Dimension</div>
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

export default PerformancePage
