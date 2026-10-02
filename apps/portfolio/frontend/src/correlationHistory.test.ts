import { describe, expect, it } from 'vitest'
import { buildCorrelationHistory } from './lib/correlationHistory'
import type { CorrelationMatrixScope } from './lib/riskCorrelation'
import { riskWindowStart, shiftIsoDate, type GroupReturnSeries, type ReturnPoint } from './lib/riskReturnAlignment'

const AS_OF_DATE = '2026-09-15'
const PRIOR_LEVELS = [0.2, 0.1, 0.25, 0.15, 0.3, 0.2, 0.1, 0.25, 0.15, 0.3, 0.2, 0.1]

function monthlyScope(levels = [0.9, ...PRIOR_LEVELS], memberCount = 3): CorrelationMatrixScope {
  const points: ReturnPoint[][] = Array.from({ length: memberCount }, () => [])
  let end = AS_OF_DATE
  for (const correlation of levels) {
    const start = riskWindowStart(end, 30)
    const dates: string[] = []
    for (let date = shiftIsoDate(start, 1); date <= end; date = shiftIsoDate(date, 1)) dates.push(date)
    dates.forEach((date, index) => {
      const phase = 2 * Math.PI * index / dates.length
      const noises = points.map((_items, member) => Math.cos((member + 2) * phase))
      const noiseMean = noises.reduce((sum, value) => sum + value, 0) / memberCount
      points.forEach((items, member) => items.push({
        date, start_date: shiftIsoDate(date, -1),
        // Orthogonal Fourier factors give the requested within-month pair
        // correlation, independently of each calendar month's observation count.
        value: (Math.sqrt((1 + (memberCount - 1) * correlation) / memberCount) * Math.sin(phase)
          + Math.sqrt(1 - correlation) * (noises[member] - noiseMean)) / 100,
      }))
    })
    end = start
  }
  const series = points.map((items, member): GroupReturnSeries => ({
    groupKey: `member-${member}`, groupLabel: `Member ${member}`, latestWeight: 1 / memberCount,
    observationCount: items.length, inputPoints: items,
    returnsByDate: new Map(items.map((point) => [point.date, point.value])),
    periodStartByDate: new Map(items.map((point) => [point.date, point.start_date!])),
    endingWeightByDate: new Map(items.map((point) => [point.date, 1 / memberCount])),
    observationCoverage: { start_date: end, end_date: AS_OF_DATE, gap_dates: [], gap_detection_basis: 'test-calendar' },
  }))
  return { memberCount, series, issues: [] }
}

describe('Correlation broad-rise observation', () => {
  it('detects a broad increase using the prior year only and exposes reproducible evidence', () => {
    const result = buildCorrelationHistory(monthlyScope(), AS_OF_DATE)
    expect(result.status).toBe('available')
    expect(result.attention).toBe(true)
    expect(result.referenceWindowCount).toBe(12)
    expect(result.referenceChangeCount).toBe(11)
    expect(result.current.averageCorrelation).toBeCloseTo(0.9, 12)
    expect(result.previous.averageCorrelation).toBeCloseTo(0.2, 12)
    expect(result.averageChange).toBeCloseTo(0.7, 12)
    expect(result.risingPairCount).toBe(3)
    expect(result.pairCount).toBe(3)
    expect(result.levelUpperFence).toBeCloseTo(0.41875, 12)
    // Prior changes have Q1 = -0.15 and Q3 = 0.10: 0.10 + 1.5 × 0.25.
    expect(result.changeUpperFence).toBeCloseTo(0.475, 12)
    expect(result.current.windowStartDate).toBe('2026-08-15')
    expect(result.previous.asOfDate).toBe(result.current.windowStartDate)
    expect(result.previous.observedEndDate).toBe(result.current.firstPeriodStartDate)
  })

  it('does not label an ordinary increase, a high but unchanged level, or a decline as broad rapid rise', () => {
    const ordinary = buildCorrelationHistory(monthlyScope([0.25, ...PRIOR_LEVELS]), AS_OF_DATE)
    expect(ordinary.status).toBe('available')
    expect(ordinary.averageChange).toBeCloseTo(0.05, 12)
    expect(ordinary.attention).toBe(false)
    const steady = buildCorrelationHistory(monthlyScope([0.9, 0.9, ...PRIOR_LEVELS.slice(1)]), AS_OF_DATE)
    expect(steady.attention).toBe(false)
    const declining = buildCorrelationHistory(monthlyScope([0.1, ...PRIOR_LEVELS]), AS_OF_DATE)
    expect(declining.averageChange).toBeCloseTo(-0.1, 12)
    expect(declining.attention).toBe(false)
  })

  it('requires a broad pair increase even if one large pair change lifts the mean', () => {
    const scope = monthlyScope([0.01, ...PRIOR_LEVELS.map((value) => value / 100)], 4)
    const start = riskWindowStart(AS_OF_DATE, 30)
    // Only one of six pairs becomes perfectly correlated; the other five fall.
    const dates = [...scope.series[0].returnsByDate.keys()].filter((date) => date > start).sort()
    dates.forEach((date, index) => {
      const phase = 2 * Math.PI * index / dates.length
      scope.series.forEach((series, member) => {
        const value = Math.cos((member < 2 ? 2 : member + 1) * phase) / 100
        series.returnsByDate.set(date, value)
        series.inputPoints!.find((point) => point.date === date)!.value = value
      })
    })
    const result = buildCorrelationHistory(scope, AS_OF_DATE)
    expect(result.status).toBe('available')
    expect(result.current.averageCorrelation!).toBeGreaterThan(result.levelUpperFence!)
    expect(result.averageChange!).toBeGreaterThan(result.changeUpperFence!)
    expect(result.risingPairCount).toBe(1)
    expect(result.pairCount).toBe(6)
    expect(result.attention).toBe(false)
  })

  it('keeps descriptive current/prior observations but does not invent attention from short or flat history', () => {
    const short = buildCorrelationHistory(monthlyScope([0.9, 0.2, 0.1]), AS_OF_DATE)
    expect(short.unavailableReason).toBe('history_incomplete')
    expect(short.current.averageCorrelation).toBeCloseTo(0.9, 12)
    expect(short.averageChange).toBeCloseTo(0.7, 12)
    expect(short.attention).toBe(false)
    const flat = buildCorrelationHistory(monthlyScope(Array(13).fill(1)), AS_OF_DATE)
    expect(flat.unavailableReason).toBe('flat_baseline')
    expect(flat.attention).toBe(false)
    const flatFractional = buildCorrelationHistory(monthlyScope([0.9, ...Array(12).fill(0.2)]), AS_OF_DATE)
    expect(flatFractional.unavailableReason).toBe('flat_baseline')
    expect(flatFractional.attention).toBe(false)
  })

  it('requires at least three members, while preserving a two-member comparison', () => {
    const result = buildCorrelationHistory(monthlyScope(undefined, 2), AS_OF_DATE)
    expect(result.unavailableReason).toBe('too_few_members')
    expect(result.averageChange).toBeCloseTo(0.7, 12)
    expect(result.pairCount).toBe(1)
    expect(result.attention).toBe(false)
  })

  it('distinguishes real low-dispersion changes from floating-point noise without an economic floor', () => {
    const result = buildCorrelationHistory(monthlyScope([0.200009, ...PRIOR_LEVELS.map((value) => 0.2 + value / 100_000)]), AS_OF_DATE)
    expect(result.status).toBe('available')
    expect(result.averageChange).toBeCloseTo(0.000007, 12)
    expect(result.attention).toBe(true)
    expect(result.risingPairCount).toBe(3)
  })

  it('treats weaker negative correlation as a possible diversification change without requiring positive levels', () => {
    const result = buildCorrelationHistory(monthlyScope([-0.01, ...PRIOR_LEVELS.map((value) => value / 10 - 0.3)]), AS_OF_DATE)
    expect(result.status).toBe('available')
    expect(result.current.averageCorrelation).toBeCloseTo(-0.01, 12)
    expect(result.previous.averageCorrelation).toBeCloseTo(-0.28, 12)
    expect(result.averageChange).toBeCloseTo(0.27, 12)
    expect(result.attention).toBe(true)
  })

  it('does not use observations after a selected date or include current in reference fences', () => {
    const scope = monthlyScope()
    const original = buildCorrelationHistory(scope, AS_OF_DATE)
    scope.series.forEach((series, member) => {
      const date = shiftIsoDate(AS_OF_DATE, 1)
      series.returnsByDate.set(date, member ? 100 : -100)
      series.periodStartByDate.set(date, AS_OF_DATE)
      series.inputPoints!.push({ date, start_date: AS_OF_DATE, value: member ? 100 : -100 })
    })
    expect(buildCorrelationHistory(scope, AS_OF_DATE)).toEqual(original)
    const lowerCurrent = buildCorrelationHistory(monthlyScope([0.05, ...PRIOR_LEVELS]), AS_OF_DATE)
    expect(lowerCurrent.levelUpperFence).toBe(original.levelUpperFence)
    expect(lowerCurrent.changeUpperFence).toBe(original.changeUpperFence)
  })

  it('uses member identity, not weight-dependent matrix order, across dates', () => {
    const scope = monthlyScope()
    const original = buildCorrelationHistory(scope, AS_OF_DATE)
    scope.series.reverse().forEach((series, index) => {
      series.endingWeightByDate = new Map([['2025-08-15', (index + 1) / 10], ['2026-08-16', (3 - index) / 10]])
    })
    expect(buildCorrelationHistory(scope, AS_OF_DATE)).toEqual(original)
  })

  it('fails closed for missing members, mismatched dates, and source gaps without dropping any pair', () => {
    const missing = monthlyScope()
    missing.series.pop()
    expect(buildCorrelationHistory(missing, AS_OF_DATE).unavailableReason).toBe('current_window')
    const mismatch = monthlyScope()
    mismatch.series[0].returnsByDate.delete('2026-09-03')
    expect(buildCorrelationHistory(mismatch, AS_OF_DATE).current.averageCorrelation).toBeNull()
    const gap = monthlyScope()
    gap.series.forEach((series) => { series.observationCoverage!.gap_dates = ['2026-09-03'] })
    expect(buildCorrelationHistory(gap, AS_OF_DATE).unavailableReason).toBe('current_window')
  })

  it('does not build a selective baseline around a missing historical month', () => {
    const scope = monthlyScope()
    scope.series.forEach((series) => { series.observationCoverage!.gap_dates = ['2026-02-02'] })
    const result = buildCorrelationHistory(scope, AS_OF_DATE)
    expect(result.unavailableReason).toBe('history_incomplete')
    expect(result.referenceWindowCount).toBe(11)
    expect(result.averageChange).toBeCloseTo(0.7, 12)
    expect(result.attention).toBe(false)
  })

  it('rejects overlapping return intervals even when monthly endpoint dates do not overlap', () => {
    const scope = monthlyScope()
    scope.series.forEach((series) => {
      series.periodStartByDate.set('2026-08-16', '2026-08-14')
      series.inputPoints!.find((point) => point.date === '2026-08-16')!.start_date = '2026-08-14'
    })
    const result = buildCorrelationHistory(scope, AS_OF_DATE)
    expect(result.current.averageCorrelation).toBeCloseTo(0.9, 12)
    expect(result.previous.averageCorrelation).toBeCloseTo(0.2, 12)
    expect(result.unavailableReason).toBe('overlapping_periods')
    expect(result.averageChange).toBeNull()
    expect(result.attention).toBe(false)
  })

  it('retains the current estimate when only the preceding month is incomplete', () => {
    const scope = monthlyScope()
    scope.series[0].observationCoverage!.gap_dates = ['2026-08-02']
    const result = buildCorrelationHistory(scope, AS_OF_DATE)
    expect(result.current.averageCorrelation).toBeCloseTo(0.9, 12)
    expect(result.previous.averageCorrelation).toBeNull()
    expect(result.averageChange).toBeNull()
    expect(result.unavailableReason).toBe('previous_window')
    expect(result.attention).toBe(false)
  })

  it('keeps unavailable for an unselected date and a constant-return member', () => {
    expect(buildCorrelationHistory(monthlyScope(), '').unavailableReason).toBe('current_window')
    const scope = monthlyScope()
    scope.series[0].returnsByDate.forEach((_value, date) => scope.series[0].returnsByDate.set(date, 0))
    scope.series[0].inputPoints!.forEach((point) => { point.value = 0 })
    const result = buildCorrelationHistory(scope, AS_OF_DATE)
    expect(result.current.averageCorrelation).toBeNull()
    expect(result.attention).toBe(false)
  })
})
