import type { InstrumentChartPoint } from './api'
import {
  actualYearFraction,
  namedReturnWindowSpec,
  resolveReturnWindow,
  type ReturnWindowName,
} from './returnWindows'

export type PerformanceMetricPeriodKey =
  | '1W'
  | '1M'
  | 'MTD'
  | 'YTD'
  | '3M'
  | '6M'
  | '1Y'
  | '2Y'
  | '3Y'
  | '5Y'
  | 'SI'

export const PERFORMANCE_METRIC_PERIODS: Array<{
  key: PerformanceMetricPeriodKey
  label: string
}> = [
  { key: '1W', label: '1W' },
  { key: '1M', label: '1M' },
  { key: 'MTD', label: 'MTD' },
  { key: 'YTD', label: 'YTD' },
  { key: '3M', label: '3M' },
  { key: '6M', label: '6M' },
  { key: '1Y', label: '1Y' },
  { key: '2Y', label: '2Y' },
  { key: '3Y', label: '3Y' },
  { key: '5Y', label: '5Y' },
  { key: 'SI', label: 'SI' },
]

export type PerformanceMetricSnapshot = {
  periodReturn: number | null
  annualizedReturn: number | null
  annualizedVolatility: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  maxDrawdown: number | null
  recoveryDays: number | null
  recoveryOpen: boolean
}

export type PerformanceMetricPeriodSnapshot = {
  key: PerformanceMetricPeriodKey
  label: string
  snapshot: PerformanceMetricSnapshot
}

function orderedPoints(points: InstrumentChartPoint[]) {
  const byDate = new Map<string, InstrumentChartPoint>()
  points.forEach((point) => {
    const pointDate = String(point.date || '').slice(0, 10)
    const value = Number(point.value)
    if (pointDate && Number.isFinite(value) && value > 0) {
      byDate.set(pointDate, { date: pointDate, value })
    }
  })
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date))
}

function anchoredWindow(
  points: InstrumentChartPoint[],
  periodKey: PerformanceMetricPeriodKey,
) {
  const ordered = orderedPoints(points)
  if (ordered.length < 2) return []
  const endDate = ordered[ordered.length - 1].date
  if (periodKey === 'SI') {
    return resolveReturnWindow(ordered, ordered[0].date, endDate)?.points ?? []
  }
  const spec = namedReturnWindowSpec(periodKey as ReturnWindowName, endDate)
  return resolveReturnWindow(ordered, spec.start, spec.end, spec.anchorMode)?.points ?? []
}

function periodicReturns(points: InstrumentChartPoint[]) {
  const returns: number[] = []
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1].value
    const current = points[index].value
    if (previous > 0 && current > 0) returns.push(current / previous - 1)
  }
  return returns
}

function sampleStandardDeviation(values: number[]) {
  if (values.length < 2) return null
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce(
    (sum, value) => sum + ((value - mean) ** 2),
    0,
  ) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function inferredPeriodsPerYear(points: InstrumentChartPoint[], returnCount: number) {
  if (points.length < 2 || returnCount < 1) return null
  const start = new Date(`${points[0].date}T00:00:00Z`).getTime()
  const end = new Date(`${points[points.length - 1].date}T00:00:00Z`).getTime()
  const elapsedDays = Math.round((end - start) / 86_400_000)
  return elapsedDays > 0 ? (returnCount / elapsedDays) * 365.25 : null
}

function drawdownStats(points: InstrumentChartPoint[]) {
  if (points.length < 2) {
    return { maxDrawdown: null, recoveryDays: null, recoveryOpen: false }
  }

  let peakValue = points[0].value
  let peakIndex = 0
  let worstDrawdown = 0
  let worstPeakIndex = 0
  let worstTroughIndex: number | null = null

  for (let index = 1; index < points.length; index += 1) {
    if (points[index].value > peakValue) {
      peakValue = points[index].value
      peakIndex = index
    }
    const drawdown = ((points[index].value / peakValue) - 1) * 100
    if (drawdown < worstDrawdown) {
      worstDrawdown = drawdown
      worstPeakIndex = peakIndex
      worstTroughIndex = index
    }
  }

  if (worstTroughIndex == null) {
    return { maxDrawdown: 0, recoveryDays: 0, recoveryOpen: false }
  }

  const recoveryTarget = points[worstPeakIndex].value
  for (let index = worstTroughIndex + 1; index < points.length; index += 1) {
    if (points[index].value < recoveryTarget) continue
    const trough = new Date(`${points[worstTroughIndex].date}T00:00:00Z`).getTime()
    const recovery = new Date(`${points[index].date}T00:00:00Z`).getTime()
    return {
      maxDrawdown: worstDrawdown,
      recoveryDays: Math.max(Math.round((recovery - trough) / 86_400_000), 0),
      recoveryOpen: false,
    }
  }

  return { maxDrawdown: worstDrawdown, recoveryDays: null, recoveryOpen: true }
}

export function buildPerformanceMetricSnapshot(
  points: InstrumentChartPoint[],
  options: { continuousDaily?: boolean; pathRiskAvailable?: boolean } = {},
): PerformanceMetricSnapshot {
  const unavailable: PerformanceMetricSnapshot = {
    periodReturn: null,
    annualizedReturn: null,
    annualizedVolatility: null,
    sharpe: null,
    sortino: null,
    calmar: null,
    maxDrawdown: null,
    recoveryDays: null,
    recoveryOpen: false,
  }
  const ordered = orderedPoints(points)
  if (ordered.length < 2) {
    return unavailable
  }

  const first = ordered[0]
  const last = ordered[ordered.length - 1]
  const periodReturn = ((last.value / first.value) - 1) * 100
  const years = actualYearFraction(first.date, last.date)
  const annualizedReturn = years >= 1
    ? (Math.pow(last.value / first.value, 1 / years) - 1) * 100
    : null
  if (options.pathRiskAvailable === false || (options.continuousDaily && ordered.some((point, index) => index > 0 && (
    new Date(`${point.date}T00:00:00Z`).getTime() -
    new Date(`${ordered[index - 1].date}T00:00:00Z`).getTime()
  ) !== 86_400_000))) {
    // Missing UTC days leave endpoint returns usable but cannot establish a
    // complete daily risk path by lowering the inferred observation density.
    return { ...unavailable, periodReturn, annualizedReturn }
  }
  const returns = periodicReturns(ordered)
  const periodsPerYear = inferredPeriodsPerYear(ordered, returns.length)
  const standardDeviation = sampleStandardDeviation(returns)
  const annualizedVolatility =
    standardDeviation == null || periodsPerYear == null
      ? null
      : standardDeviation * Math.sqrt(periodsPerYear) * 100
  const mean = returns.length
    ? returns.reduce((sum, value) => sum + value, 0) / returns.length
    : null
  const sharpe =
    mean == null || standardDeviation == null || standardDeviation === 0 || periodsPerYear == null
      ? null
      : (mean / standardDeviation) * Math.sqrt(periodsPerYear)
  const downsideDeviation = returns.length
    ? Math.sqrt(
        returns.reduce((sum, value) => sum + (Math.min(value, 0) ** 2), 0) /
          returns.length,
      )
    : null
  const sortino =
    mean == null || downsideDeviation == null || downsideDeviation === 0 || periodsPerYear == null
      ? null
      : (mean / downsideDeviation) * Math.sqrt(periodsPerYear)
  const drawdown = drawdownStats(ordered)
  const calmar =
    annualizedReturn == null || drawdown.maxDrawdown == null || drawdown.maxDrawdown === 0
      ? null
      : annualizedReturn / Math.abs(drawdown.maxDrawdown)

  return {
    periodReturn,
    annualizedReturn,
    annualizedVolatility,
    sharpe,
    sortino,
    calmar,
    ...drawdown,
  }
}

export function buildPerformanceMetricPeriodSnapshots(
  points: InstrumentChartPoint[],
  options: { continuousDaily?: boolean; pathRiskAvailable?: boolean } = {},
): PerformanceMetricPeriodSnapshot[] {
  return PERFORMANCE_METRIC_PERIODS.map((period) => ({
    ...period,
    snapshot: buildPerformanceMetricSnapshot(anchoredWindow(points, period.key), options),
  }))
}
