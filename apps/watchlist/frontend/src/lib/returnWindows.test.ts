import { describe, expect, it } from 'vitest'

import {
  namedReturnWindowSpec,
  normalizeCumulativeReturn,
  periodReturnPercent,
  resolveReturnWindow,
} from './returnWindows'

describe('canonical return window policy', () => {
  const points = [
    { date: '2025-12-31', value: 100 },
    { date: '2026-01-02', value: 101 },
    { date: '2026-06-30', value: 110 },
    { date: '2026-07-01', value: 111 },
    { date: '2026-07-02', value: 112 },
    { date: '2026-07-08', value: 120 },
  ]

  it('uses the selected start close for a custom 2-to-8 range', () => {
    const window = resolveReturnWindow(points, '2026-07-02', '2026-07-08')

    expect(window?.anchorDate).toBe('2026-07-02')
    expect(window?.endDate).toBe('2026-07-08')
    expect(window && periodReturnPercent(window)).toBeCloseTo((120 / 112 - 1) * 100)
    expect(window && normalizeCumulativeReturn(window)[0].value).toBe(0)
  })

  it('uses the previous month-end close for MTD', () => {
    const spec = namedReturnWindowSpec('MTD', '2026-07-08')
    const window = resolveReturnWindow(points, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2026-06-30')
  })

  it('uses the previous year-end close for YTD', () => {
    const spec = namedReturnWindowSpec('YTD', '2026-07-08')
    const window = resolveReturnWindow(points, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2025-12-31')
  })

  it('exposes the actual sparse-series anchor and endpoint', () => {
    const sparse = [
      { date: '2026-07-03', value: 100 },
      { date: '2026-07-10', value: 105 },
    ]
    const spec = namedReturnWindowSpec('1D', '2026-07-10')
    const window = resolveReturnWindow(sparse, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2026-07-03')
    expect(window?.endDate).toBe('2026-07-10')
  })

  it('clamps calendar-month lookbacks instead of overflowing month end', () => {
    expect(namedReturnWindowSpec('1M', '2026-03-31')).toMatchObject({
      start: '2026-02-28',
      end: '2026-03-31',
      anchorMode: 'on_or_before',
    })
  })
})
