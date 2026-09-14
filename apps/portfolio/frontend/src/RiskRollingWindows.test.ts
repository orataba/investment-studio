import { describe, expect, it } from 'vitest'
import { buildRollingRisk, realizedRiskSeries } from './lib/rollingRisk'
import { buildCorrelationMatrix } from './lib/riskCorrelation'
import { type GroupReturnSeries, shiftIsoDate } from './lib/riskReturnAlignment'
import type { PortfolioPerformanceResponse } from './lib/api'

function days(start: string, end: string) {
  const dates: string[] = []
  for (let date = start; date <= end; date = shiftIsoDate(date, 1)) dates.push(date)
  return dates
}
function series(key: string, dates = days('2026-06-01', '2026-09-10'), weight = 0.5): GroupReturnSeries {
  const points = dates.map((date, index) => ({ date, start_date: shiftIsoDate(date, -1), value: ((index % 7) - 3) / 100 }))
  return { groupKey: key, groupLabel: key, latestWeight: weight, observationCount: points.length,
    returnsByDate: new Map(points.map((p) => [p.date, p.value])), periodStartByDate: new Map(points.map((p) => [p.date, p.start_date])),
    endingWeightByDate: new Map(), inputPoints: points,
    observationCoverage: { start_date: shiftIsoDate(dates[0], -1), end_date: dates[dates.length - 1], gap_dates: [], gap_detection_basis: 'market_calendar:TEST' } }
}
function current(items: GroupReturnSeries[], lookbackDays = 30) {
  return buildRollingRisk({ series: items, asOfDate: '2026-09-10', lookbackDays })
}

describe('Window-scoped rolling and correlation risk', () => {
  it('does not let a provider gap outside 1M erase valid rolling estimates or correlation', () => {
    const a = series('a'); const b = series('b')
    a.observationCoverage!.gap_dates = ['2026-06-24']
    expect(current([a, b]).diagnostics.status).toBe('available')
    const matrix = buildCorrelationMatrix({ memberCount: 2, series: [a, b], issues: [] }, '2026-09-10', 30, 'daily')
    expect(matrix.issues).toEqual([])
    expect(matrix.matrix.groups).toHaveLength(2)
    expect(current([a, b], 90).diagnostics.status).toBe('unavailable')
  })
  it('rejects a shared provider gap even when every member has identical observed dates', () => {
    const a = series('a'); const b = series('b')
    for (const item of [a, b]) item.observationCoverage!.gap_dates = ['2026-09-04']
    const result = current([a, b])
    expect(result.diagnostics.issues.some((issue) => issue.missingDates.includes('2026-09-04'))).toBe(true)
    expect(result.volatilityPoints[result.volatilityPoints.length - 1].value).toBeNull()
    expect(buildCorrelationMatrix({ memberCount: 2, series: [a, b], issues: [] }, '2026-09-10', 30, 'daily').matrix.groups).toEqual([])
  })
  it('keeps a null estimate at a bad period instead of joining the chart across it', () => {
    const a = series('a'); const b = series('b')
    b.periodStartByDate.set('2026-08-01', '2026-07-30')
    const result = current([a, b])
    expect(result.volatilityPoints.find((point) => point.date === '2026-08-01')?.value).toBeNull()
    expect(result.volatilityPoints[result.volatilityPoints.length - 1].value).not.toBeNull()
  })
  it('rejects invalid input within the chosen window but ignores old malformed periods', () => {
    const a = series('a'); const b = series('b')
    a.inputPoints!.push({ date: '2026-06-10', start_date: null, value: NaN })
    expect(current([a, b]).diagnostics.status).toBe('available')
    a.inputPoints!.push({ date: '2026-09-04', start_date: null, value: NaN })
    expect(current([a, b]).diagnostics.status).toBe('unavailable')
  })
  it('uses the same first-period anchor for individual and common matrix coverage', () => {
    const dates = days('2026-03-02', '2026-03-23')
    const a = series('a', dates); const b = series('b', dates)
    for (const item of [a, b]) {
      item.periodStartByDate.set(dates[0], '2026-02-28')
      item.inputPoints![0].start_date = '2026-02-28'
    }
    const result = buildCorrelationMatrix({ memberCount: 2, series: [a, b], issues: [] }, '2026-03-28', 30, 'daily')
    expect(result.issues).toEqual([])
    expect(result.diagnostics.observedStartDate).toBe('2026-03-02')
    expect(result.diagnostics.periodStartDate).toBe('2026-02-28')
  })
  it('distinguishes a single member and zero variation from missing history', () => {
    const a = series('a')
    expect(buildCorrelationMatrix({ memberCount: 1, series: [a], issues: [] }, '2026-09-10', 30, 'daily').issues[0].coverageReason).toContain('at least two')
    const b = series('b'); b.returnsByDate.forEach((_v, date) => b.returnsByDate.set(date, 0))
    const result = buildCorrelationMatrix({ memberCount: 2, series: [a, b], issues: [] }, '2026-09-10', 30, 'daily')
    expect(result.issues.every((issue) => issue.reason === 'calculation_unavailable')).toBe(true)
    expect(result.issues[0].coverageReason).toContain('zero return variation')
  })
})

function performance() {
  let eligible = 0
  const daily_series = days('2026-08-10', '2026-09-10').map((as_of_date) => {
    const weekday = new Date(as_of_date + 'T00:00:00Z').getUTCDay()
    const observed = weekday !== 0 && weekday !== 6 && as_of_date !== '2026-08-10'
    return { as_of_date, market_risk_daily_return: observed ? (++eligible % 2 ? 0.01 : -0.01) : 0,
      daily_twr: 0.8, market_risk_return_observation_eligible: observed,
      market_risk_return_coverage_state: 'complete', market_risk_return_chain_continuous: true,
      market_risk_return_observation_exclusion_reason: observed ? null : 'no_fresh_market_observation' }
  })
  return { daily_series, summary: { start_boundary_kind: 'close_eod' } } as unknown as PortfolioPerformanceResponse
}
describe('Actual portfolio rolling risk', () => {
  it('uses eligible market-risk returns and the EOD window span, matching Performance annualization', () => {
    const source = performance(); const items = realizedRiskSeries(source)
    const result = buildRollingRisk({ series: items, performance: source, asOfDate: '2026-09-10', lookbackDays: 30 })
    expect(result.diagnostics.observationCount).toBe(23)
    expect(result.diagnostics.observedStartDate).toBe('2026-08-11')
    expect(result.diagnostics.periodStartDate).toBe('2026-08-10')
    const mean = 0.01 / 23
    const variance = (12 * (0.01 - mean) ** 2 + 11 * (-0.01 - mean) ** 2) / 22
    expect(result.volatilityPoints[result.volatilityPoints.length - 1].value).toBeCloseTo(Math.sqrt(variance * 23 / 31 * 365.25), 12)
  })
  it('preserves the first funded-BOD return while showing the real inception as the sample start', () => {
    const source = performance()
    const first = source.daily_series[0]
    first.market_risk_return_observation_eligible = true
    first.market_risk_daily_return = 0.015
    source.summary.start_boundary_kind = 'funded_bod'
    const items = realizedRiskSeries(source)
    expect(items[0].returnsByDate.get('2026-08-10')).toBe(0.015)
    const result = buildRollingRisk({ series: items, performance: source, asOfDate: '2026-09-10', lookbackDays: 90 })
    expect(result.diagnostics.status).toBe('unavailable')
    expect(result.diagnostics.observationCount).toBe(24)
    expect(result.diagnostics.observedStartDate).toBe('2026-08-10')
    expect(result.diagnostics.periodStartDate).toBe('2026-08-09')
  })
  it('does not discard an incomplete actual period and still show a complete estimate', () => {
    const source = performance()
    const point = source.daily_series.find((row) => row.as_of_date === '2026-09-04')!
    point.market_risk_return_coverage_state = 'partial'; point.market_risk_return_observation_eligible = false
    const result = buildRollingRisk({ series: realizedRiskSeries(source), performance: source, asOfDate: '2026-09-10', lookbackDays: 30 })
    expect(result.diagnostics.status).toBe('unavailable')
    expect(result.diagnostics.issues.some((issue) => issue.missingDates.includes('2026-09-04'))).toBe(true)
  })
})
