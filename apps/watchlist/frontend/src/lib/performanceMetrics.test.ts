import { describe, expect, it } from 'vitest'

import {
  buildPerformanceMetricPeriodSnapshots,
  buildPerformanceMetricSnapshot,
} from './performanceMetrics'

describe('performance metrics', () => {
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
