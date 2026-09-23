import type { PortfolioResearchBacktestPointRecord as Point } from './api'

const DAY = 86_400_000
const time = (date: string) => Date.parse(`${date}T00:00:00Z`)
const days = (start: string, end: string) => Math.round((time(end) - time(start)) / DAY)
const valid = (point: Point): point is { date: string; value: number } => (
  point.value != null && Number.isFinite(point.value) && point.value >= 0 && Number.isFinite(time(point.date))
)

function actualYearFraction(start: string, end: string) {
  const startTime = new Date(time(start))
  const anniversary = (years: number) => {
    const date = new Date(startTime)
    date.setUTCFullYear(date.getUTCFullYear() + years)
    if (date.getUTCMonth() !== startTime.getUTCMonth()) date.setUTCDate(0)
    return date.toISOString().slice(0, 10)
  }
  let whole = Number(end.slice(0, 4)) - Number(start.slice(0, 4))
  if (anniversary(whole) > end) whole--
  return whole + days(anniversary(whole), end) / days(anniversary(whole), anniversary(whole + 1))
}

export type ResearchSeriesSummary = {
  periodReturn: number | null
  annualizedReturn: number | null
  annualizedVolatility: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  maxDrawdown: number | null
  currentDrawdown: number | null
  maxDrawdownStart: string | null
  maxDrawdownEnd: string | null
  maxDrawdownDays: number | null
  recoveryDays: number | null
}

/** A missing observation ends the usable path; never compound across a known gap. */
export function reliableResearchPoints(points: Point[], startDate?: string | null, endDate?: string | null) {
  const result: Array<{ date: string; value: number }> = []
  for (const point of [...new Map(points.map((item) => [item.date, item])).values()].sort((a, b) => a.date.localeCompare(b.date))) {
    if (startDate && point.date < startDate || endDate && point.date > endDate) continue
    if (!valid(point)) {
      if (result.length) break
      continue
    }
    if (!result.length && point.value === 0) continue
    result.push(point)
    if (point.value === 0) break
  }
  return result
}

export function summarizeResearchSeries(points: Point[], allowAnnualization = true): ResearchSeriesSummary {
  const series = reliableResearchPoints(points)
  const empty: ResearchSeriesSummary = { periodReturn: null, annualizedReturn: null, annualizedVolatility: null,
    sharpe: null, sortino: null, calmar: null, maxDrawdown: null, currentDrawdown: null,
    maxDrawdownStart: null, maxDrawdownEnd: null, maxDrawdownDays: null, recoveryDays: null }
  if (series.length < 2) return empty
  const first = series[0]
  const last = series[series.length - 1]
  const returns = series.slice(1).map((point, index) => point.value / series[index].value - 1)
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length
  // Performance uses eligible observations over the actual anchor-to-close span.
  const periodsPerYear = returns.length / days(first.date, last.date) * 365.25
  const variance = returns.length > 1 ? returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (returns.length - 1) : null
  const annualizedVolatility = variance == null ? null : Math.sqrt(variance * periodsPerYear)
  // Match annualization.actual_year_fraction, including leap-day anniversaries.
  const years = actualYearFraction(first.date, last.date)
  const annualizedReturn = allowAnnualization && years >= 1 && last.value >= 0
    ? (last.value / first.value) ** (1 / years) - 1 : null
  let peak = first
  let maxDrawdown = 0
  let maxStart: string | null = null
  let maxEnd: string | null = null
  let recovery: string | null = null
  let recoveryValue = first.value
  for (const point of series) {
    if (point.value >= peak.value - 1e-12) peak = point
    const drawdown = point.value / peak.value - 1
    if (drawdown < maxDrawdown) {
      maxDrawdown = drawdown
      maxStart = peak.date
      maxEnd = point.date
      recovery = null
      recoveryValue = peak.value
    }
    if (maxEnd && point.date > maxEnd && recovery == null && point.value >= recoveryValue - 1e-12) recovery = point.date
  }
  const downside = Math.sqrt(returns.reduce((sum, value) => sum + Math.min(value, 0) ** 2, 0) / returns.length * periodsPerYear)
  return {
    periodReturn: last.value / first.value - 1, annualizedReturn, annualizedVolatility,
    sharpe: annualizedVolatility != null && annualizedVolatility > 1e-12 ? mean * periodsPerYear / annualizedVolatility : null,
    sortino: returns.length > 1 && downside > 1e-12 ? mean * periodsPerYear / downside : null,
    calmar: annualizedReturn != null && maxDrawdown < -1e-12 ? annualizedReturn / Math.abs(maxDrawdown) : null,
    maxDrawdown, currentDrawdown: last.value / peak.value - 1,
    maxDrawdownStart: maxStart, maxDrawdownEnd: maxEnd,
    maxDrawdownDays: maxStart && maxEnd ? days(maxStart, maxEnd) : null,
    recoveryDays: maxEnd && recovery ? days(maxEnd, recovery) : null,
  }
}

export function buildResearchComparison(actual: Point[], backtest: Point[], benchmark: Point[] = [], allowActualAnnualization = true) {
  const actualSeries = reliableResearchPoints(actual)
  const backtestSeries = reliableResearchPoints(backtest)
  const actualMap = new Map(actualSeries.map((point) => [point.date, point.value]))
  const backtestMap = new Map(backtestSeries.map((point) => [point.date, point.value]))
  const commonDates = [...actualMap.keys()].filter((date) => backtestMap.has(date))
  if (commonDates.length < 2) return null
  const normalize = (values: Map<string, number>) => commonDates.map((date) => ({ date, value: values.get(date)! / values.get(commonDates[0])! }))
  if (!(actualMap.get(commonDates[0])! > 0) || !(backtestMap.get(commonDates[0])! > 0)) return null
  const actualPoints = normalize(actualMap)
  const backtestPoints = normalize(backtestMap)
  const benchmarkMap = new Map(reliableResearchPoints(benchmark).map((point) => [point.date, point.value]))
  // A benchmark with a shorter window must not silently shorten the actual/backtest comparison.
  const benchmarkPoints = commonDates.every((date) => benchmarkMap.has(date)) && (benchmarkMap.get(commonDates[0]) ?? 0) > 0
    ? normalize(benchmarkMap) : []
  return {
    startDate: commonDates[0], endDate: commonDates[commonDates.length - 1], observationCount: commonDates.length - 1,
    actualPoints, backtestPoints, benchmarkPoints,
    actual: summarizeResearchSeries(actualPoints, allowActualAnnualization), backtest: summarizeResearchSeries(backtestPoints),
    benchmark: benchmarkPoints.length ? summarizeResearchSeries(benchmarkPoints) : null,
  }
}

export type ResearchComparison = NonNullable<ReturnType<typeof buildResearchComparison>>

export type ResearchCalendarReturn = {
  value: number
  startDate: string
  endDate: string
  partial: boolean
}

type CalendarComparison = {
  actual: ResearchCalendarReturn | null
  backtest: ResearchCalendarReturn | null
  benchmark: ResearchCalendarReturn | null
  difference: ResearchCalendarReturn | null
}

function calendarReturns(points: Point[], unit: 'month' | 'year') {
  const series = reliableResearchPoints(points)
  const result = new Map<string, ResearchCalendarReturn>()
  const keyOf = (date: string) => date.slice(0, unit === 'month' ? 7 : 4)
  const boundary = (key: string) => {
    const date = new Date(Date.UTC(Number(key.slice(0, 4)), unit === 'month' ? Number(key.slice(5, 7)) : 12, 0))
    while (date.getUTCDay() === 0 || date.getUTCDay() === 6) date.setUTCDate(date.getUTCDate() - 1)
    return date.toISOString().slice(0, 10)
  }
  const previousKey = (key: string) => unit === 'year' ? String(Number(key) - 1)
    : new Date(Date.UTC(Number(key.slice(0, 4)), Number(key.slice(5, 7)) - 1, 0)).toISOString().slice(0, 7)
  for (let first = 0; first < series.length;) {
    const key = keyOf(series[first].date)
    let last = first
    while (last + 1 < series.length && keyOf(series[last + 1].date) === key) last++
    const previous = first > 0 ? series[first - 1] : null
    const priorKey = previousKey(key)
    // Do not allocate a sparse cross-month/year return to the next period.
    // Without a reliable period-end close, show only the observed within-period interval.
    const hasBoundary = previous != null && keyOf(previous.date) === priorKey && previous.date >= boundary(priorKey)
    const anchor = hasBoundary ? first - 1 : first
    if (anchor < last) result.set(key, {
      value: series[last].value / series[anchor].value - 1,
      startDate: series[anchor].date, endDate: series[last].date,
      partial: !hasBoundary || series[last].date < boundary(key),
    })
    first = last + 1
  }
  return result
}

export function buildResearchReturnMatrix(actual: Point[], backtest: Point[], benchmark: Point[] = []) {
  const months = [actual, backtest, benchmark].map((series) => calendarReturns(series, 'month'))
  const annual = [actual, backtest, benchmark].map((series) => calendarReturns(series, 'year'))
  const compare = (maps: Array<Map<string, ResearchCalendarReturn>>, key: string): CalendarComparison => {
    const [a, b, benchmarkReturn] = maps.map((map) => map.get(key) ?? null)
    return { actual: a, backtest: b, benchmark: benchmarkReturn,
      difference: a && b && a.startDate === b.startDate && a.endDate === b.endDate
        ? { ...a, value: a.value - b.value, partial: a.partial || b.partial } : null }
  }
  const years = new Set([...months.flatMap((map) => [...map.keys()].map((key) => key.slice(0, 4))), ...annual.flatMap((map) => [...map.keys()])])
  return [...years].sort().reverse().map((year) => ({ year,
    months: Array.from({ length: 12 }, (_, index) => compare(months, `${year}-${String(index + 1).padStart(2, '0')}`)),
    annual: compare(annual, year),
  }))
}

/** Keep inception visible while aligning comparisons at their first shared close. */
export function researchChartSeries(actual: Point[], backtest: Point[], benchmark: Point[]) {
  const actualPoints = reliableResearchPoints(actual)
  const base = actualPoints[0]?.value ?? 1
  const normalizedActual = actualPoints.map((point) => ({ date: point.date, value: point.value / base }))
  const actualMap = new Map(normalizedActual.map((point) => [point.date, point.value]))
  const align = (points: Point[]) => {
    const series = reliableResearchPoints(points)
    const anchor = series.find((point) => actualMap.has(point.date))
    if (normalizedActual.length && !anchor) return []
    const first = anchor ?? series[0]
    if (!first || first.value <= 0) return []
    return series.filter((point) => point.date >= first.date).map((point) => ({
      date: point.date, value: point.value / first.value * (actualMap.get(first.date) ?? 1),
    }))
  }
  return { actualPoints: normalizedActual, backtestPoints: align(backtest), benchmarkPoints: align(benchmark) }
}
