import { describe, expect, it } from 'vitest'
import { buildResearchComparison, reliableResearchPoints, researchChartSeries, researchMonthlyReturns, summarizeResearchSeries } from './lib/researchComparison'

describe('research comparison measurement boundaries', () => {
  it('compares common closes without importing saved whole-period metrics or shortening for a benchmark', () => {
    const result = buildResearchComparison(
      [{ date: '2026-01-01', value: 1 }, { date: '2026-01-02', value: 1.05 }, { date: '2026-01-05', value: 1.1 }],
      [{ date: '2026-01-02', value: 10 }, { date: '2026-01-05', value: 12 }],
      [{ date: '2026-01-05', value: 100 }],
    )!
    expect(result.startDate).toBe('2026-01-02')
    expect(result.actual.periodReturn).toBeCloseTo(1.1 / 1.05 - 1)
    expect(result.backtest.periodReturn).toBeCloseTo(.2)
    expect(result.benchmark).toBeNull()
    expect(result.observationCount).toBe(1)
    expect(result.actual.annualizedVolatility).toBeNull()
  })

  it('uses actual elapsed dates for risk frequency and calendar anniversaries for returns', () => {
    const risk = summarizeResearchSeries([{ date: '2026-01-01', value: 1 }, { date: '2026-01-02', value: 1.1 }, { date: '2026-01-05', value: .99 }])
    expect(risk.annualizedVolatility).toBeCloseTo(Math.sqrt(.02 * 2 / 4 * 365.25))
    expect(risk.annualizedReturn).toBeNull()
    expect(risk.calmar).toBeNull()
    const annual = [{ date: '2024-02-29', value: 1 }, { date: '2025-02-28', value: 1.1 }]
    expect(summarizeResearchSeries(annual).annualizedReturn).toBeCloseTo(.1)
    expect(summarizeResearchSeries(annual, false).annualizedReturn).toBeNull()
    expect(summarizeResearchSeries([{ date: '2025-02-28', value: 1 }, { date: '2026-02-27', value: 1.1 }]).annualizedReturn).toBeNull()
  })

  it('keeps the latest equal peak, unrecovered drawdowns, and a genuine terminal total loss', () => {
    const result = summarizeResearchSeries([{ date: '2026-01-01', value: 1 }, { date: '2026-01-02', value: 1 }, { date: '2026-01-03', value: .9 }])
    expect(result.maxDrawdownStart).toBe('2026-01-02')
    expect(result.maxDrawdownDays).toBe(1)
    expect(result.recoveryDays).toBeNull()
    const loss = summarizeResearchSeries([{ date: '2026-01-01', value: 1 }, { date: '2026-01-02', value: 0 }])
    expect(loss.periodReturn).toBe(-1)
    expect(loss.maxDrawdown).toBe(-1)
    expect(summarizeResearchSeries([{ date: '2025-01-01', value: 1 }, { date: '2026-01-01', value: 0 }]).annualizedReturn).toBe(-1)
  })

  it('does not compound across a known missing return or silently restart a curve', () => {
    const points = [{ date: '2026-01-01', value: 1 }, { date: '2026-01-02', value: null }, { date: '2026-01-03', value: 1.1 }]
    expect(reliableResearchPoints(points)).toEqual([points[0]])
    expect(buildResearchComparison(points, points)).toBeNull()
  })

  it('keeps actual inception visible and aligns a later simulation at a common close', () => {
    const result = researchChartSeries(
      [{ date: '2026-01-01', value: 100 }, { date: '2026-01-02', value: 110 }, { date: '2026-01-03', value: 120 }],
      [{ date: '2026-01-02', value: 1 }, { date: '2026-01-03', value: 1.05 }], [],
    )
    expect(result.actualPoints[0]).toEqual({ date: '2026-01-01', value: 1 })
    expect(result.backtestPoints[0]).toEqual({ date: '2026-01-02', value: 1.1 })
    expect(result.backtestPoints[1].value).toBeCloseTo(1.155)
  })

  it('compounds monthly returns from the previous close and marks partial boundaries', () => {
    const series = [{ date: '2024-02-15', value: 1 }, { date: '2024-02-29', value: 1.1 }, { date: '2024-03-28', value: 1.21 }, { date: '2024-04-15', value: 1.331 }]
    const monthly = researchMonthlyReturns(buildResearchComparison(series, series)!)
    expect(monthly.map((row) => row.partial)).toEqual([true, false, true])
    monthly.forEach((row) => expect(row.actual).toBeCloseTo(.1))
    expect(monthly[1].startDate).toBe('2024-02-29')
  })

  it('does not label a sparse multi-month return as a single calendar month', () => {
    const series = [{ date: '2026-01-15', value: 1 }, { date: '2026-03-15', value: 2 }]
    expect(researchMonthlyReturns(buildResearchComparison(series, series)!)).toEqual([])
  })
})
