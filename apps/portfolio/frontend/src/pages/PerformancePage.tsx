import { useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import { downloadCsv } from '../lib/csv'
import {
  getPortfolioAssetPriceChart,
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioTaxonomyCatalog,
  type PortfolioAssetPriceChartPoint,
  type PortfolioAssetPriceChartResponse,
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
  value: PortfolioContributionAxis
  label: string
  description: string
  disabled?: boolean
}

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

function fxPnlAmount(row: Pick<CalculationGroupRow, 'cash_currency_gains' | 'asset_currency_gains'>) {
  return sumNullable(row.cash_currency_gains, row.asset_currency_gains)
}

function calculationAxisLabel(axis: PortfolioContributionAxis) {
  if (axis === 'account') {
    return 'Account'
  }
  if (axis === 'asset_type') {
    return 'Asset Type'
  }
  if (axis === 'currency') {
    return 'Currency'
  }
  if (axis === 'taxonomy') {
    return 'Taxonomy'
  }
  return 'Asset'
}

function calculationAxisCountLabel(axis: PortfolioContributionAxis) {
  if (axis === 'account') {
    return 'accounts'
  }
  if (axis === 'asset_type') {
    return 'asset types'
  }
  if (axis === 'currency') {
    return 'currencies'
  }
  if (axis === 'taxonomy') {
    return 'groups'
  }
  return 'assets'
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

function compoundReturn(values: number[]) {
  if (!values.length) {
    return null
  }
  return values.reduce((growthIndex, value) => growthIndex * (1 + value), 1) - 1
}

function annualizedReturnFromDailyReturns(values: number[], dateKeys: string[], startDate?: string | null) {
  const periodReturn = compoundReturn(values)
  const sortedDates = [...dateKeys].sort()
  const firstDate = startDate ?? sortedDates[0]
  const lastDate = sortedDates[sortedDates.length - 1]
  const elapsedDays = firstDate && lastDate ? dayDiff(firstDate, lastDate) : null
  return periodReturn != null && elapsedDays != null && elapsedDays > 0
    ? (1 + periodReturn) ** (DAYS_PER_YEAR / elapsedDays) - 1
    : null
}

function ratioToDrawdown(returnValue: number | null | undefined, maxDrawdown: number | null | undefined) {
  const finiteReturn = finiteNumber(returnValue)
  const finiteDrawdown = finiteNumber(maxDrawdown)
  return finiteReturn != null && finiteDrawdown != null && finiteDrawdown < 0
    ? finiteReturn / Math.abs(finiteDrawdown)
    : null
}

function buildBenchmarkPeriodMetrics(
  points: PortfolioAssetPriceChartPoint[],
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
      annualizedReturn != null && annualizedVol != null && annualizedVol !== 0
        ? annualizedReturn / annualizedVol
        : null,
    sortino:
      annualizedReturn != null && annualizedDownsideVol != null && annualizedDownsideVol !== 0
        ? annualizedReturn / annualizedDownsideVol
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
  const trackingError = annualizedVolatility(activeReturns, dates, dates[0])
  const portfolioAnnualizedReturn = annualizedReturnFromDailyReturns(portfolioReturns, dates, dates[0])
  const benchmarkAnnualizedReturn = annualizedReturnFromDailyReturns(benchmarkReturns, dates, dates[0])
  const activeAnnualizedReturn =
    portfolioAnnualizedReturn != null && benchmarkAnnualizedReturn != null
      ? portfolioAnnualizedReturn - benchmarkAnnualizedReturn
      : null

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
      activeAnnualizedReturn != null && trackingError != null && trackingError !== 0
        ? activeAnnualizedReturn / trackingError
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

function EmptyNumberCells({ count }: { count: number }) {
  return Array.from({ length: count }, (_, index) => (
    <td key={index} className="performance-cell-number">
      —
    </td>
  ))
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
  const [calculationGroupBy, setCalculationGroupBy] = useState<PortfolioContributionAxis>('instrument')
  const [calculationGroupByOpen, setCalculationGroupByOpen] = useState(false)
  const [calculationGroupsWorkspace, setCalculationGroupsWorkspace] =
    useState<PortfolioPerformanceCalculationGroupsResponse | null>(null)
  const [calculationGroupsLoading, setCalculationGroupsLoading] = useState(false)
  const [calculationGroupsError, setCalculationGroupsError] = useState<string | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkAssetId, setBenchmarkAssetId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioAssetPriceChartResponse | null>(null)
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
  const resolvedCalculationGroupBy =
    calculationGroupBy === 'taxonomy' && !defaultPlanningTaxonomy ? 'instrument' : calculationGroupBy
  const calculationGroupByOptions = useMemo<CalculationGroupByOption[]>(
    () => [
      {
        value: 'instrument',
        label: 'Asset',
        description: 'Group period P&L, TWR, contribution, and gain split by instrument.',
      },
      {
        value: 'asset_type',
        label: 'Asset Type',
        description: 'Group period calculation rows by asset class, with cash kept in a cash line.',
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
    calculationGroupByOptions.find((option) => option.value === resolvedCalculationGroupBy) ??
    calculationGroupByOptions[0]

  function handleCalculationGroupByChange(value: PortfolioContributionAxis) {
    setCalculationGroupBy(value)
    setCalculationGroupByOpen(false)
  }

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
    if (!portfolioId || !benchmarkAssetId) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioAssetPriceChart(portfolioId, benchmarkAssetId, {
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
  }, [benchmarkAssetId, effectiveEndDate, portfolioId])

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
    benchmarkInstruments.find((instrument) => instrument.asset_id === benchmarkAssetId) ?? null
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

  const calculationSummary = calculationWorkspace?.summary ?? null
  const calculationGroupsSummary = calculationGroupsWorkspace?.summary ?? null
  const initialValue = calculationSummary?.initial_value ?? summary?.start_nav ?? null
  const finalValue = calculationSummary?.final_value ?? summary?.end_nav ?? null
  const portfolioRealizedGain = calculationSummary?.realized_capital_gains ?? summary?.realized_pnl ?? null
  const portfolioUnrealizedGain = calculationSummary?.unrealized_capital_gains ?? null
  const portfolioIncome = calculationSummary?.earnings ?? summary?.income_cash_amount ?? null
  const portfolioFxPnl = sumNullable(calculationSummary?.cash_currency_gains, calculationSummary?.asset_currency_gains)
  const portfolioFees = expenseImpact(calculationSummary?.fees)
  const portfolioTaxes = expenseImpact(calculationSummary?.taxes)
  const portfolioPeriodPnl = calculationSummary?.delta ?? summary?.delta ?? summary?.total_pnl ?? null
  const portfolioContribution = calculationGroupsSummary?.total_period_contribution ?? summary?.cumulative_twr ?? null
  const contributionResidual = calculationGroupsSummary?.contribution_residual ?? null
  const showContributionResidual = contributionResidual != null && Math.abs(contributionResidual) > 0.0000005
  const calculationGroupLabel = calculationAxisLabel(resolvedCalculationGroupBy)
  const calculationMeta = `${periodLabel} · ${formatNumber(calculationRows.length, 0)} ${calculationAxisCountLabel(
    resolvedCalculationGroupBy,
  )}`

  function handleDownloadCalculationCsv() {
    if (!portfolioId || !calculationGroupsWorkspace) {
      return
    }

    const header = [
      'Line',
      'P&L / Flow',
      'Start Value',
      'End Value',
      'Avg Weight',
      'End Weight',
      'Realized Gain',
      'Unrealized Gain',
      'Income',
      'Fees',
      'Taxes',
      'FX P&L',
      'TWR',
      'Contribution',
    ]
    const rows: Array<Array<string | number | null>> = [
      header,
      ['Initial Value', null, csvNumber(initialValue), ...Array(11).fill(null)],
      ...calculationRows.map((row) => [
        row.group_label,
        csvNumber(row.total_pnl),
        csvNumber(row.initial_value),
        csvNumber(row.final_value),
        csvNumber(row.average_weight),
        csvNumber(row.ending_weight),
        csvNumber(row.realized_capital_gains),
        csvNumber(row.unrealized_pnl_change),
        csvNumber(row.earnings),
        csvNumber(expenseImpact(row.fees)),
        csvNumber(expenseImpact(row.taxes)),
        csvNumber(fxPnlAmount(row)),
        csvNumber(row.period_return),
        csvNumber(row.period_contribution),
      ]),
      ['Deposits', csvNumber(calculationSummary?.deposits), ...Array(12).fill(null)],
      ['Withdrawals', csvNumber(expenseImpact(calculationSummary?.withdrawals)), ...Array(12).fill(null)],
      [
        'Portfolio Total',
        csvNumber(portfolioPeriodPnl),
        csvNumber(summary?.start_nav),
        csvNumber(summary?.end_nav),
        1,
        1,
        csvNumber(portfolioRealizedGain),
        csvNumber(portfolioUnrealizedGain),
        csvNumber(portfolioIncome),
        csvNumber(portfolioFees),
        csvNumber(portfolioTaxes),
        csvNumber(portfolioFxPnl),
        csvNumber(summary?.cumulative_twr),
        csvNumber(portfolioContribution),
      ],
    ]
    if (showContributionResidual) {
      rows.push(['Contribution Residual', ...Array(12).fill(null), csvNumber(contributionResidual)])
    }
    rows.push(['Final Value', null, null, csvNumber(finalValue), ...Array(10).fill(null)])

    downloadCsv(
      `performance-calculation-${portfolioId}-${effectiveStartDate}-${effectiveEndDate}-${resolvedCalculationGroupBy}.csv`,
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
            selectedAssetId={benchmarkAssetId}
            searchValue={benchmarkSearch}
            onSearchChange={setBenchmarkSearch}
            onSelectInstrument={(instrument) => {
              setBenchmarkAssetId(instrument.asset_id)
              setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
              setBenchmarkError(null)
            }}
            onClear={() => {
              setBenchmarkAssetId('')
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
                      disabled={!calculationGroupsWorkspace || calculationGroupsLoading}
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
              {(calculationLoading && !calculationWorkspace) ||
              (calculationGroupsLoading && !calculationGroupsWorkspace) ? (
                <CalculationStatus label="Building performance calculation…" />
              ) : null}
              <div className="table-shell">
                <table className="transactions-table performance-calculation-table">
                  <thead>
                    <tr>
                      <th>Line</th>
                      <th>P&L / Flow</th>
                      <th>Start Value</th>
                      <th>End Value</th>
                      <th>Avg Weight</th>
                      <th>End Weight</th>
                      <th>Realized Gain</th>
                      <th>Unrealized Gain</th>
                      <th>Income</th>
                      <th>Fees</th>
                      <th>Taxes</th>
                      <th>FX P&L</th>
                      <th>TWR</th>
                      <th>Contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="performance-calculation-boundary-row">
                      <th scope="row">Initial Value</th>
                      <td className="performance-cell-number">—</td>
                      <td className="performance-cell-number">{formatCurrency(initialValue, baseCurrency)}</td>
                      <EmptyNumberCells count={11} />
                    </tr>
                    {calculationRows.length ? (
                      calculationRows.map((row) => {
                        const fxPnl = fxPnlAmount(row)
                        const feePnl = expenseImpact(row.fees)
                        const taxPnl = expenseImpact(row.taxes)
                        return (
                          <tr key={row.group_key}>
                            <th scope="row" className="performance-line-label">
                              {row.group_label}
                            </th>
                            <td className={`performance-cell-number ${signedValueClass(row.total_pnl)}`}>
                              {formatSignedCurrency(row.total_pnl, baseCurrency)}
                            </td>
                            <td className="performance-cell-number">
                              {formatCurrency(row.initial_value, baseCurrency)}
                            </td>
                            <td className="performance-cell-number">{formatCurrency(row.final_value, baseCurrency)}</td>
                            <td className="performance-cell-number">{formatPercent(row.average_weight)}</td>
                            <td className="performance-cell-number">{formatPercent(row.ending_weight)}</td>
                            <td className={`performance-cell-number ${signedValueClass(row.realized_capital_gains)}`}>
                              {formatSignedCurrency(row.realized_capital_gains, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(row.unrealized_pnl_change)}`}>
                              {formatSignedCurrency(row.unrealized_pnl_change, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(row.earnings)}`}>
                              {formatSignedCurrency(row.earnings, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(feePnl)}`}>
                              {formatSignedCurrency(feePnl, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(taxPnl)}`}>
                              {formatSignedCurrency(taxPnl, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(fxPnl)}`}>
                              {formatSignedCurrency(fxPnl, baseCurrency)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(row.period_return)}`}>
                              {signedPercent(row.period_return)}
                            </td>
                            <td className={`performance-cell-number ${signedValueClass(row.period_contribution)}`}>
                              {signedPercent(row.period_contribution)}
                            </td>
                          </tr>
                        )
                      })
                    ) : calculationGroupsLoading ? (
                      <TableStatusRow colSpan={14} label={`Loading ${calculationGroupLabel.toLowerCase()} calculation…`} />
                    ) : calculationGroupsError ? (
                      <TableStatusRow colSpan={14} label={calculationGroupsError} tone="error" />
                    ) : (
                      <TableStatusRow colSpan={14} label={`No ${calculationGroupLabel.toLowerCase()} calculation rows available.`} />
                    )}
                    <tr className="performance-calculation-external-row">
                      <th scope="row">Deposits</th>
                      <td className={`performance-cell-number ${signedValueClass(calculationSummary?.deposits)}`}>
                        {formatSignedCurrency(calculationSummary?.deposits, baseCurrency)}
                      </td>
                      <EmptyNumberCells count={12} />
                    </tr>
                    <tr className="performance-calculation-external-row">
                      <th scope="row">Withdrawals</th>
                      <td className={`performance-cell-number ${signedValueClass(expenseImpact(calculationSummary?.withdrawals))}`}>
                        {formatSignedCurrency(expenseImpact(calculationSummary?.withdrawals), baseCurrency)}
                      </td>
                      <EmptyNumberCells count={12} />
                    </tr>
                    <tr className="performance-calculation-total-row">
                      <th scope="row">Portfolio Total</th>
                      <td className={`performance-cell-number ${signedValueClass(portfolioPeriodPnl)}`}>
                        {formatSignedCurrency(portfolioPeriodPnl, baseCurrency)}
                      </td>
                      <td className="performance-cell-number">{formatCurrency(summary.start_nav, baseCurrency)}</td>
                      <td className="performance-cell-number">{formatCurrency(summary.end_nav, baseCurrency)}</td>
                      <td className="performance-cell-number">{formatPercent(1)}</td>
                      <td className="performance-cell-number">{formatPercent(1)}</td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioRealizedGain)}`}>
                        {formatSignedCurrency(portfolioRealizedGain, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioUnrealizedGain)}`}>
                        {formatSignedCurrency(portfolioUnrealizedGain, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioIncome)}`}>
                        {formatSignedCurrency(portfolioIncome, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioFees)}`}>
                        {formatSignedCurrency(portfolioFees, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioTaxes)}`}>
                        {formatSignedCurrency(portfolioTaxes, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioFxPnl)}`}>
                        {formatSignedCurrency(portfolioFxPnl, baseCurrency)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(summary.cumulative_twr)}`}>
                        {signedPercent(summary.cumulative_twr)}
                      </td>
                      <td className={`performance-cell-number ${signedValueClass(portfolioContribution)}`}>
                        {signedPercent(portfolioContribution)}
                      </td>
                    </tr>
                    {showContributionResidual ? (
                      <tr className="performance-calculation-residual-row">
                        <th scope="row">Contribution Residual</th>
                        <EmptyNumberCells count={12} />
                        <td className={`performance-cell-number ${signedValueClass(contributionResidual)}`}>
                          {signedPercent(contributionResidual)}
                        </td>
                      </tr>
                    ) : null}
                    <tr className="performance-calculation-boundary-row performance-calculation-final-row">
                      <th scope="row">Final Value</th>
                      <td className="performance-cell-number">—</td>
                      <td className="performance-cell-number">—</td>
                      <td className="performance-cell-number">{formatCurrency(finalValue, baseCurrency)}</td>
                      <EmptyNumberCells count={10} />
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}
      </section>
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
                    option.value === resolvedCalculationGroupBy ? 'holdings-groupby-option-active' : ''
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
