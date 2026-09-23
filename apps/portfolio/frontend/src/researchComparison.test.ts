import { describe, expect, it } from 'vitest'
import { buildResearchComparison, reliableResearchPoints, researchChartSeries, buildResearchReturnMatrix, summarizeResearchSeries } from './lib/researchComparison'

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

  it('shows twelve months plus a compounded annual return, preserving actual history before a saved backtest', () => {
    const actual = [{ date: '2025-12-31', value: 1 }, { date: '2026-01-30', value: 1.1 }, { date: '2026-02-27', value: .99 }]
    const backtest = [{ date: '2026-01-30', value: 1 }, { date: '2026-02-27', value: 1.2 }]
    const [year] = buildResearchReturnMatrix(actual, backtest)
    expect(year.year).toBe('2026')
    expect(year.months).toHaveLength(12)
    expect(year.months[0].actual?.value).toBeCloseTo(.1)
    expect(year.months[0].backtest).toBeNull()
    expect(year.months[1].actual?.value).toBeCloseTo(-.1)
    expect(year.months[1].difference?.value).toBeCloseTo(-.3)
    expect(year.annual.actual?.value).toBeCloseTo(-.01)
    expect(year.annual.backtest?.value).toBeCloseTo(.2)
    expect(year.annual.difference).toBeNull()
  })

  it('marks inception and current periods, including annual totals, without manufacturing missing months', () => {
    const series = [{ date: '2026-03-30', value: 1, is_start_anchor: true }, { date: '2026-03-31', value: 1.01 }, { date: '2026-04-30', value: 1.111 }, { date: '2026-05-15', value: 1.2221 }]
    const [year] = buildResearchReturnMatrix(series, series)
    expect(year.months[2].actual?.partial).toBe(true)
    expect(year.months[3].actual?.partial).toBe(false)
    expect(year.months[4].actual?.partial).toBe(true)
    expect(year.months[3].actual?.value).toBeCloseTo(.1)
    expect(year.annual.actual?.value).toBeCloseTo(.2221)
    expect(year.annual.actual?.partial).toBe(true)
    expect(year.months[0].actual).toBeNull()
    expect(year.months[5].actual).toBeNull()
  })

  it('does not attribute a sparse cross-month return to one month or subtract mismatched intervals', () => {
    const actual = [{ date: '2026-01-15', value: 1 }, { date: '2026-03-15', value: 2 }, { date: '2026-03-31', value: 2.2 }]
    const backtest = [{ date: '2026-02-27', value: 1 }, { date: '2026-03-15', value: 1.1 }, { date: '2026-03-31', value: 1.2 }]
    const [year] = buildResearchReturnMatrix(actual, backtest)
    expect(year.months[1].actual).toBeNull()
    expect(year.months[2].actual).toMatchObject({ startDate: '2026-03-15', endDate: '2026-03-31', partial: true })
    expect(year.months[2].actual?.value).toBeCloseTo(.1)
    expect(year.months[2].backtest?.value).toBeCloseTo(.2)
    expect(year.months[2].difference).toBeNull()
  })

  it('computes each year from its own closing boundary and stops at known return gaps', () => {
    const series = [{ date: '2024-12-31', value: 1 }, { date: '2025-12-31', value: 1.1 }, { date: '2026-01-30', value: 1.21 }, { date: '2026-02-02', value: null }, { date: '2026-02-27', value: 1.5 }]
    const matrix = buildResearchReturnMatrix(series, series)
    expect(matrix.map((row) => row.year)).toEqual(['2026', '2025'])
    expect(matrix[0].annual.actual?.value).toBeCloseTo(.1)
    expect(matrix[1].annual.actual).toMatchObject({ value: expect.any(Number), partial: false })
    expect(matrix[1].annual.actual?.value).toBeCloseTo(.1)
    expect(matrix[0].months[1].actual).toBeNull()
  })
})
