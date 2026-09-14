import type { PortfolioPerformanceResponse } from './api'
import { sampleCovariance, type CorrelationMatrixCoverageIssue } from './riskCorrelation'
import { dayDiff, riskWindowStart, shiftIsoDate, type GroupReturnSeries, type ReturnPoint } from './riskReturnAlignment'
import { assessRiskWindowCoverage } from './riskWindowCoverage'
import { alignedWindowIssues, windowDiagnostics, windowIssue, type RiskWindowDiagnostics } from './riskWindowData'

export type RollingEstimatePoint = { date: string; value: number | null }
export type RollingRiskResult = {
  volatilityPoints: RollingEstimatePoint[]
  sharpePoints: RollingEstimatePoint[]
  diagnostics: RiskWindowDiagnostics
}

/** The canonical daily market-risk path, with deliberate non-observation days excluded. */
export function realizedRiskSeries(performance: PortfolioPerformanceResponse | null): GroupReturnSeries[] {
  if (!performance) return []
  const rows = performance.daily_series.slice().sort((a, b) => a.as_of_date.localeCompare(b.as_of_date))
  const points: ReturnPoint[] = []
  const unavailablePeriods: Array<{ date: string; reason: string }> = []
  rows.forEach((row, index) => {
    const previous = rows[index - 1]
    const brokenHere = row.market_risk_return_chain_continuous === false && previous?.market_risk_return_chain_continuous !== false
    if (row.market_risk_return_coverage_state === 'partial' || brokenHere) unavailablePeriods.push({
      date: row.as_of_date, reason: 'The actual portfolio market-risk return is incomplete or its return chain breaks in this window.',
    })
    if (previous && (dayDiff(previous.as_of_date, row.as_of_date) ?? 0) > 1) unavailablePeriods.push({
      date: row.as_of_date, reason: 'The actual portfolio daily history is missing a valuation period.',
    })
    if (!row.market_risk_return_observation_eligible) return
    if (row.market_risk_return_coverage_state !== 'complete' || !Number.isFinite(row.market_risk_daily_return)) {
      unavailablePeriods.push({ date: row.as_of_date, reason: 'An eligible actual portfolio market-risk return is unavailable.' })
      return
    }
    // Funded BOD is a real first-day return; an imported EOD anchor is not eligible.
    points.push({ date: row.as_of_date, start_date: previous?.as_of_date ?? shiftIsoDate(row.as_of_date, -1), value: row.market_risk_daily_return! })
  })
  return [{ groupKey: 'realized-portfolio', groupLabel: 'Actual portfolio',
    returnsByDate: new Map(points.map((p) => [p.date, p.value])), periodStartByDate: new Map(points.map((p) => [p.date, p.start_date ?? null])),
    endingWeightByDate: new Map(), latestWeight: 1, observationCount: points.length, inputPoints: points, unavailablePeriods }]
}

function observationDensity(dates: string[]) {
  const gaps = dates.slice(1).map((date, index) => dayDiff(dates[index], date) ?? 0).filter((gap) => gap > 0).sort((a, b) => a - b)
  const span = (dayDiff(dates[0], dates[dates.length - 1]!) ?? 0) + (gaps[Math.floor(gaps.length / 2)] ?? 1)
  return span > 0 ? dates.length / span * 365.25 : null
}

export function buildRollingRisk({ series, asOfDate, lookbackDays, inputIssues = [], performance }: {
  series: GroupReturnSeries[]; asOfDate: string; lookbackDays: number
  inputIssues?: CorrelationMatrixCoverageIssue[]; performance?: PortfolioPerformanceResponse | null
}): RollingRiskResult {
  const active = series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  const starts = active.map((item) => [...item.returnsByDate.keys()].sort()[0]).filter(Boolean).sort()
  const commonStart = starts[starts.length - 1] ?? ''
  const candidateDates = [...new Set(active.flatMap((item) => [...item.returnsByDate.keys(), ...(item.unavailablePeriods ?? []).map((period) => period.date)]))]
    .filter((date) => date >= commonStart && date <= asOfDate).sort()
  if (asOfDate && !candidateDates.includes(asOfDate)) candidateDates.push(asOfDate)
  const estimate = (date: string) => {
    const { dates, issues: alignmentIssues } = alignedWindowIssues(active, date, lookbackDays)
    const issues = [...inputIssues, ...alignmentIssues]
    if (!active.length) issues.push(windowIssue('portfolio', 'Portfolio', 'missing_series', 'No eligible market-risk return observations are available.'))
    let anchor = dates.length ? active[0]?.periodStartByDate.get(dates[0]) ?? null : null
    if (performance) {
      const requiredStart = riskWindowStart(date, lookbackDays)
      const rows = performance.daily_series.filter((row) => row.as_of_date <= requiredStart).sort((a, b) => a.as_of_date.localeCompare(b.as_of_date))
      anchor = rows[rows.length - 1]?.as_of_date ?? anchor
    }
    const coverage = assessRiskWindowCoverage(dates, date, lookbackDays, 'daily', undefined, anchor)
    if (!coverage.ok) issues.push(windowIssue('portfolio', 'Portfolio', 'window_coverage', coverage.error || 'The selected window is incomplete.'))
    const diagnostics = windowDiagnostics({ asOfDate: date, lookbackDays, frequency: 'daily', dates, firstPeriodStartDate: anchor, scopeMemberCount: active.length, issues })
    if (issues.length) return { volatility: null, sharpe: null, diagnostics }
    const values = dates.map((day) => active.reduce((sum, item) => sum + item.latestWeight! * item.returnsByDate.get(day)!, 0))
    const variance = sampleCovariance(values, values)
    const span = anchor ? dayDiff(anchor, date) : null
    const periodsPerYear = performance ? (span && span > 0 ? values.length / span * 365.25 : null) : observationDensity(dates)
    if (variance == null || periodsPerYear == null || variance < 0) {
      diagnostics.issues.push(windowIssue('portfolio', 'Portfolio', 'calculation_unavailable', 'The selected sample cannot produce a volatility estimate.'))
      diagnostics.status = 'unavailable'
      return { volatility: null, sharpe: null, diagnostics }
    }
    const volatility = Math.sqrt(variance * periodsPerYear)
    const mean = values.reduce((sum, value) => sum + value, 0) / values.length
    return { volatility, sharpe: volatility > 1e-12 ? mean * periodsPerYear / volatility : null, diagnostics }
  }
  const estimates = candidateDates.map((date) => ({ date, ...estimate(date) }))
  return {
    volatilityPoints: estimates.map(({ date, volatility }) => ({ date, value: volatility })),
    sharpePoints: estimates.map(({ date, sharpe }) => ({ date, value: sharpe })),
    diagnostics: (estimates[estimates.length - 1] ?? estimate(asOfDate)).diagnostics,
  }
}

export function benchmarkRiskSeries(points: ReturnPoint[]): GroupReturnSeries[] {
  if (!points.length) return []
  return [{ groupKey: 'benchmark', groupLabel: 'Benchmark', latestWeight: 1, observationCount: points.length,
    returnsByDate: new Map(points.map((p) => [p.date, p.value])), periodStartByDate: new Map(points.map((p) => [p.date, p.start_date ?? null])),
    endingWeightByDate: new Map(), inputPoints: points }]
}
