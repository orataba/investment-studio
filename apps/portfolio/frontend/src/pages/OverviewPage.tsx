import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams } from 'react-router'

import BenchmarkSearchBox, {
  benchmarkInstrumentLabel,
  instrumentPrimaryIdentifier,
} from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import InfoHint from '../components/InfoHint'
import OverviewAssetMix from '../components/OverviewAssetMix'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import RiskRankedBars from '../components/RiskRankedBars'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  getHoldingsWorkspace,
  getPortfolioInstrumentPriceChart,
  getPortfolioPerformance,
  getPortfolioInstruments,
  getPortfolioTaxonomyCatalog,
  getWorkspaceSummaryForPortfolio,
  type HoldingsWorkspaceResponse,
  type PortfolioInstrumentPriceChartPoint,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioHoldingRow,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioWorkspaceSummary,
  type SharedInstrumentRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import {
  baseAmountForRow,
  completeAmountSum,
  holdingAmountForDisplay,
} from '../lib/holdingAmounts'
import {
  assessBenchmarkComparisonGuard,
  type BenchmarkComparisonGuard,
} from '../lib/benchmarkComparisonGuard'
import {
  holdingDayChangeUnavailable,
  holdingUsesEventValuation,
} from '../lib/holdingPresentation'
import { buildMonthlyBuckets, buildMonthlyReturnMatrixRows, MONTH_LABELS } from '../lib/monthlyReturns'
import {
  buildPortfolioValueChartPoints,
  buildTwrIndexPoints,
} from '../lib/performanceSeries'
import { assessRiskWindowCoverage } from '../lib/riskWindowCoverage'

type AllocationBucket = {
  id: string
  label: string
  topLevelId: string
  topLevelLabel: string
  value: number | null
  weight: number | null
  holdingsCount: number
}

type TopHoldingColumnKey =
  | 'instrument'
  | 'identifier'
  | 'sleeve'
  | 'sparkline'
  | 'quantity'
  | 'last_price'
  | 'market_value'
  | 'cost_basis'
  | 'unrealized_pnl'
  | 'day_change'
  | 'weight'
  | 'coverage'
  | 'return_1w'
  | 'return_mtd'
  | 'return_ytd'

type TopHoldingColumnDefinition = {
  key: TopHoldingColumnKey
  label: string
  align?: 'center'
  render: (row: PortfolioHoldingRow) => ReactNode
}

type OverviewMetricRow = {
  label: string
  value: string
  benchmark?: string | null
  emphasis?: boolean
  toneClassName?: string
  title?: string
}

const DEFAULT_TOP_HOLDING_COLUMNS: TopHoldingColumnKey[] = [
  'instrument',
  'sparkline',
  'market_value',
  'weight',
  'unrealized_pnl',
  'return_1w',
  'return_mtd',
  'return_ytd',
]

const TOP_HOLDING_COLUMN_LABELS: Record<TopHoldingColumnKey, string> = {
  instrument: 'Instrument',
  identifier: 'Identifier',
  sleeve: 'Sleeve',
  sparkline: 'Chart 6M',
  quantity: 'Quantity',
  last_price: 'Last Price',
  market_value: 'Market Value',
  cost_basis: 'Cost Basis',
  unrealized_pnl: 'Unrealized P&L',
  day_change: 'Day Change',
  weight: 'Weight',
  coverage: 'Coverage',
  return_1w: '1W Return',
  return_mtd: 'MTD',
  return_ytd: 'YTD',
}

const TOP_HOLDING_COLUMN_GROUPS: Array<{ label: string; columns: TopHoldingColumnKey[] }> = [
  { label: 'Core', columns: ['instrument', 'identifier', 'sleeve', 'coverage'] },
  { label: 'Market', columns: ['sparkline', 'last_price', 'market_value', 'weight', 'day_change'] },
  { label: 'Position', columns: ['quantity', 'cost_basis', 'unrealized_pnl'] },
  { label: 'Return', columns: ['return_1w', 'return_mtd', 'return_ytd'] },
]

const TOP_HOLDINGS_LIMIT = 10
const DERIVATIVES_BUCKET_ID = 'derivative_bucket:__derivatives__'

const DONUT_COLORS = ['#0b72d7', '#0f766e', '#64748b', '#7c3aed', '#db2777', '#14b8a6', '#475569']

function primaryIdentifier(row: PortfolioHoldingRow) {
  if (!row.instrument_core) {
    return row.derivative_contract_id ?? row.position_reference_id ?? row.line_id
  }
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function holdingReferenceId(row: PortfolioHoldingRow) {
  return row.derivative_contract_id ?? row.position_reference_id ?? row.instrument_core?.instrument_id ?? row.line_id
}

function holdingName(row: PortfolioHoldingRow) {
  return row.derivative_contract?.contract_name ?? row.instrument_core?.instrument_name ?? row.line_id
}

function holdingAssetType(row: PortfolioHoldingRow) {
  return row.derivative_contract?.contract_type ?? row.instrument_core?.instrument_type ?? 'other'
}

function holdingCurrency(row: PortfolioHoldingRow) {
  return row.derivative_contract?.currency ?? row.instrument_core?.currency ?? ''
}

function hasFiniteAllocation(
  row: PortfolioHoldingRow,
): row is PortfolioHoldingRow & { allocation: number } {
  return row.allocation != null && Number.isFinite(row.allocation)
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

function coverageClassName(coverageState: PortfolioPerformanceCoverageState) {
  return coverageState === 'complete' ? 'coverage-pill-live' : 'coverage-pill-warning'
}

function dateFromString(date: string) {
  const time = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00` : date)
  return Number.isNaN(time) ? null : new Date(time)
}

function addDays(date: Date, days: number) {
  const nextDate = new Date(date)
  nextDate.setDate(nextDate.getDate() + days)
  return nextDate
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
    return elapsedDays != null && elapsedDays > 0 ? (observationCount / elapsedDays) * 365.25 : null
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
  return observationSpanDays > 0 ? (observationCount / observationSpanDays) * 365.25 : null
}

function formatDateKey(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function previousDateKey(dateKey: string | null) {
  const parsedDate = dateKey ? dateFromString(dateKey) : null
  return parsedDate ? formatDateKey(addDays(parsedDate, -1)) : null
}

function shiftCalendarMonths(input: Date, months: number) {
  const targetMonth = new Date(input.getFullYear(), input.getMonth() + months, 1)
  const targetMonthLastDay = new Date(
    targetMonth.getFullYear(),
    targetMonth.getMonth() + 1,
    0,
  ).getDate()
  return new Date(
    targetMonth.getFullYear(),
    targetMonth.getMonth(),
    Math.min(input.getDate(), targetMonthLastDay),
  )
}

function isFundedSegmentStart(
  point: PortfolioDailyPerformancePoint | undefined,
  targetDate: string,
) {
  return Boolean(
    point?.as_of_date === targetDate &&
      point.beginning_nav != null &&
      Math.abs(point.beginning_nav) <= 1e-9 &&
      point.external_cash_in > 1e-9 &&
      point.daily_twr != null &&
      Number.isFinite(point.daily_twr) &&
      point.return_coverage_state === 'complete',
  )
}

function periodReturnFromTwr(
  points: PortfolioDailyPerformancePoint[],
  targetDate: string | null,
  options: {
    includeTargetDate?: boolean
    requireReliableBoundaries?: boolean
    effectiveEndDate?: string | null
  } = {},
) {
  const includeTargetDate = options.includeTargetDate ?? false
  const sortedPoints = points
    .filter((point) => !options.effectiveEndDate || point.as_of_date <= options.effectiveEndDate)
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
  if (!sortedPoints.length) {
    return null
  }

  const periodPoints = targetDate
    ? sortedPoints.filter((point) =>
        includeTargetDate ? point.as_of_date >= targetDate : point.as_of_date > targetDate,
      )
    : sortedPoints
  if (!periodPoints.length) {
    return null
  }
  const candidatePoints = periodPoints
  if (options.requireReliableBoundaries) {
    const anchorPoint = targetDate
      ? sortedPoints.find((point) => point.as_of_date === targetDate) ?? null
      : null
    const endPoint = candidatePoints[candidatePoints.length - 1] ?? null
    const reliableValuation = (point: PortfolioDailyPerformancePoint | null) =>
      point != null &&
      point.ending_nav != null &&
      point.valuation_coverage_state === 'complete'
    const continuousPath = candidatePoints.every((point, index) => {
      if (includeTargetDate && index === 0 && point.as_of_date === targetDate) {
        return (
          point.return_coverage_state === 'complete' &&
          point.daily_twr != null &&
          Number.isFinite(point.daily_twr)
        )
      }
      const previousDate = index === 0 ? anchorPoint?.as_of_date : candidatePoints[index - 1]?.as_of_date
      const dayGap = previousDate ? dayDiff(previousDate, point.as_of_date) : null
      return (
        dayGap === 1 &&
        point.return_coverage_state === 'complete' &&
        point.daily_twr != null &&
        Number.isFinite(point.daily_twr)
      )
    })
    if (!reliableValuation(anchorPoint) || !reliableValuation(endPoint) || !continuousPath) {
      return null
    }
  }
  const returnPoints = candidatePoints.filter(
    (point) => point.daily_twr != null && Number.isFinite(point.daily_twr),
  )
  if (!returnPoints.length) {
    return null
  }

  return returnPoints.reduce((growthIndex, point) => growthIndex * (1 + (point.daily_twr ?? 0)), 1) - 1
}

export function buildPortfolioReturnMetrics(
  points: PortfolioDailyPerformancePoint[],
  effectiveEndDate?: string | null,
) {
  const sortedPoints = points
    .filter((point) => !effectiveEndDate || point.as_of_date <= effectiveEndDate)
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const latestDate = latestPoint ? dateFromString(latestPoint.as_of_date) : null
  if (!latestPoint || !latestDate) {
    return {
      oneWeek: null,
      mtd: null,
      ytd: null,
    }
  }

  const monthStart = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1)
  const yearStart = new Date(latestDate.getFullYear(), 0, 1)
  const priorMonthEnd = addDays(monthStart, -1)
  const priorYearEnd = addDays(yearStart, -1)
  const effectiveOptions = { requireReliableBoundaries: true, effectiveEndDate }
  const includesFundedStart = (targetDate: string) =>
    isFundedSegmentStart(
      sortedPoints.find((point) => point.as_of_date === targetDate),
      targetDate,
    )
  const oneWeekTarget = formatDateKey(addDays(latestDate, -7))
  const mtdTarget = formatDateKey(priorMonthEnd)
  const ytdTarget = formatDateKey(priorYearEnd)

  return {
    oneWeek: periodReturnFromTwr(sortedPoints, oneWeekTarget, {
      ...effectiveOptions,
      includeTargetDate: includesFundedStart(oneWeekTarget),
    }),
    mtd: periodReturnFromTwr(sortedPoints, mtdTarget, {
      ...effectiveOptions,
      includeTargetDate: includesFundedStart(mtdTarget),
    }),
    ytd: periodReturnFromTwr(sortedPoints, ytdTarget, {
      ...effectiveOptions,
      includeTargetDate: includesFundedStart(ytdTarget),
    }),
  }
}

function periodReturnFromValuePoints(
  points: Array<{ date: string; value: number }>,
  targetDate: string | null,
  options: { requireExactAnchor?: boolean; requiredEndDate?: string | null } = {},
) {
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  if (sortedPoints.length < 2) {
    return null
  }

  const latestPoint = sortedPoints[sortedPoints.length - 1]
  if (options.requiredEndDate && latestPoint.date !== options.requiredEndDate) {
    return null
  }
  const anchorPoint = targetDate
    ? options.requireExactAnchor
      ? sortedPoints.find((point) => point.date === targetDate) ?? null
      : sortedPoints.reduce<PortfolioInstrumentPriceChartPoint | null>(
          (current, point) => (point.date <= targetDate ? point : current),
          null,
        )
    : sortedPoints[0]
  return anchorPoint && anchorPoint.value !== 0 ? latestPoint.value / anchorPoint.value - 1 : null
}

function latestValuePointOnOrBefore(
  points: PortfolioInstrumentPriceChartPoint[],
  targetDate: string,
): PortfolioInstrumentPriceChartPoint | null {
  let selected: PortfolioInstrumentPriceChartPoint | null = null
  points.forEach((point) => {
    if (point.date <= targetDate) {
      selected = point
    }
  })
  return selected
}

function sampleStandardDeviation(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance =
    values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
  return Math.sqrt(variance)
}

function annualizedMeanReturn(values: number[], periodsPerYear: number | null) {
  if (!values.length || periodsPerYear == null) {
    return null
  }
  return (values.reduce((sum, value) => sum + value, 0) / values.length) * periodsPerYear
}

export function trailingAnnualizedVolatility(
  points: Array<{ date: string; value: number }>,
  requestedEndDate: Date,
  lookbackMonths: number,
) {
  const endDate = formatDateKey(requestedEndDate)
  const startDate = formatDateKey(shiftCalendarMonths(requestedEndDate, -lookbackMonths))
  const orderedPoints = points
    .filter(
      (point) =>
        Boolean(point.date) &&
        point.date <= endDate &&
        Number.isFinite(point.value),
    )
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const startAnchor = orderedPoints.reduce<{ date: string; value: number } | null>(
    (selected, point) => (point.date <= startDate ? point : selected),
    null,
  )
  if (!startAnchor) {
    return null
  }
  const windowPoints = orderedPoints.filter(
    (point) => point.date > startDate && point.date <= endDate,
  )
  const lookbackDays =
    lookbackMonths === 1
      ? 30
      : lookbackMonths === 3
        ? 90
        : lookbackMonths === 6
          ? 180
          : lookbackMonths === 12
            ? 366
            : lookbackMonths === 24
              ? 730
              : Math.max(2, Math.round((lookbackMonths * 365.25) / 12))
  const coverage = assessRiskWindowCoverage(
    windowPoints.map((point) => point.date),
    endDate,
    lookbackDays,
    'daily',
    undefined,
    startAnchor.date,
  )
  if (!coverage.ok) {
    return null
  }
  const values = windowPoints.map((point) => point.value)
  const volatility = sampleStandardDeviation(values)
  const periodsPerYear = annualizationPeriodsPerYear(
    windowPoints.map((point) => point.date),
    windowPoints.length,
    startDate,
  )
  return volatility == null || periodsPerYear == null ? null : volatility * Math.sqrt(periodsPerYear)
}

function buildPortfolioRiskMetrics(
  points: PortfolioDailyPerformancePoint[],
  effectiveEndDate?: string | null,
) {
  const sortedPoints = points
    .filter(
      (point) =>
        point.market_risk_return_observation_eligible &&
        point.market_risk_daily_return != null &&
        Number.isFinite(point.market_risk_daily_return),
    )
    .map((point) => ({
      date: point.as_of_date,
      value: point.market_risk_daily_return as number,
    }))
    .sort((left, right) => left.date.localeCompare(right.date))
  const latestPoint = sortedPoints[sortedPoints.length - 1]
  const requestedEndDate =
    dateFromString(effectiveEndDate ?? '') ??
    (latestPoint ? dateFromString(latestPoint.date) : null)
  if (!requestedEndDate) {
    return {
      volatility1m: null,
      volatility3m: null,
    }
  }

  return {
    volatility1m: trailingAnnualizedVolatility(sortedPoints, requestedEndDate, 1),
    volatility3m: trailingAnnualizedVolatility(sortedPoints, requestedEndDate, 3),
  }
}

function buildDrawdownMetrics(points: PortfolioInstrumentPriceChartPoint[]) {
  let highWater = points[0]?.value ?? 0
  let maxDrawdown: number | null = null
  let currentDrawdown: number | null = null

  points.forEach((point) => {
    highWater = Math.max(highWater, point.value)
    const drawdown = highWater > 0 ? point.value / highWater - 1 : null
    if (drawdown != null) {
      currentDrawdown = drawdown
      maxDrawdown = maxDrawdown == null ? drawdown : Math.min(maxDrawdown, drawdown)
    }
  })

  return { currentDrawdown, maxDrawdown }
}

function buildBenchmarkMetrics(
  points: PortfolioInstrumentPriceChartPoint[],
  portfolioInceptionDate?: string | null,
  portfolioEndDate?: string | null,
) {
  const sortedPoints = points
    .filter((point) => Number.isFinite(point.value) && (!portfolioEndDate || point.date <= portfolioEndDate))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const firstPoint = portfolioInceptionDate
    ? latestValuePointOnOrBefore(sortedPoints, portfolioInceptionDate)
    : sortedPoints[0]
  const metricPoints = firstPoint
    ? sortedPoints.filter((point) => point.date >= firstPoint.date)
    : []
  const latestPoint = metricPoints[metricPoints.length - 1]
  const latestDate = latestPoint ? dateFromString(latestPoint.date) : null
  const requestedEndDate = dateFromString(portfolioEndDate ?? '') ?? latestDate
  if (!firstPoint || !latestPoint || !latestDate || !requestedEndDate) {
    return null
  }

  const monthStart = new Date(latestDate.getFullYear(), latestDate.getMonth(), 1)
  const yearStart = new Date(latestDate.getFullYear(), 0, 1)
  const priorMonthEnd = addDays(monthStart, -1)
  const priorYearEnd = addDays(yearStart, -1)
  const dailyReturns = metricPoints
    .slice(1)
    .map((point, index) => {
      const previous = metricPoints[index]
      return previous.value !== 0 ? { date: point.date, value: point.value / previous.value - 1 } : null
    })
    .filter((value): value is { date: string; value: number } => value != null)
  const volatility = sampleStandardDeviation(dailyReturns.map((point) => point.value))
  const metricStartDate = portfolioInceptionDate ?? firstPoint.date
  const periodsPerYear = annualizationPeriodsPerYear(
    dailyReturns.map((point) => point.date),
    dailyReturns.length,
    metricStartDate,
  )
  const startDate = dateFromString(metricStartDate)
  const daySpan = startDate ? Math.max(1, (latestDate.getTime() - startDate.getTime()) / 86_400_000) : null
  const sinceInception = firstPoint.value !== 0 ? latestPoint.value / firstPoint.value - 1 : null
  const annualizedReturn =
    sinceInception != null && daySpan != null
      ? (1 + sinceInception) ** (365.25 / daySpan) - 1
      : null
  const annualizedVolatility = volatility == null || periodsPerYear == null ? null : volatility * Math.sqrt(periodsPerYear)
  const annualizedMean = annualizedMeanReturn(dailyReturns.map((point) => point.value), periodsPerYear)
  const drawdowns = buildDrawdownMetrics(metricPoints)

  return {
    oneWeek: portfolioEndDate
      ? periodReturnFromValuePoints(
          metricPoints,
          formatDateKey(addDays(latestDate, -7)),
          { requireExactAnchor: true, requiredEndDate: portfolioEndDate },
        )
      : null,
    mtd: periodReturnFromValuePoints(metricPoints, formatDateKey(priorMonthEnd)),
    ytd: periodReturnFromValuePoints(metricPoints, formatDateKey(priorYearEnd)),
    sinceInception,
    annualizedReturn,
    annualizedVolatility,
    volatility1m: trailingAnnualizedVolatility(dailyReturns, requestedEndDate, 1),
    volatility3m: trailingAnnualizedVolatility(dailyReturns, requestedEndDate, 3),
    sharpe:
      annualizedMean != null && annualizedVolatility != null && annualizedVolatility !== 0
        ? annualizedMean / annualizedVolatility
        : null,
    currentDrawdown: drawdowns.currentDrawdown,
    maxDrawdown: drawdowns.maxDrawdown,
  }
}

function benchmarkNote(
  selected: SharedInstrumentRecord | null,
  loading: boolean,
  value: number | null | undefined,
  guard: BenchmarkComparisonGuard | null,
  formatter: (input: number | null | undefined) => string = signedPercent,
) {
  if (!selected) {
    return null
  }
  if (loading) {
    return 'BM loading'
  }
  if (guard == null || guard.mode === 'unavailable') {
    return 'BM unavailable'
  }
  const prefix =
    guard.reason === 'benchmark_price_return_comparable'
      ? 'Price BM'
      : guard.mode === 'exploratory'
        ? 'Exploratory BM'
        : 'BM'
  return `${prefix} ${formatter(value)}`
}

function StrategySleeveDonut({
  segments,
  unassigned,
}: {
  segments: Array<{ id: string; label: string; value: number; valueLabel: string; detail: string }>
  unassigned?: { valueLabel: string; detail: string } | null
}) {
  const visibleSegments = segments.filter((segment) => segment.value > 0)
  const total = visibleSegments.reduce((sum, segment) => sum + segment.value, 0)
  if (!visibleSegments.length || total <= 0) {
    return (
      <div className="overview-sleeve-empty">
        <div className="price-chart-empty">No classified sleeves.</div>
        {unassigned ? (
          <div className="overview-sleeve-unassigned">
            <span>Unassigned</span>
            <strong>{unassigned.valueLabel}</strong>
            <em>{unassigned.detail}</em>
          </div>
        ) : null}
      </div>
    )
  }

  const radius = 44
  const circumference = 2 * Math.PI * radius
  let offset = 0

  return (
    <div className="overview-sleeve-donut">
      <div className="overview-sleeve-donut-chart">
        <svg viewBox="0 0 120 120" role="img" aria-label="Strategy sleeve allocation">
          <circle className="overview-sleeve-donut-track" cx="60" cy="60" r={radius} />
          {visibleSegments.map((segment, index) => {
            const slice = (segment.value / total) * circumference
            const dashOffset = -offset
            offset += slice
            return (
              <circle
                key={segment.id}
                className="overview-sleeve-donut-slice"
                cx="60"
                cy="60"
                r={radius}
                stroke={DONUT_COLORS[index % DONUT_COLORS.length]}
                strokeDasharray={`${slice} ${circumference - slice}`}
                strokeDashoffset={dashOffset}
              />
            )
          })}
        </svg>
        <div className="overview-sleeve-donut-center">
          <strong>{formatPercent(total)}</strong>
          <span>Classified</span>
        </div>
      </div>
      <div className="overview-sleeve-donut-list">
        {visibleSegments.map((segment, index) => (
          <div className="overview-sleeve-donut-row" key={segment.id}>
            <span style={{ background: DONUT_COLORS[index % DONUT_COLORS.length] }} />
            <div>
              <strong>{segment.label}</strong>
            </div>
            <em>{segment.valueLabel}</em>
          </div>
        ))}
        {unassigned ? (
          <div className="overview-sleeve-unassigned">
            <span>Unassigned</span>
            <strong>{unassigned.valueLabel}</strong>
            <em>{unassigned.detail}</em>
          </div>
        ) : null}
      </div>
    </div>
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

function resolveNodePath(
  taxonomyNodeId: string,
  nodesById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
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

function accumulateBucket(
  buckets: Map<string, AllocationBucket>,
  args: {
    id: string
    label: string
    topLevelId: string
    topLevelLabel: string
    value: number | null
    weight: number | null
  },
) {
  const current = buckets.get(args.id)
  if (current) {
    current.value = current.value != null && args.value != null ? current.value + args.value : null
    current.weight = current.weight != null && args.weight != null ? current.weight + args.weight : null
    current.holdingsCount += 1
    return
  }
  buckets.set(args.id, {
    id: args.id,
    label: args.label,
    topLevelId: args.topLevelId,
    topLevelLabel: args.topLevelLabel,
    value: args.value,
    weight: args.weight,
    holdingsCount: 1,
  })
}

export default function OverviewPage() {
  const { portfolioId = '' } = useParams()
  const [summary, setSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [performanceWorkspace, setPerformanceWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [performanceLoading, setPerformanceLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [topHoldingColumns, setTopHoldingColumns] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnDraft, setTopHoldingColumnDraft] = useState<TopHoldingColumnKey[]>(DEFAULT_TOP_HOLDING_COLUMNS)
  const [topHoldingColumnsOpen, setTopHoldingColumnsOpen] = useState(false)
  const topHoldingColumnsDialogRef = useModalDialog(
    topHoldingColumnsOpen,
    () => setTopHoldingColumnsOpen(false),
  )

  useEffect(() => {
    if (!portfolioId) {
      setSummary(null)
      setHoldingsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio id is required.')
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    Promise.all([
      getWorkspaceSummaryForPortfolio(portfolioId),
      getHoldingsWorkspace(portfolioId),
      getPortfolioTaxonomyCatalog(portfolioId),
    ])
      .then(([summaryResponse, holdingsResponse, taxonomyResponse]) => {
        if (cancelled) {
          return
        }
        setSummary(summaryResponse)
        setHoldingsWorkspace(holdingsResponse)
        setTaxonomyCatalog(taxonomyResponse)
        setWorkspaceError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load portfolio overview.')
          setSummary(null)
          setHoldingsWorkspace(null)
          setTaxonomyCatalog(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setWorkspaceLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    const asOfDate = holdingsWorkspace?.as_of_date ?? summary?.as_of_date
    if (!portfolioId || !asOfDate) {
      setPerformanceWorkspace(null)
      setPerformanceLoading(false)
      setPerformanceError(null)
      return
    }

    let cancelled = false
    setPerformanceLoading(true)

    getPortfolioPerformance(portfolioId, { end_date: asOfDate })
      .then((response) => {
        if (!cancelled) {
          setPerformanceWorkspace(response)
          setPerformanceError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPerformanceError(requestError instanceof Error ? requestError.message : 'Failed to load NAV history.')
          setPerformanceWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPerformanceLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [holdingsWorkspace?.as_of_date, portfolioId, summary?.as_of_date])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      return
    }

    let cancelled = false
    getPortfolioInstruments(portfolioId)
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

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    const asOfDate = holdingsWorkspace?.as_of_date ?? summary?.as_of_date
    if (!portfolioId || !benchmarkInstrumentId || !asOfDate) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioInstrumentPriceChart(portfolioId, benchmarkInstrumentId, {
      as_of_date: asOfDate,
      range: 'all',
    })
      .then((response) => {
        if (!cancelled) {
          setBenchmarkChart(response)
        }
      })
      .catch((requestError) => {
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
  }, [benchmarkInstrumentId, holdingsWorkspace?.as_of_date, portfolioId, summary?.as_of_date])

  const holdingsRows = holdingsWorkspace?.rows ?? []
  const nonCashHoldingsRows = useMemo(
    () => holdingsRows.filter((row) => row.holding_category !== 'cash_and_settlement'),
    [holdingsRows],
  )
  const resolvedBaseCurrency =
    summary?.base_currency ?? holdingsWorkspace?.base_currency ?? performanceWorkspace?.base_currency ?? 'USD'
  const performanceIsOperational =
    performanceWorkspace?.summary.performance_basis === 'operational_carrying_basis'
  const operationalPerformanceDetail =
    'Operational return is used for NAV reconciliation because event-valued assets and obligations remain on carrying basis. Market Risk Watch uses a separate return chain that models derivatives and base-currency cash at zero return; benchmark overlays are therefore hidden from this TWR chart.'
  const sortedHoldings = useMemo(
    () =>
      [...nonCashHoldingsRows].sort(
        (left, right) => {
          const leftAllocation =
            left.allocation != null && Number.isFinite(left.allocation) ? left.allocation : null
          const rightAllocation =
            right.allocation != null && Number.isFinite(right.allocation) ? right.allocation : null
          if (leftAllocation == null || rightAllocation == null) {
            if (leftAllocation != null) {
              return -1
            }
            if (rightAllocation != null) {
              return 1
            }
          } else {
            const allocationDelta = rightAllocation - leftAllocation
            if (allocationDelta) {
              return allocationDelta
            }
          }
          const leftValue = baseAmountForRow(
            left,
            resolvedBaseCurrency,
            left.market_value_base,
            left.market_value,
          )
          const rightValue = baseAmountForRow(
            right,
            resolvedBaseCurrency,
            right.market_value_base,
            right.market_value,
          )
          if (leftValue == null || rightValue == null) {
            return leftValue == null ? (rightValue == null ? 0 : 1) : -1
          }
          return rightValue - leftValue
        },
      ),
    [nonCashHoldingsRows, resolvedBaseCurrency],
  )
  const holdingsMarketValueBase = useMemo(
    () =>
      completeAmountSum(
        nonCashHoldingsRows.map((row) =>
          baseAmountForRow(
            row,
            resolvedBaseCurrency,
            row.market_value_base,
            row.market_value,
          ),
        ),
      ),
    [nonCashHoldingsRows, resolvedBaseCurrency],
  )
  const holdingsAllocationsComplete = nonCashHoldingsRows.every(hasFiniteAllocation)
  const top5Holdings = sortedHoldings.slice(0, 5)
  const top5Weight = holdingsAllocationsComplete
    ? completeAmountSum(top5Holdings.map((row) => row.allocation))
    : null
  const navChartPoints = useMemo(
    () => buildPortfolioValueChartPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const twrIndexChartPoints = useMemo(
    () => buildTwrIndexPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const benchmarkChartPoints = useMemo(
    () =>
      (benchmarkChart?.points ?? [])
        .filter((point) => Number.isFinite(point.value))
        .map((point) => ({
          date: point.date,
          value: point.value,
        })),
    [benchmarkChart],
  )

  const defaultPlanningTaxonomyId =
    summary?.default_planning_taxonomy_id ?? taxonomyCatalog?.default_planning_taxonomy_id ?? null

  const composition = useMemo(() => {
    const nodesById = new Map<string, PortfolioTaxonomyNodeRecord>(
      (taxonomyCatalog?.taxonomy_nodes ?? []).map((node) => [node.taxonomy_node_id, node]),
    )
    const activeAssignments = (taxonomyCatalog?.taxonomy_assignments ?? []).filter(
      (assignment) =>
        assignment.taxonomy_id === defaultPlanningTaxonomyId &&
        assignment.target_scope === 'instrument' &&
        assignment.status === 'active',
    )
    const assignmentByInstrumentId = new Map<string, PortfolioTaxonomyAssignmentRecord>(
      activeAssignments.map((assignment) => [assignment.target_entity_id, assignment]),
    )

    const topLevelBuckets = new Map<string, AllocationBucket>()
    const assignedLabelByInstrumentId = new Map<
      string,
      {
        topLevelLabel: string
        leafLabel: string
      }
    >()

    nonCashHoldingsRows.forEach((row) => {
      const value = baseAmountForRow(
        row,
        resolvedBaseCurrency,
        row.market_value_base,
        row.market_value,
      )
      const weight =
        row.allocation != null && Number.isFinite(row.allocation) ? row.allocation : null
      const rowReferenceId = holdingReferenceId(row)
      const isDerivative = row.holding_category === 'derivatives'
      const assignment = isDerivative ? null : assignmentByInstrumentId.get(rowReferenceId)
      const leafNode = assignment ? nodesById.get(assignment.taxonomy_node_id) ?? null : null
      const path = leafNode ? resolveNodePath(leafNode.taxonomy_node_id, nodesById) : []
      const topLevelNode = path[0] ?? leafNode
      const topLevelId = isDerivative
        ? DERIVATIVES_BUCKET_ID
        : topLevelNode?.taxonomy_node_id ?? '__unassigned__'
      const topLevelLabel = isDerivative ? 'Derivatives' : topLevelNode?.node_name ?? 'Unassigned'
      const leafLabel = isDerivative ? 'Derivatives' : leafNode?.node_name ?? 'Unassigned'

      accumulateBucket(topLevelBuckets, {
        id: topLevelId,
        label: topLevelLabel,
        topLevelId,
        topLevelLabel,
        value,
        weight,
      })

      assignedLabelByInstrumentId.set(rowReferenceId, {
        topLevelLabel,
        leafLabel,
      })
    })

    const sortedTopLevelBuckets = [...topLevelBuckets.values()].sort((left, right) => {
      if (left.weight == null || right.weight == null) {
        if (left.weight != null) {
          return -1
        }
        if (right.weight != null) {
          return 1
        }
      } else {
        const weightDelta = right.weight - left.weight
        if (weightDelta) {
          return weightDelta
        }
      }
      if (left.value == null || right.value == null) {
        return left.value == null ? (right.value == null ? 0 : 1) : -1
      }
      return right.value - left.value
    })

    return {
      topLevelBuckets: sortedTopLevelBuckets,
      assignedLabelByInstrumentId,
    }
  }, [
    defaultPlanningTaxonomyId,
    nonCashHoldingsRows,
    holdingsWorkspace?.as_of_date,
    resolvedBaseCurrency,
    summary?.as_of_date,
    summary?.nav,
    taxonomyCatalog,
  ])

  const completeSleeveBuckets = composition.topLevelBuckets.filter(
    (bucket): bucket is AllocationBucket & { value: number; weight: number } =>
      bucket.value != null && Number.isFinite(bucket.value) &&
      bucket.weight != null && Number.isFinite(bucket.weight),
  )
  const unassignedSleeveBucket = completeSleeveBuckets.find(
    (bucket) => bucket.id === '__unassigned__',
  )
  const sleeveRibbonSegments =
    completeSleeveBuckets.length === composition.topLevelBuckets.length
      ? completeSleeveBuckets
        .filter((bucket) => bucket.id !== '__unassigned__')
        .map((bucket) => ({
          id: bucket.id,
          label: bucket.label,
          value: bucket.weight,
          valueLabel: formatPercent(bucket.weight),
          detail: `${bucket.holdingsCount} lines · ${formatCurrency(bucket.value, resolvedBaseCurrency)}`,
        }))
      : null
  const unassignedSleeve =
    completeSleeveBuckets.length === composition.topLevelBuckets.length &&
    unassignedSleeveBucket &&
    unassignedSleeveBucket.weight > 0
      ? {
          valueLabel: formatPercent(unassignedSleeveBucket.weight),
          detail: `${unassignedSleeveBucket.holdingsCount} lines · ${formatCurrency(unassignedSleeveBucket.value, resolvedBaseCurrency)}`,
        }
      : null
  const topHoldingRows = sortedHoldings.slice(0, TOP_HOLDINGS_LIMIT)
  const completeTopHoldingRows = topHoldingRows.filter(hasFiniteAllocation)
  const topHoldingBarItems =
    holdingsAllocationsComplete &&
    holdingsMarketValueBase != null &&
    completeTopHoldingRows.length === topHoldingRows.length
      ? completeTopHoldingRows.map((row) => {
          const marketValue = holdingAmountForDisplay(
            row,
            resolvedBaseCurrency,
            row.market_value_base,
            row.market_value,
          )
          return {
            id: row.line_id,
            label: holdingName(row),
            subtitle: composition.assignedLabelByInstrumentId.get(holdingReferenceId(row))?.leafLabel ?? formatLabel(holdingAssetType(row)),
            value: row.allocation,
            valueLabel: formatPercent(row.allocation),
            detail: formatCurrency(marketValue.value, marketValue.currency),
          }
        })
      : null
  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId) ?? null
  const portfolioInceptionDate =
    performanceWorkspace?.summary.effective_start_date ??
    performanceWorkspace?.summary.start_date ??
    performanceWorkspace?.daily_series.find((point) => point.ending_nav != null)?.as_of_date ??
    null
  const benchmarkEndDate =
    performanceWorkspace?.summary.effective_end_date ??
    performanceWorkspace?.summary.end_date ??
    holdingsWorkspace?.as_of_date ??
    summary?.as_of_date ??
    null
  const benchmarkStartBoundaryDate = previousDateKey(portfolioInceptionDate)
  const benchmarkEligibleDates = useMemo(
    () =>
      (performanceWorkspace?.daily_series ?? [])
        .filter(
          (point) =>
            portfolioInceptionDate != null &&
            benchmarkEndDate != null &&
            point.as_of_date >= portfolioInceptionDate &&
            point.as_of_date <= benchmarkEndDate &&
            Number.isFinite(point.market_risk_daily_return) &&
            point.market_risk_return_observation_eligible,
        )
        .map((point) => point.as_of_date)
        .sort(),
    [benchmarkEndDate, performanceWorkspace?.daily_series, portfolioInceptionDate],
  )
  const benchmarkGuard = useMemo(
    () =>
      selectedBenchmarkInstrument && benchmarkChart && benchmarkStartBoundaryDate
        ? assessBenchmarkComparisonGuard({
            chartBasis: benchmarkChart.chart_basis,
            returnSemantics: benchmarkChart.return_semantics,
            benchmarkCurrency: benchmarkChart.currency,
            portfolioCurrency: resolvedBaseCurrency,
            points: benchmarkChart.points,
            startBoundaryDate: benchmarkStartBoundaryDate,
            eligiblePortfolioDates: benchmarkEligibleDates,
          })
        : null,
    [
      benchmarkChart,
      benchmarkEligibleDates,
      benchmarkStartBoundaryDate,
      resolvedBaseCurrency,
      selectedBenchmarkInstrument,
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
        : buildBenchmarkMetrics(
            benchmarkChart?.points ?? [],
            benchmarkStartBoundaryDate,
            benchmarkEndDate,
          ) ?? null,
    [benchmarkChart, benchmarkEndDate, benchmarkGuard?.mode, benchmarkStartBoundaryDate],
  )
  const portfolioReturnMetrics = useMemo(
    () =>
      buildPortfolioReturnMetrics(
        performanceWorkspace?.daily_series ?? [],
        performanceWorkspace?.summary.effective_end_date ?? performanceWorkspace?.summary.end_date,
      ),
    [performanceWorkspace],
  )
  const portfolioRiskMetrics = useMemo(
    () =>
      buildPortfolioRiskMetrics(
        performanceWorkspace?.daily_series ?? [],
        performanceWorkspace?.summary.effective_end_date ?? performanceWorkspace?.summary.end_date,
      ),
    [performanceWorkspace],
  )
  const monthlyBuckets = useMemo(
    () => buildMonthlyBuckets(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace],
  )
  const monthlyMatrixRows = useMemo(() => buildMonthlyReturnMatrixRows(monthlyBuckets), [monthlyBuckets])

  const overviewMetricGroups: Array<{ label: string; rows: OverviewMetricRow[] }> = [
    {
      label: performanceIsOperational ? 'Operational Performance' : 'Performance',
      rows: [
        {
          label: '1W Return',
          value: signedPercent(portfolioReturnMetrics.oneWeek),
          benchmark: performanceIsOperational
            ? null
            : benchmarkNote(
                selectedBenchmarkInstrument,
                benchmarkLoading,
                benchmarkMetrics?.oneWeek,
                benchmarkGuard,
              ),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.oneWeek),
        },
        {
          label: 'MTD',
          value: signedPercent(portfolioReturnMetrics.mtd),
          benchmark: performanceIsOperational
            ? null
            : benchmarkNote(
                selectedBenchmarkInstrument,
                benchmarkLoading,
                benchmarkMetrics?.mtd,
                benchmarkGuard,
              ),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.mtd),
          title: portfolioReturnMetrics.mtd == null ? 'Reliable month-start boundary and continuous coverage required.' : undefined,
        },
        {
          label: 'YTD',
          value: signedPercent(portfolioReturnMetrics.ytd),
          benchmark: performanceIsOperational
            ? null
            : benchmarkNote(
                selectedBenchmarkInstrument,
                benchmarkLoading,
                benchmarkMetrics?.ytd,
                benchmarkGuard,
              ),
          emphasis: true,
          toneClassName: signedValueClass(portfolioReturnMetrics.ytd),
          title: portfolioReturnMetrics.ytd == null ? 'No year-start anchor.' : undefined,
        },
        {
          label: 'Since Inception',
          value: signedPercent(performanceWorkspace?.summary.cumulative_twr),
          benchmark: performanceIsOperational
            ? null
            : benchmarkNote(
                selectedBenchmarkInstrument,
                benchmarkLoading,
                benchmarkMetrics?.sinceInception,
                benchmarkGuard,
              ),
          emphasis: true,
          toneClassName: signedValueClass(performanceWorkspace?.summary.cumulative_twr),
        },
      ],
    },
    {
      label: 'Market Risk Watch',
      rows: [
        {
          label: 'Current DD',
          value: signedPercent(performanceWorkspace?.summary.current_drawdown),
          benchmark: benchmarkNote(
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkMetrics?.currentDrawdown,
            benchmarkGuard,
          ),
          toneClassName: signedValueClass(performanceWorkspace?.summary.current_drawdown),
        },
        {
          label: 'Max DD',
          value: signedPercent(performanceWorkspace?.summary.max_drawdown),
          benchmark: benchmarkNote(
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkMetrics?.maxDrawdown,
            benchmarkGuard,
          ),
          toneClassName: signedValueClass(performanceWorkspace?.summary.max_drawdown),
        },
        {
          label: '1M VOL',
          value: formatPercent(portfolioRiskMetrics.volatility1m),
          benchmark: benchmarkNote(
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkMetrics?.volatility1m,
            benchmarkGuard,
            formatPercent,
          ),
          title: portfolioRiskMetrics.volatility1m == null ? 'Need full 1M history.' : undefined,
        },
        {
          label: '3M VOL',
          value: formatPercent(portfolioRiskMetrics.volatility3m),
          benchmark: benchmarkNote(
            selectedBenchmarkInstrument,
            benchmarkLoading,
            benchmarkMetrics?.volatility3m,
            benchmarkGuard,
            formatPercent,
          ),
          title: portfolioRiskMetrics.volatility3m == null ? 'Need full 3M history.' : undefined,
        },
      ],
    },
    {
      label: 'Portfolio',
      rows: [
        { label: 'Total NAV', value: formatCurrency(summary?.nav, resolvedBaseCurrency), emphasis: true },
        {
          label: 'Total P&L',
          value: formatSignedCurrency(performanceWorkspace?.summary.total_pnl, resolvedBaseCurrency),
          emphasis: true,
          toneClassName: signedValueClass(performanceWorkspace?.summary.total_pnl),
        },
        { label: 'Top 5 Weight', value: formatPercent(top5Weight) },
      ],
    },
  ]
  const topHoldingColumnDefinitions: TopHoldingColumnDefinition[] = [
    {
      key: 'instrument',
      label: TOP_HOLDING_COLUMN_LABELS.instrument,
      render: (row) => holdingName(row),
    },
    {
      key: 'identifier',
      label: TOP_HOLDING_COLUMN_LABELS.identifier,
      render: (row) => primaryIdentifier(row),
    },
    {
      key: 'sleeve',
      label: TOP_HOLDING_COLUMN_LABELS.sleeve,
      render: (row) => composition.assignedLabelByInstrumentId.get(holdingReferenceId(row))?.leafLabel ?? 'Unassigned',
    },
    {
      key: 'sparkline',
      label: TOP_HOLDING_COLUMN_LABELS.sparkline,
      align: 'center',
      render: (row) => (
        holdingUsesEventValuation(row)
          ? 'N/A'
          : <Sparkline values={row.price_chart_6m} maxPoints={120} />
      ),
    },
    {
      key: 'quantity',
      label: TOP_HOLDING_COLUMN_LABELS.quantity,
      render: (row) => formatQuantity(row.quantity),
    },
    {
      key: 'last_price',
      label: TOP_HOLDING_COLUMN_LABELS.last_price,
      render: (row) =>
        holdingUsesEventValuation(row)
          ? 'N/A'
          : formatUnitPrice(row.last_price, holdingCurrency(row)),
    },
    {
      key: 'market_value',
      label: TOP_HOLDING_COLUMN_LABELS.market_value,
      render: (row) => {
        const amount = holdingAmountForDisplay(
          row,
          resolvedBaseCurrency,
          row.market_value_base,
          row.market_value,
        )
        return formatCurrency(amount.value, amount.currency)
      },
    },
    {
      key: 'cost_basis',
      label: TOP_HOLDING_COLUMN_LABELS.cost_basis,
      render: (row) => {
        const amount = holdingAmountForDisplay(
          row,
          resolvedBaseCurrency,
          row.cost_basis_base,
          row.cost_basis,
        )
        return formatCurrency(amount.value, amount.currency)
      },
    },
    {
      key: 'unrealized_pnl',
      label: TOP_HOLDING_COLUMN_LABELS.unrealized_pnl,
      render: (row) => {
        if (holdingUsesEventValuation(row)) {
          return 'N/A'
        }
        const unrealized =
          row.market_value_base != null && row.cost_basis_base != null
            ? row.market_value_base - row.cost_basis_base
            : null
        return (
          <span className={signedValueClass(unrealized)}>
            {formatSignedCurrency(unrealized, resolvedBaseCurrency)}
          </span>
        )
      },
    },
    {
      key: 'day_change',
      label: TOP_HOLDING_COLUMN_LABELS.day_change,
      render: (row) => {
        if (holdingDayChangeUnavailable(row)) {
          return 'N/A'
        }
        const amount = holdingAmountForDisplay(
          row,
          resolvedBaseCurrency,
          row.day_change_value_base,
          row.day_change_value,
        )
        return (
          <span className={signedValueClass(row.day_change_pct)}>
            {formatSignedCurrency(amount.value, amount.currency)} ({signedPercent(row.day_change_pct)})
          </span>
        )
      },
    },
    {
      key: 'weight',
      label: TOP_HOLDING_COLUMN_LABELS.weight,
      render: (row) => formatPercent(row.allocation),
    },
    {
      key: 'coverage',
      label: TOP_HOLDING_COLUMN_LABELS.coverage,
      render: (row) => (
        <span className={`coverage-pill ${row.last_price != null ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
          {row.coverage_status}
        </span>
      ),
    },
    {
      key: 'return_1w',
      label: TOP_HOLDING_COLUMN_LABELS.return_1w,
      render: (row) => {
        if (holdingUsesEventValuation(row)) {
          return 'N/A'
        }
        const value = row.instrument_return_1w ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
    },
    {
      key: 'return_mtd',
      label: TOP_HOLDING_COLUMN_LABELS.return_mtd,
      render: (row) => {
        if (holdingUsesEventValuation(row)) {
          return 'N/A'
        }
        const value = row.instrument_return_mtd ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
    },
    {
      key: 'return_ytd',
      label: TOP_HOLDING_COLUMN_LABELS.return_ytd,
      render: (row) => {
        if (holdingUsesEventValuation(row)) {
          return 'N/A'
        }
        const value = row.instrument_return_ytd ?? null
        return <span className={signedValueClass(value)}>{signedPercent(value)}</span>
      },
    },
  ]
  const topHoldingColumnDefinitionByKey = new Map(
    topHoldingColumnDefinitions.map((definition) => [definition.key, definition]),
  )
  const visibleTopHoldingColumns = topHoldingColumns
    .map((column) => topHoldingColumnDefinitionByKey.get(column))
    .filter((definition): definition is TopHoldingColumnDefinition => Boolean(definition))

  return (
    <PortfolioWorkspaceLayout
      activeSection="Overview"
      busy={workspaceLoading || performanceLoading || benchmarkLoading}
    >
      <section className="portfolio-detail-surface portfolio-overview-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}
        {performanceWorkspace?.summary.as_of_clamp_reason ? (
          <div
            className="inline-notice"
            role="status"
            title={performanceWorkspace.summary.as_of_clamp_reason}
            aria-label={`Performance shown through ${
              performanceWorkspace.summary.effective_end_date ?? performanceWorkspace.summary.end_date ?? '—'
            }. ${performanceWorkspace.summary.as_of_clamp_reason}`}
          >
            Performance shown through{' '}
            {performanceWorkspace.summary.effective_end_date ?? performanceWorkspace.summary.end_date ?? '—'}.
          </div>
        ) : null}
        <QualityWarningsNotice
          warnings={[
            ...(holdingsWorkspace?.quality_warnings ?? []),
            ...(performanceWorkspace?.summary.quality_warnings ?? []),
          ]}
        />

        {workspaceLoading ? <CalculationStatus /> : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {!workspaceLoading && holdingsWorkspace ? (
          <>
            <div className="performance-block-grid">
              <section className="portfolio-section-block overview-performance-block">
                <div className="overview-nav-grid">
                  <div className="overview-nav-chart-panel">
                    <div className="overview-chart-controls">
                      <BenchmarkSearchBox
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
                      />
                      {benchmarkComparisonDetail ? (
                        <InfoHint
                          label="Benchmark comparison"
                          detail={benchmarkComparisonDetail}
                          tone={benchmarkGuard?.mode === 'canonical' ? 'info' : 'warning'}
                        />
                      ) : null}
                    </div>
                    {benchmarkError ? <div className="overview-benchmark-error">{benchmarkError}</div> : null}
                    {performanceLoading && !performanceWorkspace ? (
                      <CalculationStatus />
                    ) : null}
                    {!performanceLoading && performanceWorkspace ? (
                      <PerformanceNavChart
                        points={navChartPoints}
                        twrPoints={twrIndexChartPoints}
                        benchmarkPoints={
                          !performanceIsOperational && benchmarkGuard && benchmarkGuard.mode !== 'unavailable'
                            ? benchmarkChartPoints
                            : []
                        }
                        benchmarkLabel={
                          !performanceIsOperational && selectedBenchmarkInstrument
                            ? `${instrumentPrimaryIdentifier(selectedBenchmarkInstrument)}${
                                benchmarkGuard?.reason === 'benchmark_price_return_comparable'
                                  ? ' (price return)'
                                  : benchmarkGuard?.mode === 'exploratory'
                                    ? ' (exploratory)'
                                    : ''
                              }`
                            : null
                        }
                        currency={resolvedBaseCurrency}
                        showRangeControls
                        variant="overview"
                      />
                    ) : null}
                  </div>

                  <aside className="overview-key-metrics" aria-label="Portfolio overview key metrics">
                    <div className="overview-key-metric-groups">
                      {overviewMetricGroups.map((group) => (
                        <table className="overview-key-metrics-table" key={group.label}>
                          <thead>
                            <tr>
                              <th colSpan={2}>
                                <span className="portfolio-title-with-hint">
                                  <span>{group.label}</span>
                                  {performanceIsOperational && group.label === 'Operational Performance' ? (
                                    <InfoHint
                                      label="Operational performance basis"
                                      detail={operationalPerformanceDetail}
                                      tone="warning"
                                    />
                                  ) : null}
                                </span>
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {group.rows.map((row) => (
                              <tr key={`${group.label}:${row.label}`}>
                                <th title={row.title}>{row.label}</th>
                                <td
                                  className={[
                                    row.emphasis ? 'overview-key-metric-emphasis' : '',
                                    row.toneClassName ?? '',
                                  ]
                                    .filter(Boolean)
                                    .join(' ') || undefined}
                                >
                                  <strong>{row.value}</strong>
                                  {'benchmark' in row && row.benchmark ? <small>{row.benchmark}</small> : null}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ))}
                    </div>
                  </aside>
                </div>
              </section>

              <OverviewAssetMix workspace={holdingsWorkspace} />

              <section className="portfolio-section-block">
                <div className="portfolio-detail-toolbar portfolio-section-toolbar overview-section-toolbar">
                  <div className="panel-title">Monthly Return Matrix</div>
                </div>
                <div className="table-shell">
                  <table className="transactions-table performance-return-matrix-table">
                    <thead>
                      <tr>
                        <th>Year</th>
                        {MONTH_LABELS.map((monthLabel) => (
                          <th key={monthLabel}>{monthLabel}</th>
                        ))}
                        <th title="Requires year-start anchor.">YTD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyMatrixRows.length ? (
                        monthlyMatrixRows.map((row) => (
                          <tr key={row.year}>
                            <th scope="row">
                              <span>{row.year}</span>
                              <span className={`coverage-dot ${coverageClassName(row.coverageState)}`} />
                            </th>
                            {row.months.map((bucket, index) => (
                              <td
                                key={`${row.year}:${MONTH_LABELS[index]}`}
                                className={`performance-cell-number performance-return-cell ${signedValueClass(
                                  bucket?.cumulative_twr,
                                )}`}
                                title={
                                  bucket
                                    ? `${bucket.start_date} to ${bucket.end_date}; ${formatLabel(
                                        bucket.coverage_state,
                                      )}; ${formatNumber(bucket.observation_count, 0)} observations${
                                        bucket.unavailable_reason ? `; ${bucket.unavailable_reason}` : ''
                                      }`
                                    : undefined
                                }
                              >
                                {signedPercent(bucket?.cumulative_twr)}
                              </td>
                            ))}
                            <td
                              className={`performance-cell-number performance-return-cell ${signedValueClass(row.ytd)}`}
                              title={row.hasYearStartAnchor ? `${row.year} YTD` : 'No year-start anchor.'}
                            >
                              {signedPercent(row.ytd)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow colSpan={14} label="No monthly returns." />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <div className="overview-panel-grid">
                <section className="portfolio-section-block">
                  <div className="portfolio-detail-toolbar portfolio-section-toolbar">
                    <div className="panel-title">Strategy Sleeves</div>
                  </div>
                  {sleeveRibbonSegments ? (
                    <StrategySleeveDonut
                      segments={sleeveRibbonSegments}
                      unassigned={unassignedSleeve}
                    />
                  ) : (
                    <div className="price-chart-empty">Sleeve allocation unavailable.</div>
                  )}
                </section>

                <section className="portfolio-section-block">
                  <div className="portfolio-detail-toolbar portfolio-section-toolbar">
                    <div className="panel-title">Top Holdings</div>
                  </div>
                  {topHoldingBarItems ? (
                    <RiskRankedBars
                      items={topHoldingBarItems}
                      ariaLabel="Top holdings ranked by current weight"
                      emptyLabel="No holdings."
                    />
                  ) : (
                    <div className="price-chart-empty">Top holdings allocation unavailable.</div>
                  )}
                </section>
              </div>

              <section className="portfolio-section-block">
                <div className="portfolio-detail-toolbar portfolio-section-toolbar">
                  <div className="panel-title">Top Holdings Detail</div>
                  <button
                    type="button"
                    className="overview-columns-button"
                    onClick={() => {
                      setTopHoldingColumnDraft(topHoldingColumns)
                      setTopHoldingColumnsOpen(true)
                    }}
                  >
                    Data Columns
                  </button>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        {visibleTopHoldingColumns.map((column) => (
                          <th key={column.key} className={column.align === 'center' ? 'chart-cell' : undefined}>
                            {column.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedHoldings.length ? (
                        sortedHoldings.slice(0, TOP_HOLDINGS_LIMIT).map((row) => (
                          <tr key={row.line_id}>
                            {visibleTopHoldingColumns.map((column) => (
                              <td key={column.key} className={column.align === 'center' ? 'chart-cell' : undefined}>
                                {column.render(row)}
                              </td>
                            ))}
                          </tr>
                        ))
                      ) : (
                        <TableStatusRow
                          colSpan={Math.max(1, visibleTopHoldingColumns.length)}
                          label="No holdings."
                        />
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </>
        ) : null}

        {topHoldingColumnsOpen ? (
          <div className="overview-columns-modal-backdrop" onClick={() => setTopHoldingColumnsOpen(false)}>
            <div
              ref={topHoldingColumnsDialogRef}
              className="overview-columns-modal"
              role="dialog"
              aria-modal="true"
              aria-label="Choose top holdings columns"
              tabIndex={-1}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="overview-columns-modal-header">
                <div className="panel-title">Data Columns</div>
                <button type="button" onClick={() => setTopHoldingColumnsOpen(false)}>
                  Close
                </button>
              </div>

              <div className="overview-columns-modal-body">
                <div className="overview-columns-field-groups">
                  {TOP_HOLDING_COLUMN_GROUPS.map((group) => (
                    <section className="overview-columns-field-group" key={group.label}>
                      <div className="section-heading">{group.label}</div>
                      {group.columns.map((column) => {
                        const locked = column === 'instrument'
                        return (
                          <label className="overview-columns-field-item" key={column}>
                            <input
                              type="checkbox"
                              checked={topHoldingColumnDraft.includes(column)}
                              disabled={locked}
                              onChange={(event) =>
                                setTopHoldingColumnDraft((current) =>
                                  event.target.checked
                                    ? [...current, column]
                                    : current.filter((item) => item !== column),
                                )
                              }
                            />
                            <span>{TOP_HOLDING_COLUMN_LABELS[column]}</span>
                          </label>
                        )
                      })}
                    </section>
                  ))}
                </div>

                <div className="overview-columns-arrange">
                  <div className="overview-columns-arrange-head">
                    <span>Selected</span>
                    <button type="button" onClick={() => setTopHoldingColumnDraft(DEFAULT_TOP_HOLDING_COLUMNS)}>
                      Reset
                    </button>
                  </div>
                  <div className="overview-columns-arrange-list">
                    {topHoldingColumnDraft.map((column, index) => (
                      <div className="overview-columns-arrange-item" key={column}>
                        <span>{TOP_HOLDING_COLUMN_LABELS[column]}</span>
                        <div>
                          <button
                            type="button"
                            disabled={index === 0}
                            onClick={() =>
                              setTopHoldingColumnDraft((current) => {
                                const next = [...current]
                                const [moved] = next.splice(index, 1)
                                next.splice(index - 1, 0, moved)
                                return next
                              })
                            }
                          >
                            Up
                          </button>
                          <button
                            type="button"
                            disabled={index === topHoldingColumnDraft.length - 1}
                            onClick={() =>
                              setTopHoldingColumnDraft((current) => {
                                const next = [...current]
                                const [moved] = next.splice(index, 1)
                                next.splice(index + 1, 0, moved)
                                return next
                              })
                            }
                          >
                            Down
                          </button>
                          {column !== 'instrument' ? (
                            <button
                              type="button"
                              onClick={() => setTopHoldingColumnDraft((current) => current.filter((item) => item !== column))}
                            >
                              Remove
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="overview-columns-modal-actions">
                <button
                  type="button"
                  onClick={() => {
                    setTopHoldingColumnDraft(topHoldingColumns)
                    setTopHoldingColumnsOpen(false)
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => {
                    setTopHoldingColumns(
                      topHoldingColumnDraft.includes('instrument')
                        ? topHoldingColumnDraft
                        : ['instrument', ...topHoldingColumnDraft],
                    )
                    setTopHoldingColumnsOpen(false)
                  }}
                >
                  Update
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
