import { describe, expect, it } from 'vitest'

import {
  buildPerformanceMetricPeriodSnapshots,
  buildPerformanceMetricSnapshot,
} from './performanceMetrics'

describe('performance metrics', () => {
  it('uses all UTC days for crypto risk and withholds path metrics across a missing day', () => {
    const points = [
      { date: '2026-09-04', value: 100 },
      { date: '2026-09-05', value: 99 },
      { date: '2026-09-06', value: 101 },
      { date: '2026-09-07', value: 100 },
    ]
    const returns = [-0.01, 101 / 99 - 1, 100 / 101 - 1]
    const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length
    const variance = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / 2
    const full = buildPerformanceMetricSnapshot(points, { continuousDaily: true })
    expect(full.annualizedVolatility).toBeCloseTo(Math.sqrt(variance * 365.25) * 100)
    expect(full.maxDrawdown).not.toBeNull()
    const missingSunday = buildPerformanceMetricPeriodSnapshots(
      points.filter((point) => point.date !== '2026-09-06'), { continuousDaily: true },
    ).find((period) => period.key === 'SI')!.snapshot
    expect(missingSunday.periodReturn).toBe(0)
    for (const key of ['annualizedVolatility', 'sharpe', 'sortino', 'calmar', 'maxDrawdown', 'recoveryDays'] as const) {
      expect(missingSunday[key]).toBeNull()
    }
    expect(missingSunday.recoveryOpen).toBe(false)
  })

  it('calculates drawdown and recovery from the supplied index path', () => {
    const snapshot = buildPerformanceMetricSnapshot([
      { date: '2026-01-01', value: 100 },
      { date: '2026-01-02', value: 110 },
      { date: '2026-01-03', value: 88 },
      { date: '2026-01-10', value: 111 },
    ])

    expect(snapshot.periodReturn).toBeCloseTo(11)
    expect(snapshot.maxDrawdown).toBeCloseTo(-20)
    expect(snapshot.recoveryDays).toBe(7)
    expect(snapshot.recoveryOpen).toBe(false)
  })

  it('anchors MTD to the last observation before the current month', () => {
    const periods = buildPerformanceMetricPeriodSnapshots([
      { date: '2025-12-31', value: 90 },
      { date: '2026-01-30', value: 95 },
      { date: '2026-02-27', value: 100 },
      { date: '2026-03-31', value: 110 },
    ])

    expect(periods.find(({ key }) => key === 'MTD')?.snapshot.periodReturn).toBeCloseTo(10)
    expect(periods.find(({ key }) => key === 'YTD')?.snapshot.periodReturn).toBeCloseTo(22.2222, 3)
    expect(periods.find(({ key }) => key === 'SI')?.snapshot.periodReturn).toBeCloseTo(22.2222, 3)
  })

  it('annualizes an exact one-year anniversary window', () => {
    const snapshot = buildPerformanceMetricSnapshot([
      { date: '2025-03-31', value: 100 },
      { date: '2025-09-30', value: 104 },
      { date: '2026-03-31', value: 110 },
    ])

    expect(snapshot.periodReturn).toBeCloseTo(10)
    expect(snapshot.annualizedReturn).toBeCloseTo(10)
  })
})
